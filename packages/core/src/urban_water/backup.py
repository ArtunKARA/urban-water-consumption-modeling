"""Consistent PostgreSQL snapshot plus checksum-verified file objects.

Restore targets must be empty and explicitly named *_restore or *_test. No overwrite path.
"""

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from urban_water.datasets import file_sha, verify_dataset_files
from urban_water.db import connect
from urban_water.settings import Settings
from urban_water.sources.registration import ensure_environment
from urban_water.storage import FileStore


def _binary(name: str, pg_bin: Path | None) -> str:
    suffix = ".exe" if os.name == "nt" else ""
    if pg_bin:
        path = pg_bin / (name + suffix)
    else:
        found = shutil.which(name)
        if found:
            return found
        candidates = (
            sorted(Path("C:/Program Files/PostgreSQL").glob(f"*/bin/{name}.exe"), reverse=True)
            if os.name == "nt"
            else []
        )
        path = candidates[0] if candidates else Path(name)
    if not path.is_file():
        raise ValueError("PostgreSQL client tools not found; provide --pg-bin")
    return str(path)


def _environment(dsn: str) -> dict:
    config = conninfo_to_dict(dsn)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    mapping = {
        "host": "PGHOST",
        "port": "PGPORT",
        "dbname": "PGDATABASE",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "sslmode": "PGSSLMODE",
    }
    unsupported = set(config) - set(mapping)
    if unsupported:
        raise ValueError("unsupported backup connection options")
    env.update({mapping[k]: v for k, v in config.items()})
    return env


def _counts(conn) -> dict:
    names = [
        r["tablename"]
        for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='uw' ORDER BY tablename"
        )
    ]
    return {
        name: conn.execute(
            sql.SQL("SELECT count(*) AS n FROM uw.{}").format(sql.Identifier(name))
        ).fetchone()["n"]
        for name in names
    }


def create_backup(settings: Settings, destination: Path, pg_bin: Path | None = None) -> dict:
    destination = destination.resolve()
    if destination.is_relative_to(settings.storage_root.resolve()):
        raise ValueError("backup destination must be outside active storage")
    destination.mkdir(parents=True, exist_ok=False)
    store = FileStore(settings)
    objects = set()
    with connect(settings) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        ensure_environment(conn, settings)
        snapshot = conn.execute("SELECT pg_export_snapshot() AS s").fetchone()["s"]
        counts = _counts(conn)
        sources = list(conn.execute("SELECT object_key,sha256,byte_size FROM source"))
        for row in sources:
            store.verify(row["object_key"], row["sha256"], row["byte_size"])
            objects.add(row["object_key"])
        datasets = list(conn.execute("SELECT * FROM dataset"))
        for row in datasets:
            verify_dataset_files(store, row)
            objects.add(row["object_key"])
            objects.add(str(Path(row["object_key"]).with_name("manifest.json")).replace("\\", "/"))
            if normalization := row["manifest"].get("normalization"):
                objects.add(normalization["object_key"])
        dump = destination / "database.dump"
        subprocess.run(
            [
                _binary("pg_dump", pg_bin),
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--snapshot",
                snapshot,
                "--file",
                str(dump),
            ],
            env=_environment(settings.database_url),
            check=True,
            capture_output=True,
        )
        hashes = {"database.dump": file_sha(dump)}
        for key in sorted(objects):
            target = destination / "objects" / key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(store.path(key), target)
            hashes[f"objects/{key}"] = file_sha(target)
        # Detect corruption during copying as well as corruption present before backup.
        copied_store = FileStore(replace(settings, storage_root=destination / "objects"))
        for row in sources:
            copied_store.verify(row["object_key"], row["sha256"], row["byte_size"])
        for row in datasets:
            verify_dataset_files(copied_store, row)
        manifest = {
            "format": "urban-water.backup.v1",
            "environment": settings.environment,
            "database_name": conn.info.dbname,
            "counts": counts,
            "files": hashes,
        }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"directory": str(destination), "files": len(hashes), "counts": counts}


def restore_backup(settings: Settings, backup: Path, pg_bin: Path | None = None) -> dict:
    backup = backup.resolve()
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "urban-water.backup.v1":
        raise ValueError("unknown backup format")
    if manifest.get("environment") != settings.environment:
        raise ValueError("backup environment must match the restore environment")
    config = conninfo_to_dict(settings.database_url)
    dbname = config.get("dbname", "")
    if not dbname.endswith(("_restore", "_test")) or dbname == manifest["database_name"]:
        raise ValueError("restore requires a different, empty *_restore or *_test database")
    root = settings.storage_root.resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("restore storage root must be empty")
    if root == backup or root.is_relative_to(backup) or backup.is_relative_to(root):
        raise ValueError("restore and backup directories must be disjoint")
    for key, expected in manifest["files"].items():
        path = (backup / key).resolve()
        if not path.is_relative_to(backup) or file_sha(path) != expected:
            raise ValueError("backup checksum or path mismatch")
    with psycopg.connect(settings.database_url) as conn:
        if conn.execute("""SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema')
            AND n.nspname NOT LIKE 'pg_toast%%' AND c.relkind IN ('r','p') LIMIT 1""").fetchone():
            raise ValueError("restore database must be empty")
    # Copy before restore: interrupted restores are never reported as successful.
    root.mkdir(parents=True, exist_ok=True)
    for key in manifest["files"]:
        if key.startswith("objects/"):
            target = FileStore(settings).path(key[len("objects/") :])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(backup / key, target)
    subprocess.run(
        [
            _binary("pg_restore", pg_bin),
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-acl",
            "--dbname",
            dbname,
            str(backup / "database.dump"),
        ],
        env=_environment(settings.database_url),
        check=True,
        capture_output=True,
    )
    with connect(settings) as conn:
        counts = _counts(conn)
        if counts != manifest["counts"]:
            raise ValueError("restored database counts differ from the snapshot")
        for row in conn.execute("SELECT object_key,sha256,byte_size FROM source"):
            FileStore(settings).verify(row["object_key"], row["sha256"], row["byte_size"])
        for row in conn.execute("SELECT * FROM dataset"):
            verify_dataset_files(FileStore(settings), row)
    return {"restored": True, "counts": counts, "storage_root": str(root)}
