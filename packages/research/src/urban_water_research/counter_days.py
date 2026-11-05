"""Calendar-day counter bounds, without interpolation or fabricated volume units.

Exactness is conditional on a continuously cumulative, nondecreasing counter.
It concerns recorded values, not calibration accuracy or historical data delivery.
"""

from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from itertools import pairwise
from zoneinfo import ZoneInfo

from urban_water.processing import _day_start


@dataclass(frozen=True)
class CounterSample:
    time: datetime
    value: Decimal | None
    event_id: str
    clock_conflict: bool = False


@dataclass(frozen=True)
class Boundary:
    lower: Decimal | None
    upper: Decimal | None
    left: int | None
    right: int | None
    span_seconds: float | None
    reason: str | None = None


def _boundary(samples, clocks, instant, maximum_seconds):
    right = bisect_left(clocks, instant)
    if right < len(clocks) and clocks[right] == instant:
        value = samples[right].value
        reason = (
            "boundary_clock_conflict"
            if samples[right].clock_conflict
            else ("boundary_counter_missing" if value is None else None)
        )
        return Boundary(value, value, right, right, 0.0, reason)
    if right == 0:
        return Boundary(None, None, None, right if samples else None, None, "missing_left_boundary")
    if right == len(clocks):
        return Boundary(
            None, None, right - 1 if samples else None, None, None, "missing_right_boundary"
        )
    left = right - 1
    a, b = samples[left], samples[right]
    span = (clocks[right] - clocks[left]).total_seconds()
    reason = None
    if a.clock_conflict or b.clock_conflict:
        reason = "boundary_clock_conflict"
    elif a.value is None or b.value is None:
        reason = "boundary_counter_missing"
    elif b.value < a.value:
        reason = "boundary_counter_decrease"
    elif span > maximum_seconds:
        reason = "boundary_gap_exceeds_policy"
    return Boundary(a.value, b.value, left, right, span, reason)


def calendar_day_bounds(
    samples: list[CounterSample],
    *,
    start_date: date,
    end_exclusive: date,
    timezone: str = "UTC",
    maximum_boundary_bracket_seconds: int = 630,
    interior_gap_warning_seconds: int = 630,
) -> list[dict]:
    """Return one row per requested civil date, including unresolved dates.

    Samples must be strictly ordered, unique instants from one physical counter.
    No values are rounded, sorted, filled, extrapolated or assigned a midpoint.
    A future end-bracketing sample is label evidence, never an earlier feature.
    """
    if start_date >= end_exclusive or (end_exclusive - start_date).days > 3660:
        raise ValueError("declare a positive, bounded date window")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in (maximum_boundary_bracket_seconds, interior_gap_warning_seconds)
    ):
        raise ValueError("bracket/gap limits must be positive integer seconds")
    zone = ZoneInfo(timezone)
    clocks = []
    identities = set()
    for sample in samples:
        if sample.time.tzinfo is None or sample.time.utcoffset() is None:
            raise ValueError("counter sample timestamps must be offset-aware")
        instant = sample.time.astimezone(UTC)
        if clocks and instant <= clocks[-1]:
            raise ValueError("counter samples must have strictly increasing unique timestamps")
        if sample.value is not None and (
            not isinstance(sample.value, Decimal)
            or not sample.value.is_finite()
            or sample.value < 0
        ):
            raise ValueError("counter values must be finite nonnegative Decimals or missing")
        if sample.value is not None and (
            sample.value.adjusted() >= 30 or sample.value.as_tuple().exponent < -40
        ):
            raise ValueError("counter exceeds supported source decimal precision")
        if (
            not isinstance(sample.event_id, str)
            or not sample.event_id
            or sample.event_id in identities
        ):
            raise ValueError("counter samples require distinct nonempty source event IDs")
        identities.add(sample.event_id)
        clocks.append(instant)
    records = []
    day = start_date
    while day < end_exclusive:
        start = _day_start(day, zone)
        end = _day_start(day + timedelta(days=1), zone)
        a = _boundary(samples, clocks, start, maximum_boundary_bracket_seconds)
        b = _boundary(samples, clocks, end, maximum_boundary_bracket_seconds)
        reasons = {bound.reason for bound in (a, b) if bound.reason}
        if start >= end:
            reasons.add("nonpositive_civil_day")
        lo, hi = a.left, b.right
        missing = decreases = gaps = conflicts = 0
        if lo is not None and hi is not None:
            used = samples[lo : hi + 1]
            missing = sum(s.value is None for s in used)
            conflicts = sum(s.clock_conflict for s in used)
            for offset, (previous, current) in enumerate(pairwise(used)):
                gaps += (
                    clocks[lo + offset + 1] - clocks[lo + offset]
                ).total_seconds() > interior_gap_warning_seconds
                if previous.value is not None and current.value is not None:
                    decreases += current.value < previous.value
            if missing:
                reasons.add("unusable_counter_in_evidence_span")
            if decreases:
                reasons.add("counter_decrease_in_evidence_span")
            if conflicts:
                reasons.add("clock_conflict_in_evidence_span")
        lower = upper = width = point = None
        status = "unresolved"
        if not reasons:
            with localcontext() as context:
                context.prec = 90
                lower = max(Decimal(0), b.lower - a.upper)
                upper = b.upper - a.lower
                if upper < lower:
                    reasons.add("inconsistent_counter_bounds")
                    lower = upper = None
                else:
                    width = upper - lower
                    point = lower if width == 0 else None
                    status = "exact_counter" if point is not None else "bounded_counter"
        records.append(
            {
                "target_date": day,
                "day_start_utc": start,
                "day_end_utc": end,
                "day_elapsed_seconds": (end - start).total_seconds(),
                "timezone": timezone,
                "status": status,
                "reasons": sorted(reasons),
                "volume_lower_source": lower,
                "volume_upper_source": upper,
                "bounds_width_source": width,
                "point_target_source": point,
                "start_boundary_lower_source": a.lower,
                "start_boundary_upper_source": a.upper,
                "end_boundary_lower_source": b.lower,
                "end_boundary_upper_source": b.upper,
                "start_bracket_seconds": a.span_seconds,
                "end_bracket_seconds": b.span_seconds,
                "missing_counters_in_used_span": missing,
                "counter_decreases_in_used_span": decreases,
                "sampling_gaps_in_used_span": gaps,
                "clock_conflicts_in_used_span": conflicts,
                "label_available_at_sample_clock": clocks[b.right]
                if b.right is not None and status != "unresolved"
                else None,
                "start_left_event_id": samples[a.left].event_id if a.left is not None else None,
                "start_right_event_id": samples[a.right].event_id if a.right is not None else None,
                "end_left_event_id": samples[b.left].event_id if b.left is not None else None,
                "end_right_event_id": samples[b.right].event_id if b.right is not None else None,
            }
        )
        day += timedelta(days=1)
    return records


def verify_counter_day_run(directory):
    """Verify a frozen inspection run, without enabling model training."""
    import json
    from pathlib import Path

    import pyarrow.parquet as pq
    from urban_water.datasets import file_sha

    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest.get("schema") != "urban-water.counter-day-result.v1"
        or manifest.get("model_training_allowed") is not False
        or manifest.get("litre_conversion_performed") is not False
    ):
        raise ValueError("unsupported or unapproved counter-day inspection manifest")
    artifacts = manifest["artifacts"]
    names = [item["path"] for item in artifacts]
    if len(names) != len(set(names)) or not {"policy.json", "source-manifest.json"} <= set(names):
        raise ValueError("counter-day run requires distinct artifacts and source/policy snapshots")
    parquet_rows = 0
    for item in artifacts:
        path = (directory / item["path"]).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("counter-day artifact escapes its run directory")
        if file_sha(path) != item["sha256"]:
            raise ValueError("counter-day artifact checksum mismatch")
        if path.suffix == ".parquet":
            rows = pq.read_metadata(path).num_rows
            if rows != item["rows"]:
                raise ValueError("counter-day artifact row count mismatch")
            parquet_rows += rows
            flags = pq.read_table(path, columns=["training_allowed"])[
                "training_allowed"
            ].to_pylist()
            if any(value is not False for value in flags):
                raise ValueError("counter-day table contains an unapproved training flag")
    if file_sha(directory / "policy.json") != manifest["policy_sha256"]:
        raise ValueError("counter-day policy snapshot differs from declared policy")
    if file_sha(directory / "source-manifest.json") != manifest["source_manifest_sha256"]:
        raise ValueError("counter-day source snapshot differs from declared source")
    policy = json.loads((directory / "policy.json").read_text(encoding="utf-8"))
    if policy.get("schema") != "urban-water.counter-day-policy.v1" or any(
        policy.get(key) is not False
        for key in ("model_training_allowed", "litre_conversion_allowed", "midpoint_target_allowed")
    ):
        raise ValueError("counter-day policy snapshot is not approved for inspection")
    source = json.loads((directory / "source-manifest.json").read_text(encoding="utf-8"))
    if source["archive_sha256"] != manifest["source_archive_sha256"]:
        raise ValueError("source archive identity differs between snapshots")
    if parquet_rows != sum(c["days"] for c in manifest["components"].values()):
        raise ValueError("counter-day inventory differs from table rows")
    return {
        "verified": True,
        "artifacts": len(artifacts),
        "rows": parquet_rows,
        "algorithm_matches_current": file_sha(Path(__file__)) == manifest["algorithm_sha256"],
        "model_training_allowed": False,
    }
