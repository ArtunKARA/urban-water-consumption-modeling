"""Explicit synthetic registry. A meter, installation, household and cohort are distinct."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from urban_water.db import connect
from urban_water.settings import Settings


def stable_id(code: str):
    return uuid5(NAMESPACE_URL, f"urban-water:synthetic:{code}")


def seed_synthetic(settings: Settings, households: int = 2, kind: str = "pulse") -> None:
    if not 1 <= households <= 10000 or kind not in {"pulse", "interval", "index"}:
        raise ValueError("invalid synthetic registry size or measurement kind")
    since = datetime(2000, 1, 1, tzinfo=UTC)
    with connect(settings) as conn:
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
