"""Source-independent daily examples from frozen canonical hourly volumes.

Availability is an explicit retrospective assumption, not historical provider evidence.
"""

import json
import math
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, model_validator
from urban_water.datasets import file_sha
from urban_water.processing import _day_start

HISTORY_FEATURES = (
    "last_complete_day_liters",
    "weekly_lag_7_liters",
    "weekly_lag_14_liters",
    "last7_mean_liters",
    "last7_std_liters",
    "last28_mean_liters",
    "last28_zero_fraction",
    "target_day_of_week",
    "target_month",
    "target_year_sin",
    "target_year_cos",
    "target_day_hours",
)
INTRADAY_FEATURES = (
    "intraday_observed_liters",
    "intraday_available_hours",
    "intraday_elapsed_hours",
    "intraday_available_fraction",
)


class SplitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    train_start: date
    validation_start: date
    test_start: date
    end_exclusive: date
    test_refit_dates: tuple[date, ...]
    holdout_households: tuple[str, ...]

    @model_validator(mode="after")
    def validate_periods(self):
        if not self.train_start < self.validation_start < self.test_start < self.end_exclusive:
            raise ValueError("split dates must be strictly chronological")
        dates = self.test_refit_dates
        if not dates or dates[0] != self.test_start or tuple(sorted(set(dates))) != dates:
            raise ValueError("test refit dates must be unique, ordered and begin at test_start")
        if dates[-1] >= self.end_exclusive:
            raise ValueError("test refit date is outside evaluation period")
        if not self.holdout_households or len(set(self.holdout_households)) != len(
            self.holdout_households
        ):
            raise ValueError("declare distinct held-out households")
        return self


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    loss: Literal["poisson"]
    max_iter: int = Field(ge=1, le=1000)
    learning_rate: float = Field(gt=0, le=1, allow_inf_nan=False)
    max_leaf_nodes: int = Field(ge=2, le=255)
    min_samples_leaf: int = Field(ge=1)
    early_stopping: Literal[False]
    random_state: int


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    experiment_id: str = Field(pattern=r"^[a-z0-9-]+$")
    model_flow: Literal["F01"]
    scope: str
    timezone: str
    issuance_hours: tuple[int, ...]
    lookback_days: Literal[28]
    availability_delay_minutes: int = Field(ge=0, le=1440)
    availability_rule: Literal["interval_end plus assumed delay strictly before issuance"]
    availability_basis: str
    value_semantics: Literal["upstream_processed_volume"]
    seed: int
    split: SplitConfig
    model: ModelConfig

    @model_validator(mode="after")
    def validate_clocks(self):
        ZoneInfo(self.timezone)
        if self.issuance_hours != (18, 23):
            raise ValueError("v1 evaluates the predeclared paired 18:00 and 23:00 experiments")
        return self


def load_config(path: Path) -> ExperimentConfig:
    with path.open("rb") as incoming:
        return ExperimentConfig.model_validate(tomllib.load(incoming))


@dataclass(frozen=True)
class Hour:
    household: str
    start: datetime
    end: datetime
    liters: Decimal


@dataclass(frozen=True)
class Day:
    start: datetime
    end: datetime
    hours: tuple[Hour, ...]
    complete: bool

    @property
    def liters(self):
        return sum((r.liters for r in self.hours), Decimal(0))


@dataclass(frozen=True)
class Example:
    household: str
    target_day: date
    issuance_hour: int
    as_of: datetime
    target_start: datetime
    target_end: datetime
    label_available_at: datetime
    latest_feature_available_at: datetime
    target_liters: Decimal
    seasonal_naive: Decimal
    features: dict[str, float]
    split: str

    @property
    def key(self):
        return f"{self.household}/{self.target_day.isoformat()}/{self.issuance_hour}"


def load_hourly(path: Path, config: ExperimentConfig) -> tuple[list[Hour], dict]:
    path = path.resolve()
    manifest_path = path.with_name("manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("format") != "urban-water.dataset.v2"
        or manifest.get("grain") != "hour"
        or manifest.get("timezone") != config.timezone
        or manifest.get("environment") != "public_research"
        or manifest.get("value_semantics") != [config.value_semantics]
        or manifest.get("receipt_basis") != ["research_archive_import"]
    ):
        raise ValueError("input snapshot does not match the scoped reference protocol")
    if file_sha(path) != manifest["data_sha256"]:
        raise ValueError("input snapshot checksum mismatch")
    table = pq.read_table(path)
    if table.num_rows != manifest["row_count"]:
        raise ValueError("input snapshot row count mismatch")
    result = []
    for row in table.to_pylist():
        result.append(
            Hour(
                row["household_code"],
                datetime.fromisoformat(row["start"]),
                datetime.fromisoformat(row["end"]),
                Decimal(row["observed_liters"]),
            )
        )
    return result, {
        "dataset_id": manifest["dataset_id"],
        "path": str(path),
        "manifest_path": str(manifest_path),
        "data_sha256": file_sha(path),
        "manifest_sha256": file_sha(manifest_path),
        "row_count": table.num_rows,
        "source_ids": [s["id"] for s in manifest["sources"]],
        "coverage_status": manifest["coverage_status"],
        "value_semantics": manifest["value_semantics"],
    }


def day_grid(day: date, zone: ZoneInfo) -> tuple[datetime, ...]:
    start, end = _day_start(day, zone), _day_start(day + timedelta(days=1), zone)
    count = int((end - start).total_seconds() / 3600)
    return tuple(start + timedelta(hours=i) for i in range(count))


def group_days(hours: list[Hour], timezone: str) -> dict[tuple[str, date], Day]:
    zone = ZoneInfo(timezone)
    grouped = defaultdict(dict)
    for row in hours:
        if (
            row.start.tzinfo is None
            or row.end.tzinfo is None
            or row.end - row.start != timedelta(hours=1)
            or not row.liters.is_finite()
            or row.liters < 0
        ):
            raise ValueError("invalid canonical hourly interval")
        start = row.start.astimezone(UTC)
        day = start.astimezone(zone).date()
        key = (row.household, day)
        if start in grouped[key]:
            raise ValueError("duplicate canonical household/hour")
        if start not in day_grid(day, zone):
            raise ValueError("hour does not align with the declared calendar")
        grouped[key][start] = row
    result = {}
    for (household, day), rows in grouped.items():
        grid = day_grid(day, zone)
        result[(household, day)] = Day(
            grid[0],
            grid[-1] + timedelta(hours=1),
            tuple(rows[k] for k in sorted(rows)),
            set(rows) == set(grid),
        )
    return result


def split_of(day: date, split: SplitConfig) -> str | None:
    if not split.train_start <= day < split.end_exclusive:
        return None
    if day < split.validation_start:
        return "train"
    return "validation" if day < split.test_start else "test"


def build_examples(
    hours: list[Hour], config: ExperimentConfig
) -> tuple[list[Example], list[dict], dict]:
    days = group_days(hours, config.timezone)
    zone = ZoneInfo(config.timezone)
    households = sorted({h.household for h in hours})
    if not set(config.split.holdout_households) < set(households):
        raise ValueError("holdout must be a proper subset of input households")
    delay = timedelta(minutes=config.availability_delay_minutes)
    examples, excluded = [], []
    target_day = config.split.train_start
    while target_day < config.split.end_exclusive:
        for household in households:
            for issuance in config.issuance_hours:
                issue_day = target_day - timedelta(days=1)
                as_of = (
                    datetime.combine(issue_day, datetime.min.time(), zone)
                    .replace(hour=issuance)
                    .astimezone(UTC)
                )
                reason = None
                target = days.get((household, target_day))
                if target is None or not target.complete:
                    reason = "target_grid_incomplete"
                history = [
                    days.get((household, issue_day - timedelta(days=i)))
                    for i in range(1, config.lookback_days + 1)
                ]
                if reason is None and any(d is None or not d.complete for d in history):
                    reason = "history_grid_incomplete"
                if reason is None and any(d.end + delay >= as_of for d in history):
                    reason = "history_not_available_before_issuance"
                available_grid = [
                    s for s in day_grid(issue_day, zone) if s + timedelta(hours=1) + delay < as_of
                ]
                observed = days.get((household, issue_day))
                by_start = {h.start.astimezone(UTC): h for h in observed.hours} if observed else {}
                if reason is None and (
                    not available_grid or not set(available_grid) <= set(by_start)
                ):
                    reason = "intraday_grid_incomplete"
                if reason:
                    excluded.append(
                        {
                            "household": household,
                            "target_day": target_day.isoformat(),
                            "issuance_hour": issuance,
                            "reason": reason,
                        }
                    )
                    continue
                values = [float(d.liters) for d in history]
                recent = values[:7]
                mean7 = sum(recent) / 7
                available = [by_start[s] for s in available_grid]
                elapsed = (as_of - _day_start(issue_day, zone)).total_seconds() / 3600
                week7 = days[(household, target_day - timedelta(days=7))].liters
                week14 = days[(household, target_day - timedelta(days=14))].liters
                features = {
                    "last_complete_day_liters": values[0],
                    "weekly_lag_7_liters": float(week7),
                    "weekly_lag_14_liters": float(week14),
                    "last7_mean_liters": mean7,
                    "last7_std_liters": math.sqrt(sum((v - mean7) ** 2 for v in recent) / 7),
                    "last28_mean_liters": sum(values) / 28,
                    "last28_zero_fraction": sum(v == 0 for v in values) / 28,
                    "target_day_of_week": float(target_day.weekday()),
                    "target_month": float(target_day.month),
                    "target_year_sin": math.sin(2 * math.pi * target_day.timetuple().tm_yday / 366),
                    "target_year_cos": math.cos(2 * math.pi * target_day.timetuple().tm_yday / 366),
                    "target_day_hours": (target.end - target.start).total_seconds() / 3600,
                    "intraday_observed_liters": float(
                        sum((h.liters for h in available), Decimal(0))
                    ),
                    "intraday_available_hours": float(len(available)),
                    "intraday_elapsed_hours": elapsed,
                    "intraday_available_fraction": len(available) / elapsed,
                }
                latest = max([d.end + delay for d in history] + [h.end + delay for h in available])
                if latest >= as_of or not all(math.isfinite(v) for v in features.values()):
                    raise ValueError("feature boundary or finite-value invariant failed")
                examples.append(
                    Example(
                        household,
                        target_day,
                        issuance,
                        as_of,
                        target.start,
                        target.end,
                        target.end + delay,
                        latest,
                        target.liters,
                        week7,
                        features,
                        split_of(target_day, config.split),
                    )
                )
        target_day += timedelta(days=1)
    # Each issuance retains its own eligible sample; paired comparisons use the intersection.
    keys = {
        hour: {(r.household, r.target_day) for r in examples if r.issuance_hour == hour}
        for hour in config.issuance_hours
    }
    inventory = {
        "input_hours": len(hours),
        "households": households,
        "retained_examples": len(examples),
        "candidate_examples": len(examples) + len(excluded),
        "retained_by_split_and_hour": dict(
            Counter(f"{r.split}/{r.issuance_hour}" for r in examples)
        ),
        "excluded_by_reason": dict(Counter(r["reason"] for r in excluded)),
        "complete_household_days": sum(d.complete for d in days.values()),
        "partial_household_days": sum(not d.complete for d in days.values()),
        "paired_common_target_days": len(keys[18] & keys[23]),
        "paired_unmatched_by_hour": {
            str(hour): len(keys[hour] - keys[18 if hour == 23 else 23])
            for hour in config.issuance_hours
        },
    }
    return examples, excluded, inventory


def select_training(
    examples: list[Example], *, origin: datetime, issuance_hour: int, held_out: set[str]
) -> tuple[list[Example], list[Example]]:
    candidates = [
        r
        for r in examples
        if r.issuance_hour == issuance_hour and r.household not in held_out and r.as_of < origin
    ]
    training = [r for r in candidates if r.label_available_at < origin]
    purged = [r for r in candidates if r.label_available_at >= origin]
    if not training:
        raise ValueError("insufficient training history before forecast origin")
    return training, purged
