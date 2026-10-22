"""Frozen Parquet extracts and manifests, registered only after durable file creation."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
from psycopg.types.json import Jsonb

from urban_water.db import PROJECT_ROOT, connect
from urban_water.processing import summarize_connection
from urban_water.settings import Settings
from urban_water.storage import FileStore


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def code_sha() -> str:
    digest = hashlib.sha256()
    paths = [
        p
        for directory in ("packages", "apps", "migrations")
        for p in (PROJECT_ROOT / directory).rglob("*")
        if p.suffix in {".py", ".sql", ".toml"}
    ]
    paths += [PROJECT_ROOT / "pyproject.toml", PROJECT_ROOT / "uv.lock"]
    for path in sorted(paths):
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def freeze(settings: Settings, grain: str = "day") -> dict:
    store = FileStore(settings)
    dataset_id = uuid4()
    directory = store.path(f"datasets/{dataset_id}")
    directory.mkdir(parents=True, exist_ok=False)
    with connect(settings) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        report = summarize_connection(
            conn,
            grain,
            settings.timezone,
            settings.study_code,
            settings.cohort_code,
        )
        sources = list(
            conn.execute(
                "SELECT id,sha256,byte_size,object_key,contract_version,parser_version,"
                "accepted_at,published_at FROM source "
                "WHERE state='published' ORDER BY id"
            )
        )
        # Referenced source bytes must be available, including in restored environments.
        for source in sources:
            store.verify(source["object_key"], source["sha256"], source["byte_size"])
        schema = pa.schema(
            [
                ("household_code", pa.string()),
                ("start", pa.string()),
                ("end", pa.string()),
                ("observed_liters", pa.string()),
                ("observations", pa.int64()),
                ("coverage_status", pa.string()),
            ]
        )
        path = directory / "observed_volume.parquet"
        pq.write_table(
            pa.Table.from_pylist(report["rows"], schema=schema), path, compression="zstd"
        )
        with path.open("rb+") as stream:
            os.fsync(stream.fileno())
        quality = list(
            conn.execute(
                "SELECT code,count(*) AS count FROM quality_issue q "
                "JOIN source s ON q.source_id=s.id WHERE s.state='published' "
                "GROUP BY code ORDER BY code"
            )
        )
        # Full temporal registry is small and freezes attribution decisions for this extract.
        registry = list(conn.execute("SELECT * FROM installation ORDER BY id"))
        streams = list(conn.execute("SELECT * FROM stream ORDER BY id"))
        households = list(conn.execute("SELECT * FROM household ORDER BY id"))
        meters = list(conn.execute("SELECT * FROM meter ORDER BY id"))
        memberships = list(conn.execute("SELECT * FROM membership ORDER BY cohort_id,household_id"))
        manifest = {
            "format": "urban-water.dataset.v1",
            "dataset_id": str(dataset_id),
            "purpose": "observed-volume-correctness",
            "environment": "synthetic",
            "grain": grain,
            "timezone": settings.timezone,
            "study_code": settings.study_code,
            "cohort_code": settings.cohort_code,
            "code_sha256": code_sha(),
            "data_sha256": file_sha(path),
            "row_count": len(report["rows"]),
            "coverage_status": "unknown",
            "data_status": report["data_status"],
            "population": report["population"],
            "diagnostics": report["diagnostics"],
            "unallocated_liters": report["unallocated_liters"],
            "quality": quality,
            "sources": [{**s, "id": str(s["id"])} for s in sources],
            "registry": registry,
            "streams": streams,
            "households": households,
            "meters": meters,
            "memberships": memberships,
            "database_snapshot": conn.execute("SELECT pg_current_snapshot()::text AS s").fetchone()[
                "s"
            ],
            "dependencies_sha256": file_sha(PROJECT_ROOT / "uv.lock"),
        }
        # Normalize UUID/datetime values identically for file and database representations.
        manifest = json.loads(json.dumps(manifest, default=str))
        manifest_path = directory / "manifest.json"
        with manifest_path.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        key = path.relative_to(store.root).as_posix()
        conn.execute(
            "INSERT INTO dataset(id,state,object_key,manifest) VALUES (%s,'ready',%s,%s)",
            (dataset_id, key, Jsonb(manifest)),
        )
        for source in sources:
            conn.execute("INSERT INTO dataset_source VALUES (%s,%s)", (dataset_id, source["id"]))
    return {"dataset_id": str(dataset_id), "directory": str(directory), **manifest}


def verify_dataset(settings: Settings, dataset_id: str) -> dict:
    with connect(settings) as conn:
        row = conn.execute("SELECT * FROM dataset WHERE id=%s", (dataset_id,)).fetchone()
    if row is None or row["state"] != "ready":
        raise ValueError("dataset is not ready")
    path = FileStore(settings).path(row["object_key"])
    if file_sha(path) != row["manifest"]["data_sha256"]:
        raise ValueError("dataset checksum mismatch")
    actual = json.loads(path.with_name("manifest.json").read_text(encoding="utf-8"))
    if actual != row["manifest"]:
        raise ValueError("dataset manifest mismatch")
    if pq.read_metadata(path).num_rows != row["manifest"]["row_count"]:
        raise ValueError("dataset row count mismatch")
    return {"dataset_id": str(row["id"]), "verified": True}
