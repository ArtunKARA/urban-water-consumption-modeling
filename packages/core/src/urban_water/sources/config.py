"""Allowlisted source points. Connection secrets never enter the parser snapshot."""

import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LEGACY_CONTRACT = "synthetic.measurement.v1"
RESEARCH_CONTRACT = "urban-water.measurement.v1"
LEGACY_PARSER = "synthetic-jsonl/1"


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    namespace: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    family: Literal["meter_measurements"] = "meter_measurements"
    data_kind: Literal["synthetic", "public_research"] = "synthetic"
    reader: Literal["local_file_v1", "http_file_v1"] = "local_file_v1"
    normalizer: Literal["measurement_jsonl_v1", "mapped_csv_v1"] = "measurement_jsonl_v1"
    path: str | None = None
    path_env: str | None = None
    url_env: str | None = None
    token_env: str | None = None
    citation: str | None = Field(default=None, max_length=2048)
    license: str | None = Field(default=None, max_length=128)
    timeout_seconds: float = Field(default=15, ge=0.1, le=60, allow_inf_nan=False)
    fields: dict[str, str] = Field(default_factory=dict)
    constants: dict[str, str | int] = Field(default_factory=dict)
    delimiter: str = ","

    @model_validator(mode="after")
    def validate_source(self):
        if self.reader == "local_file_v1":
            if bool(self.path) == bool(self.path_env) or self.url_env or self.token_env:
                raise ValueError("local reader requires exactly one path/path_env")
        elif not self.url_env or self.path or self.path_env:
            raise ValueError("HTTP reader requires url_env and no local path")
        if self.namespace == "legacy" and self.data_kind != "synthetic":
            raise ValueError("legacy namespace is reserved for synthetic compatibility")
        validate_options(self.normalizer, self.options)
        return self

    @property
    def contract(self) -> str:
        return LEGACY_CONTRACT if self.data_kind == "synthetic" else RESEARCH_CONTRACT

    @property
    def parser(self) -> str:
        if self.normalizer == "mapped_csv_v1":
            return "mapped-csv/1"
        return LEGACY_PARSER if self.data_kind == "synthetic" else "measurement-jsonl/1"

    @property
    def options(self) -> dict:
        if self.normalizer == "measurement_jsonl_v1":
            return (
                {}
                if not self.fields and not self.constants and self.delimiter == ","
                else {
                    "fields": self.fields,
                    "constants": self.constants,
                    "delimiter": self.delimiter,
                }
            )
        return {"fields": self.fields, "constants": self.constants, "delimiter": self.delimiter}


def validate_options(normalizer: str, options: dict) -> None:
    if normalizer == "measurement_jsonl_v1":
        if options:
            raise ValueError("JSONL uses the measurement contract without field mappings")
        return
    if normalizer != "mapped_csv_v1" or set(options) != {"fields", "constants", "delimiter"}:
        raise ValueError("unknown normalizer/options version")
    fields, constants, delimiter = options["fields"], options["constants"], options["delimiter"]
    required = {"stream_code", "event_id", "kind", "occurred_at", "received_at", "value", "unit"}
    allowed = required | {"revision", "interval_start"}
    if not isinstance(fields, dict) or not isinstance(constants, dict):
        raise ValueError("field mappings must be objects")
    keys = set(fields) | set(constants)
    if set(fields) & set(constants) or not required <= keys or not keys <= allowed:
        raise ValueError("incomplete or ambiguous measurement mapping")
    if not all(isinstance(v, str) and v for v in fields.values()):
        raise ValueError("column names must be nonempty strings")
    if not all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in constants.values()):
        raise ValueError("mapping constants must be strings or integers")
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in '\r\n"\x00':
        raise ValueError("invalid CSV delimiter")


def options_digest(options: dict) -> str:
    return hashlib.sha256(
        json.dumps(options, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def load_source(path: Path, name: str) -> SourceSpec:
    path = Path(path).resolve()
    with path.open("rb") as incoming:
        config = tomllib.load(incoming)
    if set(config) != {"sources"} or not isinstance(config["sources"], dict):
        raise ValueError("configuration must contain a sources table")
    # Validate every entry so a misspelled or unsupported source cannot silently disappear.
    specs = {key: SourceSpec.model_validate(value) for key, value in config["sources"].items()}
    if name not in specs:
        raise ValueError("unknown source point")
    spec = specs[name]
    if spec.reader == "local_file_v1":
        location = spec.path if spec.path is not None else os.environ.get(spec.path_env)
        if not location:
            raise ValueError("source path environment variable is missing")
        resolved = Path(location)
        if not resolved.is_absolute():
            resolved = path.parent / resolved
        spec = spec.model_copy(update={"path": str(resolved.resolve()), "path_env": None})
    return spec
