"""Aalborg/Zenodo 15180141 daily-labelled, hourly-volume export (fixed v1 mapping).

The original R scripts use index differences: each timestamp closes the previous hour.
Daily occupancy labels use noon-to-noon days and never become measurement features here.
"""

import csv
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from urban_water_contracts import ResearchMeasurement

from urban_water.sources.normalizers import NormalizationError, NormalizedRow, _lines

HEADER = (
    "building",
    "date_time",
    "date",
    "water_use_l",
    "occ_01",
    "occ_02",
    "occ_03",
    "occ_04",
    "occ_05",
)
BUILDINGS = {f"BUILD-D-{i:02}": f"AALBORG-D-{i:02}" for i in range(1, 11)}
ZONE = ZoneInfo("Europe/Copenhagen")


class AalborgDailyCsv:
    def __init__(self, archive_received_at: str):
        self.received_at = datetime.fromisoformat(archive_received_at)

    def rows(self, incoming, max_line_bytes, max_records):
        lines = _lines(incoming, max_line_bytes)
        try:
            header = next(csv.reader([next(lines).decode("utf-8-sig")], strict=True))
        except NormalizationError:
            raise
        except (StopIteration, ValueError, csv.Error):
            raise NormalizationError("invalid Aalborg daily CSV header") from None
        if tuple(header) != HEADER:
            raise NormalizationError("unsupported Aalborg export columns/order")
        for number, line in enumerate(lines, 1):
            if number > max_records:
                raise NormalizationError("source record limit exceeded")
            try:
                cells = next(csv.reader([line.decode("utf-8")], strict=True))
                if len(cells) != len(HEADER):
                    raise ValueError("invalid Aalborg column count")
                raw = dict(zip(HEADER, cells, strict=True))
                alias = BUILDINGS.get(raw["building"])
                if alias is None:
                    raise ValueError("unknown exported building")
                end = datetime.fromisoformat(raw["date_time"])
                if end.tzinfo is None or end.utcoffset() is None:
                    raise ValueError("source timestamp must have an offset")
                end = end.astimezone(UTC)
                if (end.minute, end.second, end.microsecond) != (0, 0, 0):
                    raise ValueError("Aalborg timestamp must close a whole UTC hour")
                local = end.astimezone(ZONE)
                label_day = local.date() - timedelta(days=int(local.hour < 12))
                if raw["date"] != label_day.isoformat():
                    raise ValueError("unexpected upstream noon-to-noon date label")
                volume = Decimal(raw["water_use_l"])
                if not volume.is_finite() or volume < 0 or volume != volume.to_integral_value():
                    raise ValueError("expected nonnegative integer litres from published export")
                record = ResearchMeasurement(
                    stream_code=f"{alias}-hourly",
                    event_id=f"{alias}:{end:%Y%m%dT%H%M%SZ}",
                    kind="interval",
                    interval_start=end - timedelta(hours=1),
                    occurred_at=end,
                    received_at=self.received_at,
                    value=volume,
                    unit="L",
                )
            except (ValueError, csv.Error, ArithmeticError):
                yield NormalizedRow(number, None, "invalid_contract")
            else:
                yield NormalizedRow(number, record)
