"""Counter identities, uncertainty and label timing, independently of litre units."""

import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

module = pytest.importorskip("urban_water_research.counter_days")
CounterSample = module.CounterSample
calendar_day_bounds = module.calendar_day_bounds
pytestmark = pytest.mark.research
START = datetime(2025, 1, 1, tzinfo=UTC)


def sample(minutes, value, **options):
    return CounterSample(
        START + timedelta(minutes=minutes),
        Decimal(str(value)) if value is not None else None,
        str(minutes),
        **options,
    )


def one(samples, **options):
    return calendar_day_bounds(
        samples, start_date=date(2025, 1, 1), end_exclusive=date(2025, 1, 2), **options
    )[0]


def exact_samples():
    return [sample(-2, 10), sample(2, 10), sample(1438, 15), sample(1442, 15)]


def test_flat_brackets_identify_day_without_interpolation():
    row = one(exact_samples())
    assert row["status"] == "exact_counter"
    assert (
        row["point_target_source"] == row["volume_lower_source"] == row["volume_upper_source"] == 5
    )
    assert row["label_available_at_sample_clock"] == START + timedelta(minutes=1442)
    assert row["start_bracket_seconds"] == row["end_bracket_seconds"] == 240


def test_uncertain_boundaries_produce_bounds_and_no_midpoint():
    row = one([sample(-2, 10), sample(2, 12), sample(1438, 20), sample(1442, 23)])
    assert row["status"] == "bounded_counter"
    assert row["volume_lower_source"] == 8
    assert row["volume_upper_source"] == 13
    assert row["bounds_width_source"] == 5
    assert row["point_target_source"] is None


def test_true_zero_is_an_exact_target_and_not_missing():
    row = one([sample(-2, 10), sample(2, 10), sample(1438, 10), sample(1442, 10)])
    assert row["status"] == "exact_counter" and row["point_target_source"] == Decimal(0)


def test_missing_edge_days_are_not_extrapolated():
    row = one([sample(2, 10), sample(1438, 15)])
    assert row["status"] == "unresolved" and row["point_target_source"] is None
    assert set(row["reasons"]) == {"missing_left_boundary", "missing_right_boundary"}
    assert row["label_available_at_sample_clock"] is None


def test_long_boundary_gap_is_rejected_even_when_values_are_equal():
    row = one([sample(-20, 10), sample(2, 10), sample(1438, 15), sample(1442, 15)])
    assert row["status"] == "unresolved"
    assert "boundary_gap_exceeds_policy" in row["reasons"]


def test_sampling_gap_does_not_erase_recoverable_cumulative_volume():
    row = one(exact_samples())
    assert row["sampling_gaps_in_used_span"] == 1
    assert row["point_target_source"] == 5  # conditional on continuous cumulative counter


def test_recorded_reset_inside_day_blocks_target():
    row = one(
        [
            sample(-2, 10),
            sample(2, 10),
            sample(600, 12),
            sample(700, 2),
            sample(1438, 4),
            sample(1442, 4),
        ]
    )
    assert row["status"] == "unresolved" and row["point_target_source"] is None
    assert row["counter_decreases_in_used_span"] == 1


def test_missing_interior_value_is_not_filled_or_bridged_for_point_label():
    row = one(
        [sample(-2, 10), sample(2, 10), sample(700, None), sample(1438, 15), sample(1442, 15)]
    )
    assert row["status"] == "unresolved" and row["volume_lower_source"] is None
    assert row["missing_counters_in_used_span"] == 1


def test_clock_conflict_inside_used_span_blocks_target():
    rows = exact_samples()
    rows.insert(2, sample(700, 12, clock_conflict=True))
    row = one(rows)
    assert "clock_conflict_in_evidence_span" in row["reasons"]
    assert row["point_target_source"] is None


def test_future_values_after_label_evidence_cannot_change_earlier_target():
    a = one(exact_samples() + [sample(1600, 30)])
    b = one(exact_samples() + [sample(1600, 9999)])
    assert a == b


def test_exact_end_reading_requires_no_later_sample():
    row = one([sample(0, 10), sample(1440, 15)])
    assert row["point_target_source"] == 5
    assert row["label_available_at_sample_clock"] == START + timedelta(days=1)


@pytest.mark.parametrize("day,hours", [(date(2025, 3, 30), 23), (date(2025, 10, 26), 25)])
def test_local_calendar_days_follow_dst_elapsed_time(day, hours):
    zone = ZoneInfo("Europe/Madrid")
    a = datetime.combine(day, datetime.min.time(), zone)
    b = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone)
    row = calendar_day_bounds(
        [CounterSample(a, Decimal(10), "a"), CounterSample(b, Decimal(15), "b")],
        start_date=day,
        end_exclusive=day + timedelta(days=1),
        timezone="Europe/Madrid",
    )[0]
    assert row["day_elapsed_seconds"] == hours * 3600
    assert row["point_target_source"] == 5


def test_repeated_wall_hour_is_two_distinct_absolute_instants():
    zone = ZoneInfo("Europe/Madrid")
    clocks = [
        datetime(2025, 10, 26, 0, tzinfo=zone),
        datetime(2025, 10, 26, 2, 30, tzinfo=zone, fold=0),
        datetime(2025, 10, 26, 2, 30, tzinfo=zone, fold=1),
        datetime(2025, 10, 27, 0, tzinfo=zone),
    ]
    values = [CounterSample(t, Decimal(i), str(i)) for i, t in enumerate(clocks)]
    row = calendar_day_bounds(
        values,
        start_date=date(2025, 10, 26),
        end_exclusive=date(2025, 10, 27),
        timezone="Europe/Madrid",
    )[0]
    assert row["point_target_source"] == 3
    assert row["sampling_gaps_in_used_span"] == 3


def test_skipped_civil_date_is_not_a_zero_consumption_day():
    row = calendar_day_bounds(
        [], start_date=date(2011, 12, 30), end_exclusive=date(2011, 12, 31), timezone="Pacific/Apia"
    )[0]
    assert row["day_elapsed_seconds"] == 0
    assert row["status"] == "unresolved" and row["point_target_source"] is None


@pytest.mark.parametrize("values", [[sample(2, 1), sample(1, 2)], [sample(1, 1), sample(1, 2)]])
def test_out_of_order_or_duplicate_instants_require_review(values):
    with pytest.raises(ValueError, match="strictly increasing"):
        one(values)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal(-1), 1.0])
def test_bad_numeric_values_cannot_silently_enter_bounds(value):
    with pytest.raises(ValueError, match="Decimals or missing"):
        one([CounterSample(START, value, "a")])


def test_unsupported_precision_is_not_silently_rounded():
    with pytest.raises(ValueError, match="source decimal precision"):
        one([sample(0, "1e-41")])


def test_duplicate_event_identity_is_rejected():
    with pytest.raises(ValueError, match="source event IDs"):
        one(
            [
                CounterSample(START, Decimal(1), "same"),
                CounterSample(START + timedelta(minutes=7), Decimal(2), "same"),
            ]
        )


def test_naive_clock_requires_explicit_interpretation():
    with pytest.raises(ValueError, match="offset-aware"):
        one([CounterSample(datetime(2025, 1, 1), Decimal(1), "a")])


@pytest.mark.parametrize("limit", [0, -1, True, float("nan"), float("inf")])
def test_invalid_gap_limits_cannot_bypass_boundary_policy(limit):
    with pytest.raises(ValueError, match="positive integer seconds"):
        one(exact_samples(), maximum_boundary_bracket_seconds=limit)


@pytest.fixture
def preparation_script():
    path = Path(__file__).resolve().parents[1] / "scripts/prepare_hihaigua_days.py"
    spec = importlib.util.spec_from_file_location("counter_day_preparation", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def write_samples(path, *, clock_type=None, value_type=None):
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
            ("stream_code", pa.string()),
            ("occurred_at", clock_type or pa.timestamp("ms", tz="UTC")),
            ("counter_value", value_type or pa.decimal256(76, 40)),
            ("event_id", pa.string()),
            ("quality_flags", pa.list_(pa.string())),
        ]
    )
    values = (
        [Decimal("32.001999999999995"), None, Decimal(0)]
        if value_type is None
        else [1.0, None, 0.0]
    )
    rows = [
        {
            "stream_code": "s",
            "occurred_at": START + timedelta(minutes=i * 7),
            "counter_value": v,
            "event_id": str(i),
            "quality_flags": [],
        }
        for i, v in enumerate(values)
    ]
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def test_fast_arrow_reader_preserves_full_precision_missing_and_zero(tmp_path, preparation_script):
    path = tmp_path / "counters.parquet"
    write_samples(path)
    rows = preparation_script.read_counter_samples(path, "s")
    assert [r.value for r in rows] == [Decimal("32.001999999999995"), None, Decimal(0)]
    assert [r.time for r in rows] == [START + timedelta(minutes=i * 7) for i in range(3)]


def test_fast_reader_rejects_naive_timestamps(tmp_path, preparation_script):
    import pyarrow as pa

    path = tmp_path / "counters.parquet"
    write_samples(path, clock_type=pa.timestamp("ms"))
    with pytest.raises(ValueError, match="explicit timezone"):
        preparation_script.read_counter_samples(path, "s")


def test_fast_reader_rejects_implicit_float_precision(tmp_path, preparation_script):
    import pyarrow as pa

    path = tmp_path / "counters.parquet"
    write_samples(path, value_type=pa.float64())
    with pytest.raises(ValueError, match="exact Arrow decimal"):
        preparation_script.read_counter_samples(path, "s")


@pytest.fixture
def frozen_run(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from urban_water.datasets import file_sha

    pq.write_table(pa.table({"training_allowed": [False, False]}), tmp_path / "water-daily.parquet")
    policy = {
        "schema": "urban-water.counter-day-policy.v1",
        "model_training_allowed": False,
        "litre_conversion_allowed": False,
        "midpoint_target_allowed": False,
    }
    (tmp_path / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    (tmp_path / "source-manifest.json").write_text(
        json.dumps({"archive_sha256": "0" * 64}), encoding="utf-8"
    )
    artifacts = [
        {"path": name, "sha256": file_sha(tmp_path / name)}
        for name in ["water-daily.parquet", "policy.json", "source-manifest.json"]
    ]
    artifacts[0]["rows"] = 2
    manifest = {
        "schema": "urban-water.counter-day-result.v1",
        "model_training_allowed": False,
        "litre_conversion_performed": False,
        "artifacts": artifacts,
        "components": {"water": {"days": 2}},
        "policy_sha256": file_sha(tmp_path / "policy.json"),
        "source_manifest_sha256": file_sha(tmp_path / "source-manifest.json"),
        "source_archive_sha256": "0" * 64,
        "algorithm_sha256": file_sha(Path(module.__file__)),
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path, manifest


def test_frozen_target_inventory_and_hashes_verify(frozen_run):
    path, _ = frozen_run
    assert module.verify_counter_day_run(path) == {
        "verified": True,
        "artifacts": 3,
        "rows": 2,
        "algorithm_matches_current": True,
        "model_training_allowed": False,
    }


def test_tampered_daily_table_is_rejected(frozen_run):
    path, _ = frozen_run
    (path / "water-daily.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        module.verify_counter_day_run(path)


def test_artifact_cannot_escape_run_directory(frozen_run):
    path, manifest = frozen_run
    manifest["artifacts"][0]["path"] = "../outside.parquet"
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes its run"):
        module.verify_counter_day_run(path)


def test_manifest_cannot_silently_enable_training(frozen_run):
    path, manifest = frozen_run
    manifest["model_training_allowed"] = True
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="unapproved"):
        module.verify_counter_day_run(path)
