"""Transport interchangeability, immutable mapping and public-data boundaries."""

import io
import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from conftest import reading
from pydantic import ValidationError
from urban_water.datasets import freeze, verify_dataset
from urban_water.db import PROJECT_ROOT, connect, migrate
from urban_water.ingestion import _stage_rows, accept, claim, process, work_once
from urban_water.processing import summarize
from urban_water.settings import Settings
from urban_water.sources import SourceSpec, collect, load_source
from urban_water.sources.config import RESEARCH_CONTRACT, options_digest
from urban_water.sources.normalizers import (
    NormalizationError,
    NormalizedRow,
    get_normalizer,
    qualify,
)
from urban_water.sources.readers import AcquisitionError, get_reader
from urban_water.sources.registration import register_file

DEMO = PROJECT_ROOT / "configs/sources/local-demo.toml"
CSV = PROJECT_ROOT / "examples/sources/measurements.csv"
REGISTRY = PROJECT_ROOT / "examples/sources/registry.json"


@contextmanager
def http_source(body: bytes, *, status=200, declared_length=None, encoding=None):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(status)
            self.send_header(
                "Content-Length", str(len(body) if declared_length is None else declared_length)
            )
            if status == 302:
                self.send_header("Location", "/redirect-target")
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/data", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def csv_spec(**changes):
    payload = load_source(DEMO, "meter_readings").model_dump()
    payload.update(changes)
    return SourceSpec.model_validate(payload)


def rows(spec, body=None, **limits):
    normalizer = get_normalizer(spec.contract, spec.parser, spec.options)
    return list(
        normalizer.rows(
            io.BytesIO(CSV.read_bytes() if body is None else body),
            limits.get("max_line_bytes", 65536),
            limits.get("max_records", 100),
        )
    )


def total(settings):
    return sum((Decimal(r["observed_liters"]) for r in summarize(settings)["rows"]), Decimal(0))


def test_config_paths_are_relative_to_config_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert Path(load_source(DEMO, "meter_readings").path) == CSV.resolve()


@pytest.mark.parametrize(
    "changes",
    [
        {"reader": "load_arbitrary_python"},
        {"family": "weather"},
        {"normalizer": "guess"},
        {"unexpected": True},
        {"namespace": "x:y"},
        {"path": None},
        {"path_env": "EXTRA"},
        {"fields": {}},
        {"delimiter": "\n"},
        {"timeout_seconds": float("nan")},
        {"constants": {"private_field": "x"}},
    ],
)
def test_configuration_fails_closed(changes):
    with pytest.raises(ValueError):
        csv_spec(**changes)


def test_missing_env_and_unknown_point(tmp_path):
    config = tmp_path / "source.toml"
    config.write_text(
        '[sources.meter_readings]\nnamespace="test"\npath_env="UW_MISSING_987"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="environment variable"):
        load_source(config, "meter_readings")
    with pytest.raises(ValueError, match="unknown source"):
        load_source(DEMO, "missing")


def test_csv_preserves_decimal_zero_intervals_and_utf8_header():
    result = rows(csv_spec(), b"\xef\xbb\xbf" + CSV.read_bytes())
    assert [r.record.value for r in result] == [Decimal("12.5"), Decimal("0"), Decimal("7.25")]
    assert all(r.record.interval_start < r.record.occurred_at for r in result)
    assert all(r.issue is None for r in result)


@pytest.mark.parametrize(
    "header",
    [
        "reading_id,reading_id\n",
        "reading_id,,volume_l\n",
        "unmapped\n",
        "",
    ],
)
def test_bad_csv_header_is_a_source_error(header):
    with pytest.raises(NormalizationError):
        rows(csv_spec(), header.encode())


def test_bad_csv_rows_quarantined_without_zero_imputation():
    body = CSV.read_bytes().replace(b",12.5\n", b",unknown\n")
    result = rows(csv_spec(), body)
    assert result[0].record is None and result[0].issue == "invalid_contract"
    assert result[1].record.value == Decimal("0")
    assert result[2].record.value == Decimal("7.25")


def test_limits_and_unknown_parser():
    with pytest.raises(NormalizationError, match="record limit"):
        rows(csv_spec(), max_records=2)
    with pytest.raises(NormalizationError, match="line limit"):
        rows(csv_spec(), max_line_bytes=10)
    with pytest.raises(NormalizationError, match="parser"):
        get_normalizer(RESEARCH_CONTRACT, "future/99", {})


def test_json_duplicate_fields_and_schema_mismatch_are_not_accepted():
    normalizer = get_normalizer(RESEARCH_CONTRACT, "measurement-jsonl/1", {})
    legacy = json.dumps(reading()).encode()
    duplicate = legacy[:-1] + b',"schema_version":"urban-water.measurement.v1"}\n'
    result = list(normalizer.rows(io.BytesIO(legacy + b"\n" + duplicate), 65536, 10))
    assert all(r.record is None for r in result)


def test_namespace_is_transport_independent_and_preserves_revision():
    record = rows(csv_spec())[0].record
    assert qualify(record, "a").event_id == qualify(record, "a").event_id
    assert qualify(record, "a").event_id != qualify(record, "b").event_id
    assert qualify(record, "legacy").event_id == record.event_id
    assert (
        qualify(record.model_copy(update={"revision": 2}), "a").event_id
        == qualify(record, "a").event_id
    )


@pytest.mark.parametrize(
    "status,declared_length,encoding",
    [
        (302, None, None),
        (500, None, None),
        (200, 100000, None),
        (200, 9, None),
        (200, None, "gzip"),
    ],
)
def test_http_rejects_redirect_error_oversize_incomplete_or_encoded(
    monkeypatch,
    status,
    declared_length,
    encoding,
):
    spec = csv_spec(reader="http_file_v1", path=None, url_env="UW_TEST_HTTP")
    with http_source(
        b"small", status=status, declared_length=declared_length, encoding=encoding
    ) as (url, seen):
        monkeypatch.setenv("UW_TEST_HTTP", url)
        with pytest.raises(AcquisitionError):
            with get_reader(spec.reader).acquire(spec, 1000):
                pytest.fail("invalid HTTP response must not be accepted")
        assert len(seen) == 1


def test_http_token_is_env_only_and_temp_file_is_cleaned(monkeypatch):
    spec = csv_spec(
        reader="http_file_v1", path=None, url_env="UW_TEST_HTTP", token_env="UW_TEST_TOKEN"
    )
    monkeypatch.setenv("UW_TEST_TOKEN", "test-secret")
    with http_source(b"data") as (url, seen):
        monkeypatch.setenv("UW_TEST_HTTP", url)
        with get_reader(spec.reader).acquire(spec, 1000) as path:
            assert path.read_bytes() == b"data"
        assert not path.exists()
        assert seen == ["Bearer test-secret"]
    assert "test-secret" not in spec.model_dump_json()


def test_http_rejects_unencrypted_remote_and_missing_token(monkeypatch):
    spec = csv_spec(reader="http_file_v1", path=None, url_env="UW_TEST_HTTP")
    monkeypatch.setenv("UW_TEST_HTTP", "http://example.com/data")
    with pytest.raises(AcquisitionError):
        with get_reader(spec.reader).acquire(spec, 1000):
            pass


@pytest.mark.postgres
def test_file_and_http_use_same_layer_and_do_not_double_volume(settings, monkeypatch):
    register_file(settings, REGISTRY)
    first = collect(settings, DEMO, "meter_readings")
    assert work_once(settings)["accepted_rows"] == 3
    assert total(settings) == Decimal("19.75")
    with http_source(CSV.read_bytes()) as (url, _):
        monkeypatch.setenv("UW_METER_URL", url)
        duplicate = collect(
            settings, PROJECT_ROOT / "configs/sources/http-demo.toml", "meter_readings"
        )
    assert duplicate == {"source_id": first["source_id"], "duplicate": True}
    # Different raw bytes, same logical events: retain both originals, still count once.
    spec = csv_spec(reader="http_file_v1", path=None, url_env="UW_METER_URL")
    with http_source(CSV.read_bytes().replace(b"\n", b"\r\n")) as (url, _):
        monkeypatch.setenv("UW_METER_URL", url)
        with get_reader(spec.reader).acquire(spec, settings.max_source_bytes) as path:
            second = accept(settings, path, source=spec)
    assert not second["duplicate"]
    assert work_once(settings)["accepted_rows"] == 3
    assert total(settings) == Decimal("19.75")
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM measurement").fetchone()["n"] == 3
        assert conn.execute("SELECT count(*) AS n FROM provenance").fetchone()["n"] == 6
    frozen = freeze(settings)
    assert verify_dataset(settings, frozen["dataset_id"])["verified"]
    assert {s["acquisition"]["reader_version"] for s in frozen["sources"]} == {
        "local-file/1",
        "http-file/1",
    }
    assert all(
        s["options_sha256"] == options_digest(s["normalizer_options"]) for s in frozen["sources"]
    )


@pytest.mark.postgres
def test_csv_crash_resume_and_configuration_pin(settings, tmp_path):
    register_file(settings, REGISTRY)
    accepted = accept(settings, CSV, source=csv_spec())
    job = claim(settings)
    staged = [
        NormalizedRow(r.number, qualify(r.record, "local-demo")) for r in rows(csv_spec())[:2]
    ]
    _stage_rows(settings, job, staged, 2)
    assert total(settings) == 0
    with connect(settings) as conn:
        conn.execute("UPDATE job SET lease_until=clock_timestamp()-interval '1 second'")
    resumed = claim(settings)
    assert resumed["checkpoint"] == 2
    assert process(settings, resumed)["accepted_rows"] == 3
    assert total(settings) == Decimal("19.75")
    with connect(settings) as conn:
        source = conn.execute(
            "SELECT * FROM source WHERE id=%s", (accepted["source_id"],)
        ).fetchone()
    assert source["normalizer_options"] == csv_spec().options
    with pytest.raises(psycopg.errors.CheckViolation):
        with connect(settings) as conn:
            conn.execute(
                "UPDATE source SET normalizer_options='{}' WHERE id=%s", (accepted["source_id"],)
            )


@pytest.mark.postgres
def test_mapping_change_is_new_source_and_cannot_silently_rewrite_events(settings):
    register_file(settings, REGISTRY)
    first = accept(settings, CSV, source=csv_spec())
    assert work_once(settings)["state"] == "succeeded"
    options = csv_spec().model_dump()
    del options["fields"]["value"]
    options["constants"]["value"] = "99"
    second = accept(settings, CSV, source=SourceSpec.model_validate(options))
    assert second["source_id"] != first["source_id"] and not second["duplicate"]
    assert work_once(settings)["error_code"] == "SourceConflict"
    assert total(settings) == Decimal("19.75")


@pytest.mark.postgres
def test_public_research_environment_registry_and_neutral_contract(restore_settings):
    public = replace(
        restore_settings,
        environment="public_research",
        study_code="public-study",
        cohort_code="public",
    )
    migrate(public)
    assert register_file(public, REGISTRY)["environment"] == "public_research"
    assert register_file(public, REGISTRY)["registered_entries"] == 1  # idempotent
    spec = csv_spec(
        data_kind="public_research",
        namespace="public-demo",
        citation="test-only public-data transport fixture",
        license="test-fixture",
    )
    accepted = accept(public, CSV, source=spec)
    assert work_once(public)["accepted_rows"] == 3
    assert total(public) == Decimal("19.75")
    # A JSONL delivery of the same canonical events does not require model/summary changes.
    path = public.storage_root.parent / "neutral.jsonl"
    path.write_text(
        "".join(r.record.model_dump_json() + "\n" for r in rows(spec)), encoding="utf-8"
    )
    jsonl = SourceSpec(namespace=spec.namespace, data_kind="public_research", path=str(path))
    accept(public, path, source=jsonl)
    assert work_once(public)["state"] == "succeeded"
    assert total(public) == Decimal("19.75")
    frozen = freeze(public)
    assert frozen["environment"] == "public_research"
    assert frozen["sources"][0]["contract_version"] == RESEARCH_CONTRACT
    assert verify_dataset(public, frozen["dataset_id"])["verified"]
    # Synthetic settings cannot relabel an already registered public database.
    with pytest.raises(ValueError):
        accept(replace(public, environment="synthetic"), CSV)
    assert accepted["duplicate"] is False


@pytest.mark.postgres
def test_public_data_is_rejected_before_acquisition_in_synthetic_environment(settings):
    spec = csv_spec(data_kind="public_research", namespace="public-demo")
    with pytest.raises(ValueError, match="environment"):
        accept(settings, CSV, source=spec)
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM source").fetchone()["n"] == 0


@pytest.mark.postgres
def test_registry_conflict_rolls_back_and_does_not_reassign_household(settings, tmp_path):
    register_file(settings, REGISTRY)
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    data["entries"][0]["household_code"] = "OTHER-HOUSE"
    path = tmp_path / "changed-registry.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="different content"):
        register_file(settings, path)
    with connect(settings) as conn:
        assert conn.execute("SELECT 1 FROM household WHERE code='OTHER-HOUSE'").fetchone() is None


def test_settings_does_not_enable_institution_environment(tmp_path):
    with pytest.raises(ValueError):
        Settings(database_url="not-used", storage_root=tmp_path, environment="production")
    with pytest.raises(ValidationError):
        csv_spec(namespace="legacy", data_kind="public_research")
