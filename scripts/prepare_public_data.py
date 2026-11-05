"""Compile verified reference CSVs to separate, source-preserving Parquet tables.

This is an inspection layer, not the canonical measurement normalizer. Source
values stay strings to prevent guessed units, time zones or label semantics.
No cross-source joins, zero filling, resampling, deduplication or publisher-code
execution occurs. Each table has explicit source identity and row lineage.
"""

import argparse
import csv
import io
import json
import math
import re
import uuid
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pyarrow as pa
import pyarrow.parquet as pq
from collect_public_data import CATALOG, LIBRARY, digest_file, validate_file, write_json


def safe_member(name):
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in name:
        raise ValueError(f"Unsafe archive member: {name}")
    return path


def table_role(source_id, source_file):
    if source_id == "hihaigua-v2":
        if "/lab-generated/" in source_file:
            return (
                "manual_controlled_lab_labels"
                if source_file.endswith("labels.csv")
                else "controlled_lab_telemetry_or_devices"
            )
        return "field_device_registry" if source_file.endswith("devs.csv") else "field_telemetry"
    if source_id.startswith("ciws-"):
        if "/RawData/" in source_file:
            return "observed_pulse_counts"
        if "TrainingDataset_SS" in source_file:
            return "publisher_manual_training_labels"
        if "/Results/" in source_file:
            return "predicted_end_use_labels_duplicate_result_views"
        return "household_context_unverified_against_paper"
    return {
        "hsb-living-lab-v1.1": "cleaned_fixture_values_unit_conflict",
        "illinois-actual-events": "publisher_ground_truthed_event_features",
        "illinois-atypical-events": "predicted_events_or_context_temperature",
        "illinois-showers": "observed_shower_event_features",
        "illinois-ctgan-balanced": "synthetic_event_features",
        "illinois-appliance-traces": "fixture_relative_time_traces",
        "aalborg-v1-reference": "processed_diary_cohort"
        if "hourly_data.csv" in source_file
        else "processed_annual_cohort",
    }.get(source_id, "unmapped_inspection_only")


def prepare_csv(stream, output, source_id, source_file, delimiter=","):
    """Preserve every nonblank CSV record and its row position; reject ragged rows."""
    reader = csv.reader(
        io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""), delimiter=delimiter
    )
    original_headers = next(reader)
    headers = [h if h else f"_column_{i}" for i, h in enumerate(original_headers)]
    lineage = ["_source_id", "_source_file", "_source_row"]
    if len(set(headers)) != len(headers) or set(headers) & set(lineage):
        raise ValueError(f"Ambiguous CSV headers: {source_file}")
    schema = pa.schema(
        [(h, pa.string()) for h in headers]
        + [("_source_id", pa.string()), ("_source_file", pa.string()), ("_source_row", pa.int64())],
        metadata={b"semantics": b"source-preserving inspection; no unit or timezone conversion"},
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    counts = {h: Counter() for h in headers}
    distinct = {h: set() for h in headers}
    overflow = set()
    missing = Counter()
    rows, blank_lines, buffer = 0, 0, []

    def flush(writer):
        writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
        buffer.clear()

    with pq.ParquetWriter(output, schema, compression="zstd") as writer:
        for source_row, values in enumerate(reader, start=1):
            if not values:
                blank_lines += 1
                continue
            if len(values) != len(headers):
                raise ValueError(f"Ragged CSV {source_file}, record {source_row}")
            row = dict(zip(headers, values, strict=True))
            for h, value in row.items():
                if not value.strip():
                    missing[h] += 1
                if h not in overflow:
                    distinct[h].add(value)
                    if len(distinct[h]) > 50:
                        overflow.add(h)
                        distinct[h].clear()
                        counts[h].clear()
                    else:
                        counts[h][value] += 1
            row.update(_source_id=source_id, _source_file=source_file, _source_row=source_row)
            buffer.append(row)
            rows += 1
            if len(buffer) >= 50_000:
                flush(writer)
        if buffer:
            flush(writer)
    return {
        "source": source_id,
        "source_file": source_file,
        "table_role": table_role(source_id, source_file),
        "rows": rows,
        "blank_csv_records_skipped": blank_lines,
        "original_headers": original_headers,
        "columns": headers,
        "empty_string_counts": dict(missing),
        "low_cardinality_values": {h: dict(c) for h, c in counts.items() if h not in overflow},
        "high_cardinality_columns": sorted(overflow),
        "parquet": str(output),
        "output_sha256": digest_file(output)["sha256"],
    }


def compile_source(source, library, output):
    results, archives, inputs = [], [], []
    for spec in source.get("files", []):
        path = library / source["id"] / "raw" / spec["path"]
        receipt = json.loads(
            path.with_name(path.name + ".receipt.json").read_text(encoding="utf-8")
        )
        actual = validate_file(path, spec, receipt)
        inputs.append({"path": str(path), **actual})
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > 2000 or sum(m.file_size for m in members) > 2_000_000_000:
                    raise ValueError(f"Archive exceeds inspection budget: {path}")
                for member in members:
                    member_path = safe_member(member.filename)
                    if member.is_dir() or "__MACOSX" in member_path.parts:
                        continue
                    if member.file_size > 200_000_000:
                        raise ValueError(f"Member exceeds inspection budget: {member.filename}")
                    archives.append(
                        {
                            "archive": spec["path"],
                            "member": member.filename,
                            "bytes": member.file_size,
                            "crc32": f"{member.CRC:08x}",
                        }
                    )
                    if member_path.suffix.lower() == ".csv":
                        table_path = output / spec["path"] / member_path.with_suffix(".parquet")
                        with archive.open(member) as stream:
                            results.append(
                                prepare_csv(
                                    stream,
                                    table_path,
                                    source["id"],
                                    spec["path"] + "!" + member.filename,
                                    ";" if source["id"].startswith("hsb-") else ",",
                                )
                            )
                    elif member_path.suffix.lower() in (".txt", ".md", ".json", ".docx"):
                        # Documentation only. No downloaded scripts are executed.
                        if member.file_size > 1_000_000:
                            raise ValueError("Documentation member exceeds budget")
                        doc_path = output / "documentation" / member_path
                        doc_path.parent.mkdir(parents=True, exist_ok=True)
                        doc_path.write_bytes(archive.read(member))
        elif path.suffix.lower() == ".csv":
            with path.open("rb") as stream:
                results.append(
                    prepare_csv(
                        stream,
                        output / Path(spec["path"]).with_suffix(".parquet"),
                        source["id"],
                        spec["path"],
                    )
                )
    return {
        "source": source["id"],
        "reuse_gate": source["reuse_gate"],
        "inputs": inputs,
        "archive_inventory": archives,
        "tables": results,
    }


def prepare_weather(source, library, output):
    path = library / source["id"] / "raw/daily-1981-2025.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    parameters = data["properties"]["parameter"]
    fill = data["header"]["fill_value"]
    days = sorted(set.union(*(set(values) for values in parameters.values())))
    records = []
    for day in days:
        record = {
            "date_utc": datetime.strptime(day, "%Y%m%d").date(),
            "source_id": source["id"],
            "latitude_requested": 40.77,
            "longitude_requested": 29.94,
            "context_kind": "city_reference_grid",
        }
        for parameter, values in parameters.items():
            value = values.get(day)
            record[parameter] = None if value == fill else value
        records.append(record)
    path_out = output / "weather_daily.parquet"
    path_out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(records), path_out, compression="zstd")
    return {
        "rows": len(records),
        "parameters": data["parameters"],
        "header": data["header"],
        "geometry": data.get("geometry"),
        "null_counts": {p: sum(row[p] is None for row in records) for p in parameters},
        "parquet": str(path_out),
        "output_sha256": digest_file(path_out)["sha256"],
        "date_min": days[0],
        "date_max": days[-1],
    }


def regional_records(snapshots):
    records = []
    for snapshot in snapshots:
        headers = snapshot["rows"][0]
        for row_number, row in enumerate(snapshot["rows"][1:], start=2):
            if not row or not isinstance(row[0], str) or not row[0].strip():
                continue
            for column, header in enumerate(headers[1:], start=1):
                # Year/month keys only. The 2023 grand-total column is not a month.
                if header is None:
                    continue
                match = re.fullmatch(
                    r"(\d{4})(\d{2})?(?:\s*\(.*\))?", str(header).replace(".0", "")
                )
                if not match:
                    raise ValueError(f"Unrecognized regional period: {header}")
                year, month = match.groups()
                if month and not 1 <= int(month) <= 12:
                    raise ValueError(f"Invalid month: {header}")
                value = row[column] if column < len(row) else None
                if value is not None and (
                    not isinstance(value, (int, float)) or not math.isfinite(value)
                ):
                    raise ValueError("Regional consumption must be numeric or missing")
                district = row[0].strip()
                external_area = district in {"KOCAELİ", "GEBZE-İSKİ"}
                records.append(
                    {
                        "source_id": "istanbul-district-consumption",
                        "source_file": snapshot["file"],
                        "source_sheet": snapshot["sheet"],
                        "source_row": row_number,
                        "source_column": column + 1,
                        "district_source": row[0],
                        "district": district,
                        "entity_kind": (
                            "external_reporting_area"
                            if external_area
                            else "publisher_total"
                            if district == "GENEL TOPLAM"
                            else "district"
                        ),
                        "geography": "unresolved_external_reporting_area"
                        if external_area
                        else "Istanbul",
                        "period": year + ("-" + month if month else ""),
                        "grain": "month" if month else "year",
                        "volume_m3": value,
                        "unit_basis": "publisher CKAN resource description",
                    }
                )
    return records


def prepare_regional(source, library, output):
    path = library / source["id"] / "worksheet-snapshot.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for sheet in data["sheets"]:
        spec = next(f for f in source["files"] if f["path"] == sheet["file"])
        if sheet["input_sha256"] != spec["sha256"]:
            raise ValueError("Regional worksheet snapshot does not match pinned workbook")
    records = regional_records(data["sheets"])
    path_out = output / "regional_consumption.parquet"
    path_out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(records), path_out, compression="zstd")
    overlapping = {}
    for row in records:
        if row["entity_kind"] == "district":
            key = row["district"] + "/" + row["period"]
            overlapping.setdefault(key, []).append((row["source_file"], row["volume_m3"]))
    conflicts = {k: v for k, v in overlapping.items() if len(v) > 1}
    return {
        "rows": len(records),
        "parquet": str(path_out),
        "output_sha256": digest_file(path_out)["sha256"],
        "snapshot_sha256": digest_file(path)["sha256"],
        "overlapping_keys": conflicts,
        "conflicting_values": sum(len({v for _, v in rows}) > 1 for rows in conflicts.values()),
        "aggregation_policy": "Keep releases, years, months and publisher totals separate",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--library", type=Path, default=LIBRARY)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    run = args.library / "prepared" / uuid.uuid4().hex
    run.mkdir(parents=True)
    profiles = []
    for source in catalog["sources"]:
        profile = compile_source(source, args.library, run / source["id"])
        if source["id"] == "nasa-power-kocaeli-context":
            profile["weather"] = prepare_weather(source, args.library, run / source["id"])
        if source["id"] == "istanbul-district-consumption":
            profile["regional"] = prepare_regional(source, args.library, run / source["id"])
        profiles.append(profile)
        print(
            f"prepared {source['id']}: {sum(t['rows'] for t in profile['tables'])} CSV rows",
            flush=True,
        )
    manifest = {
        "schema": "urban-water.public-preparation.v1",
        "prepared_at": datetime.now(UTC).isoformat(),
        "catalog_sha256": digest_file(args.catalog)["sha256"],
        "script_sha256": digest_file(Path(__file__))["sha256"],
        "source_profiles": profiles,
        "table_count": sum(len(p["tables"]) for p in profiles),
        "csv_row_count": sum(t["rows"] for p in profiles for t in p["tables"]),
        "training_status": "inspection_only_source_specific_semantic_gates_remain",
    }
    write_json(run / "profiles.json", manifest)
    write_json(
        args.library / "prepared/latest.json",
        {"run": str(run), "profiles_sha256": digest_file(run / "profiles.json")["sha256"]},
    )
    print(json.dumps({k: v for k, v in manifest.items() if k != "source_profiles"}, indent=2))


if __name__ == "__main__":
    main()
