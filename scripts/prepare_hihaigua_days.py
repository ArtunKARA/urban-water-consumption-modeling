"""Inspect UTC daily counter targets; never publish or train on unresolved units."""

import argparse
import json
import math
import re
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import urban_water_research.counter_days as algorithm
from urban_water.db import PROJECT_ROOT
from urban_water.sources import hihaigua_reference as reference
from urban_water_research.counter_days import CounterSample, calendar_day_bounds

POLICY = PROJECT_ROOT / "research/experiments/hihaigua-counter-days-v1.json"
OUTPUT = PROJECT_ROOT / "var/research/hihaigua/v2/counter-days"
DECIMALS = {
    "volume_lower_source",
    "volume_upper_source",
    "bounds_width_source",
    "point_target_source",
    "start_boundary_lower_source",
    "start_boundary_upper_source",
    "end_boundary_lower_source",
    "end_boundary_upper_source",
}


def read_counter_samples(path, stream_code):
    """Read exact decimals and absolute clocks without costly Arrow scalar conversion."""
    table = pq.read_table(
        path,
        filters=[("stream_code", "=", stream_code)],
        columns=["occurred_at", "counter_value", "event_id", "quality_flags"],
    )
    if not pa.types.is_timestamp(table["occurred_at"].type) or not table["occurred_at"].type.tz:
        raise ValueError("source counter timestamps require an explicit timezone")
    if not pa.types.is_decimal(table["counter_value"].type):
        raise ValueError("source counter values require an exact Arrow decimal type")
    clocks = pc.cast(
        pc.cast(table["occurred_at"], pa.timestamp("ms", tz="UTC")), pa.int64()
    ).to_pylist()
    values = pc.cast(table["counter_value"], pa.string()).to_pylist()
    identities = table["event_id"].to_pylist()
    issues = table["quality_flags"].to_pylist()
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return [
        CounterSample(
            epoch + timedelta(milliseconds=clock),
            Decimal(value) if value is not None else None,
            identity,
            "text_epoch_clock_mismatch" in flags,
        )
        for clock, value, identity, flags in zip(clocks, values, identities, issues, strict=True)
    ]


def save_rows(path, records):
    fields = []
    for key, value in records[0].items():
        kind = (
            pa.decimal256(76, 40)
            if key in DECIMALS
            else pa.timestamp("us", tz="UTC")
            if key
            in {
                "day_start_utc",
                "day_end_utc",
                "label_available_at_sample_clock",
                "archive_received_at",
            }
            else pa.date32()
            if key == "target_date"
            else pa.list_(pa.string())
            if key == "reasons"
            else pa.string()
            if key
            in {
                "source_unit",
                "start_left_event_id",
                "start_right_event_id",
                "end_left_event_id",
                "end_right_event_id",
            }
            else pa.bool_()
            if isinstance(value, bool)
            else pa.int64()
            if isinstance(value, int)
            else pa.float64()
            if isinstance(value, float) or key.endswith("_seconds")
            else pa.string()
        )
        fields.append((key, kind))
    pq.write_table(
        pa.Table.from_pylist(records, schema=pa.schema(fields)), path, compression="zstd"
    )


def plot_statuses(directory, summaries, policy):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    components = sorted(summaries, key=lambda c: (c != "water", c))
    figure = Figure(figsize=(11, 7), dpi=160)
    FigureCanvasAgg(figure)
    axes = figure.subplots(len(components), 1, squeeze=False)
    kinds = [
        ("exact_counter", "Tek günlük değer", "#267B8B"),
        ("bounded_counter", "Alt–üst sınır", "#E4A33E"),
        ("unresolved", "Çözülemeyen", "#A4ACB7"),
    ]
    for i, component in enumerate(components):
        axis = axes[i, 0]
        households = sorted(summaries[component]["households"])
        bottom = [0.0] * len(households)
        for key, label, color in kinds:
            shares = [
                100
                * summaries[component]["households"][h].get(key, 0)
                / sum(summaries[component]["households"][h].values())
                for h in households
            ]
            axis.bar(
                range(len(households)), shares, bottom=bottom, color=color, width=0.68, label=label
            )
            if key == "exact_counter":
                for x, share in enumerate(shares):
                    if share >= 20:
                        axis.text(
                            x,
                            share / 2,
                            f"{share:.1f}%",
                            ha="center",
                            va="center",
                            color="white",
                            fontsize=9,
                        )
            bottom = [a + b for a, b in zip(bottom, shares, strict=True)]
        axis.set_ylim(0, 100)
        axis.set_ylabel("Bileşen-gün oranı (%)")
        axis.set_xticks(range(len(households)), [h.replace("HIHAIGUA-", "") for h in households])
        axis.set_title(
            "Soğuk su sayaç ailesi" if component == "water" else "Sıcak su sayaç ailesi",
            loc="left",
            fontsize=11,
        )
        axis.spines[["top", "right"]].set_visible(False)
    figure.suptitle(
        f"Günlük hedefin endeksten belirlenebilirliği ({policy['timezone']})", fontsize=14, y=0.97
    )
    handles, labels = axes[0, 0].get_legend_handles_labels()
    figure.legend(
        handles, labels, ncol=3, loc="lower center", bbox_to_anchor=(0.5, 0.085), frameon=False
    )
    figure.text(
        0.5,
        0.025,
        "Kümülatif endeks sürekliliği varsayılır. Hacim birimi ve fiziksel doğruluk doğrulanmadı.\n"
        "Kaynak: hihAigua v2, doi:10.5281/zenodo.21704780. Model eğitimi yapılmadı.",
        ha="center",
        fontsize=8,
        color="#4B5563",
    )
    figure.subplots_adjust(left=0.08, right=0.98, bottom=0.18, top=0.88, hspace=0.42)
    paths = [
        directory / "target-status-by-household.png",
        directory / "target-status-by-household.svg",
    ]
    figure.savefig(paths[0])
    figure.savefig(paths[1], metadata={"Date": None})
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=POLICY)
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    if not isinstance(policy.get("experiment_id"), str) or not re.fullmatch(
        r"[a-z0-9-]{1,80}", policy["experiment_id"]
    ):
        raise ValueError("declare a safe counter-day experiment identifier")
    if policy["schema"] != "urban-water.counter-day-policy.v1" or any(
        policy[key] is not False
        for key in ("midpoint_target_allowed", "litre_conversion_allowed", "model_training_allowed")
    ):
        raise ValueError(
            "inspection policy must keep midpoint, unit conversion and training disabled"
        )
    pointer = PROJECT_ROOT / "var/research/hihaigua/v2/latest.json"
    latest = json.loads(pointer.read_text(encoding="utf-8"))
    directory = Path(latest["directory"])
    if reference.sha256(directory / "manifest.json") != latest["manifest_sha256"]:
        raise ValueError("source preparation manifest changed")
    source = reference.verify_prepared(directory, reference.sha256(reference.POLICY))
    if source is None:
        raise ValueError("run source preparation first")
    run = OUTPUT / uuid.uuid4().hex
    run.mkdir(parents=True)
    all_summaries, artifacts = {}, []
    for component, info in source["components"].items():
        records = []
        for stream in info["streams"]:
            samples = read_counter_samples(
                directory / component / "counters.parquet", stream["stream_code"]
            )
            days = calendar_day_bounds(
                samples,
                start_date=date.fromisoformat(policy["start_date"]),
                end_exclusive=date.fromisoformat(policy["end_exclusive"]),
                timezone=policy["timezone"],
                maximum_boundary_bracket_seconds=policy["maximum_boundary_bracket_seconds"],
                interior_gap_warning_seconds=policy["interior_gap_warning_seconds"],
            )
            for row in days:
                row.update(
                    household_code=stream["household_code"],
                    stream_code=stream["stream_code"],
                    component=component,
                    source_unit=info["unit_review"]["unit"],
                    archive_received_at=datetime.fromisoformat(source["archive_received_at"]),
                    training_allowed=False,
                )
            records.extend(days)
            print(f"inspected {stream['stream_code']}", flush=True)
        path = run / (component + "-daily.parquet")
        save_rows(path, records)
        statuses = Counter(r["status"] for r in records)
        reasons = Counter(reason for row in records for reason in row["reasons"])
        widths = [r["bounds_width_source"] for r in records if r["bounds_width_source"] is not None]
        delays = sorted(
            (r["label_available_at_sample_clock"] - r["day_end_utc"]).total_seconds()
            for r in records
            if r["label_available_at_sample_clock"] is not None
        )
        summary = {
            "days": len(records),
            "statuses": dict(statuses),
            "unresolved_reasons": dict(reasons),
            "days_with_internal_or_boundary_sampling_gaps": sum(
                r["sampling_gaps_in_used_span"] > 0 for r in records
            ),
            "exact_counter_days_with_sampling_gaps": sum(
                r["status"] == "exact_counter" and r["sampling_gaps_in_used_span"] > 0
                for r in records
            ),
            "maximum_bounds_width_source_units": str(max(widths, default=Decimal(0))),
            "source_unit": info["unit_review"]["unit"],
            "label_delay_on_sample_clock_seconds": {
                "n": len(delays),
                "minimum": min(delays) if delays else None,
                "median": median(delays) if delays else None,
                "p95_nearest_rank": delays[math.ceil(0.95 * len(delays)) - 1] if delays else None,
                "maximum": max(delays) if delays else None,
                "provider_arrival_delay_measured": False,
            },
            "households": {
                s["household_code"]: dict(
                    Counter(
                        r["status"] for r in records if r["household_code"] == s["household_code"]
                    )
                )
                for s in info["streams"]
            },
        }
        all_summaries[component] = summary
        artifacts.append(
            {"path": path.name, "sha256": reference.sha256(path), "rows": len(records)}
        )
        print(f"prepared {component}: {dict(statuses)}", flush=True)
    frozen_policy = run / "policy.json"
    reference.write_bytes(frozen_policy, args.policy.read_bytes(), immutable=True)
    artifacts.append({"path": frozen_policy.name, "sha256": reference.sha256(frozen_policy)})
    source_snapshot = run / "source-manifest.json"
    reference.write_bytes(
        source_snapshot, (directory / "manifest.json").read_bytes(), immutable=True
    )
    artifacts.append({"path": source_snapshot.name, "sha256": reference.sha256(source_snapshot)})
    for path in plot_statuses(run, all_summaries, policy):
        artifacts.append({"path": path.name, "sha256": reference.sha256(path)})
    manifest = {
        "schema": "urban-water.counter-day-result.v1",
        "review_date": "2026-11-05",
        "prepared_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": policy["experiment_id"],
        "timezone": policy["timezone"],
        "policy_sha256": reference.sha256(args.policy),
        "source_manifest_sha256": latest["manifest_sha256"],
        "source_archive_sha256": source["archive_sha256"],
        "source_prepared_directory": str(directory.relative_to(PROJECT_ROOT)),
        "algorithm_sha256": reference.sha256(Path(algorithm.__file__)),
        "runner_sha256": reference.sha256(Path(__file__)),
        "components": all_summaries,
        "artifacts": artifacts,
        "availability_basis": (
            "Required sample clock; archive receipt separate. "
            "Historic online availability not established."
        ),
        "exactness_basis": "Conditional recorded-counter identity; physical accuracy is unverified",
        "model_training_allowed": False,
        "litre_conversion_performed": False,
        "prepared_directory": str(run.relative_to(PROJECT_ROOT)),
    }
    reference.write_json(run / "manifest.json", manifest, immutable=True)
    algorithm.verify_counter_day_run(run)
    reference.write_json(
        OUTPUT / "latest.json",
        {"directory": str(run), "manifest_sha256": reference.sha256(run / "manifest.json")},
    )
    reference.write_json(
        PROJECT_ROOT / f"research/experiments/results/{policy['experiment_id']}.json", manifest
    )
    print(json.dumps(all_summaries, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
