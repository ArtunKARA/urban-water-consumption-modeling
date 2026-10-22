"""Explicit runtime configuration; real-data processing is gated in this milestone."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    storage_root: Path
    timezone: str = "Europe/Istanbul"
    study_code: str = "synthetic-pilot"
    cohort_code: str = "fixture"
    lease_seconds: int = 60
    chunk_size: int = 1000
    max_attempts: int = 3
    max_source_bytes: int = 268435456
    max_line_bytes: int = 65536
    max_records: int = 2000000

    def __post_init__(self) -> None:
        ZoneInfo(self.timezone)
        for name in (
            "lease_seconds",
            "chunk_size",
            "max_attempts",
            "max_source_bytes",
            "max_line_bytes",
            "max_records",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(override=False)
        if os.getenv("UW_ENV") != "synthetic":
            raise ValueError(
                "this release requires UW_ENV=synthetic; real adapters are not approved"
            )
        dsn = os.environ.get("UW_DATABASE_URL")
        root = os.environ.get("UW_STORAGE_ROOT")
        if not dsn or not root:
            raise ValueError("UW_DATABASE_URL and UW_STORAGE_ROOT are required")
        return cls(
            database_url=dsn,
            storage_root=Path(root).resolve(),
            timezone=os.getenv("UW_TIMEZONE", "Europe/Istanbul"),
            study_code=os.getenv("UW_STUDY_CODE", "synthetic-pilot"),
            cohort_code=os.getenv("UW_COHORT_CODE", "fixture"),
            **{
                name: int(os.getenv(f"UW_{name.upper()}", str(default)))
                for name, default in (
                    ("lease_seconds", 60),
                    ("chunk_size", 1000),
                    ("max_attempts", 3),
                    ("max_source_bytes", 268435456),
                    ("max_line_bytes", 65536),
                    ("max_records", 2000000),
                )
            },
        )
