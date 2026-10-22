"""Versioned transport contracts shared by the simulator and research core."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class Measurement(BaseModel):
    """Synthetic v1 only. Source event identity must be verified for real adapters.

    `occurred_at` is a pulse instant or the exclusive end of an interval/index reading.
    A revision replaces the entire logical event, never adds consumption to it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["synthetic.measurement.v1"] = "synthetic.measurement.v1"
    stream_code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    event_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    revision: int = Field(default=1, ge=1, strict=True)
    kind: Literal["pulse", "interval", "index"]
    occurred_at: AwareDatetime
    received_at: AwareDatetime
    interval_start: AwareDatetime | None = None
    value: Decimal = Field(ge=0, max_digits=24, decimal_places=9, allow_inf_nan=False)
    unit: Literal["L", "m3", "pulse"]

    @field_validator("occurred_at", "received_at", "interval_start", mode="before")
    @classmethod
    def explicit_timestamp(cls, value):
        if value is None:
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        if not isinstance(value, datetime):
            raise ValueError("timestamp must be an ISO-8601 string with an explicit offset")
        return value

    @model_validator(mode="after")
    def check_semantics(self) -> "Measurement":
        if self.kind == "interval":
            if self.interval_start is None or self.interval_start >= self.occurred_at:
                raise ValueError("interval requires an earlier inclusive start")
        elif self.interval_start is not None:
            raise ValueError("only interval measurements have interval_start")
        if self.kind == "pulse":
            if self.unit != "pulse" or self.value != self.value.to_integral_value():
                raise ValueError("pulse measurements require integer pulse counts")
        elif self.unit == "pulse":
            raise ValueError("interval/index measurements require a volume unit")
        if self.received_at < self.occurred_at:
            raise ValueError("received_at cannot precede the measurement instant")
        return self


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an explicit timestamp offset is required")
    return value
