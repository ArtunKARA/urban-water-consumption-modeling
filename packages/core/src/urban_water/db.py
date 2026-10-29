"""PostgreSQL transactions and forward-only, checksum-checked migrations."""

import hashlib
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from urban_water.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[4]


@contextmanager
def connect(settings: Settings):
    with psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute("SET search_path TO uw, public")
        conn.execute("SET TIME ZONE 'UTC'")
        yield conn


def migrate(settings: Settings, directory: Path | None = None) -> list[str]:
    applied = []
    with connect(settings) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(83621001)")
        conn.execute("CREATE SCHEMA IF NOT EXISTS uw")
        conn.execute("""CREATE TABLE IF NOT EXISTS uw.schema_migration
                     (name text PRIMARY KEY, sha256 char(64) NOT NULL,
                      applied_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
        known = {
            r["name"]: r["sha256"]
            for r in conn.execute("SELECT name, sha256 FROM uw.schema_migration")
        }
        paths = sorted((directory or PROJECT_ROOT / "migrations").glob("[0-9]*.sql"))
        if not paths:
            raise ValueError("no migration files found")
        if set(known) - {p.name for p in paths}:
            raise ValueError("database contains unknown migrations")
        if set(known) != {p.name for p in paths[: len(known)]}:
            raise ValueError(
                "applied migrations must be a contiguous prefix; out-of-order migration"
            )
        prepared = []
        for path in paths:
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if path.name in known:
                if known[path.name] != digest:
                    raise ValueError(f"applied migration changed: {path.name}")
                continue
            prepared.append((path, content, digest))
        for path, content, digest in prepared:
            conn.execute(content.decode("utf-8"), prepare=False)
            conn.execute(
                "INSERT INTO uw.schema_migration(name,sha256) VALUES (%s,%s)", (path.name, digest)
            )
            applied.append(path.name)
    return applied
