"""Destructive schema reset is restricted to an explicitly dedicated *_test database."""

import os
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from urban_water.db import PROJECT_ROOT, migrate
from urban_water.registry import seed_synthetic
from urban_water.settings import Settings


@pytest.fixture
def settings(tmp_path):
    dsn = os.getenv("UW_TEST_DATABASE_URL")
    if not dsn:
        # The fallback is available only for our own marked local synthetic cluster.
        marker = PROJECT_ROOT / "var/dev-postgres/cluster.json"
        config = dotenv_values(PROJECT_ROOT / ".env")
        original = config.get("UW_DATABASE_URL")
        if marker.exists() and original:
            info = conninfo_to_dict(original)
            if info.get("dbname") == "urban_water" and info.get("port") == "55432":
                dsn = make_conninfo(original, dbname="urban_water_test")
    if not dsn:
        pytest.skip("set UW_TEST_DATABASE_URL to an isolated *_test database")
    info = conninfo_to_dict(dsn)
    if not info.get("dbname", "").endswith("_test"):
        pytest.fail("refusing to reset a database not explicitly named *_test")
    with psycopg.connect(dsn) as conn:
        conn.execute("DROP SCHEMA IF EXISTS uw CASCADE")
    result = Settings(database_url=dsn, storage_root=tmp_path / "storage", chunk_size=2)
    migrate(result)
    seed_synthetic(result)
    return result


@pytest.fixture
def restore_settings(settings, tmp_path):
    """A newly-created disposable database, not a pre-existing user database."""
    from uuid import uuid4

    from psycopg import sql

    name = f"uw_{uuid4().hex}_restore"
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    target = replace(
        settings,
        database_url=make_conninfo(settings.database_url, dbname=name),
        storage_root=tmp_path / "restored",
    )
    try:
        yield target
    finally:
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


@pytest.fixture
def source_file(tmp_path):
    import json
    from uuid import uuid4

    def write(records: list[dict | str]) -> Path:
        path = tmp_path / f"source-{uuid4().hex}.jsonl"
        path.write_text(
            "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in records),
            encoding="utf-8",
        )
        return path

    return write


def reading(event="e1", value="1", time="2026-01-01T00:00:00+03:00", **kwargs):
    return {
        "schema_version": "synthetic.measurement.v1",
        "stream_code": "S0001-pulse",
        "event_id": event,
        "revision": 1,
        "kind": "pulse",
        "occurred_at": time,
        "received_at": "2026-02-01T00:00:00+03:00",
        "value": value,
        "unit": "pulse",
        **kwargs,
    }
