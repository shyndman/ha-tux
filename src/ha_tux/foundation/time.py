"""Time unit conversion helpers."""

from __future__ import annotations

from typing import Final

MILLISECONDS_PER_SECOND: Final = 1000

__all__ = [
    "MILLISECONDS_PER_SECOND",
    "seconds_to_milliseconds",
]


def seconds_to_milliseconds(value_seconds: float) -> int:
    """Convert a non-negative duration in seconds into integer milliseconds."""
    if value_seconds < 0:
        raise ValueError("Seconds must be non-negative")

    return int(value_seconds * MILLISECONDS_PER_SECOND)
