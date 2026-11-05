"""Counterexamples for forecast availability, calendar splits and fit isolation."""

import json
import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("urban_water_research", reason="install the research extra")
pytest.importorskip("sklearn", reason="install the research extra")

import joblib
import pyarrow as pa
import pyarrow.parquet as pq
from urban_water.datasets import file_sha
from urban_water.db import PROJECT_ROOT
from urban_water_research.daily_benchmark import paired_tables, run_benchmark, scores, verify_run
from urban_water_research.daily_data import (
    HISTORY_FEATURES,
    INTRADAY_FEATURES,
    ExperimentConfig,
    Hour,
    build_examples,
    day_grid,
    group_days,
    load_config,
    load_hourly,
    select_training,
)

pytestmark = pytest.mark.research


@pytest.fixture
def config():
    raw = load_config(PROJECT_ROOT / "research/experiments/aalborg-daily-v1.toml").model_dump()
    raw["split"].update(
        train_start=date(2022, 1, 1),
        validation_start=date(2022, 3, 1),
        test_start=date(2022, 4, 1),
        end_exclusive=date(2022, 5, 1),
        test_refit_dates=(date(2022, 4, 1), date(2022, 4, 16)),
        holdout_households=("C",),
    )
    raw["model"].update(max_iter=5, min_samples_leaf=5)
    return ExperimentConfig.model_validate(raw)


@pytest.fixture
def hours():
    zone = ZoneInfo("Europe/Copenhagen")
    result = []
    day = date(2022, 1, 1)
    while day < date(2022, 5, 1):
        for household, base in (("A", 1), ("B", 2), ("C", 3)):
            for start in day_grid(day, zone):
                value = Decimal(base + start.astimezone(zone).hour % 4 + day.weekday())
                result.append(Hour(household, start, start + timedelta(hours=1), value))
        day += timedelta(days=1)
    return result


def selected(examples, household="A", target=date(2022, 3, 10), hour=18):
    return next(
        r
        for r in examples
        if r.household == household and r.target_day == target and r.issuance_hour == hour
    )


def test_later_intraday_values_cannot_change_earlier_features(hours, config):
    examples, _, _ = build_examples(hours, config)
    early, late = selected(examples), selected(examples, hour=23)
    changed = [
        replace(h, liters=h.liters + 100)
        if h.household == "A" and early.as_of <= h.end < late.as_of
        else h
        for h in hours
    ]
    modified, _, _ = build_examples(changed, config)
    assert selected(modified).features == early.features
    assert selected(modified, hour=23).features["intraday_observed_liters"] == (
        late.features["intraday_observed_liters"] + 500
    )
    assert early.features["intraday_available_hours"] == 17
    assert late.features["intraday_available_hours"] == 22
    assert early.latest_feature_available_at < early.as_of


def test_future_target_is_not_a_feature(hours, config):
    original, _, _ = build_examples(hours, config)
    sample = selected(original)
    changed = [
        replace(h, liters=h.liters + 1000)
        if h.household == "A" and sample.target_start <= h.start < sample.target_end
        else h
        for h in hours
    ]
    modified, _, _ = build_examples(changed, config)
    assert selected(modified).features == sample.features
    assert selected(modified).seasonal_naive == sample.seasonal_naive
    assert selected(modified).target_liters != sample.target_liters
    assert set(sample.features) == set(HISTORY_FEATURES + INTRADAY_FEATURES)
    assert not set(sample.features) & {"household", "target_liters", "occ_01", "label_available_at"}


def test_missing_target_hour_is_excluded_instead_of_imputed(hours, config):
    zone = ZoneInfo(config.timezone)
    missing = datetime(2022, 3, 10, 2, tzinfo=zone).astimezone(UTC)
    changed = [h for h in hours if not (h.household == "A" and h.start == missing)]
    examples, excluded, _ = build_examples(changed, config)
    assert not any(r.household == "A" and r.target_day == date(2022, 3, 10) for r in examples)
    assert {
        (r["issuance_hour"], r["reason"])
        for r in excluded
        if r["household"] == "A" and r["target_day"] == "2022-03-10"
    } == {(18, "target_grid_incomplete"), (23, "target_grid_incomplete")}


def test_unmatched_issuance_eligibility_is_counted(hours, config):
    end = datetime(2022, 3, 9, 21, tzinfo=ZoneInfo(config.timezone)).astimezone(UTC)
    changed = [h for h in hours if not (h.household == "A" and h.end == end)]
    examples, excluded, inventory = build_examples(changed, config)
    assert selected(examples).issuance_hour == 18
    assert not any(
        r.household == "A" and r.target_day == date(2022, 3, 10) and r.issuance_hour == 23
        for r in examples
    )
    assert inventory["paired_unmatched_by_hour"]["18"] >= 1
    assert any(r["reason"] == "intraday_grid_incomplete" for r in excluded)


def test_zero_volume_target_and_zero_wape_denominator(hours, config):
    sample = selected(build_examples(hours, config)[0])
    changed = [
        replace(h, liters=Decimal(0))
        if h.household == "A" and sample.target_start <= h.start < sample.target_end
        else h
        for h in hours
    ]
    assert selected(build_examples(changed, config)[0]).target_liters == 0
    result = scores([0.0, 0.0], [0.0, 3.0])
    assert result["mae_liters"] == 1.5 and result["wape"] is None
    assert result["wape_status"] == "zero_target_denominator"
    assert scores([0.0, 10.0], [2.0, 8.0])["wape"] == 0.4


def test_dst_days_have_23_and_25_distinct_hourly_intervals():
    zone = ZoneInfo("Europe/Copenhagen")
    rows = []
    for day, count in ((date(2022, 3, 27), 23), (date(2022, 10, 30), 25)):
        grid = day_grid(day, zone)
        assert len(grid) == count and len(set(grid)) == count
        rows += [Hour("A", start, start + timedelta(hours=1), Decimal(1)) for start in grid]
    grouped = group_days(rows, str(zone))
    assert grouped[("A", date(2022, 3, 27))].complete
    assert grouped[("A", date(2022, 10, 30))].liters == Decimal(25)


def test_duplicates_and_nonfinite_inputs_fail_closed(hours):
    with pytest.raises(ValueError, match="duplicate"):
        group_days([hours[0], hours[0]], "Europe/Copenhagen")
    with pytest.raises(ValueError, match="invalid"):
        group_days([replace(hours[0], liters=Decimal("NaN"))], "Europe/Copenhagen")


def test_training_purges_unfinished_labels_and_holdout_targets(hours, config):
    examples = build_examples(hours, config)[0]
    origin = selected(examples, target=date(2022, 3, 1)).as_of
    training, purged = select_training(examples, origin=origin, issuance_hour=18, held_out={"C"})
    assert training and purged
    assert all(r.household != "C" and r.label_available_at < origin for r in training)
    assert {r.target_day for r in purged} == {date(2022, 2, 28)}
    assert all(r.target_day < date(2022, 3, 1) for r in training)


@pytest.mark.parametrize(
    "change",
    [
        {"issuance_hours": [23, 18]},
        {"availability_delay_minutes": -1},
        {"lookback_days": 7},
    ],
)
def test_protocol_rejects_unapproved_clock_or_history_changes(config, change):
    raw = config.model_dump()
    raw.update(change)
    with pytest.raises(ValueError):
        ExperimentConfig.model_validate(raw)


def test_random_internal_validation_is_disabled(config):
    raw = config.model_dump()
    raw["model"]["early_stopping"] = True
    with pytest.raises(ValueError):
        ExperimentConfig.model_validate(raw)


def test_paired_comparison_uses_common_targets_and_rejects_duplicates():
    rows = [
        {
            "scope": "temporal",
            "split": "test",
            "method": "m",
            "issuance_hour": hour,
            "household": household,
            "target_day": "2022-11-01",
            "actual_liters": 10.0,
            "predicted_liters": predicted,
        }
        for hour, household, predicted in [(18, "A", 7.0), (23, "A", 9.0), (18, "B", 0.0)]
    ]
    paired = paired_tables(rows)[0]
    assert paired["common_n"] == 1 and paired["unmatched_18"] == 1
    assert paired["mae_23_minus_18_liters"] == -2
    with pytest.raises(ValueError, match="duplicate"):
        paired_tables([*rows, rows[0]])


@pytest.mark.parametrize("actual,predicted", [([], []), ([1], []), ([1], [-1]), ([1], [math.inf])])
def test_invalid_metrics_fail_closed(actual, predicted):
    with pytest.raises(ValueError):
        scores(actual, predicted)


def snapshot(path, hours, config):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "household_code": h.household,
            "start": h.start.isoformat(),
            "end": h.end.isoformat(),
            "observed_liters": str(h.liters),
            "observations": 1,
            "coverage_status": "unknown",
        }
        for h in hours
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)
    manifest = {
        "format": "urban-water.dataset.v2",
        "dataset_id": "test-only-snapshot",
        "grain": "hour",
        "timezone": config.timezone,
        "environment": "public_research",
        "value_semantics": ["upstream_processed_volume"],
        "receipt_basis": ["research_archive_import"],
        "data_sha256": file_sha(path),
        "row_count": len(rows),
        "sources": [{"id": "test-source"}],
        "coverage_status": "unknown",
    }
    path.with_name("manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_input_snapshot_tampering_is_rejected(tmp_path, hours, config):
    path = tmp_path / "snapshot/observed_volume.parquet"
    snapshot(path, hours[:100], config)
    with path.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        load_hourly(path, config)


def test_complete_experiment_and_repeatability(tmp_path, hours, config):
    data = tmp_path / "snapshot/observed_volume.parquet"
    snapshot(data, hours, config)
    text = (PROJECT_ROOT / "research/experiments/aalborg-daily-v1.toml").read_text(encoding="utf-8")
    text = text.replace('"2022-09-01"', '"2022-03-01"').replace('"2022-11-01"', '"2022-04-01"')
    text = text.replace('"2023-01-01"', '"2022-05-01"').replace('"2022-12-01"', '"2022-04-16"')
    text = text.replace('["AALBORG-D-09", "AALBORG-D-10"]', '["C"]')
    text = text.replace("max_iter = 100", "max_iter = 5").replace(
        "min_samples_leaf = 20", "min_samples_leaf = 5"
    )
    protocol = tmp_path / "protocol.toml"
    protocol.write_text(text, encoding="utf-8")
    first = run_benchmark(data, protocol, tmp_path / "runs")
    second = run_benchmark(data, protocol, tmp_path / "runs")
    assert first["test_metrics"] == second["test_metrics"]
    directory = Path(first["directory"])
    assert verify_run(directory)["verified"]
    saved = joblib.load(next((directory / "models").glob("*.joblib")))
    assert saved["model"].do_early_stopping_ is False
    assert saved["model"].n_iter_ == 5
    folds = json.loads((directory / "folds.json").read_text(encoding="utf-8"))
    for fold in folds:
        assert datetime.fromisoformat(
            fold["latest_training_label_available_at"]
        ) < datetime.fromisoformat(fold["origin"])
        assert not set(fold["training_keys"]) & set(fold["evaluation_keys"])
        if fold["scope"] == "household_holdout":
            assert "C" not in fold["training_households"]
    predictions = pq.read_table(directory / "predictions.parquet").to_pylist()
    keys = [
        (r["scope"], r["method"], r["issuance_hour"], r["household"], r["target_day"])
        for r in predictions
    ]
    assert len(keys) == len(set(keys))
    with (directory / "predictions.parquet").open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError, match="checksum"):
        verify_run(directory)
