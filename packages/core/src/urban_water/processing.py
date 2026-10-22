"""Observed volume summaries; no fabricated coverage or within-interval allocation."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from urban_water.db import connect
from urban_water.settings import Settings


def _bucket(instant: datetime, grain: str, zone: ZoneInfo) -> tuple[datetime, datetime]:
    if grain == "hour":
        start = instant.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        return start, start + timedelta(hours=1)
    local = instant.astimezone(zone)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def summarize_connection(
    conn,
    grain: str,
    timezone: str,
    study_code: str = "synthetic-pilot",
    cohort_code: str = "fixture",
) -> dict:
    if grain not in {"hour", "day"}:
        raise ValueError("grain must be hour or day")
    zone = ZoneInfo(timezone)
    cohort = conn.execute(
        "SELECT c.id FROM cohort c JOIN study s ON s.id=c.study_id WHERE s.code=%s AND c.code=%s",
        (study_code, cohort_code),
    ).fetchone()
    if cohort is None:
        raise ValueError("unknown study/cohort scope")
    members = defaultdict(list)
    for row in conn.execute("SELECT * FROM membership WHERE cohort_id=%s", (cohort["id"],)):
        members[row["household_id"]].append(row)
    registry = defaultdict(list)
    for row in conn.execute("""SELECT s.id AS stream_id, i.*, h.code AS household_code
        FROM stream s JOIN installation i ON i.meter_id=s.meter_id
        JOIN household h ON h.id=i.household_id WHERE i.retired_at IS NULL"""):
        registry[row["stream_id"]].append(row)
    totals = defaultdict(lambda: Decimal(0))
    counts = defaultdict(int)
    diagnostics = defaultdict(int)
    unallocated = Decimal(0)
    previous = {}
    input_observations = 0
    with conn.cursor(name="volume_summary") as cursor:
        cursor.itersize = 2000
        cursor.execute("SELECT * FROM current_measurement ORDER BY stream_id,occurred_at,event_id")
        for row in cursor:
            input_observations += 1
            end = row["occurred_at"]
            start = row["interval_start"]
            volume = row["liters"]
            if row["kind"] == "index":
                prior = previous.get(row["stream_id"])
                previous[row["stream_id"]] = row
                if prior is None:
                    diagnostics["index_without_previous"] += 1
                    continue
                start = prior["occurred_at"]
                volume -= prior["liters"]
                if volume < 0:
                    diagnostics["negative_index_delta"] += 1
                    continue
            instant = start if start is not None else end
            candidates = [
                i
                for i in registry[row["stream_id"]]
                if i["valid_from"] <= instant
                and (
                    i["valid_to"] is None
                    or (i["valid_to"] >= end if start is not None else i["valid_to"] > end)
                )
            ]
            if len(candidates) != 1:
                diagnostics["unresolved_installation"] += 1
                unallocated += volume
                continue
            bucket_start, bucket_end = _bucket(instant, grain, zone)
            eligible = any(
                member["valid_from"] <= instant
                and (
                    member["valid_to"] is None
                    or (
                        member["valid_to"] >= end if start is not None else member["valid_to"] > end
                    )
                )
                for member in members[candidates[0]["household_id"]]
            )
            if not eligible:
                diagnostics["outside_cohort"] += 1
                unallocated += volume
                continue
            if start is not None and end > bucket_end:
                diagnostics["cross_bucket_interval"] += 1
                unallocated += volume
                continue
            key = (candidates[0]["household_code"], bucket_start, bucket_end)
            totals[key] += volume
            counts[key] += 1
            if len(totals) > 1_000_000:
                raise ValueError("summary bucket budget exceeded; narrow the extraction scope")
    return {
        "grain": grain,
        "timezone": timezone,
        "study_code": study_code,
        "cohort_code": cohort_code,
        "coverage_status": "unknown",
        "data_status": "observed_partial" if totals else "insufficient_data",
        "population": {
            "registered_households": len(members),
            "represented_households": len({key[0] for key in totals}),
            "input_observations": input_observations,
        },
        "rows": [
            {
                "household_code": key[0],
                "start": key[1].isoformat(),
                "end": key[2].isoformat(),
                "observed_liters": str(total),
                "observations": counts[key],
                "coverage_status": "unknown",
            }
            for key, total in sorted(totals.items())
        ],
        "diagnostics": dict(diagnostics),
        "unallocated_liters": str(unallocated),
    }


def summarize(settings: Settings, grain: str = "day") -> dict:
    with connect(settings) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        return summarize_connection(
            conn,
            grain,
            settings.timezone,
            settings.study_code,
            settings.cohort_code,
        )
