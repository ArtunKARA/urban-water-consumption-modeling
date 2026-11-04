"""Aalborg export clock/identity semantics and pinned-reference regressions."""

import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import urban_water.sources.aalborg_reference as reference
from conftest import reading
from urban_water.datasets import freeze, verify_dataset
from urban_water.db import connect, migrate
from urban_water.ingestion import SourceConflict, accept, work_once
from urban_water.processing import summarize
from urban_water.sources import SourceSpec, collect, load_source
from urban_water.sources.aalborg import HEADER, AalborgDailyCsv
from urban_water.sources.aalborg_reference import audit_daily, fetch_reference, prepare_reference
from urban_water.sources.config import RESEARCH_CONTRACT
from urban_water.sources.normalizers import NormalizationError, get_normalizer
from urban_water.sources.registration import register_file

RECEIPT = "2026-11-04T10:00:00+00:00"


def csv_bytes(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(HEADER)
    for changes in rows:
        raw = {
            "building": "BUILD-D-01",
            "date_time": "2022-01-01T11:00:00Z",
            "date": "2022-01-01",
            "water_use_l": "10",
            **{f"occ_{i:02}": "Uncertain" for i in range(1, 6)},
            **changes,
        }
        writer.writerow([raw[key] for key in HEADER])
    return output.getvalue().encode()


def normalize(rows):
    return list(AalborgDailyCsv(RECEIPT).rows(io.BytesIO(csv_bytes(rows)), 65536, 100))


def test_previous_hour_end_and_noon_label_are_separate():
    row = normalize([{}])[0].record
    assert row.occurred_at == datetime(2022, 1, 1, 11, tzinfo=UTC)
    assert row.interval_start == datetime(2022, 1, 1, 10, tzinfo=UTC)
    assert row.value == Decimal("10") and row.unit == "L"
    assert row.received_at == datetime.fromisoformat(RECEIPT)
    assert row.stream_code == "AALBORG-D-01-hourly"
    assert not any(key.startswith("occ_") or key == "date" for key in row.model_dump())


@pytest.mark.parametrize("day,label", [("2022-03-27", "2022-03-26"), ("2022-10-30", "2022-10-29")])
def test_dst_changes_do_not_change_period_length_or_collapse_events(day, label):
    result = normalize(
        [
            {"date_time": f"{day}T00:00:00Z", "date": label},
            {"date_time": f"{day}T01:00:00Z", "date": label},
        ]
    )
    a, b = [r.record for r in result]
    assert a.event_id != b.event_id
    assert a.occurred_at == b.interval_start
    assert (a.occurred_at - a.interval_start).total_seconds() == 3600
    assert (b.occurred_at - b.interval_start).total_seconds() == 3600


def test_value_or_row_order_does_not_change_source_event_identity():
    a = normalize([{}])[0].record
    b = normalize([{"water_use_l": "20", "occ_01": "Not Occupied"}])[0].record
    assert a.event_id == b.event_id
    assert a.value != b.value


@pytest.mark.parametrize(
    "changes",
    [
        {"water_use_l": "-1"},
        {"water_use_l": "NaN"},
        {"water_use_l": ""},
        {"water_use_l": "0.5"},
        {"building": "UNPUBLISHED-HOUSE"},
        {"date_time": "2022-01-01T11:00:00"},
        {"date_time": "2022-01-01T11:30:00Z"},
        {"date": "2022-01-02"},
    ],
)
def test_invalid_reference_rows_are_quarantined(changes):
    result = normalize([changes, {"date_time": "2022-01-01T12:00:00Z", "water_use_l": "0"}])
    assert result[0].record is None and result[0].issue == "invalid_contract"
    assert result[1].record.value == 0


def test_reference_requires_known_header_and_public_preprocessed_policy():
    with pytest.raises(NormalizationError):
        list(AalborgDailyCsv(RECEIPT).rows(io.BytesIO(b"building,volume\n"), 65536, 10))
    with pytest.raises(ValueError):
        SourceSpec(namespace="a", path="unused.csv", normalizer="aalborg_daily_v1")
    with pytest.raises(ValueError):
        get_normalizer(
            "synthetic.measurement.v1", "aalborg-daily-csv/1", {"archive_received_at": RECEIPT}
        )
    with pytest.raises(ValueError):
        get_normalizer(
            RESEARCH_CONTRACT, "aalborg-daily-csv/1", {"archive_received_at": "2026-11-04T10:00:00"}
        )


@pytest.fixture
def reference_cache(tmp_path, monkeypatch):
    data = csv_bytes([{}, {"date_time": "2022-01-01T12:00:00Z", "water_use_l": "0"}])
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("final_data/daily_data.csv", data)
        z.writestr("../must-not-extract.txt", "untrusted extra member")
    payload = archive.getvalue()
    catalog = {
        "record_id": "15180141",
        "doi": "test-reference",
        "report_doi": "test-report",
        "authors": "test fixture",
        "license": "CC-BY-4.0",
        "metadata_url": "test-metadata",
        "zip_url": "test-zip",
        "zip_bytes": len(payload),
        "zip_sha256": reference.digest(payload),
        "zip_md5": hashlib.md5(payload).hexdigest(),
        "selected_member": "final_data/daily_data.csv",
        "csv_bytes": len(data),
        "csv_sha256": reference.digest(data),
        "upstream_scripts": [],
        "expected_rows": 2,
        "expected_buildings": 1,
        "expected_total_liters": "10",
        "timezone": "Europe/Copenhagen",
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    metadata = json.dumps(
        {"id": 15180141, "metadata": {"license": {"id": "cc-by-4.0"}, "access_right": "open"}}
    ).encode()
    monkeypatch.setattr(reference, "CATALOG", path)
    calls = []

    def fetch(url, limit):
        calls.append(url)
        return {"test-metadata": metadata, "test-zip": payload}[url]

    monkeypatch.setattr(reference, "_fetch", fetch)
    return tmp_path / "reference", catalog, calls


def test_prepare_preserves_original_and_reuses_receipt_offline(reference_cache):
    root, catalog, calls = reference_cache
    first = prepare_reference(root)
    spec = load_source(root / "sources.toml", "meter_readings")
    assert spec.expected_sha256 == catalog["csv_sha256"]
    assert spec.receipt_basis == "research_archive_import"
    assert spec.value_semantics == "upstream_processed_volume"
    assert spec.reference_sha256 == reference.digest(reference.CATALOG.read_bytes())
    assert (root / "registry.json").is_file()
    assert not (root / "raw/must-not-extract.txt").exists()
    second = prepare_reference(root, offline=True)
    assert second == first and len(calls) == 2
    assert (
        spec.archive_received_at
        == load_source(root / "sources.toml", "meter_readings").archive_received_at
    )


def test_changed_cached_original_is_not_silently_refetched(reference_cache):
    root, _, calls = reference_cache
    fetch_reference(root)
    with (root / "raw/final_data.zip").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        fetch_reference(root, offline=True)
    assert len(calls) == 2


def test_source_side_inventory_rejects_duplicate_hour_and_changed_total(tmp_path):
    path = tmp_path / "daily.csv"
    path.write_bytes(csv_bytes([{}, {}]))
    expected = {
        "timezone": "Europe/Copenhagen",
        "expected_rows": 2,
        "expected_buildings": 1,
        "expected_total_liters": "20",
    }
    with pytest.raises(ValueError, match="duplicate"):
        audit_daily(path, expected=expected)
    path.write_bytes(csv_bytes([{}]))
    with pytest.raises(ValueError, match="inventory"):
        audit_daily(path, expected=expected)


@pytest.mark.postgres
def test_pinned_reference_checksum_is_checked_before_job_creation(settings, source_file):
    path = source_file([reading()])
    spec = SourceSpec(namespace="test", path=str(path), expected_sha256="0" * 64)
    with pytest.raises(SourceConflict, match="checksum"):
        accept(settings, path, source=spec)
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM source").fetchone()["n"] == 0


@pytest.mark.postgres
def test_prepared_reference_runs_through_common_layer(reference_cache, restore_settings):
    root, _, _ = reference_cache
    prepared = prepare_reference(root)
    settings = replace(
        restore_settings,
        environment="public_research",
        study_code="aalborg-test",
        cohort_code="daily",
        timezone="Europe/Copenhagen",
    )
    migrate(settings)
    register_file(settings, root / "registry.json")
    accepted = collect(settings, root / "sources.toml", "meter_readings")
    assert work_once(settings)["accepted_rows"] == 2
    report = summarize(settings)
    assert [Decimal(r["observed_liters"]) for r in report["rows"]] == [Decimal("10")]
    assert report["rows"][0]["household_code"] == "AALBORG-D-01"
    assert report["coverage_status"] == "unknown"
    assert collect(settings, root / "sources.toml", "meter_readings") == {
        "source_id": accepted["source_id"],
        "duplicate": True,
    }
    frozen = freeze(settings)
    assert frozen["value_semantics"] == ["upstream_processed_volume"]
    assert frozen["receipt_basis"] == ["research_archive_import"]
    assert frozen["sources"][0]["sha256"] == reference.digest(
        (root / "raw/final_data/daily_data.csv").read_bytes()
    )
    assert verify_dataset(settings, frozen["dataset_id"])["verified"]
    assert prepared["audit"]["source_total_liters"] == "10"


@pytest.mark.postgres
def test_cli_and_worker_use_explicit_public_environment(reference_cache, restore_settings):
    root, _, _ = reference_cache
    prepare_reference(root)
    settings = replace(
        restore_settings,
        environment="public_research",
        study_code="cli-reference",
        cohort_code="daily",
        timezone="Europe/Copenhagen",
    )
    migrate(settings)
    register_file(settings, root / "registry.json")
    env_file = root / "research.env"
    values = {
        "UW_ENV": "public_research",
        "UW_DATABASE_URL": settings.database_url,
        "UW_STORAGE_ROOT": str(settings.storage_root),
        "UW_STUDY_CODE": settings.study_code,
        "UW_COHORT_CODE": settings.cohort_code,
        "UW_TIMEZONE": settings.timezone,
    }
    env_file.write_text(
        "".join(f"{key}={json.dumps(value)}\n" for key, value in values.items()), encoding="utf-8"
    )
    child_env = {key: value for key, value in os.environ.items() if not key.startswith("UW_")}

    def command(module, *args):
        result = subprocess.run(
            [sys.executable, "-m", module, "--env-file", str(env_file), *args],
            env=child_env,
            capture_output=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stdout
        return json.loads(result.stdout)

    assert (
        command(
            "urban_water_cli.main",
            "collect",
            "meter_readings",
            "--config",
            str(root / "sources.toml"),
        )["duplicate"]
        is False
    )
    assert command("urban_water_worker.main", "--once")["accepted_rows"] == 2
    report = command("urban_water_cli.main", "summarize", "--grain", "day")
    assert report["study_code"] == "cli-reference"
    assert Decimal(report["rows"][0]["observed_liters"]) == 10
