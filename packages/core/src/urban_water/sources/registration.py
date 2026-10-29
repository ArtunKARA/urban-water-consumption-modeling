"""Explicit pseudonymous meter/household registration; never inferred from measurements."""

from datetime import UTC
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from psycopg import sql
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from urban_water_contracts import require_aware

from urban_water.db import connect
from urban_water.settings import Settings


class RegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    household_code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    meter_code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    stream_code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    kind: Literal["pulse", "interval", "index"]
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    liters_per_pulse: Decimal | None = Field(default=None, gt=0, lt=10**15, decimal_places=9)

    @model_validator(mode="after")
    def validate_period(self):
        require_aware(self.valid_from)
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("registry period must be positive")
        if (self.kind == "pulse") != (self.liters_per_pulse is not None):
            raise ValueError("only pulse streams require liters_per_pulse")
        return self


class RegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["urban-water.registry.v1"]
    entries: list[RegistryEntry] = Field(min_length=1, max_length=10000)


def ensure_environment(conn, settings: Settings) -> None:
    if conn.execute(
        "SELECT 1 FROM study WHERE environment<>%s LIMIT 1", (settings.environment,)
    ).fetchone():
        raise ValueError("use a separate database for each data environment")
    if conn.execute(
        "SELECT 1 FROM source WHERE acquisition->>'data_kind'<>%s LIMIT 1", (settings.environment,)
    ).fetchone():
        raise ValueError("use separate source storage/databases for each data environment")


def _insert_exact(conn, table: str, payload: dict) -> None:
    names = list(payload)
    conn.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (id) DO NOTHING").format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, names)),
            sql.SQL(",").join(sql.Placeholder() for _ in names),
        ),
        list(payload.values()),
    )
    row = conn.execute(
        sql.SQL("SELECT * FROM {} WHERE id=%s FOR SHARE").format(sql.Identifier(table)),
        (payload["id"],),
    ).fetchone()
    if any(row[key] != value for key, value in payload.items()):
        raise ValueError("registry identity already exists with different content")


def register_file(settings: Settings, path: Path) -> dict:
    if path.stat().st_size > min(settings.max_source_bytes, 8 * 1024 * 1024):
        raise ValueError("registry document exceeds the byte budget")
    document = RegistryDocument.model_validate_json(path.read_bytes())

    def identity(value):
        return uuid5(
            NAMESPACE_URL,
            f"urban-water:registered:{settings.environment}:{settings.study_code}:{value}",
        )

    with connect(settings) as conn:
        # Serialize initial registration/environment selection across competing importers.
        conn.execute("SELECT pg_advisory_xact_lock(83621002)")
        ensure_environment(conn, settings)
        existing_study = conn.execute(
            "SELECT id FROM study WHERE code=%s", (settings.study_code,)
        ).fetchone()
        study_id = existing_study["id"] if existing_study else identity("study")
        existing_cohort = conn.execute(
            "SELECT id FROM cohort WHERE study_id=%s AND code=%s", (study_id, settings.cohort_code)
        ).fetchone()
        cohort_id = (
            existing_cohort["id"] if existing_cohort else identity(f"cohort:{settings.cohort_code}")
        )
        _insert_exact(
            conn,
            "study",
            {"id": study_id, "code": settings.study_code, "environment": settings.environment},
        )
        _insert_exact(
            conn, "cohort", {"id": cohort_id, "study_id": study_id, "code": settings.cohort_code}
        )
        for entry in document.entries:
            hid, mid, sid = (
                identity(f"{kind}:{code}")
                for kind, code in (
                    ("household", entry.household_code),
                    ("meter", entry.meter_code),
                    ("stream", entry.stream_code),
                )
            )
            _insert_exact(conn, "household", {"id": hid, "code": entry.household_code})
            _insert_exact(conn, "meter", {"id": mid, "code": entry.meter_code})
            _insert_exact(
                conn,
                "stream",
                {
                    "id": sid,
                    "meter_id": mid,
                    "code": entry.stream_code,
                    "kind": entry.kind,
                    "liters_per_pulse": entry.liters_per_pulse,
                    "valid_from": entry.valid_from,
                    "valid_to": entry.valid_to,
                },
            )
            _insert_exact(
                conn,
                "installation",
                {
                    "id": identity(
                        f"installation:{entry.meter_code}:"
                        f"{entry.valid_from.astimezone(UTC).isoformat()}"
                    ),
                    "meter_id": mid,
                    "household_id": hid,
                    "valid_from": entry.valid_from,
                    "valid_to": entry.valid_to,
                },
            )
            conn.execute(
                "INSERT INTO membership(cohort_id,household_id,valid_from,valid_to) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (cohort_id, hid, entry.valid_from, entry.valid_to),
            )
            row = conn.execute(
                "SELECT valid_to FROM membership WHERE cohort_id=%s "
                "AND household_id=%s AND valid_from=%s",
                (cohort_id, hid, entry.valid_from),
            ).fetchone()
            if row is None or row["valid_to"] != entry.valid_to:
                raise ValueError("membership period conflicts with the existing registry")
        conn.execute(
            "INSERT INTO audit_event(action,target_id) VALUES ('registry.import',%s)", (cohort_id,)
        )
    return {
        "registered_entries": len(document.entries),
        "study_code": settings.study_code,
        "cohort_code": settings.cohort_code,
        "environment": settings.environment,
    }
