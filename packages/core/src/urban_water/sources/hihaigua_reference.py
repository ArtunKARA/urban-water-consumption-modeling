"""Pinned hihAigua field counter preparation; canonical publication fails closed.

The publisher's preprocessing code is evidence only, never executed. Water and
hotWater remain separate physical counter streams. No zero filling, clock rounding,
daily resets, interpolation, leak labels, or whole-house totals are fabricated.
"""

import csv
import hashlib
import io
import json
import re
import tomllib
import zipfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from urban_water.db import PROJECT_ROOT
from urban_water.sources.aalborg_reference import write_bytes, write_json
from urban_water.sources.config import SourceSpec

POLICY = PROJECT_ROOT / "research/reference_data/hihaigua-v2.json"
LIBRARY = PROJECT_ROOT / "var/data-library/hihaigua-v2/raw"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
NATIVE_HEADER = ["entity_id", "key", "ts", "ts_dt", "value"]
DEVICE_HEADER = ["_id", "installation", "asset", "type"]
COMPONENTS = {"water", "hotWater"}
SCHEMA = pa.schema(
    [
        ("household_source", pa.string()),
        ("household_code", pa.string()),
        ("component", pa.string()),
        ("entity_id", pa.string()),
        ("asset", pa.string()),
        ("stream_code", pa.string()),
        ("event_id", pa.string()),
        ("source_member", pa.string()),
        ("source_row", pa.int64()),
        ("epoch_ms", pa.int64()),
        ("occurred_at", pa.timestamp("ms", tz="UTC")),
        ("received_at", pa.timestamp("us", tz="UTC")),
        ("ts_dt_source", pa.string()),
        ("value_source", pa.string()),
        ("counter_value", pa.decimal256(76, 40)),
        ("counter_delta_source", pa.decimal256(76, 40)),
        ("contract_value", pa.decimal128(24, 9)),
        ("contract_adjustment_source", pa.decimal256(76, 40)),
        ("unit", pa.string()),
        ("delta_status", pa.string()),
        ("quality_flags", pa.list_(pa.string())),
    ]
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_policy(path: Path) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schema") != "urban-water.hihaigua-reference.v1":
        raise ValueError("unsupported hihAigua policy")
    if set(policy["components"]) != COMPONENTS:
        raise ValueError("only the two independently scoped water families are supported")
    if not re.fullmatch(r"[0-9a-f]{64}", policy["archive_sha256"]):
        raise ValueError("reference requires a pinned archive SHA-256")
    homes = policy["household_ids"]
    if (
        not homes
        or len(homes) != len(set(homes))
        or any(not re.fullmatch(r"\d{1,4}", h) for h in homes)
    ):
        raise ValueError("invalid or duplicated native installation IDs")
    if len(set(map(int, homes))) != len(homes):
        raise ValueError("native installation aliases would collide")
    if not 0 < policy["expected_cadence_ms"] <= policy["gap_threshold_ms"]:
        raise ValueError("invalid cadence/gap policy")
    for component in COMPONENTS:
        review = policy["components"][component]["unit_review"]
        if review["status"] == "pending":
            if review["unit"] is not None:
                raise ValueError("pending unit cannot be assigned a canonical scale")
        elif review["status"] == "verified":
            if (
                review["unit"] not in {"L", "m3"}
                or not isinstance(review["evidence_url"], str)
                or not review["evidence_url"].startswith("https://")
                or not isinstance(review["evidence_quote"], str)
                or not review["evidence_quote"].strip()
            ):
                raise ValueError(
                    "verified units require a volume unit and explicit source evidence"
                )
        else:
            raise ValueError("unknown unit review status")
    return policy


def read_csv(archive, name, header):
    member = archive.getinfo(name)
    if member.file_size > 200_000_000:
        raise ValueError("native CSV exceeds the member byte budget")
    with archive.open(member) as stream:
        rows = csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""), strict=True)
        if next(rows, None) != header:
            raise ValueError(f"native export header changed: {name}")
        for number, cells in enumerate(rows, 1):
            if len(cells) != len(header):
                raise ValueError(f"native export column count changed: {name}:{number}")
            yield number, dict(zip(header, cells, strict=True))


def parse_counter(value: str):
    if value == "":
        return None, "missing_counter"
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0 or number >= Decimal(10) ** 15:
            raise ValueError("counter outside supported numeric range")
        if number.as_tuple().exponent < -40:
            return None, "unsupported_source_precision"
    except (InvalidOperation, ValueError):
        return None, "invalid_counter"
    return number, None


def prepare_row(raw, *, home, device, component, member, source_row, receipt, unit, prior, gap_ms):
    """Preserve source counters, including bad/missing values, with explicit barriers."""
    if not raw["ts"].isascii() or not raw["ts"].isdigit():
        raise ValueError("epoch must be integer milliseconds")
    epoch_ms = int(raw["ts"])
    instant = EPOCH + timedelta(milliseconds=epoch_ms)
    if not 2000 <= instant.year <= 2100:
        raise ValueError("epoch outside supported research period")
    text_clock = datetime.fromisoformat(raw["ts_dt"])
    # Source text is checked, never localized using the ambiguous GMT+1 description.
    text_utc = (
        text_clock.replace(tzinfo=UTC) if text_clock.tzinfo is None else text_clock.astimezone(UTC)
    )
    flags = []
    if text_utc != instant:
        flags.append("text_epoch_clock_mismatch")
    value, invalid = parse_counter(raw["value"])
    if invalid:
        flags.append(invalid)
    with localcontext() as context:
        context.prec = 90
        contract_value = (
            value.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_EVEN)
            if value is not None
            else None
        )
        adjustment = contract_value - value if value is not None else None
    if contract_value is not None and contract_value >= Decimal(10) ** 15:
        contract_value, adjustment = None, None
        flags.append("canonical_counter_out_of_range")
    if adjustment:
        flags.append("canonical_precision_adjusted")
    delta = None
    if prior is None:
        status = "initial_reading"
    elif epoch_ms <= prior["epoch_ms"]:
        status = "nonincreasing_timestamp"
        flags.append(status)
    elif value is None or prior["counter_value"] is None:
        status = "missing_counter_barrier"
    else:
        with localcontext() as context:
            context.prec = 90
            delta = value - prior["counter_value"]
        status = "counter_decrease" if delta < 0 else "observed_adjacent_delta"
        if delta < 0:
            flags.append("counter_decrease")
    if prior is not None and epoch_ms - prior["epoch_ms"] > gap_ms:
        flags.append("observation_gap")
    if prior is not None and prior["counter_value"] is None:
        flags.append("previous_counter_unusable")
    if unit is None:
        flags.append("unit_unverified")
    return {
        "household_source": home,
        "household_code": f"HIHAIGUA-H{int(home):02}",
        "component": component,
        "entity_id": raw["entity_id"],
        "asset": device["asset"],
        "stream_code": f"HIHAIGUA-H{int(home):02}-{component}",
        "event_id": f"{raw['entity_id']}:{epoch_ms}",
        "source_member": member,
        "source_row": source_row,
        "epoch_ms": epoch_ms,
        "occurred_at": instant,
        "received_at": receipt,
        "ts_dt_source": raw["ts_dt"],
        "value_source": raw["value"],
        "counter_value": value,
        "counter_delta_source": delta,
        "contract_value": contract_value,
        "contract_adjustment_source": adjustment,
        "unit": unit,
        "delta_status": status,
        "quality_flags": flags,
    }


def verify_prepared(directory: Path, policy_digest: str) -> dict | None:
    marker = directory / "manifest.json"
    if not marker.exists():
        return None
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    if manifest["policy_sha256"] != policy_digest or manifest["code_sha256"] != sha256(
        Path(__file__)
    ):
        raise ValueError("prepared reference differs from current policy/code; use a new directory")
    for item in manifest["artifacts"]:
        path = (directory / item["path"]).resolve()
        if not path.is_relative_to(directory.resolve()) or sha256(path) != item["sha256"]:
            raise ValueError("prepared reference artifact changed")
    return manifest


def export_canonical_config(directory, component, policy, streams, receipt, policy_digest):
    review = policy["components"][component]["unit_review"]
    if review["status"] != "verified":
        return []
    path = directory / component / "counter-readings.csv"
    columns = [
        "stream_code",
        "event_id",
        "occurred_at",
        "received_at",
        "value",
        "value_source",
        "source_member",
        "source_row",
        "entity_id",
        "ts_dt_source",
        "delta_status",
        "quality_flags",
    ]
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for batch in pq.ParquetFile(directory / component / "counters.parquet").iter_batches():
            for row in batch.to_pylist():
                if (
                    "text_epoch_clock_mismatch" in row["quality_flags"]
                    or "nonincreasing_timestamp" in row["quality_flags"]
                ):
                    raise ValueError("clock conflicts require review before canonical export")
                writer.writerow(
                    {
                        **{
                            k: row[k]
                            for k in (
                                "stream_code",
                                "event_id",
                                "source_member",
                                "source_row",
                                "entity_id",
                                "ts_dt_source",
                            )
                        },
                        "occurred_at": row["occurred_at"].isoformat(),
                        "received_at": receipt.isoformat(),
                        "value": str(row["contract_value"])
                        if row["contract_value"] is not None
                        else row["value_source"],
                        "value_source": row["value_source"],
                        "delta_status": row["delta_status"],
                        "quality_flags": json.dumps(row["quality_flags"], separators=(",", ":")),
                    }
                )
    registry = directory / component / "registry.json"
    write_json(
        registry,
        {
            "format": "urban-water.registry.v1",
            "entries": [
                {
                    "household_code": s["household_code"],
                    "meter_code": s["stream_code"],
                    "stream_code": s["stream_code"],
                    "kind": "index",
                    "valid_from": s["start"],
                    "valid_to": (
                        datetime.fromisoformat(s["end"]) + timedelta(milliseconds=1)
                    ).isoformat(),
                }
                for s in streams
            ],
        },
        immutable=True,
    )
    spec = SourceSpec(
        namespace=f"hihaigua-{policy['record_id']}-{component}",
        data_kind="public_research",
        normalizer="mapped_csv_v1",
        path="counter-readings.csv",
        fields={k: k for k in ("stream_code", "event_id", "occurred_at", "received_at", "value")},
        constants={"kind": "index", "unit": review["unit"]},
        receipt_basis="research_archive_import",
        value_semantics="as_delivered",
        citation=f"doi:{policy['doi']}; {component} counters; unit evidence in reference policy",
        license=policy["license"],
        expected_sha256=sha256(path),
        reference_sha256=policy_digest,
    )
    config = directory / component / "sources.toml"
    text = "[sources.meter_readings]\n"
    for key, value in spec.model_dump().items():
        if value is None or key in {"fields", "constants"}:
            continue
        text += f"{key} = {json.dumps(value)}\n"
    for section in ("fields", "constants"):
        text += f"\n[sources.meter_readings.{section}]\n"
        text += "".join(f"{k} = {json.dumps(v)}\n" for k, v in getattr(spec, section).items())
    # TOML and the allowlisted interface must agree before exposing a source point.
    SourceSpec.model_validate(tomllib.loads(text)["sources"]["meter_readings"])
    write_bytes(config, text.encode(), immutable=True)
    return [path, registry, config]


def prepare_reference(directory: Path, *, library: Path = LIBRARY, policy_path: Path = POLICY):
    directory = Path(directory).resolve()
    policy = load_policy(policy_path)
    policy_digest = sha256(policy_path)
    archive_path = library / policy["archive"]
    if (
        archive_path.stat().st_size != policy["archive_bytes"]
        or sha256(archive_path) != policy["archive_sha256"]
    ):
        raise ValueError("native hihAigua archive differs from the pinned release")
    receipt_path = archive_path.with_name(archive_path.name + ".receipt.json")
    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt_data["sha256"] != policy["archive_sha256"]:
        raise ValueError("archive receipt differs from pinned release")
    receipt = datetime.fromisoformat(receipt_data["downloaded_at"])
    if receipt.tzinfo is None or receipt.utcoffset() is None:
        raise ValueError("archive receipt requires an offset")
    cached = verify_prepared(directory, policy_digest)
    if cached is not None:
        if cached["archive_received_at"] != receipt.isoformat():
            raise ValueError("archive receipt changed after preparation")
        return cached
    directory.mkdir(parents=True, exist_ok=False)
    artifacts, buffers, writers, inventories, flag_counts = [], {}, {}, {}, {}
    total_rows = 0
    for component in sorted(COMPONENTS):
        folder = directory / component
        folder.mkdir()
        path = folder / "counters.parquet"
        artifacts.append(path)
        writers[component] = pq.ParquetWriter(path, SCHEMA, compression="zstd")
        buffers[component], inventories[component], flag_counts[component] = [], [], Counter()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if (
                len(archive.infolist()) > 2000
                or sum(m.file_size for m in archive.infolist()) > 2_000_000_000
            ):
                raise ValueError("native archive exceeds the inspection budget")
            for home in policy["household_ids"]:
                prefix = f"{policy['zip_prefix']}/installation_{home}"
                devices = [r for _, r in read_csv(archive, prefix + "/devs.csv", DEVICE_HEADER)]
                by_id = {d["_id"]: d for d in devices}
                if (
                    len(by_id) != len(devices)
                    or any(d["installation"] != home for d in devices)
                    or any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", d["_id"]) for d in devices)
                ):
                    raise ValueError("ambiguous installation/device identity")
                for component in COMPONENTS:
                    if sum(d["type"] == component for d in devices) != 1:
                        raise ValueError(
                            "each selected home requires exactly one counter per water family"
                        )
                previous, stats = {}, {}
                member = prefix + "/ts.csv"
                for number, raw in read_csv(archive, member, NATIVE_HEADER):
                    total_rows += 1
                    device = by_id.get(raw["entity_id"])
                    if device is None:
                        raise ValueError("telemetry references an unregistered source device")
                    component = device["type"]
                    if component not in COMPONENTS or raw["key"] != "volume":
                        continue
                    unit = policy["components"][component]["unit_review"]["unit"]
                    row = prepare_row(
                        raw,
                        home=home,
                        device=device,
                        component=component,
                        member=member,
                        source_row=number,
                        receipt=receipt,
                        unit=unit,
                        prior=previous.get(component),
                        gap_ms=policy["gap_threshold_ms"],
                    )
                    previous[component] = row
                    flag_counts[component].update(row["quality_flags"])
                    item = stats.setdefault(
                        component,
                        {
                            "household_code": row["household_code"],
                            "stream_code": row["stream_code"],
                            "entity_id": row["entity_id"],
                            "asset": row["asset"],
                            "rows": 0,
                            "start": row["occurred_at"].isoformat(),
                            "end": row["occurred_at"].isoformat(),
                        },
                    )
                    item["rows"] += 1
                    item["end"] = row["occurred_at"].isoformat()
                    buffers[component].append(row)
                    if len(buffers[component]) >= 25_000:
                        writers[component].write_table(
                            pa.Table.from_pylist(buffers[component], schema=SCHEMA)
                        )
                        buffers[component].clear()
                for component in COMPONENTS:
                    if component not in stats:
                        raise ValueError("a declared water stream has no telemetry")
                    inventories[component].append(stats[component])
                print(f"prepared native field installation {home}", flush=True)
        for component in COMPONENTS:
            if buffers[component]:
                writers[component].write_table(
                    pa.Table.from_pylist(buffers[component], schema=SCHEMA)
                )
    finally:
        for writer in writers.values():
            writer.close()
    for component in sorted(COMPONENTS):
        artifacts += export_canonical_config(
            directory, component, policy, inventories[component], receipt, policy_digest
        )
    manifest = {
        "schema": "urban-water.hihaigua-prepared.v1",
        "directory": str(directory),
        "policy_sha256": policy_digest,
        "code_sha256": sha256(Path(__file__)),
        "archive_sha256": policy["archive_sha256"],
        "archive_received_at": receipt.isoformat(),
        "receipt_basis": "research_archive_import",
        "timezone": "UTC",
        "field_households": len(policy["household_ids"]),
        "native_field_rows_scanned": total_rows,
        "components": {
            c: {
                "rows": sum(s["rows"] for s in inventories[c]),
                "streams": inventories[c],
                "quality_counts": dict(flag_counts[c]),
                "unit_review": policy["components"][c]["unit_review"],
                "canonical_ready": policy["components"][c]["unit_review"]["status"] == "verified",
            }
            for c in sorted(COMPONENTS)
        },
        "artifacts": [
            {"path": str(p.relative_to(directory)), "sha256": sha256(p), "bytes": p.stat().st_size}
            for p in artifacts
        ],
        "model_ready": False,
        "whole_household_total_available": False,
        "numeric_transform": {
            "raw_counter": "exact source decimal, up to 40 fractional digits; source text retained",
            "contract_value": "9 fractional digits, Decimal ROUND_HALF_EVEN",
            "maximum_absolute_adjustment_source_units": "0.0000000005",
            "litre_conversion": "blocked until component unit evidence is verified",
        },
        "scope": "field-only counter components; no lab or other utility telemetry",
    }
    write_json(directory / "manifest.json", manifest, immutable=True)
    return manifest


def publish_component(settings, directory: Path, component: str, *, policy_path: Path = POLICY):
    """Publish one evidence-verified component into a dedicated research scope.

    Never creates or resets a database. The caller supplies the research environment.
    The session lock serializes this guarded workflow, including competing components.
    Direct generic CLI use is lower-level and does not provide this scope guarantee.
    """
    from urban_water.datasets import freeze, verify_dataset
    from urban_water.db import connect
    from urban_water.ingestion import retry, work_once
    from urban_water.processing import summarize
    from urban_water.sources import collect
    from urban_water.sources.registration import register_file

    directory = Path(directory).resolve()
    policy = load_policy(policy_path)
    manifest = verify_prepared(directory, sha256(policy_path))
    if manifest is None or component not in COMPONENTS:
        raise ValueError("unknown or unprepared component")
    if (
        policy["components"][component]["unit_review"]["status"] != "verified"
        or not manifest["components"][component]["canonical_ready"]
    ):
        raise ValueError("canonical publication blocked: volume unit evidence is pending")
    expected_study = f"hihaigua-{policy['record_id']}-{component}"
    expected_cohort = f"field-{component}-v2"
    if (
        settings.environment != "public_research"
        or settings.study_code != expected_study
        or settings.cohort_code != expected_cohort
        or settings.timezone != "UTC"
    ):
        raise ValueError(f"use a dedicated public/UTC scope: {expected_study}/{expected_cohort}")
    with connect(settings) as guard:
        guard.execute("SELECT pg_advisory_lock(83621004)")
        if (
            guard.execute("SELECT to_regclass('uw.study') AS table_name").fetchone()["table_name"]
            is None
        ):
            raise ValueError("migrate a dedicated research database before component publication")
        if guard.execute(
            "SELECT 1 FROM study WHERE code<>%s LIMIT 1", (expected_study,)
        ).fetchone():
            raise ValueError(
                "component publication requires a separate database; another study exists"
            )
        namespace = f"hihaigua-{policy['record_id']}-{component}"
        if guard.execute(
            "SELECT 1 FROM source WHERE namespace<>%s LIMIT 1", (namespace,)
        ).fetchone():
            raise ValueError("another source family already exists in the target database")
        register_file(settings, directory / component / "registry.json")
        accepted = collect(settings, directory / component / "sources.toml", "meter_readings")
        with connect(settings) as conn:
            source = conn.execute(
                "SELECT state FROM source WHERE id=%s", (accepted["source_id"],)
            ).fetchone()
            job = conn.execute(
                "SELECT state FROM job WHERE source_id=%s", (accepted["source_id"],)
            ).fetchone()
        if source["state"] != "published":
            if job["state"] == "failed":
                retry(settings, accepted["source_id"])
            outcome = work_once(settings)
            if outcome["state"] not in {"succeeded", "partial"}:
                raise RuntimeError(f"reference ingestion incomplete: {outcome['state']}")
        with connect(settings) as conn:
            source = conn.execute(
                "SELECT state FROM source WHERE id=%s", (accepted["source_id"],)
            ).fetchone()
            if source["state"] != "published":
                raise RuntimeError("target reference is not published; inspect earlier queued work")
            counts = conn.execute(
                "SELECT count(*) AS n FROM provenance WHERE source_id=%s", (accepted["source_id"],)
            ).fetchone()
            issues = conn.execute(
                "SELECT count(*) AS n FROM quality_issue WHERE source_id=%s",
                (accepted["source_id"],),
            ).fetchone()
        if counts["n"] + issues["n"] != manifest["components"][component]["rows"]:
            raise ValueError("published/quarantined rows differ from native source inventory")
        summary = summarize(settings, "hour")
        dataset = freeze(settings, "hour")
        if not verify_dataset(settings, dataset["dataset_id"])["verified"]:
            raise ValueError("reference dataset verification failed")
        report = {
            "status": "published_with_quality_issues" if issues["n"] else "published",
            "component": component,
            "source_id": accepted["source_id"],
            "duplicate": accepted["duplicate"],
            "accepted_rows": counts["n"],
            "quarantined_rows": issues["n"],
            "coverage_status": summary["coverage_status"],
            "unallocated_liters": summary["unallocated_liters"],
            "diagnostics": summary["diagnostics"],
            "dataset": dataset,
            "model_ready": False,
            "whole_household_total_available": False,
        }
        write_json(directory / component / "import-report.json", report)
        return report
