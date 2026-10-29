"""Permanent counterexamples for audit F01-F10, using the isolated PostgreSQL fixture."""

import gzip
import json
import shutil
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pyarrow.parquet as pq
import pytest
import urban_water.ingestion as ingestion
from conftest import reading
from urban_water.backup import create_backup, restore_backup
from urban_water.datasets import file_sha, freeze, verify_dataset
from urban_water.db import PROJECT_ROOT, connect, migrate
from urban_water.leases import renew
from urban_water.processing import _bucket, summarize
from urban_water.registry import (
    ConfigurationPeriod,
    configure_stream,
    seed_synthetic,
    stable_id,
)
from urban_water.storage import FileStore
from urban_water_worker import main as worker

pytestmark = pytest.mark.postgres


def load(settings, source_file, records):
    accepted = ingestion.accept(settings, source_file(records))
    outcome = ingestion.work_once(settings)
    assert outcome["state"] in {"succeeded", "partial", "insufficient_data"}, outcome
    return accepted, outcome


def total(settings):
    return sum((Decimal(r["observed_liters"]) for r in summarize(settings)["rows"]), Decimal(0))


@pytest.mark.parametrize(
    "filename", ["observed_volume.parquet", "manifest.json", "normalization.parquet"]
)
def test_f01_backup_rejects_already_corrupt_dataset(settings, source_file, tmp_path, filename):
    load(settings, source_file, [reading()])
    frozen = freeze(settings)
    with (Path(frozen["directory"]) / filename).open("ab") as stream:
        stream.write(b"corruption")
    backup = tmp_path / "backup"
    with pytest.raises(ValueError):
        create_backup(settings, backup)
    assert not (backup / "manifest.json").exists()


@pytest.mark.parametrize(
    "filename", ["observed_volume.parquet", "manifest.json", "normalization.parquet"]
)
def test_f01_restore_checks_db_manifest_not_only_transport_hash(
    settings,
    restore_settings,
    source_file,
    tmp_path,
    filename,
):
    load(settings, source_file, [reading()])
    frozen = freeze(settings)
    backup = tmp_path / "backup"
    create_backup(settings, backup)
    # Simulate an older/bad backup producer that hashes an already-corrupt artifact.
    path = next((backup / "objects" / "datasets").rglob(filename))
    with path.open("ab") as stream:
        stream.write(b"corruption")
    manifest = json.loads((backup / "manifest.json").read_text())
    manifest["files"][path.relative_to(backup).as_posix()] = file_sha(path)
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        restore_backup(restore_settings, backup)
    with pytest.raises(ValueError):
        verify_dataset(restore_settings, frozen["dataset_id"])


@pytest.mark.parametrize("target", ["observed_volume.parquet", ".gz"])
def test_f01_corruption_while_copying_cannot_complete_backup(
    settings,
    source_file,
    tmp_path,
    monkeypatch,
    target,
):
    load(settings, source_file, [reading()])
    freeze(settings)
    copy = shutil.copyfile

    def corrupt_copy(source, destination):
        result = copy(source, destination)
        if str(source).endswith(target):
            with Path(destination).open("ab") as stream:
                stream.write(b"corruption")
        return result

    monkeypatch.setattr("urban_water.backup.shutil.copyfile", corrupt_copy)
    backup = tmp_path / "backup"
    with pytest.raises((ValueError, OSError)):
        create_backup(settings, backup)
    assert not (backup / "manifest.json").exists()


def test_f02_configuration_payloads_are_immutable_in_database(settings):
    for statement in (
        "UPDATE stream SET liters_per_pulse=0.2 WHERE code='S0001-pulse'",
        "UPDATE stream_configuration SET liters_per_pulse=0.2 WHERE stream_id="
        "(SELECT id FROM stream WHERE code='S0001-pulse')",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            with connect(settings) as conn:
                conn.execute(statement)


def test_f02_revision_between_chunks_keeps_old_pin_and_frozen_reproduction(settings, source_file):
    path = source_file([reading(), reading(event="e2")])
    ingestion.accept(settings, path)
    job = ingestion.claim(settings)
    ingestion._stage_chunk(settings, job, [(1, path.read_bytes().splitlines()[0])], 1)
    configure_stream(
        settings,
        "S0001-pulse",
        [
            ConfigurationPeriod(
                datetime(2000, 1, 1, tzinfo=UTC),
                liters_per_pulse=Decimal("0.2"),
            )
        ],
    )
    job["checkpoint"] = 1
    assert ingestion.process(settings, job)["accepted_rows"] == 2
    assert total(settings) == Decimal("0.2")
    frozen = freeze(settings)
    assert len(frozen["configurations"]) == 1
    assert frozen["configurations"][0]["liters_per_pulse"] == "0.100000000"
    rows = pq.read_table(Path(frozen["directory"]) / "normalization.parquet").to_pylist()
    assert {r["configuration_id"] for r in rows} == {frozen["configurations"][0]["id"]}
    assert sum(Decimal(r["original_value"]) * Decimal("0.1") for r in rows) == total(settings)
    # An explicit source revision can use the new config, without changing the frozen extract.
    load(settings, source_file, [reading(revision=2)])
    assert total(settings) == Decimal("0.3")
    assert verify_dataset(settings, frozen["dataset_id"])["verified"]


def test_f02_upgrade_backfills_existing_measurements_without_rewriting_volume(
    restore_settings,
    tmp_path,
):
    directory = tmp_path / "legacy-migrations"
    directory.mkdir()
    for path in sorted((PROJECT_ROOT / "migrations").glob("00[1-3]*.sql")):
        shutil.copyfile(path, directory / path.name)
    migrate(restore_settings, directory)
    seed_synthetic(restore_settings, 1)
    with connect(restore_settings) as conn:
        conn.execute(
            """INSERT INTO measurement(stream_id,event_id,revision,kind,occurred_at,
            original_time,original_value,original_unit,liters,fingerprint)
            VALUES (%s,'legacy',1,'pulse','2026-01-01T00:00:00Z',
                    '2026-01-01T00:00:00Z',1,'pulse',0.1,%s)""",
            (stable_id("stream-1-pulse"), "a" * 64),
        )
    assert migrate(restore_settings) == [
        "004_versioned_stream_configuration.sql",
        "005_source_adapters.sql",
    ]
    with connect(restore_settings) as conn:
        row = conn.execute("SELECT * FROM current_measurement").fetchone()
        assert row["configuration_id"] is not None
        assert row["liters"] == Decimal("0.1")


def test_f02_adjacent_configurations_selected_by_event_time(settings, source_file):
    boundary = datetime.fromisoformat("2026-01-02T00:00:00Z")
    configure_stream(
        settings,
        "S0001-pulse",
        [
            ConfigurationPeriod(datetime(2000, 1, 1, tzinfo=UTC), boundary, Decimal("0.1")),
            ConfigurationPeriod(boundary, None, Decimal("0.2")),
        ],
    )
    load(
        settings,
        source_file,
        [
            reading(time="2026-01-01T23:59:59Z"),
            reading(event="new-config", time=boundary.isoformat()),
        ],
    )
    assert total(settings) == Decimal("0.3")
    frozen = freeze(settings)
    assert {r["version"] for r in frozen["configurations"]} == {2, 3}
    assert "liters_per_pulse" not in frozen["streams"][0]


def test_f02_configuration_precision_cannot_be_silently_rounded(settings):
    with pytest.raises(ValueError, match="precision"):
        configure_stream(
            settings,
            "S0001-pulse",
            [
                ConfigurationPeriod(
                    datetime(2000, 1, 1, tzinfo=UTC),
                    liters_per_pulse=Decimal("0.1234567891"),
                )
            ],
        )
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT * FROM stream_configuration WHERE stream_id=%s", (stable_id("stream-1-pulse"),)
        ).fetchone()
        assert row["version"] == 1 and row["retired_at"] is None


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", "2"),
        ("2025-12-31T23:59:59Z", "2026-01-01T00:30:00Z", "0"),
        ("2026-01-01T00:30:00Z", "2026-01-01T01:00:01Z", "0"),
    ],
)
def test_f03_entire_half_open_interval_must_fit_configuration(
    settings,
    source_file,
    start,
    end,
    expected,
):
    seed_synthetic(settings, 1, "interval")
    configure_stream(
        settings,
        "S0001-interval",
        [
            ConfigurationPeriod(
                datetime.fromisoformat("2026-01-01T00:00:00Z"),
                datetime.fromisoformat("2026-01-01T01:00:00Z"),
            )
        ],
    )
    load(
        settings,
        source_file,
        [
            reading(
                kind="interval",
                stream_code="S0001-interval",
                unit="L",
                value="2",
                interval_start=start,
                time=end,
            )
        ],
    )
    assert total(settings) == Decimal(expected)


def test_f03_pulse_end_excluded_but_index_closing_reference_included(settings, source_file):
    start = datetime.fromisoformat("2026-01-01T00:00:00Z")
    end = datetime.fromisoformat("2026-01-01T01:00:00Z")
    configure_stream(settings, "S0001-pulse", [ConfigurationPeriod(start, end, Decimal("0.1"))])
    _, outcome = load(settings, source_file, [reading(time=end.isoformat())])
    assert outcome["quarantined_rows"] == 1
    seed_synthetic(settings, 1, "index")
    configure_stream(settings, "S0001-index", [ConfigurationPeriod(start, end)])
    load(
        settings,
        source_file,
        [
            reading(
                event="i1",
                kind="index",
                stream_code="S0001-index",
                unit="L",
                value="10",
                time=start.isoformat(),
            ),
            reading(
                event="i2",
                kind="index",
                stream_code="S0001-index",
                unit="L",
                value="12",
                time=end.isoformat(),
            ),
        ],
    )
    assert total(settings) == Decimal("2")


def test_f04_f05_cohort_population_manifest_and_dependencies_are_scoped(settings, source_file):
    first, _ = load(settings, source_file, [reading()])
    other, _ = load(settings, source_file, [reading(event="h2", stream_code="S0002-pulse")])
    cohort_id = uuid4()
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO cohort(id,study_id,code) VALUES (%s,%s,'only-h1')",
            (cohort_id, stable_id("study")),
        )
        conn.execute(
            "INSERT INTO membership VALUES (%s,%s,'2000-01-01T00:00:00Z',NULL)",
            (cohort_id, stable_id("household-1")),
        )
        key = conn.execute(
            "SELECT object_key FROM source WHERE id=%s", (other["source_id"],)
        ).fetchone()["object_key"]
    scoped = replace(settings, cohort_code="only-h1")
    summary = summarize(scoped)
    assert summary["population"] == {
        "registered_households": 1,
        "represented_households": 1,
        "input_observations": 1,
    }
    FileStore(settings).path(key).write_bytes(b"unrelated corruption")
    frozen = freeze(scoped)
    assert [r["code"] for r in frozen["households"]] == ["H0001"]
    assert [r["id"] for r in frozen["sources"]] == [first["source_id"]]
    assert {r["cohort_id"] for r in frozen["memberships"]} == {str(cohort_id)}
    assert len(frozen["streams"]) == len(frozen["configurations"]) == 1
    assert verify_dataset(scoped, frozen["dataset_id"])["verified"]


def test_f05_mixed_source_keeps_provenance_but_filters_registry(settings, source_file):
    accepted, _ = load(
        settings, source_file, [reading(), reading(event="h2", stream_code="S0002-pulse")]
    )
    with connect(settings) as conn:
        conn.execute("DELETE FROM membership WHERE household_id=%s", (stable_id("household-2"),))
    frozen = freeze(settings)
    assert frozen["row_count"] == 1
    assert len(frozen["households"]) == 1
    assert frozen["sources"][0]["id"] == accepted["source_id"]
    assert frozen["normalization"]["row_count"] == 1


def test_f05_scoped_snapshot_keeps_required_revision_ancestors(settings, source_file):
    first, _ = load(settings, source_file, [reading()])
    second, _ = load(settings, source_file, [reading(revision=2, value="2")])
    frozen = freeze(settings)
    assert {r["id"] for r in frozen["sources"]} == {first["source_id"], second["source_id"]}
    history = pq.read_table(Path(frozen["directory"]) / "normalization.parquet").to_pylist()
    assert [(r["revision"], r["is_current"]) for r in history] == [(1, False), (2, True)]
    assert total(settings) == Decimal("0.2")


def test_f06_heartbeat_covers_slow_checksum(settings, source_file, monkeypatch):
    settings = replace(settings, lease_seconds=1, max_attempts=1)
    ingestion.accept(settings, source_file([reading()]))
    verify = FileStore.verify

    def slow_verify(self, *args):
        verify(self, *args)
        time.sleep(1.3)

    monkeypatch.setattr(FileStore, "verify", slow_verify)
    assert ingestion.work_once(settings)["state"] == "succeeded"
    assert total(settings) == Decimal("0.1")


def test_f06_heartbeat_covers_checkpoint_scan_and_fences_old_token(
    settings,
    source_file,
    monkeypatch,
):
    settings = replace(settings, lease_seconds=1)
    path = source_file([reading(event=f"e{i}") for i in range(4)])
    ingestion.accept(settings, path)
    job = ingestion.claim(settings)
    lines = path.read_bytes().splitlines()
    ingestion._stage_chunk(settings, job, list(enumerate(lines[:3], 1)), 3)
    job["checkpoint"] = 3
    original = gzip.open

    class SlowReplay:
        def __init__(self, *args):
            self.stream = original(*args)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, *args):
            return self.stream.read(*args)

        def readline(self, *args):
            time.sleep(0.35)
            return self.stream.readline(*args)

    monkeypatch.setattr("urban_water.ingestion.gzip.open", SlowReplay)
    assert ingestion.process(settings, job)["accepted_rows"] == 4
    assert total(settings) == Decimal("0.4")
    # A terminal/stale token cannot renew its old lease after publication.
    assert not renew(settings, job)


def test_f07_connection_failure_before_claim_recovers_without_consuming_attempt(
    settings,
    source_file,
    monkeypatch,
):
    ingestion.accept(settings, source_file([reading()]))
    claim = ingestion.claim
    failed = False

    def flaky(s):
        nonlocal failed
        if not failed:
            failed = True
            raise psycopg.OperationalError("simulated connection failure")
        return claim(s)

    monkeypatch.setattr(ingestion, "claim", flaky)
    assert ingestion.work_once(settings)["state"] == "connection_unavailable"
    assert ingestion.work_once(settings)["state"] == "succeeded"
    with connect(settings) as conn:
        assert conn.execute("SELECT attempts FROM job").fetchone()["attempts"] == 1


def test_f07_connection_failure_while_recording_failure_is_contained(
    settings,
    source_file,
    monkeypatch,
):
    ingestion.accept(settings, source_file([reading()]))

    def bad_process(*args):
        raise ValueError("invalid input")

    def disconnected(*args):
        raise psycopg.OperationalError("simulated outage during failure recording")

    monkeypatch.setattr(ingestion, "process", bad_process)
    monkeypatch.setattr(ingestion, "fail", disconnected)
    assert ingestion.work_once(settings)["state"] == "connection_unavailable"


def test_f07_worker_reconnects_with_bounded_backoff(monkeypatch):
    sequence = iter(
        [
            "connection_unavailable",
            "connection_unavailable",
            "succeeded",
            "connection_unavailable",
            "idle",
        ]
    )
    monkeypatch.setattr(worker, "work_once", lambda _: {"state": next(sequence)})

    class Stop:
        waits = []

        def is_set(self):
            return len(self.waits) >= 4

        def wait(self, delay):
            self.waits.append(delay)

    stop = Stop()
    worker.run_worker(None, poll_seconds=0.1, stop=stop)
    assert stop.waits == [0.1, 0.2, 0.1, 0.1]


def test_f08_optional_columns_do_not_break_bulk_loading(settings, source_file):
    with connect(settings) as conn:
        conn.execute("ALTER TABLE staged_measurement ADD COLUMN optional_note text DEFAULT 'new'")
        conn.execute("ALTER TABLE staged_quality_issue ADD COLUMN optional_note text DEFAULT 'new'")
    _, result = load(settings, source_file, [reading(), "invalid json"])
    assert result["accepted_rows"] == result["quarantined_rows"] == 1
    assert total(settings) == Decimal("0.1")


def test_f09_retroactive_migration_rejected_before_ddl(settings, tmp_path):
    directory = tmp_path / "migrations"
    shutil.copytree(PROJECT_ROOT / "migrations", directory)
    (directory / "000_retroactive.sql").write_text("CREATE TABLE uw.must_not_exist(id int);")
    with pytest.raises(ValueError, match="out-of-order"):
        migrate(settings, directory)
    with connect(settings) as conn:
        assert conn.execute("SELECT to_regclass('uw.must_not_exist') AS t").fetchone()["t"] is None


def test_f10_repeated_midnight_has_one_calendar_day_bucket():
    zone = ZoneInfo("America/Havana")
    early = datetime.fromisoformat("2026-11-01T04:30:00Z")
    late = datetime.fromisoformat("2026-11-01T05:30:00Z")
    first = _bucket(early, "day", zone)
    assert first == _bucket(late, "day", zone)
    assert first[0] == datetime.fromisoformat("2026-11-01T04:00:00Z")
    assert (first[1] - first[0]).total_seconds() == 25 * 3600


def test_f10_nonexistent_midnight_uses_first_real_instant():
    zone = ZoneInfo("America/Santiago")
    instant = datetime.fromisoformat("2026-09-06T16:00:00Z")
    start, end = _bucket(instant, "day", zone)
    assert start.astimezone(zone).hour == 1
    assert start.astimezone(zone).date() == instant.astimezone(zone).date()
    assert (end - start).total_seconds() == 23 * 3600
