import os
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from conftest import reading
from urban_water.db import connect
from urban_water.ingestion import accept, claim, work_once
from urban_water.processing import _bucket, summarize
from urban_water.registry import stable_id

pytestmark = pytest.mark.postgres


def test_real_process_crash_then_worker_restart(settings, source_file, tmp_path):
    accept(settings, source_file([reading(), reading(event="e2"), reading(event="e3")]))
    signal = tmp_path / "checkpoint.ready"
    env = {
        **os.environ,
        "UW_ENV": "synthetic",
        "UW_DATABASE_URL": settings.database_url,
        "UW_STORAGE_ROOT": str(settings.storage_root),
        "UW_TEST_SIGNAL": str(signal),
    }
    # Commit a real checkpoint in another OS process, then kill that process abruptly.
    code = """
import gzip, os
from pathlib import Path
from urban_water.settings import Settings
from urban_water.db import connect
from urban_water.ingestion import claim, _stage_chunk
from urban_water.storage import FileStore
s = Settings.from_env()
j = claim(s)
with connect(s) as c:
    source = c.execute('SELECT object_key FROM source WHERE id=%s', (j['source_id'],)).fetchone()
with gzip.open(FileStore(s).path(source['object_key']), 'rb') as f:
    _stage_chunk(s, j, [(1, f.readline())], 1)
Path(os.environ['UW_TEST_SIGNAL']).write_text('ready')
input()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while not signal.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert signal.exists(), "child did not commit its checkpoint"
        child.kill()
        child.wait(timeout=10)
        assert summarize(settings)["rows"] == []
        with connect(settings) as conn:
            conn.execute("UPDATE job SET lease_until=clock_timestamp()-interval '1 second'")
        restarted = subprocess.run(
            [sys.executable, "-m", "urban_water_worker.main", "--once"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert '"accepted_rows": 3' in restarted.stdout
        assert Decimal(summarize(settings)["rows"][0]["observed_liters"]) == Decimal("0.3")
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        if child.stdin:
            child.stdin.close()


def test_batch_and_file_replay_have_equal_final_results(settings, source_file):
    rows = [reading(event=f"e{i}", value=str(i + 1)) for i in range(4)]
    accept(settings, source_file(rows))
    assert work_once(settings)["state"] == "succeeded"
    expected = summarize(settings)
    for row in reversed(rows):
        accept(settings, source_file([row]))
        assert work_once(settings)["state"] == "succeeded"
    assert summarize(settings) == expected
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM measurement").fetchone()["n"] == 4


def test_membership_is_applied_at_measurement_time(settings, source_file):
    with connect(settings) as conn:
        conn.execute(
            "UPDATE membership SET valid_to='2026-01-02T00:00:00+03:00' WHERE household_id=%s",
            (stable_id("household-1"),),
        )
    accept(
        settings,
        source_file([reading(), reading(event="outside", time="2026-01-02T00:00:00+03:00")]),
    )
    work_once(settings)
    report = summarize(settings)
    assert len(report["rows"]) == 1
    assert report["diagnostics"]["outside_cohort"] == 1
    assert Decimal(report["unallocated_liters"]) == Decimal("0.1")
    with pytest.raises(ValueError, match="unknown study/cohort"):
        summarize(replace(settings, cohort_code="nonexistent"))


def test_crash_on_last_attempt_eventually_becomes_terminal(settings, source_file):
    settings = replace(settings, max_attempts=1)
    accept(settings, source_file([reading()]))
    assert claim(settings) is not None
    with connect(settings) as conn:
        conn.execute("UPDATE job SET lease_until=clock_timestamp()-interval '1 second'")
    assert claim(settings) is None
    with connect(settings) as conn:
        assert conn.execute("SELECT state FROM job").fetchone()["state"] == "failed"
        assert conn.execute("SELECT state FROM source").fetchone()["state"] == "failed"


def test_dst_calendar_day_boundaries_are_not_always_24_hours():
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Europe/Berlin")
    start, end = _bucket(datetime(2026, 3, 29, 12, tzinfo=UTC), "day", zone)
    assert (end - start).total_seconds() == 23 * 3600
    start, end = _bucket(datetime(2026, 10, 25, 12, tzinfo=UTC), "day", zone)
    assert (end - start).total_seconds() == 25 * 3600
