"""Versioned, restartable normalization of stored originals; no database side effects."""

import csv
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import BinaryIO, Protocol

from urban_water_contracts import Measurement, ResearchMeasurement

from urban_water.sources.config import (
    LEGACY_CONTRACT,
    LEGACY_PARSER,
    RESEARCH_CONTRACT,
    validate_options,
)


class NormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedRow:
    number: int
    record: Measurement | None
    issue: str | None = None


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite JSON number")


def parse_json(line: bytes, contract: str = LEGACY_CONTRACT) -> Measurement:
    raw = json.loads(
        line, object_pairs_hook=_object, parse_float=Decimal, parse_constant=_reject_constant
    )
    model = Measurement if contract == LEGACY_CONTRACT else ResearchMeasurement
    return model.model_validate(raw)


def qualify(record: Measurement, namespace: str) -> Measurement:
    if namespace == "legacy":
        return record
    identity = hashlib.sha256(f"{namespace}\0{record.event_id}".encode()).hexdigest()
    return record.model_copy(update={"event_id": f"src:{identity}"})


def _lines(incoming: BinaryIO, max_line_bytes: int) -> Iterator[bytes]:
    while line := incoming.readline(max_line_bytes + 1):
        if len(line) > max_line_bytes:
            raise NormalizationError("source line limit exceeded")
        yield line


class SourceNormalizer(Protocol):
    def rows(
        self, incoming: BinaryIO, max_line_bytes: int, max_records: int
    ) -> Iterator[NormalizedRow]: ...


class MeasurementJsonl:
    def __init__(self, contract: str):
        self.contract = contract

    def rows(self, incoming, max_line_bytes, max_records):
        for number, line in enumerate(_lines(incoming, max_line_bytes), 1):
            if number > max_records:
                raise NormalizationError("source record limit exceeded")
            try:
                record = parse_json(line, self.contract)
            except ValueError:
                yield NormalizedRow(number, None, "invalid_contract")
            else:
                yield NormalizedRow(number, record)


class MappedCsv:
    def __init__(self, contract: str, options: dict):
        self.model = Measurement if contract == LEGACY_CONTRACT else ResearchMeasurement
        self.options = options

    def _cells(self, line, *, header=False):
        # v1 deliberately permits one physical line per record; multiline CSV is rejected.
        return next(
            csv.reader(
                [line.decode("utf-8-sig" if header else "utf-8")],
                delimiter=self.options["delimiter"],
                strict=True,
            )
        )

    def rows(self, incoming, max_line_bytes, max_records):
        lines = _lines(incoming, max_line_bytes)
        try:
            header = self._cells(next(lines), header=True)
        except NormalizationError:
            raise
        except (StopIteration, ValueError, csv.Error):
            raise NormalizationError("missing or invalid CSV header") from None
        if len(header) != len(set(header)) or not all(header):
            raise NormalizationError("duplicate or empty CSV columns")
        if not set(self.options["fields"].values()) <= set(header):
            raise NormalizationError("CSV is missing a mapped column")
        for number, line in enumerate(lines, 1):
            if number > max_records:
                raise NormalizationError("source record limit exceeded")
            try:
                cells = self._cells(line)
                if len(cells) != len(header):
                    raise ValueError("CSV column count mismatch")
                raw = dict(zip(header, cells, strict=True))
                mapped = {key: raw[column] for key, column in self.options["fields"].items()}
                mapped.update(self.options["constants"])
                if "revision" in mapped and isinstance(mapped["revision"], str):
                    if not mapped["revision"].isascii() or not mapped["revision"].isdigit():
                        raise ValueError("revision must be an integer")
                    mapped["revision"] = int(mapped["revision"])
                record = self.model.model_validate(mapped)
            except (ValueError, csv.Error):
                yield NormalizedRow(number, None, "invalid_contract")
            else:
                yield NormalizedRow(number, record)


def get_normalizer(contract: str, parser: str, options: dict) -> SourceNormalizer:
    if contract not in {LEGACY_CONTRACT, RESEARCH_CONTRACT}:
        raise NormalizationError("unsupported measurement contract")
    expected = LEGACY_PARSER if contract == LEGACY_CONTRACT else "measurement-jsonl/1"
    if parser == expected:
        validate_options("measurement_jsonl_v1", options)
        return MeasurementJsonl(contract)
    if parser == "mapped-csv/1":
        validate_options("mapped_csv_v1", options)
        return MappedCsv(contract, options)
    raise NormalizationError("unsupported parser version")
