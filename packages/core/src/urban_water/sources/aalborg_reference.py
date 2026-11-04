"""Reproducible, checksum-pinned reference acquisition and preparation.

Downloads data and reads upstream scripts as evidence. Never executes downloaded code.
"""

import csv
import hashlib
import io
import json
import os
import urllib.request
import zipfile
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from urban_water.db import PROJECT_ROOT
from urban_water.sources.aalborg import BUILDINGS, HEADER
from urban_water.sources.config import SourceSpec

CATALOG = PROJECT_ROOT / "research/reference_data/aalborg-v1.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bytes(path: Path, data: bytes, *, immutable=False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and immutable:
        if path.read_bytes() != data:
            raise ValueError("cached reference content differs; use a new reference directory")
        return
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def write_json(path: Path, value: dict, *, immutable=False) -> None:
    write_bytes(
        path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode(), immutable=immutable
    )


def _fetch(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "urban-water-research/0.1"})
    with urllib.request.urlopen(request, timeout=25) as response:
        if response.status != 200:
            raise ValueError("reference download did not return a complete object")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("reference download budget exceeded")
    return data


class _RangeArchive(io.RawIOBase):
    """Bounded ZIP-directory/member reads, avoiding the full 745 MB archive."""

    def __init__(self, url: str, size: int):
        self.url, self.size, self.position, self.bytes_read = url, size, 0, 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = (
            offset
            if whence == 0
            else (self.position + offset if whence == 1 else self.size + offset)
        )
        if not 0 <= position <= self.size:
            raise ValueError("archive seek outside object")
        self.position = position
        return position

    def read(self, size=-1):
        size = self.size - self.position if size < 0 else min(size, self.size - self.position)
        if size == 0:
            return b""
        if size > 1024 * 1024 or self.bytes_read + size > 4 * 1024 * 1024:
            raise ValueError("selective archive range budget exceeded")
        end = self.position + size - 1
        request = urllib.request.Request(
            self.url, headers={"Range": f"bytes={self.position}-{end}"}
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            expected = f"bytes {self.position}-{end}/{self.size}"
            if response.status != 206 or response.headers.get("Content-Range") != expected:
                raise ValueError("server did not honor the bounded archive range")
            data = response.read(size + 1)
        if len(data) != size:
            raise ValueError("incomplete archive range")
        self.position += size
        self.bytes_read += size
        return data


def fetch_reference(root: Path, *, offline=False) -> dict:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    receipt_path = raw / "download-receipt.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    else:
        if offline:
            raise ValueError("offline mode requires previously verified reference downloads")
        metadata = _fetch(catalog["metadata_url"], 128 * 1024)
        record = json.loads(metadata)
        if (
            str(record.get("id")) != catalog["record_id"]
            or record["metadata"].get("license", {}).get("id") != "cc-by-4.0"
            or record["metadata"].get("access_right") != "open"
        ):
            raise ValueError("reference identity, license or access metadata changed")
        data = _fetch(catalog["zip_url"], catalog["zip_bytes"])
        if (
            len(data) != catalog["zip_bytes"]
            or digest(data) != catalog["zip_sha256"]
            or hashlib.md5(data).hexdigest() != catalog["zip_md5"]
        ):
            raise ValueError("published reference ZIP checksum mismatch")
        write_bytes(raw / "record.json", metadata, immutable=True)
        write_bytes(raw / "final_data.zip", data, immutable=True)
        receipt = {
            "record_id": catalog["record_id"],
            "zip_sha256": digest(data),
            "archive_received_at": datetime.now(UTC).isoformat(),
            "receipt_basis": "research_archive_import",
            "historical_provider_receipt": "not_published",
        }
        write_json(receipt_path, receipt, immutable=True)
    data = (raw / "final_data.zip").read_bytes()
    if (
        digest(data) != catalog["zip_sha256"]
        or len(data) != catalog["zip_bytes"]
        or receipt.get("zip_sha256") != catalog["zip_sha256"]
        or receipt.get("record_id") != catalog["record_id"]
    ):
        raise ValueError("cached reference ZIP/receipt checksum mismatch")
    instant = datetime.fromisoformat(receipt["archive_received_at"])
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("archive receipt requires a real offset-aware timestamp")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        member = archive.getinfo(catalog["selected_member"])
        if member.file_size != catalog["csv_bytes"]:
            raise ValueError("unexpected reference CSV size")
        with archive.open(member) as stream:
            csv_data = stream.read(catalog["csv_bytes"] + 1)
        if digest(csv_data) != catalog["csv_sha256"]:
            raise ValueError("reference CSV checksum mismatch")
        write_bytes(raw / catalog["selected_member"], csv_data, immutable=True)

    missing = []
    for script in catalog["upstream_scripts"]:
        target = raw / script["member"]
        if target.exists():
            if digest(target.read_bytes()) != script["sha256"]:
                raise ValueError("cached upstream evidence checksum mismatch")
        else:
            missing.append(script)
    if missing:
        if offline:
            raise ValueError("offline mode requires cached upstream processing evidence")
        with zipfile.ZipFile(
            _RangeArchive(catalog["reference_archive_url"], catalog["reference_archive_bytes"])
        ) as archive:
            for script in missing:
                info = archive.getinfo(script["member"])
                if info.file_size > 64 * 1024:
                    raise ValueError("upstream evidence exceeds the byte budget")
                evidence = archive.read(info)
                if digest(evidence) != script["sha256"]:
                    raise ValueError("upstream evidence checksum mismatch")
                write_bytes(raw / script["member"], evidence, immutable=True)
    return {"catalog": catalog, "receipt": receipt, "csv_path": raw / catalog["selected_member"]}


def audit_daily(path: Path, *, expected: dict) -> dict:
    """Independent source-side totals and calendar inventory; not model truth."""
    zone = ZoneInfo(expected["timezone"])
    buildings = {}
    identities = set()
    total = Decimal(0)
    rows = zero_rows = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != HEADER:
            raise ValueError("unexpected reference CSV header")
        for raw in reader:
            if set(raw) != set(HEADER) or None in raw.values():
                raise ValueError("malformed reference CSV row")
            source = raw["building"]
            if source not in BUILDINGS:
                raise ValueError("unregistered reference building")
            end = datetime.fromisoformat(raw["date_time"])
            if end.tzinfo is None or end.utcoffset() is None:
                raise ValueError("reference timestamp is not offset-aware")
            end = end.astimezone(UTC)
            if end.minute or end.second or end.microsecond:
                raise ValueError("reference period does not close a whole hour")
            key = (source, end)
            if key in identities:
                raise ValueError("duplicate source building/hour")
            identities.add(key)
            value = Decimal(raw["water_use_l"])
            if not value.is_finite() or value < 0 or value != value.to_integral_value():
                raise ValueError("invalid reference volume")
            start = end - timedelta(hours=1)
            item = buildings.setdefault(
                source,
                {
                    "alias": BUILDINGS[source],
                    "rows": 0,
                    "total": Decimal(0),
                    "start": start,
                    "end": end,
                    "previous": None,
                    "days": Counter(),
                },
            )
            if item["previous"] is not None and end - item["previous"] != timedelta(hours=1):
                raise ValueError("reference series is not consecutive hourly data")
            item["previous"] = end
            item["rows"] += 1
            item["total"] += value
            item["end"] = end
            item["days"][start.astimezone(zone).date()] += 1
            rows += 1
            total += value
            zero_rows += value == 0
    if (
        rows != expected["expected_rows"]
        or len(buildings) != expected["expected_buildings"]
        or total != Decimal(expected["expected_total_liters"])
    ):
        raise ValueError("reference row, building or total inventory changed")
    report = {}
    for source, item in sorted(buildings.items()):
        calendar = []
        for day, count in sorted(item["days"].items()):
            start = datetime.combine(day, time(), zone).astimezone(UTC)
            stop = datetime.combine(day + timedelta(days=1), time(), zone).astimezone(UTC)
            expected_hours = int((stop - start).total_seconds() / 3600)
            calendar.append(
                {
                    "date": day.isoformat(),
                    "hours_present": count,
                    "hours_in_local_day": expected_hours,
                    "complete": count == expected_hours,
                }
            )
        report[source] = {
            "household_alias": item["alias"],
            "records": item["rows"],
            "total_liters": str(item["total"]),
            "interval_start": item["start"].isoformat(),
            "interval_end": item["end"].isoformat(),
            "complete_calendar_days": sum(d["complete"] for d in calendar),
            "partial_calendar_days": [d for d in calendar if not d["complete"]],
            "dst_days": [d for d in calendar if d["hours_in_local_day"] != 24],
        }
    return {
        "source_rows": rows,
        "exported_buildings": len(buildings),
        "source_total_liters": str(total),
        "zero_volume_rows": zero_rows,
        "timezone": expected["timezone"],
        "buildings": report,
        "coverage_status": "unknown",
        "value_semantics": "upstream_processed_volume",
        "occupancy_features_imported": False,
        "calendar_basis": "interval_start",
    }


def prepare_reference(root: Path, *, offline=False) -> dict:
    root = Path(root).resolve()
    reference = fetch_reference(root, offline=offline)
    catalog, receipt = reference["catalog"], reference["receipt"]
    audit = audit_daily(reference["csv_path"], expected=catalog)
    entries = []
    for item in audit["buildings"].values():
        alias = item["household_alias"]
        entries.append(
            {
                "household_code": alias,
                "meter_code": f"{alias}-logical-meter",
                "stream_code": f"{alias}-hourly",
                "kind": "interval",
                "valid_from": item["interval_start"],
                "valid_to": item["interval_end"],
            }
        )
    registry = root / "registry.json"
    write_json(registry, {"format": "urban-water.registry.v1", "entries": entries}, immutable=True)
    spec = SourceSpec(
        namespace="aalborg-15180141-daily",
        data_kind="public_research",
        normalizer="aalborg_daily_v1",
        path="raw/final_data/daily_data.csv",
        archive_received_at=receipt["archive_received_at"],
        receipt_basis="research_archive_import",
        value_semantics="upstream_processed_volume",
        expected_sha256=catalog["csv_sha256"],
        reference_sha256=digest(CATALOG.read_bytes()),
        citation=f"{catalog['authors']}; doi:{catalog['doi']}; report doi:{catalog['report_doi']}",
        license=catalog["license"],
    )
    values = {
        key: value
        for key, value in spec.model_dump().items()
        if value is not None and key not in {"fields", "constants"}
    }
    text = "# Generated from the pinned reference catalog; original CSV is preserved.\n"
    text += "[sources.meter_readings]\n"
    text += "".join(
        f"{key} = {json.dumps(value, ensure_ascii=False)}\n" for key, value in values.items()
    )
    source_config = root / "sources.toml"
    write_bytes(source_config, text.encode(), immutable=True)
    write_json(
        root / "source-audit.json",
        {
            **audit,
            "reference": catalog,
            "archive_received_at": receipt["archive_received_at"],
            "receipt_basis": spec.receipt_basis,
        },
    )
    return {
        "directory": str(root),
        "source_config": str(source_config),
        "registry": str(registry),
        "audit": audit,
    }
