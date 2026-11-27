from __future__ import annotations

from datetime import date

import pytest

from ha_tux.foundation.time import (
    microseconds_to_seconds,
    parse_iso_date,
    seconds_to_microseconds,
    seconds_to_milliseconds,
)


def test_seconds_to_milliseconds_converts_positive_values_and_rejects_negative() -> (
    None
):
    assert seconds_to_milliseconds(0) == 0
    assert seconds_to_milliseconds(1.25) == 1_250
    with pytest.raises(ValueError, match="Seconds must be non-negative"):
        _ = seconds_to_milliseconds(-0.1)


def test_microsecond_conversion_helpers() -> None:
    assert microseconds_to_seconds(1_999_999) == 1
    assert microseconds_to_seconds(-1) == 0
    assert seconds_to_microseconds(1.25) == 1_250_000
    with pytest.raises(ValueError, match="Seconds must be non-negative"):
        _ = seconds_to_microseconds(-1)


def test_parse_iso_date_parses_valid_and_rejects_invalid_or_none() -> None:
    assert parse_iso_date(None) is None
    assert parse_iso_date("not-a-date") is None
    assert parse_iso_date("2026-11-27") == date(2026, 11, 27)
