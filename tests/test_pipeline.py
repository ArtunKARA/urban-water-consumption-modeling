import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal

import pytest
from conftest import reading
from urban_water.datasets import freeze, verify_dataset
from urban_water.db import connect
from urban_water.ingestion import (
    LostLease,
    _stage_chunk,
    accept,
    claim,
    process,
    retry,
    work_once,
)
from urban_water.processing import summarize
from urban_water.registry import seed_synthetic, stable_id
from urban_water.storage import FileStore, ObjectCorrupt
from urban_water_simulator import generate

pytestmark = pytest.mark.postgres


def total(settings, grain="day"):
    return sum(
        (Decimal(r["observed_liters"]) for r in summarize(settings, grain)["rows"]), Decimal(0)
    )


def load(settings, source_file, records):
    result = accept(settings, source_file(records))
    outcome = work_once(settings)
    return result, outcome


def test_fixture_e2e_and_independent_daily_oracle(settings, tmp_path):
    settings = replace(settings, chunk_size=128)
    fixture = generate(tmp_path / "fixture", days=2)
    accepted = accept(settings, __import__("pathlib").Path(fixture["source"]))
    result = work_once(settings)
    assert result["state"] == "succeeded"
    assert result["accepted_rows"] == 2208
    daily = summarize(settings)
    assert [Decimal(r["observed_liters"]) for r in daily["rows"]] == [
        Decimal("54"),
        Decimal("54"),
        Decimal("56.4"),
        Decimal("56.4"),
    ]
    assert total(settings, "hour") == Decimal("220.8")
    assert daily["coverage_status"] == "unknown"
    assert accept(settings, __import__("pathlib").Path(fixture["source"])) == {
        "source_id": accepted["source_id"],
        "duplicate": True,
    }
    assert work_once(settings)["state"] == "idle"


def test_overlap_dedup_distinguishes_real_identical_pulses(settings, source_file):
    a = reading()
    assert load(settings, source_file, [a])[1]["state"] == "succeeded"
    # Same source event redelivered at another time; another pulse at the same instant is real.
    redelivery = {**a, "received_at": "2026-02-02T00:00:00Z"}
    assert load(settings, source_file, [redelivery, reading(event="e2")])[1]["state"] == "succeeded"
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM measurement").fetchone()["n"] == 2
        assert conn.execute("SELECT count(*) AS n FROM provenance").fetchone()["n"] == 3
    assert total(settings) == Decimal("0.2")


def test_changed_identity_is_conflict_not_duplicate(settings, source_file):
    load(settings, source_file, [reading()])
    _, result = load(settings, source_file, [reading(value="2"), reading(event="e2")])
    assert result["error_code"] == "SourceConflict"
    assert total(settings) == Decimal("0.1")  # Entire conflicting batch stays unpublished.


def test_correction_and_late_event_preserve_frozen_dataset(settings, source_file):
    load(settings, source_file, [reading(time="2026-01-02T00:00:00+03:00")])
    frozen = freeze(settings)
    _, result = load(settings, source_file, [reading(revision=2, value="3"), reading(event="late")])
    assert result["state"] == "succeeded"
    assert total(settings) == Decimal("0.4")
    assert verify_dataset(settings, frozen["dataset_id"])["verified"]
    assert frozen["row_count"] == 1


def test_revision_gap_fails(settings, source_file):
    _, result = load(settings, source_file, [reading(revision=2)])
    assert result["error_code"] == "SourceConflict"
    assert total(settings) == 0


def test_quarantine_then_registry_resolution_and_explicit_retry(settings, source_file):
    accepted, outcome = load(
        settings, source_file, [reading(stream_code="S0003-pulse"), "bad json"]
    )
    assert outcome["quarantined_rows"] == 2
    assert outcome["state"] == "insufficient_data"
    assert total(settings) == 0
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM quality_issue").fetchone()["n"] == 2
        assert (
            conn.execute(
                "SELECT state FROM source WHERE id=%s", (accepted["source_id"],)
            ).fetchone()["state"]
            == "published"
        )
    seed_synthetic(settings, households=3)
    retry(settings, accepted["source_id"])
    result = work_once(settings)
    assert result["accepted_rows"] == 1 and result["quarantined_rows"] == 1
    assert total(settings) == Decimal("0.1")


def test_crash_resume_and_stale_worker_fencing(settings, source_file):
    path = source_file([reading(), reading(event="e2"), reading(event="e3")])
    accept(settings, path)
    first = claim(settings)
    _stage_chunk(settings, first, [(1, path.read_bytes().splitlines()[0])], 1)
    assert total(settings) == 0  # Staged input must never leak into research summaries.
    with connect(settings) as conn:
        conn.execute("UPDATE job SET lease_until=clock_timestamp()-interval '1 second'")
    second = claim(settings)
    assert second["checkpoint"] == 1 and second["token"] != first["token"]
    with pytest.raises(LostLease):
        process(settings, first)
    assert process(settings, second)["accepted_rows"] == 3
    assert total(settings) == Decimal("0.3")


def test_workers_cannot_claim_same_job(settings, source_file):
    accept(settings, source_file([reading()]))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(settings), range(2)))
    assert sum(r is not None for r in results) == 1


def test_failed_source_can_be_retried_without_new_identity(settings, source_file):
    accepted = accept(settings, source_file([reading(revision=2)]))
    assert work_once(settings)["error_code"] == "SourceConflict"
    load(settings, source_file, [reading()])
    retry(settings, accepted["source_id"])
    assert work_once(settings)["state"] == "succeeded"
    assert total(settings) == Decimal("0.1")


def test_corrupt_source_and_limits_fail_without_publication(settings, source_file):
    accepted = accept(settings, source_file([reading()]))
    with connect(settings) as conn:
        row = conn.execute("SELECT * FROM source WHERE id=%s", (accepted["source_id"],)).fetchone()
    obj = FileStore(settings).path(row["object_key"])
    obj.write_bytes(b"not gzip")
    assert work_once(settings)["state"] == "failed_attempt"
    assert total(settings) == 0
    with pytest.raises(ValueError):
        accept(replace(settings, max_source_bytes=1), source_file([reading()]))


def test_source_checksum_and_path_guards(settings, source_file):
    store = FileStore(settings)
    digest, size, key = store.put(source_file([reading()]))
    with pytest.raises(ObjectCorrupt):
        store.verify(key, digest, size + 1)
    with pytest.raises(ValueError):
        store.path("../outside")


def test_midnight_and_installation_reassignment_use_event_time(settings, source_file):
    from uuid import uuid4

    with connect(settings) as conn:
        conn.execute(
            "UPDATE installation SET valid_to='2026-01-02T00:00:00+03:00' WHERE meter_id=%s",
            (stable_id("meter-1-pulse"),),
        )
        conn.execute(
            "INSERT INTO installation(id,meter_id,household_id,valid_from) "
            "VALUES (%s,%s,%s,'2026-01-02T00:00:00+03:00')",
            (uuid4(), stable_id("meter-1-pulse"), stable_id("household-2")),
        )
    load(
        settings,
        source_file,
        [
            reading(time="2026-01-01T23:59:59+03:00"),
            reading(event="e2", time="2026-01-02T00:00:00+03:00"),
        ],
    )
    rows = summarize(settings)["rows"]
    assert [r["household_code"] for r in rows] == ["H0001", "H0002"]
    assert [r["start"] for r in rows] == ["2025-12-31T21:00:00+00:00", "2026-01-01T21:00:00+00:00"]


def test_index_reset_is_not_zero_or_negative_consumption(settings, source_file):
    seed_synthetic(settings, households=1, kind="index")
    records = [
        reading(
            event=f"i{i}",
            stream_code="S0001-index",
            kind="index",
            unit="L",
            value=value,
            time=f"2026-01-01T0{i}:00:00+03:00",
        )
        for i, value in enumerate(["100", "103", "1", "5"])
    ]
    assert load(settings, source_file, records)[1]["state"] == "succeeded"
    report = summarize(settings)
    assert total(settings) == Decimal("7")
    assert report["diagnostics"]["negative_index_delta"] == 1
    assert report["diagnostics"]["index_without_previous"] == 1


def test_interval_not_distributed_across_hours(settings, source_file):
    seed_synthetic(settings, households=1, kind="interval")
    row = reading(
        kind="interval",
        stream_code="S0001-interval",
        unit="m3",
        value="0.002",
        interval_start="2026-01-01T00:30:00+03:00",
        time="2026-01-01T01:30:00+03:00",
    )
    load(settings, source_file, [row])
    assert total(settings) == Decimal("2")
    hourly = summarize(settings, "hour")
    assert hourly["rows"] == []
    assert Decimal(hourly["unallocated_liters"]) == Decimal("2")


def test_overlapping_intervals_rejected_atomically(settings, source_file):
    seed_synthetic(settings, households=1, kind="interval")
    a = reading(
        kind="interval",
        stream_code="S0001-interval",
        unit="L",
        interval_start="2026-01-01T00:00:00+03:00",
        time="2026-01-01T02:00:00+03:00",
    )
    b = {**a, "event_id": "e2", "interval_start": "2026-01-01T01:00:00+03:00"}
    assert load(settings, source_file, [a, b])[1]["error_code"] == "SourceConflict"
    assert total(settings) == 0


def test_dataset_tamper_is_detected(settings, source_file):
    load(settings, source_file, [reading()])
    frozen = freeze(settings)
    path = __import__("pathlib").Path(frozen["directory"]) / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["row_count"] = 999
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="manifest mismatch"):
        verify_dataset(settings, frozen["dataset_id"])
