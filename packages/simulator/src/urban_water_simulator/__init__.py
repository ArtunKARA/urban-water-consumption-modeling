"""Small correctness fixtures. Not a validated household behavior simulator."""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from urban_water_contracts import Measurement, require_aware


def generate(directory: Path, households: int = 2, days: int = 2, seed: int = 42) -> dict:
    if not 1 <= households <= 10000 or not 1 <= days <= 366:
        raise ValueError("invalid fixture size")
    # Explicit budget prevents accidental enormous fixtures; larger workloads are separate profiles.
    if households * days * 24 * (10 + households + 23) > 2_000_000:
        raise ValueError("fixture exceeds two million pulse budget")
    directory.mkdir(parents=True, exist_ok=False)
    rng = random.Random(seed)
    start = require_aware(datetime.fromisoformat("2026-01-01T00:00:00+03:00"))
    path = directory / "measurements.jsonl"
    totals = {}
    count = 0
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for day in range(days):
            for household in range(1, households + 1):
                totals[f"H{household:04d}/{(start + timedelta(days=day)).date()}"] = (
                    f"{(24 * (10 + household) + 276) / 10:.1f}"
                )
                for hour in range(24):
                    pulses = 10 + household + hour
                    offsets = sorted(rng.sample(range(3600), pulses))
                    for number, offset in enumerate(offsets):
                        instant = start + timedelta(days=day, hours=hour, seconds=offset)
                        record = Measurement(
                            stream_code=f"S{household:04d}-pulse",
                            event_id=f"h{household}-d{day}-h{hour}-p{number}",
                            kind="pulse",
                            occurred_at=instant,
                            received_at=instant + timedelta(seconds=5),
                            value="1",
                            unit="pulse",
                        )
                        stream.write(record.model_dump_json() + "\n")
                        count += 1
    truth = {
        "profile": "correctness-v1",
        "seed": seed,
        "households": households,
        "days": days,
        "pulse_count": count,
        "expected_daily_liters": totals,
    }
    # Ground truth is a separate file. It is never ingested by the core.
    (directory / "truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return {"source": str(path), "truth": truth}
