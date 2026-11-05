"""Daily point-forecast benchmark with chronological label purging and household holdout."""

import importlib.metadata
import json
import math
import os
import platform
import shutil
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits
from urban_water.datasets import code_sha, file_sha
from urban_water.db import PROJECT_ROOT

from urban_water_research.daily_data import (
    HISTORY_FEATURES,
    INTRADAY_FEATURES,
    build_examples,
    load_config,
    load_hourly,
    select_training,
)

METHODS = ("seasonal_naive", "history_only", "history_plus_intraday")


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(
            json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, default=str) + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def scores(actual, predicted):
    if len(actual) != len(predicted) or not actual:
        raise ValueError("metrics require matching nonempty samples")
    if any(not math.isfinite(v) or v < 0 for v in [*actual, *predicted]):
        raise ValueError("metrics require finite nonnegative point forecasts/targets")
    errors = [abs(a - p) for a, p in zip(actual, predicted, strict=True)]
    denominator = sum(abs(a) for a in actual)
    return {
        "n": len(actual),
        "mae_liters": sum(errors) / len(errors),
        "rmse_liters": math.sqrt(sum(e * e for e in errors) / len(errors)),
        "wape": sum(errors) / denominator if denominator else None,
        "wape_status": "defined" if denominator else "zero_target_denominator",
        "interval_coverage": None,
        "interval_status": "point_forecasts_only",
    }


def _matrix(rows, names):
    result = np.asarray([[r.features[name] for name in names] for r in rows], dtype=np.float64)
    if not np.isfinite(result).all():
        raise ValueError("training/prediction feature matrix is not finite")
    return result


def _example_row(row):
    return {
        "example_key": row.key,
        "household": row.household,
        "target_day": row.target_day.isoformat(),
        "issuance_hour": row.issuance_hour,
        "split": row.split,
        "as_of": row.as_of.isoformat(),
        "target_start": row.target_start.isoformat(),
        "target_end": row.target_end.isoformat(),
        "label_available_at": row.label_available_at.isoformat(),
        "latest_feature_available_at": row.latest_feature_available_at.isoformat(),
        "target_liters": str(row.target_liters),
        "seasonal_naive_liters": str(row.seasonal_naive),
        **row.features,
    }


def metric_tables(predictions):
    pooled, by_household, by_fold = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in predictions:
        key = (row["scope"], row["issuance_hour"], row["split"], row["method"])
        pooled[key].append(row)
        by_household[(*key, row["household"])].append(row)
        by_fold[(*key, row["fold_id"])].append(row)

    def table(groups, extra=None):
        result = []
        for key, rows in sorted(groups.items()):
            scope, hour, split, method, *_ = key
            item = {
                "scope": scope,
                "issuance_hour": hour,
                "split": split,
                "method": method,
                **scores([r["actual_liters"] for r in rows], [r["predicted_liters"] for r in rows]),
            }
            if extra:
                item[extra] = key[-1]
            result.append(item)
        return result

    return {
        "pooled": table(pooled),
        "households": table(by_household, "household"),
        "folds": table(by_fold, "fold_id"),
    }


def paired_tables(predictions):
    grouped = defaultdict(lambda: defaultdict(dict))
    for row in predictions:
        key = (row["scope"], row["split"], row["method"])
        pair = (row["household"], row["target_day"])
        if pair in grouped[key][row["issuance_hour"]]:
            raise ValueError("duplicate paired forecast for a household/target day")
        grouped[key][row["issuance_hour"]][pair] = row
    result = []
    for (scope, split, method), by_hour in sorted(grouped.items()):
        a, b = by_hour[18], by_hour[23]
        common = sorted(set(a) & set(b))
        if not common:
            raise ValueError("no common target household-days for paired comparison")
        actual = [a[key]["actual_liters"] for key in common]
        if any(a[key]["actual_liters"] != b[key]["actual_liters"] for key in common):
            raise ValueError("paired issuance targets differ")
        early = scores(actual, [a[key]["predicted_liters"] for key in common])
        late = scores(actual, [b[key]["predicted_liters"] for key in common])
        result.append(
            {
                "scope": scope,
                "split": split,
                "method": method,
                "common_n": len(common),
                "unmatched_18": len(a) - len(common),
                "unmatched_23": len(b) - len(common),
                "mae_18_liters": early["mae_liters"],
                "mae_23_liters": late["mae_liters"],
                "mae_23_minus_18_liters": late["mae_liters"] - early["mae_liters"],
                "inference": "descriptive paired difference; no population significance claim",
            }
        )
    return result


def verify_run(directory: Path):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest.get("status") != "complete"
        or manifest.get("format") != "urban-water.daily-benchmark.v1"
    ):
        raise ValueError("experiment is not complete")
    required = {
        "protocol.toml",
        "input-dataset-manifest.json",
        "examples.parquet",
        "predictions.parquet",
        "metrics.json",
        "paired.json",
        "folds.json",
        "exclusions.json",
        "inventory.json",
        "report.md",
        "test-mae.png",
        "test-mae.svg",
    }
    if not required <= set(manifest["files"]):
        raise ValueError("experiment manifest omits required artifacts")
    for key, expected in manifest["files"].items():
        path = (directory / key).resolve()
        if not path.is_relative_to(directory.resolve()) or file_sha(path) != expected["sha256"]:
            raise ValueError("experiment artifact checksum/path mismatch")
        if path.stat().st_size != expected["bytes"]:
            raise ValueError("experiment artifact byte-size mismatch")
    return {"verified": True, "files": len(manifest["files"]), "run_id": manifest["run_id"]}


def _plot(directory, metrics):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 5), layout="constrained")
    labels = {
        "seasonal_naive": "Weekly naive",
        "history_only": "History",
        "history_plus_intraday": "History + intraday",
    }
    colors = ("#64748b", "#287a54", "#d58936")
    for ax, scope, title in zip(
        axes,
        ("temporal", "household_holdout"),
        ("Known households", "Unseen households (history available)"),
        strict=True,
    ):
        for i, (method, color) in enumerate(zip(METHODS, colors, strict=True)):
            values = [
                next(
                    r["mae_liters"]
                    for r in metrics
                    if r["scope"] == scope
                    and r["split"] == "test"
                    and r["issuance_hour"] == hour
                    and r["method"] == method
                )
                for hour in (18, 23)
            ]
            bars = ax.bar(
                np.arange(2) + (i - 1) * 0.24, values, width=0.24, color=color, label=labels[method]
            )
            ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=8)
        ax.set_xticks([0, 1], ["18:00", "23:00"])
        ax.set_xlabel("Issuance (Europe/Copenhagen)")
        ax.set_ylabel("Test MAE (litres / household-day)")
        ax.set_title(title, fontsize=11)
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_ylim(
            0,
            max(r["mae_liters"] for r in metrics if r["scope"] == scope and r["split"] == "test")
            * 1.18,
        )
    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="outside lower center", ncol=3, frameon=False)
    fig.suptitle(
        "Aalborg v1: retrospective daily forecasting of the published processed series", fontsize=11
    )
    fig.savefig(directory / "test-mae.png", dpi=170)
    fig.savefig(directory / "test-mae.svg")
    plt.close(fig)


def _report(directory, config, inventory, metrics, paired):
    lines = [
        "# Aalborg: ilk günlük tahmin deneyi",
        "",
        "Kapsam: yayımlanmış, ön işlemden geçmiş seri üzerinde geriye dönük karşılaştırma.",
        "Kaynak: [Schaffer ve diğerleri (2025), Aalborg v1]"
        "(https://doi.org/10.5281/zenodo.15180141), CC BY 4.0.",
        "Saatlik erişim varsayımsaldır; geçmiş sağlayıcı teslimatı doğrulanmış değildir.",
        "",
        f"Takvim: {config.timezone}. Tahmin saatleri: 18:00 ve 23:00; hedef ertesi yerel gün.",
        f"Eğitim başlangıcı {config.split.train_start}; doğrulama {config.split.validation_start}; "
        f"test {config.split.test_start}; bitiş hariç {config.split.end_exclusive}.",
        "Testte önceden belirlenmiş Kasım/Aralık başlangıçlarında genişleyen eğitim kullanılır. "
        "Aralık modeli yalnız o zamana kadar bitmiş Kasım hedeflerinden yararlanabilir.",
        "",
        "| Örneklem | Saat | Yöntem | n | MAE (L) | RMSE (L) | WAPE |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for row in metrics:
        if row["split"] == "test":
            wape = f"{row['wape'] * 100:.2f}%" if row["wape"] is not None else "tanımsız"
            lines.append(
                f"| {row['scope']} | {row['issuance_hour']}:00 | {row['method']} | "
                f"{row['n']} | {row['mae_liters']:.3f} | {row['rmse_liters']:.3f} | {wape} |"
            )
    lines += [
        "",
        f"Elde tutulan örnek sayısı: {inventory['retained_examples']}; "
        f"aday örnek: {inventory['candidate_examples']}.",
        "Dışlama nedenleri: "
        + json.dumps(inventory["excluded_by_reason"], ensure_ascii=False)
        + ".",
        "",
        "İki saatin karşılaştırması aynı hane/hedef günlerin kesişiminde ayrıca kaydedildi.",
        "Hane holdout modelleri ayrılan hanelerin hedefleriyle hiçbir dönemde eğitilmedi. "
        "Bu hanelerin tahmin anına kadar olan geçmiş tüketimi özellik olarak kullanılabilir.",
        "Hane kimliği, doluluk etiketi, anket ve hava bilgisi model özelliği değildir.",
        "Tahmin aralıkları üretilmedi; kapsama başarısı hesaplanmadı.",
        "",
        "Veri sınırlaması: upstream interpolasyon ve günlük minimum çıkarımı gelecekteki "
        "saatlerden etkilenebilir. Gerçek zamanlı ham tüketim/kaçak/saha başarısı çıkarılamaz.",
        "",
        "Dosyalar: `metrics.json`, `paired.json`, `folds.json`, `examples.parquet`, "
        "`predictions.parquet`, `exclusions.json`, `models/`, `manifest.json`.",
        "",
        "![Test MAE](test-mae.png)",
        "",
    ]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(hourly_path: Path, config_path: Path, output_root: Path):
    config = load_config(config_path)
    hours, input_manifest = load_hourly(hourly_path, config)
    examples, exclusions, inventory = build_examples(hours, config)
    directory = output_root.resolve() / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    run_id = directory.name
    shutil.copyfile(config_path, directory / "protocol.toml")
    shutil.copyfile(
        hourly_path.with_name("manifest.json"), directory / "input-dataset-manifest.json"
    )
    predictions, folds = [], []
    zone = ZoneInfo(config.timezone)
    periods = [("validation", config.split.validation_start, config.split.test_start)]
    dates = [*config.split.test_refit_dates, config.split.end_exclusive]
    periods += [("test", start, stop) for start, stop in zip(dates[:-1], dates[1:], strict=True)]
    heldout = set(config.split.holdout_households)
    with threadpool_limits(limits=1):
        for scope in ("temporal", "household_holdout"):
            for hour in config.issuance_hours:
                for split, start, stop in periods:
                    origin = (
                        datetime.combine(start - timedelta(days=1), datetime.min.time(), zone)
                        .replace(hour=hour)
                        .astimezone(UTC)
                    )
                    training, purged = select_training(
                        examples,
                        origin=origin,
                        issuance_hour=hour,
                        held_out=heldout if scope == "household_holdout" else set(),
                    )
                    evaluation = [
                        r
                        for r in examples
                        if r.issuance_hour == hour
                        and start <= r.target_day < stop
                        and (scope == "temporal" or r.household in heldout)
                    ]
                    if not evaluation:
                        raise ValueError("evaluation fold is empty")
                    if scope == "household_holdout" and any(
                        r.household in heldout for r in training
                    ):
                        raise ValueError("held-out household entered model fitting")
                    fold_id = f"{scope}-{hour}-{start.isoformat()}"
                    folds.append(
                        {
                            "fold_id": fold_id,
                            "scope": scope,
                            "issuance_hour": hour,
                            "split": split,
                            "origin": origin.isoformat(),
                            "target_start": start,
                            "target_end_exclusive": stop,
                            "training_n": len(training),
                            "evaluation_n": len(evaluation),
                            "purged_label_n": len(purged),
                            "latest_training_label_available_at": max(
                                r.label_available_at for r in training
                            ),
                            "training_households": sorted({r.household for r in training}),
                            "training_keys": [r.key for r in training],
                            "evaluation_keys": [r.key for r in evaluation],
                            "purged_keys": [r.key for r in purged],
                        }
                    )
                    targets = np.asarray([float(r.target_liters) for r in training])
                    if not np.isfinite(targets).all() or (targets < 0).any() or targets.sum() <= 0:
                        raise ValueError("insufficient nonnegative Poisson training signal")
                    for method in METHODS:
                        if method == "seasonal_naive":
                            values = [float(r.seasonal_naive) for r in evaluation]
                            model_key = None
                        else:
                            names = HISTORY_FEATURES + (
                                INTRADAY_FEATURES if method == "history_plus_intraday" else ()
                            )
                            model = HistGradientBoostingRegressor(
                                **config.model.model_dump(), categorical_features=None
                            )
                            model.fit(_matrix(training, names), targets)
                            values = model.predict(_matrix(evaluation, names)).tolist()
                            model_path = directory / "models" / f"{fold_id}-{method}.joblib"
                            model_path.parent.mkdir(parents=True, exist_ok=True)
                            joblib.dump(
                                {
                                    "model": model,
                                    "feature_names": names,
                                    "origin": origin.isoformat(),
                                },
                                model_path,
                                compress=3,
                            )
                            model_key = model_path.relative_to(directory).as_posix()
                        for row, value in zip(evaluation, values, strict=True):
                            if not math.isfinite(value) or value < 0:
                                raise ValueError("model returned an invalid point forecast")
                            predictions.append(
                                {
                                    "scope": scope,
                                    "issuance_hour": hour,
                                    "split": split,
                                    "fold_id": fold_id,
                                    "method": method,
                                    "household": row.household,
                                    "target_day": row.target_day.isoformat(),
                                    "as_of": row.as_of.isoformat(),
                                    "actual_liters": float(row.target_liters),
                                    "predicted_liters": value,
                                    "absolute_error_liters": abs(float(row.target_liters) - value),
                                    "model_key": model_key,
                                }
                            )
    tables, paired = metric_tables(predictions), paired_tables(predictions)
    pq.write_table(
        pa.Table.from_pylist([_example_row(r) for r in examples]),
        directory / "examples.parquet",
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_pylist(predictions), directory / "predictions.parquet", compression="zstd"
    )
    write_json(directory / "metrics.json", tables)
    write_json(directory / "paired.json", paired)
    write_json(directory / "folds.json", folds)
    write_json(directory / "exclusions.json", exclusions)
    write_json(directory / "inventory.json", inventory)
    _plot(directory, tables["pooled"])
    _report(directory, config, inventory, tables["pooled"], paired)
    files = {
        p.relative_to(directory).as_posix(): {"sha256": file_sha(p), "bytes": p.stat().st_size}
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }
    manifest = {
        "format": "urban-water.daily-benchmark.v1",
        "status": "complete",
        "run_id": run_id,
        "experiment_id": config.experiment_id,
        "created_at": datetime.now(UTC),
        "input": input_manifest,
        "protocol_sha256": file_sha(config_path),
        "code_sha256": code_sha(),
        "dependencies_sha256": file_sha(PROJECT_ROOT / "uv.lock"),
        "versions": {
            name: importlib.metadata.version(name)
            for name in (
                "scikit-learn",
                "numpy",
                "scipy",
                "pyarrow",
                "joblib",
                "threadpoolctl",
                "matplotlib",
            )
        },
        "python": platform.python_version(),
        "model_parameters": config.model.model_dump(),
        "feature_names": {
            "history_only": HISTORY_FEATURES,
            "history_plus_intraday": HISTORY_FEATURES + INTRADAY_FEATURES,
        },
        "scope": config.scope,
        "timezone": config.timezone,
        "availability_basis": config.availability_basis,
        "availability_rule": config.availability_rule,
        "availability_delay_minutes": config.availability_delay_minutes,
        "upstream_retrospective_preprocessing": True,
        "historical_online_performance_proven": False,
        "intervals_estimated": False,
        "thread_limit": 1,
        "files": files,
    }
    write_json(directory / "manifest.json", manifest)
    verify_run(directory)
    result = {
        "run_id": run_id,
        "directory": str(directory),
        "report": str(directory / "report.md"),
        "figure": str(directory / "test-mae.png"),
        "verified": True,
        "inventory": inventory,
        "test_metrics": [r for r in tables["pooled"] if r["split"] == "test"],
    }
    write_json(output_root.resolve() / "latest.json", result)
    return result
