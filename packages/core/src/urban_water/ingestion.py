"""Durable, fenced ingestion shared by configured file and HTTP sources."""

import gzip
import hashlib
import json
from datetime import UTC
from decimal import Decimal, localcontext
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
from urban_water_contracts import Measurement

from urban_water.db import connect
from urban_water.leases import LostLease, keep_lease, owned
from urban_water.settings import Settings
from urban_water.sources.config import SourceSpec, options_digest
from urban_water.sources.normalizers import (
    NormalizationError,
    NormalizedRow,
    get_normalizer,
    parse_json,
    qualify,
)
from urban_water.sources.registration import ensure_environment
from urban_water.storage import FileStore

CONTRACT = "synthetic.measurement.v1"
PARSER = "synthetic-jsonl/1"


class SourceConflict(ValueError):
    pass


class SourceLimit(ValueError):
    pass


def accept(settings: Settings, path: Path, *, source: SourceSpec | None = None) -> dict:
    contract = source.contract if source else CONTRACT
    parser = source.parser if source else PARSER
    namespace = source.namespace if source else "legacy"
    options = json.loads(json.dumps(source.options)) if source else {}
    digest_options = options_digest(options)
    if (source.data_kind if source else "synthetic") != settings.environment:
        raise ValueError("source data kind must match the runtime environment")
    get_normalizer(contract, parser, options)  # Reject unknown versions before accepting bytes.
    acquisition = {
        "reader_version": "http-file/1"
        if source and source.reader == "http_file_v1"
        else "local-file/1",
        "data_kind": source.data_kind if source else "synthetic",
        "citation": source.citation if source else None,
        "license": source.license if source else None,
    }
    digest, size, key = FileStore(settings).put(path)
    with connect(settings) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(83621002)")
        ensure_environment(conn, settings)
        source_id = uuid4()
        row = conn.execute(
            "INSERT INTO source(id,sha256,byte_size,object_key,"
            "contract_version,parser_version,namespace,normalizer_options,options_sha256,"
            "acquisition,state) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'accepted') "
            "ON CONFLICT DO NOTHING RETURNING id",
            (
                source_id,
                digest,
                size,
                key,
                contract,
                parser,
                namespace,
                Jsonb(options),
                digest_options,
                Jsonb(acquisition),
            ),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id FROM source WHERE sha256=%s "
                "AND contract_version=%s AND parser_version=%s AND namespace=%s "
                "AND options_sha256=%s",
                (digest, contract, parser, namespace, digest_options),
            ).fetchone()
            return {"source_id": str(row["id"]), "duplicate": True}
        conn.execute(
            "INSERT INTO job(id,source_id,state,max_attempts) VALUES (%s,%s,'queued',%s)",
            (uuid4(), source_id, settings.max_attempts),
        )
        conn.execute(
            "INSERT INTO audit_event(action,target_id) VALUES ('source.accept',%s)", (source_id,)
        )
        return {"source_id": str(source_id), "duplicate": False}


def claim(settings: Settings) -> dict | None:
    with connect(settings) as conn:
        # Exhausted crashed jobs must not remain 'running' forever.
        exhausted = list(
            conn.execute("""SELECT id,source_id FROM job
            WHERE state='running' AND lease_until < clock_timestamp()
            AND attempts >= max_attempts FOR UPDATE SKIP LOCKED""")
        )
        for expired in exhausted:
            conn.execute(
                "UPDATE job SET state='failed',error_code='lease_exhausted',"
                "token=NULL,lease_until=NULL WHERE id=%s",
                (expired["id"],),
            )
            conn.execute(
                "UPDATE source SET state='failed' WHERE id=%s AND state<>'published'",
                (expired["source_id"],),
            )
        row = conn.execute("""SELECT * FROM job WHERE attempts < max_attempts AND
            ((state='queued' AND available_at <= clock_timestamp()) OR
             (state='running' AND lease_until < clock_timestamp()))
            ORDER BY available_at, id FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if row is None:
            return None
        token = uuid4()
        if not row["configurations_pinned"]:
            conn.execute(
                """INSERT INTO job_configuration(job_id,configuration_id)
                SELECT %s,id FROM stream_configuration WHERE retired_at IS NULL""",
                (row["id"],),
            )
        conn.execute(
            """UPDATE job SET state='running', attempts=attempts+1, token=%s,
            lease_until=clock_timestamp() + %s * interval '1 second', error_code=NULL,
            configurations_pinned=true
            WHERE id=%s""",
            (token, settings.lease_seconds, row["id"]),
        )
        conn.execute(
            "UPDATE source SET state='processing' WHERE id=%s AND state<>'published'",
            (row["source_id"],),
        )
        row.update(token=token, attempts=row["attempts"] + 1, state="running")
        return row


def _fingerprint(record: Measurement) -> str:
    content = {
        "kind": record.kind,
        "time": record.occurred_at.astimezone(UTC).isoformat(),
        "start": record.interval_start.astimezone(UTC).isoformat()
        if record.interval_start
        else None,
        "value": str(record.value.normalize()),
        "unit": record.unit,
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def _stage_chunk(settings: Settings, job: dict, lines: list[tuple[int, bytes]], last: int) -> None:
    """Compatibility entry point for legacy callers and checkpoint regressions."""
    rows = []
    for number, line in lines:
        try:
            rows.append(NormalizedRow(number, parse_json(line)))
        except ValueError:
            rows.append(NormalizedRow(number, None, "invalid_contract"))
    _stage_rows(settings, job, rows, last)


def _stage_rows(settings: Settings, job: dict, rows: list[NormalizedRow], last: int) -> None:
    with connect(settings) as conn:
        owned(conn, job)
        streams = {r["code"]: r for r in conn.execute("SELECT * FROM stream")}
        configurations = {}
        for cfg in conn.execute(
            """SELECT c.* FROM stream_configuration c
            JOIN job_configuration j ON j.configuration_id=c.id WHERE j.job_id=%s
            ORDER BY c.valid_from,c.version""",
            (job["id"],),
        ):
            configurations.setdefault(cfg["stream_id"], []).append(cfg)
        prepared = []
        issues = []
        for item in rows:
            row_no, record = item.number, item.record
            if record is None:
                issues.append((job["source_id"], row_no, item.issue or "invalid_contract"))
                continue
            stream = streams.get(record.stream_code)
            code = None
            if stream is None:
                code = "unknown_stream"
            elif stream["kind"] != record.kind:
                code = "stream_kind_mismatch"
            cfg = None
            if code is None:
                candidates = [
                    c for c in configurations.get(stream["id"], ()) if _covered(record, c)
                ]
                if candidates:
                    # An end-index at an adjacent boundary closes the preceding period.
                    cfg = min(
                        candidates,
                        key=lambda c: (c["valid_to"] != record.occurred_at, -c["version"]),
                    )
                else:
                    code = "outside_stream_configuration"
            # Registry mapping is applied by event time during analysis, not receipt time.
            if code:
                issues.append((job["source_id"], row_no, code))
                continue
            with localcontext() as context:
                context.prec = 60
                liters = record.value * (
                    cfg["liters_per_pulse"]
                    if record.kind == "pulse"
                    else Decimal(1000)
                    if record.unit == "m3"
                    else Decimal(1)
                )
                precise = liters == liters.quantize(Decimal("0.000000000001"))
            if liters.adjusted() >= 18 or not precise:
                issues.append((job["source_id"], row_no, "volume_out_of_range"))
                continue
            prepared.append(
                (
                    job["source_id"],
                    row_no,
                    stream["id"],
                    record.event_id,
                    record.revision,
                    record.kind,
                    record.occurred_at,
                    record.interval_start,
                    record.received_at,
                    record.occurred_at.isoformat(),
                    record.value,
                    record.unit,
                    liters,
                    _fingerprint(record),
                    cfg["id"],
                ),
            )
        with conn.cursor() as cursor:
            if prepared:
                with cursor.copy("""COPY staged_measurement(source_id,row_no,stream_id,event_id,
                    revision,kind,occurred_at,interval_start,received_at,original_time,
                    original_value,original_unit,liters,fingerprint,configuration_id)
                    FROM STDIN""") as copy:
                    for row in prepared:
                        copy.write_row(row)
            if issues:
                with cursor.copy(
                    "COPY staged_quality_issue(source_id,row_no,code) FROM STDIN"
                ) as copy:
                    for row in issues:
                        copy.write_row(row)
        conn.execute(
            """UPDATE job SET checkpoint=%s,
            lease_until=clock_timestamp() + %s * interval '1 second' WHERE id=%s""",
            (last, settings.lease_seconds, job["id"]),
        )


def _covered(record: Measurement, config: dict) -> bool:
    start = record.interval_start if record.kind == "interval" else record.occurred_at
    if start < config["valid_from"]:
        return False
    end = config["valid_to"]
    if end is None:
        return True
    return record.occurred_at < end if record.kind == "pulse" else record.occurred_at <= end


def publish(settings: Settings, job: dict) -> None:
    with connect(settings) as conn:
        owned(conn, job)
        sid = job["source_id"]
        for row in conn.execute(
            "SELECT DISTINCT stream_id::text AS id FROM staged_measurement "
            "WHERE source_id=%s ORDER BY id",
            (sid,),
        ):
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (row["id"],))
        bad = conn.execute(
            """SELECT 1 FROM staged_measurement WHERE source_id=%s
            GROUP BY stream_id,event_id,revision HAVING count(DISTINCT fingerprint)>1 LIMIT 1""",
            (sid,),
        ).fetchone()
        conflict = conn.execute(
            """SELECT 1 FROM staged_measurement s JOIN measurement m
            USING(stream_id,event_id,revision) WHERE s.source_id=%s
            AND (s.fingerprint <> m.fingerprint OR s.liters <> m.liters) LIMIT 1""",
            (sid,),
        ).fetchone()
        if bad or conflict:
            raise SourceConflict("event identity/revision has conflicting content")
        gap = conn.execute(
            """WITH versions AS (
            SELECT stream_id,event_id,revision FROM staged_measurement WHERE source_id=%s
            UNION SELECT m.stream_id,m.event_id,m.revision FROM measurement m
            WHERE EXISTS (SELECT 1 FROM staged_measurement s WHERE s.source_id=%s
                AND s.stream_id=m.stream_id AND s.event_id=m.event_id))
            SELECT 1 FROM versions GROUP BY stream_id,event_id HAVING max(revision)<>count(*)
            LIMIT 1""",
            (sid, sid),
        ).fetchone()
        if gap:
            raise SourceConflict("revision history must start at 1 and be contiguous")
        # Validate effective records, including corrections, before the atomic publication.
        overlap = conn.execute(
            """WITH candidates AS (
            SELECT stream_id,event_id,revision,kind,occurred_at,interval_start
            FROM current_measurement
            WHERE stream_id IN (SELECT stream_id FROM staged_measurement WHERE source_id=%s)
            UNION ALL SELECT stream_id,event_id,revision,kind,occurred_at,interval_start
            FROM staged_measurement WHERE source_id=%s), effective AS (
            SELECT DISTINCT ON (stream_id,event_id) * FROM candidates
            ORDER BY stream_id,event_id,revision DESC), intervals AS (
            SELECT interval_start, max(occurred_at) OVER (
                PARTITION BY stream_id ORDER BY interval_start,occurred_at,event_id
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous_end
            FROM effective WHERE kind='interval')
            SELECT 1 FROM intervals WHERE interval_start < previous_end
            UNION ALL SELECT 1 FROM effective WHERE kind='index'
            GROUP BY stream_id,occurred_at HAVING count(*)>1 LIMIT 1""",
            (sid, sid),
        ).fetchone()
        if overlap:
            raise SourceConflict("overlapping intervals or ambiguous index timestamps")
        conn.execute(
            """INSERT INTO measurement(stream_id,event_id,revision,kind,occurred_at,
            interval_start,original_time,original_value,original_unit,liters,fingerprint,
            configuration_id)
            SELECT DISTINCT ON (stream_id,event_id,revision) stream_id,event_id,revision,kind,
                occurred_at,interval_start,original_time,original_value,original_unit,liters,
                fingerprint,configuration_id
            FROM staged_measurement WHERE source_id=%s
            ORDER BY stream_id,event_id,revision,row_no ON CONFLICT DO NOTHING""",
            (sid,),
        )
        conn.execute(
            """INSERT INTO provenance(source_id,row_no,measurement_id,received_at)
            SELECT s.source_id,s.row_no,m.id,s.received_at FROM staged_measurement s
            JOIN measurement m USING(stream_id,event_id,revision) WHERE s.source_id=%s
            ON CONFLICT DO NOTHING""",
            (sid,),
        )
        conn.execute("DELETE FROM quality_issue WHERE source_id=%s", (sid,))
        conn.execute(
            "INSERT INTO quality_issue(source_id,row_no,code) "
            "SELECT source_id,row_no,code FROM staged_quality_issue WHERE source_id=%s",
            (sid,),
        )
        conn.execute("DELETE FROM staged_quality_issue WHERE source_id=%s", (sid,))
        conn.execute(
            "UPDATE source SET state='published',"
            "published_at=COALESCE(published_at,clock_timestamp()) WHERE id=%s",
            (sid,),
        )
        conn.execute(
            "UPDATE job SET state='succeeded',token=NULL,lease_until=NULL WHERE id=%s", (job["id"],)
        )
        conn.execute("DELETE FROM staged_measurement WHERE source_id=%s", (sid,))


def fail(settings: Settings, job: dict, code: str, permanent: bool = False) -> None:
    with connect(settings) as conn:
        owned(conn, job)
        terminal = permanent or job["attempts"] >= job["max_attempts"]
        conn.execute(
            """UPDATE job SET state=%s,error_code=%s,token=NULL,lease_until=NULL,
            available_at=clock_timestamp() + %s * interval '1 second' WHERE id=%s""",
            ("failed" if terminal else "queued", code, min(60, 2 ** job["attempts"]), job["id"]),
        )
        conn.execute(
            "UPDATE source SET state=%s WHERE id=%s AND state<>'published'",
            ("failed" if terminal else "accepted", job["source_id"]),
        )


def process(settings: Settings, job: dict) -> dict:
    with keep_lease(settings, job):
        return _process(settings, job)


def _process(settings: Settings, job: dict) -> dict:
    with connect(settings) as conn:
        source = conn.execute("SELECT * FROM source WHERE id=%s", (job["source_id"],)).fetchone()
    if source["options_sha256"] != options_digest(source["normalizer_options"]):
        raise SourceConflict("source mapping checksum mismatch")
    if source["acquisition"].get("data_kind") != settings.environment:
        raise SourceConflict("source environment does not match worker environment")
    normalizer = get_normalizer(
        source["contract_version"], source["parser_version"], source["normalizer_options"]
    )
    store = FileStore(settings)
    store.verify(source["object_key"], source["sha256"], source["byte_size"])
    chunk = []
    row_no = 0
    with gzip.open(store.path(source["object_key"]), "rb") as incoming:
        for item in normalizer.rows(incoming, settings.max_line_bytes, settings.max_records):
            row_no = item.number
            if row_no <= job["checkpoint"]:
                continue
            if item.record is not None:
                item = NormalizedRow(row_no, qualify(item.record, source["namespace"]))
            chunk.append(item)
            if len(chunk) >= settings.chunk_size:
                _stage_rows(settings, job, chunk, row_no)
                chunk = []
    if row_no == 0:
        raise SourceConflict("empty source")
    if chunk:
        _stage_rows(settings, job, chunk, row_no)
    publish(settings, job)
    with connect(settings) as conn:
        issues = conn.execute(
            "SELECT count(*) AS n FROM quality_issue WHERE source_id=%s", (job["source_id"],)
        ).fetchone()["n"]
        accepted = conn.execute(
            "SELECT count(*) AS n FROM provenance WHERE source_id=%s", (job["source_id"],)
        ).fetchone()["n"]
    state = "insufficient_data" if accepted == 0 else "partial" if issues else "succeeded"
    return {
        "job_id": str(job["id"]),
        "state": state,
        "accepted_rows": accepted,
        "quarantined_rows": issues,
    }


def work_once(settings: Settings) -> dict:
    try:
        job = claim(settings)
    except (psycopg.OperationalError, psycopg.InterfaceError):
        return {"state": "connection_unavailable"}
    if job is None:
        return {"state": "idle"}
    try:
        return process(settings, job)
    except LostLease:
        return {"job_id": str(job["id"]), "state": "lease_lost"}
    except (psycopg.OperationalError, psycopg.InterfaceError):
        return {"job_id": str(job["id"]), "state": "connection_unavailable"}
    except Exception as exc:
        code = type(exc).__name__
        try:
            fail(
                settings,
                job,
                code,
                isinstance(exc, (SourceConflict, SourceLimit, NormalizationError)),
            )
        except LostLease:
            return {"job_id": str(job["id"]), "state": "lease_lost"}
        except (psycopg.OperationalError, psycopg.InterfaceError):
            return {"job_id": str(job["id"]), "state": "connection_unavailable"}
        return {"job_id": str(job["id"]), "state": "failed_attempt", "error_code": code}


def retry(settings: Settings, source_id: str) -> None:
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT * FROM job WHERE source_id=%s FOR UPDATE", (source_id,)
        ).fetchone()
        if not row or row["state"] not in {"failed", "succeeded"}:
            raise ValueError("only terminal jobs may be explicitly reprocessed")
        # Existing measurements remain immutable; newly resolved rows may now be added.
        if (
            row["state"] == "succeeded"
            and not conn.execute(
                "SELECT 1 FROM quality_issue WHERE source_id=%s LIMIT 1", (source_id,)
            ).fetchone()
        ):
            raise ValueError("clean source requires a new parser version or corrected source")
        conn.execute("DELETE FROM staged_measurement WHERE source_id=%s", (source_id,))
        conn.execute("DELETE FROM staged_quality_issue WHERE source_id=%s", (source_id,))
        conn.execute("DELETE FROM job_configuration WHERE job_id=%s", (row["id"],))
        conn.execute(
            "UPDATE job SET state='queued',max_attempts=attempts+%s,checkpoint=0,"
            "available_at=clock_timestamp(),error_code=NULL,configurations_pinned=false "
            "WHERE id=%s",
            (settings.max_attempts, row["id"]),
        )
        conn.execute(
            "UPDATE source SET state='accepted' WHERE id=%s AND state<>'published'", (source_id,)
        )
        conn.execute(
            "INSERT INTO audit_event(action,target_id) VALUES ('source.retry',%s)", (source_id,)
        )
