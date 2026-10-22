from uuid import uuid4

import psycopg
import pytest
from conftest import reading
from urban_water.datasets import freeze
from urban_water.db import connect
from urban_water.ingestion import accept, work_once
from urban_water.registry import stable_id

pytestmark = pytest.mark.postgres


def test_database_rejects_missing_pulse_scale(settings):
    with pytest.raises(psycopg.errors.CheckViolation):
        with connect(settings) as conn:
            meter = uuid4()
            conn.execute("INSERT INTO meter VALUES (%s,'missing-scale-meter')", (meter,))
            conn.execute(
                "INSERT INTO stream(id,meter_id,code,kind) VALUES (%s,%s,'missing-scale','pulse')",
                (uuid4(), meter),
            )


def test_database_rejects_overlapping_household_installations(settings):
    with pytest.raises(psycopg.errors.ExclusionViolation):
        with connect(settings) as conn:
            conn.execute(
                "INSERT INTO installation(id,meter_id,household_id,valid_from) "
                "VALUES (%s,%s,%s,'2026-01-01T00:00:00Z')",
                (uuid4(), stable_id("meter-1-pulse"), stable_id("household-2")),
            )


def test_database_rejects_overlapping_membership(settings):
    with pytest.raises(psycopg.errors.ExclusionViolation):
        with connect(settings) as conn:
            conn.execute(
                "INSERT INTO membership VALUES (%s,%s,'2026-01-01T00:00:00Z',NULL)",
                (stable_id("cohort"), stable_id("household-1")),
            )


def test_manifest_preserves_normalization_and_source_versions(settings, source_file):
    accept(settings, source_file([reading()]))
    work_once(settings)
    manifest = freeze(settings)
    stream = next(r for r in manifest["streams"] if r["code"] == "S0001-pulse")
    assert stream["liters_per_pulse"] == "0.100000000"
    assert manifest["sources"][0]["parser_version"] == "synthetic-jsonl/1"
    assert manifest["population"]["represented_households"] == 1
    assert manifest["population"]["registered_households"] == 2
