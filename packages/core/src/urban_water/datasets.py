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
from urban_water.sources.registration import ensure_environment
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


def _write_normalization(conn, path: Path) -> int:
    """Stream canonical measurements + exact config references into the frozen extract."""
    names = [
        "measurement_id",
        "stream_id",
        "event_id",
        "revision",
        "kind",
        "occurred_at",
        "interval_start",
        "original_value",
        "original_unit",
        "liters",
        "configuration_id",
        "is_current",
    ]
    schema = pa.schema(
        [
            (
                name,
                pa.int64()
                if name in {"measurement_id", "revision"}
                else pa.bool_()
                if name == "is_current"
                else pa.string(),
            )
            for name in names
        ]
    )
    count = 0
    with pq.ParquetWriter(path, schema, compression="zstd") as writer:
        with conn.cursor(name="normalization_export") as cursor:
            cursor.execute("""SELECT m.id AS measurement_id,m.stream_id,m.event_id,m.revision,
                m.kind,m.occurred_at,m.interval_start,m.original_value,m.original_unit,m.liters,
                m.configuration_id,d.is_current FROM measurement m JOIN dataset_history d
                ON d.measurement_id=m.id ORDER BY m.id""")
            while batch := cursor.fetchmany(2000):
                rows = [
                    {
                        name: row[name]
                        if name in {"measurement_id", "revision", "is_current"}
                        else None
                        if row[name] is None
                        else str(row[name])
                        for name in names
                    }
                    for row in batch
                ]
                writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                count += len(rows)
    with path.open("rb+") as stream:
        os.fsync(stream.fileno())
    return count


def freeze(settings: Settings, grain: str = "day") -> dict:
    store = FileStore(settings)
    dataset_id = uuid4()
    directory = store.path(f"datasets/{dataset_id}")
    directory.mkdir(parents=True, exist_ok=False)
    with connect(settings) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        ensure_environment(conn, settings)
        report = summarize_connection(
            conn,
            grain,
            settings.timezone,
            settings.study_code,
            settings.cohort_code,
            collect_lineage=True,
        )
        # A selected revision also depends on its earlier event revisions. Keeping those
        # ancestors avoids exporting an unreplayable revision-2 source without revision 1.
        conn.execute("""CREATE TEMP TABLE dataset_history ON COMMIT DROP AS
            SELECT h.id AS measurement_id,(h.id=c.id) AS is_current FROM measurement h
            JOIN measurement c ON c.stream_id=h.stream_id AND c.event_id=h.event_id
                AND h.revision<=c.revision
            JOIN dataset_measurement d ON d.measurement_id=c.id""")
        conn.execute("CREATE UNIQUE INDEX ON dataset_history(measurement_id)")
        sources = list(
            conn.execute(
                "SELECT id,sha256,byte_size,object_key,contract_version,parser_version,"
                "accepted_at,published_at,namespace,normalizer_options,options_sha256,"
                "acquisition FROM source "
                "WHERE state='published' AND EXISTS (SELECT 1 FROM provenance p "
                "JOIN dataset_history d ON d.measurement_id=p.measurement_id "
                "WHERE p.source_id=source.id) ORDER BY id"
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
        normalization_path = directory / "normalization.parquet"
        normalization_count = _write_normalization(conn, normalization_path)
        quality = list(
            conn.execute(
                "SELECT code,count(*) AS count FROM quality_issue q "
                "WHERE q.source_id=ANY(%s) GROUP BY code ORDER BY code",
                ([s["id"] for s in sources],),
            )
        )
        memberships = list(
            conn.execute(
                """SELECT p.* FROM membership p JOIN cohort c
            ON c.id=p.cohort_id JOIN study s ON s.id=c.study_id WHERE s.code=%s AND c.code=%s
            ORDER BY p.household_id,p.valid_from""",
                (settings.study_code, settings.cohort_code),
            )
        )
        household_ids = list({p["household_id"] for p in memberships})
        households = list(
            conn.execute("SELECT * FROM household WHERE id=ANY(%s) ORDER BY id", (household_ids,))
        )
        streams = list(
            conn.execute("""SELECT id,meter_id,code,kind,created_at FROM stream WHERE id IN
            (SELECT m.stream_id FROM measurement m JOIN dataset_measurement d
             ON d.measurement_id=m.id) ORDER BY id""")
        )
        meter_ids = list({s["meter_id"] for s in streams})
        registry = list(
            conn.execute(
                "SELECT * FROM installation WHERE household_id=ANY(%s) "
                "AND meter_id=ANY(%s) ORDER BY id",
                (household_ids, meter_ids),
            )
        )
        meters = list(
            conn.execute("SELECT * FROM meter WHERE id=ANY(%s) ORDER BY id", (meter_ids,))
        )
        configurations = list(
            conn.execute("""SELECT * FROM stream_configuration WHERE id IN
            (SELECT m.configuration_id FROM measurement m JOIN dataset_history d
                ON d.measurement_id=m.id) ORDER BY stream_id,version""")
        )
        manifest = {
            "format": "urban-water.dataset.v2",
            "dataset_id": str(dataset_id),
            "purpose": "observed-volume-correctness",
            "environment": settings.environment,
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
            "quality_scope": "referenced-source-packages",
            "lineage_scope": "cohort-meter-candidates-and-revision-ancestors",
            "normalization": {
                "object_key": normalization_path.relative_to(store.root).as_posix(),
                "sha256": file_sha(normalization_path),
                "row_count": normalization_count,
            },
            "sources": [{**s, "id": str(s["id"])} for s in sources],
            "registry": registry,
            "streams": streams,
            "configurations": configurations,
            "normalization_policy": "job-pinned-configurations; explicit-event-revisions",
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
    verify_dataset_files(FileStore(settings), row)
    return {"dataset_id": str(row["id"]), "verified": True}


def verify_dataset_files(store: FileStore, row: dict) -> None:
    """Verify against the caller's DB snapshot, not a new connection/snapshot."""
    path = store.path(row["object_key"])
    if file_sha(path) != row["manifest"]["data_sha256"]:
        raise ValueError("dataset checksum mismatch")
    actual = json.loads(path.with_name("manifest.json").read_text(encoding="utf-8"))
    if actual != row["manifest"]:
        raise ValueError("dataset manifest mismatch")
    if pq.read_metadata(path).num_rows != row["manifest"]["row_count"]:
        raise ValueError("dataset row count mismatch")
    normalization = row["manifest"].get("normalization")
    if normalization:
        normal_path = store.path(normalization["object_key"])
        if file_sha(normal_path) != normalization["sha256"]:
            raise ValueError("normalization checksum mismatch")
        if pq.read_metadata(normal_path).num_rows != normalization["row_count"]:
            raise ValueError("normalization row count mismatch")
