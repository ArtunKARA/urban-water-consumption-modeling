"""Check prepared reference evidence and write a compact reproducible audit.

No model fitting or canonical volume conversion. Measurements, source claims,
derived labels and independent truth are deliberately reported separately.
"""

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from collect_public_data import CATALOG, LIBRARY, ROOT, digest_file, write_json


def counter_quality(clocks, numbers):
    """Do not create deltas across missing or non-increasing counter readings."""
    finite = np.isfinite(numbers)
    chronological = np.diff(clocks) > 0
    adjacent_valid = finite[:-1] & finite[1:] & chronological
    return {
        "nonfinite_or_missing_values": int(np.count_nonzero(~finite)),
        "nonincreasing_timestamps": int(np.count_nonzero(~chronological)),
        "counter_decreases": int(np.count_nonzero((np.diff(numbers) < 0) & adjacent_valid)),
        "valid_adjacent_pairs": int(np.count_nonzero(adjacent_valid)),
        "gaps_over_10_5_minutes": int(np.count_nonzero(np.diff(clocks) > 630_000)),
        "value_min_source_units": float(numbers[finite].min()) if finite.any() else None,
        "value_max_source_units": float(numbers[finite].max()) if finite.any() else None,
    }


def main():
    latest = json.loads((LIBRARY / "prepared/latest.json").read_text(encoding="utf-8"))
    profile_path = Path(latest["run"]) / "profiles.json"
    if digest_file(profile_path)["sha256"] != latest["profiles_sha256"]:
        raise ValueError("Prepared profiles changed")
    profiles = json.loads(profile_path.read_text(encoding="utf-8"))
    if profiles["catalog_sha256"] != digest_file(CATALOG)["sha256"]:
        raise ValueError("Re-prepare after changing the catalog")
    by_source = {p["source"]: p for p in profiles["source_profiles"]}
    tables = [t for p in by_source.values() for t in p["tables"]]
    for table in tables:
        if digest_file(Path(table["parquet"]))["sha256"] != table["output_sha256"]:
            raise ValueError("Prepared table changed")
        if pq.read_metadata(table["parquet"]).num_rows != table["rows"]:
            raise ValueError("Prepared row count differs from profile")
    derived = [p[key] for p in by_source.values() for key in ("weather", "regional") if key in p]
    for table in derived:
        if digest_file(Path(table["parquet"]))["sha256"] != table["output_sha256"]:
            raise ValueError("Derived reference table changed")
        if pq.read_metadata(table["parquet"]).num_rows != table["rows"]:
            raise ValueError("Derived reference row count differs from profile")

    hsb = pq.read_table(by_source["hsb-living-lab-v1.1"]["tables"][0]["parquet"])
    values = pc.cast(hsb["value"], pa.float64())
    nonres = pc.and_(pc.equal(hsb["room_type"], "Bathroom"), pc.equal(hsb["cluster_name"], ""))
    all_sum = pc.sum(values).as_py()
    nonres_sum = pc.sum(pc.filter(values, nonres)).as_py()
    published = json.loads(
        (LIBRARY / "hsb-living-lab-v1.1/raw/verified_numbers_R5.json").read_text(encoding="utf-8")
    )["boundary"]
    boundary = {
        "rows": hsb.num_rows,
        "sensors": pc.count_distinct(hsb["sensor_id"]).as_py(),
        "nonresidential_rows": pc.sum(pc.cast(nonres, pa.int64())).as_py(),
        "value_sum_as_delivered": all_sum,
        "nonresidential_value_sum_as_delivered": nonres_sum,
        "residential_value_sum_as_delivered": all_sum - nonres_sum,
        "publisher_boundary": published,
        "canonical_unit": None,
        "unit_gate": "dictionary says litres; R5 code and totals treat same numbers as m3",
    }
    if (
        boundary["rows"] != published["all_clean_records"]
        or abs(all_sum - published["whole_building_m3"]) > 0.001
    ):
        raise ValueError("HSB release no longer reconciles with its R5 verification numbers")

    ciws = by_source["ciws-utah-v2026-11-04"]
    pulse_tables = [t for t in ciws["tables"] if "/RawData/" in t["source_file"]]
    pulse_profiles = []
    for table in pulse_tables:
        data = pq.read_table(table["parquet"], columns=["time", "pulses"])
        counts = pc.cast(data["pulses"], pa.int64())
        clocks = pc.cast(data["time"], pa.timestamp("us", tz="UTC"))
        steps = np.diff(clocks.cast(pa.int64()).to_numpy())
        pulse_profiles.append(
            {
                "file": table["source_file"],
                "rows": data.num_rows,
                "start_utc": str(pc.min(clocks).as_py()),
                "end_utc": str(pc.max(clocks).as_py()),
                "pulse_sum": pc.sum(counts).as_py(),
                "negative_counts": pc.sum(pc.cast(pc.less(counts, 0), pa.int64())).as_py(),
                "steps_not_four_seconds": int(np.count_nonzero(steps != 4_000_000)),
            }
        )
    manual = next(t for t in ciws["tables"] if "TrainingDataset_SS" in t["source_file"])

    spanish = by_source["hihaigua-v2"]
    field_tables = [
        t
        for t in spanish["tables"]
        if "/installation_" in t["source_file"] and t["source_file"].endswith("/ts.csv")
    ]
    water_profiles = []
    for table in field_tables:
        registry_table = next(
            t
            for t in spanish["tables"]
            if t["source_file"] == table["source_file"].replace("/ts.csv", "/devs.csv")
        )
        registry = pq.read_table(registry_table["parquet"]).to_pylist()
        water_devices = [r for r in registry if r["type"] in ("water", "hotWater")]
        data = pq.read_table(table["parquet"], columns=["entity_id", "key", "ts", "value"])
        for device in water_devices:
            selected = data.filter(
                pc.and_(pc.equal(data["entity_id"], device["_id"]), pc.equal(data["key"], "volume"))
            )
            clocks = pc.cast(selected["ts"], pa.int64()).to_numpy()
            source_values = selected["value"]
            # Empty CSV values are missing, never zeros or forward-filled counters.
            numeric_values = pc.if_else(pc.equal(source_values, ""), None, source_values)
            numbers = pc.cast(numeric_values, pa.float64()).to_numpy()
            water_profiles.append(
                {
                    "file": table["source_file"],
                    "device_type": device["type"],
                    "entity_id": device["_id"],
                    "rows": len(numbers),
                    "epoch_ms_min": int(clocks.min()),
                    "epoch_ms_max": int(clocks.max()),
                    **counter_quality(clocks, numbers),
                }
            )
    lab = next(
        t for t in spanish["tables"] if t["source_file"].endswith("lab-generated/labels.csv")
    )

    benchmark_path = ROOT / "research/experiments/results/aalborg-daily-v1.json"
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))

    def score(scope, hour, method):
        return next(
            m
            for m in benchmark["metrics"]
            if m["scope"] == scope
            and m["issuance_hour"] == hour
            and m["method"] == method
            and m["split"] == "test"
        )

    # Method keys are taken from the saved benchmark, never re-fit here.
    methods = {
        "seasonal_naive": "seasonal_naive",
        "history_only": "history_only",
        "intraday": "history_plus_intraday",
    }
    temporal = {key: score("temporal", 23, method) for key, method in methods.items()}
    holdout = {
        key: score("household_holdout", 23, method)
        for key, method in methods.items()
        if key != "seasonal_naive"
    }
    assessment = {
        "benchmark_run": benchmark["run_id"],
        "mae_temporal_23": temporal,
        "mae_holdout_23": holdout,
        "improvement_pct_vs_weekly_naive": 100
        * (1 - temporal["intraday"]["mae_liters"] / temporal["seasonal_naive"]["mae_liters"]),
        "incremental_improvement_pct_vs_history": 100
        * (1 - temporal["intraday"]["mae_liters"] / temporal["history_only"]["mae_liters"]),
        "holdout_intraday_error_increase_pct_vs_history": 100
        * (holdout["intraday"]["mae_liters"] / holdout["history_only"]["mae_liters"] - 1),
        "decision": "Retrospective baseline; external validation and raw-data semantics unresolved",
        "next_experiment_rule": (
            "New frozen external-cohort protocol. Do not tune against the consumed Aalborg test "
            "and then report it as untouched."
        ),
    }
    report = {
        "schema": "urban-water.public-library-audit.v1",
        "catalog_sha256": digest_file(CATALOG)["sha256"],
        "prepared_run": str(Path(latest["run"]).relative_to(ROOT)),
        "prepared_profiles_sha256": latest["profiles_sha256"],
        "verified_csv_tables": len(tables),
        "verified_derived_tables": len(derived),
        "csv_records": sum(t["rows"] for t in tables),
        "count_caveat": (
            "Includes metadata and duplicate result views. "
            "Not independent training observations or unique households."
        ),
        "source_rows": {
            p["source"]: sum(t["rows"] for t in p["tables"]) for p in by_source.values()
        },
        "hsb_boundary_check": boundary,
        "ciws_pulse_files": pulse_profiles,
        "ciws_manual_released_rows": manual["rows"],
        "ciws_manual_label_counts": manual["low_cardinality_values"]["Label"],
        "hihaigua_field_water_streams": water_profiles,
        "hihaigua_lab_label_counts": lab["low_cardinality_values"]["class"],
        "regional": by_source["istanbul-district-consumption"].get("regional"),
        "weather": by_source["nasa-power-kocaeli-context"]["weather"],
        "benchmark_assessment": assessment,
    }
    write_json(LIBRARY / "audit.json", report)
    # A tracked compact snapshot makes the research assessment reviewable without raw data in Git.
    compact = {k: v for k, v in report.items() if k not in ("regional", "weather")}
    for key in ("regional", "weather"):
        compact[key] = {
            k: v for k, v in report[key].items() if k not in ("parquet", "overlapping_keys")
        }
    write_json(ROOT / "research/reference_data/public-library-audit.v1.json", compact)
    print(
        json.dumps(
            {
                "verified_tables": len(tables),
                "csv_records": report["csv_records"],
                "ciws_manual_rows": manual["rows"],
                "benchmark": assessment,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
