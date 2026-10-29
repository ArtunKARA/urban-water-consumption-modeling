"""Explicit synthetic registry. A meter, installation, household and cohort are distinct."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from uuid import NAMESPACE_URL, uuid5

from urban_water_contracts import require_aware

from urban_water.db import connect
from urban_water.settings import Settings


def stable_id(code: str):
    return uuid5(NAMESPACE_URL, f"urban-water:synthetic:{code}")


@dataclass(frozen=True)
class ConfigurationPeriod:
    valid_from: datetime
    valid_to: datetime | None = None
    liters_per_pulse: Decimal | None = None


def configure_stream(
    settings: Settings, stream_code: str, periods: list[ConfigurationPeriod]
) -> list[str]:
    """Append an atomic configuration revision. Existing/pinned payloads never change.

    This affects new jobs only. Correcting an already published volume requires an explicit
    source event revision, rather than silently rewriting its normalization.
    """
    if not periods:
        raise ValueError("at least one configuration period is required")
    for period in periods:
        require_aware(period.valid_from)
        if period.valid_to is not None:
            require_aware(period.valid_to)
            if period.valid_to <= period.valid_from:
                raise ValueError("configuration period must be positive")
    with connect(settings) as conn:
        stream = conn.execute(
            "SELECT * FROM stream WHERE code=%s FOR UPDATE", (stream_code,)
        ).fetchone()
        if stream is None:
            raise ValueError("unknown stream")
        for period in periods:
            scale = period.liters_per_pulse
            if stream["kind"] == "pulse":
                if (
                    not isinstance(scale, Decimal)
                    or not scale.is_finite()
                    or not 0 < scale < 10**15
                ):
                    raise ValueError("pulse scale must be a positive finite Decimal")
                with localcontext() as context:
                    context.prec = 60
                    if scale != scale.quantize(Decimal("0.000000001")):
                        raise ValueError("pulse scale exceeds stored precision")
            elif scale is not None:
                raise ValueError("non-pulse configurations do not have a pulse scale")
        version = conn.execute(
            "SELECT max(version) AS v FROM stream_configuration WHERE stream_id=%s", (stream["id"],)
        ).fetchone()["v"]
        conn.execute(
            "UPDATE stream_configuration SET retired_at=clock_timestamp() "
            "WHERE stream_id=%s AND retired_at IS NULL",
            (stream["id"],),
        )
        ids = []
        for number, period in enumerate(periods, version + 1):
            row = conn.execute(
                """INSERT INTO stream_configuration
                (stream_id,version,kind,liters_per_pulse,valid_from,valid_to)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    stream["id"],
                    number,
                    stream["kind"],
                    period.liters_per_pulse,
                    period.valid_from,
                    period.valid_to,
                ),
            ).fetchone()
            ids.append(str(row["id"]))
        conn.execute(
            "INSERT INTO audit_event(action,target_id) VALUES ('stream.configure',%s)",
            (stream["id"],),
        )
        return ids


def seed_synthetic(settings: Settings, households: int = 2, kind: str = "pulse") -> None:
    if settings.environment != "synthetic":
        raise ValueError("synthetic registry requires a synthetic environment")
    if not 1 <= households <= 10000 or kind not in {"pulse", "interval", "index"}:
        raise ValueError("invalid synthetic registry size or measurement kind")
    since = datetime(2000, 1, 1, tzinfo=UTC)
    with connect(settings) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(83621002)")
        if conn.execute("SELECT 1 FROM study WHERE environment<>'synthetic' LIMIT 1").fetchone():
            raise ValueError("synthetic registry cannot be added to a public research database")
        sid, cid = stable_id("study"), stable_id("cohort")
        conn.execute(
            "INSERT INTO study(id,code) VALUES (%s,'synthetic-pilot') ON CONFLICT DO NOTHING",
            (sid,),
        )
        conn.execute(
            "INSERT INTO cohort(id,study_id,code) VALUES (%s,%s,'fixture') ON CONFLICT DO NOTHING",
            (cid, sid),
        )
        for i in range(1, households + 1):
            household, meter = stable_id(f"household-{i}"), stable_id(f"meter-{i}-{kind}")
            stream = stable_id(f"stream-{i}-{kind}")
            conn.execute(
                "INSERT INTO household(id,code) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (household, f"H{i:04d}"),
            )
            conn.execute(
                "INSERT INTO membership VALUES (%s,%s,%s,NULL) ON CONFLICT DO NOTHING",
                (cid, household, since),
            )
            conn.execute(
                "INSERT INTO meter VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (meter, f"M{i:04d}-{kind}"),
            )
            conn.execute(
                "INSERT INTO stream(id,meter_id,code,kind,liters_per_pulse) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    stream,
                    meter,
                    f"S{i:04d}-{kind}",
                    kind,
                    Decimal("0.1") if kind == "pulse" else None,
                ),
            )
            conn.execute(
                "INSERT INTO installation(id,meter_id,household_id,valid_from) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (stable_id(f"installation-{i}-{kind}"), meter, household, since),
            )
