"""Time unit conversion and ISO date parsing helpers."""

from __future__ import annotations

from datetime import date
from typing import Final

MILLISECONDS_PER_SECOND: Final = 1000
MICROSECONDS_PER_SECOND: Final = 1_000_000

__all__ = [
    "MICROSECONDS_PER_SECOND",
    "MILLISECONDS_PER_SECOND",
    "microseconds_to_seconds",
    "parse_iso_date",
    "seconds_to_microseconds",
    "seconds_to_milliseconds",
]


def seconds_to_milliseconds(value_seconds: float) -> int:
    """Convert a non-negative duration in seconds into integer milliseconds."""
    if value_seconds < 0:
        raise ValueError("Seconds must be non-negative")

    return int(value_seconds * MILLISECONDS_PER_SECOND)


def microseconds_to_seconds(value_us: int) -> int:
    """Convert microsecond timestamps into non-negative whole seconds."""
    if value_us <= 0:
        return 0

    return value_us // MICROSECONDS_PER_SECOND


def seconds_to_microseconds(value_seconds: float) -> int:
    """Convert non-negative seconds into integer microseconds."""
    if value_seconds < 0:
        raise ValueError("Seconds must be non-negative")

    return int(value_seconds * MICROSECONDS_PER_SECOND)


def parse_iso_date(value: str | None) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` date string, returning ``None`` if absent or invalid."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
