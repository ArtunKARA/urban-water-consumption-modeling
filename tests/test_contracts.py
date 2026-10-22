import pytest
from conftest import reading
from pydantic import ValidationError
from urban_water_contracts import Measurement


@pytest.mark.parametrize(
    "changes",
    [
        {"occurred_at": "2026-01-01T00:00:00"},
        {"value": "NaN"},
        {"value": "-1"},
        {"value": "0.5"},
        {"unit": "m3"},
        {"revision": "1"},
        {"private_name": "not accepted"},
        {"kind": "interval", "unit": "L"},
        {"kind": "index", "unit": "pulse"},
        {"received_at": "2020-01-01T00:00:00Z"},
        {"occurred_at": 1767225600},
        {"occurred_at": "1767225600"},
    ],
)
def test_invalid_measurements_fail_closed(changes):
    with pytest.raises(ValidationError):
        Measurement.model_validate(reading(**changes))


def test_raw_decimal_precision_is_preserved():
    row = Measurement.model_validate(reading(kind="index", unit="m3", value="0.123456789"))
    assert str(row.value) == "0.123456789"
