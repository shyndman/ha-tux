"""General-purpose string formatting and identifier helpers."""

from __future__ import annotations

import re
from typing import Final

_NON_ALNUM_PATTERN: Final = re.compile(r"[^a-z0-9]+")

__all__ = [
    "human_bytes",
    "plural_suffix",
    "slugify",
]


def slugify(value: str, *, separator: str = "-") -> str:
    """Normalize ``value`` into lowercase ASCII tokens joined by ``separator``."""
    return _NON_ALNUM_PATTERN.sub(separator, value.lower()).strip(separator)


def human_bytes(n: int) -> str:
    """Format a byte count using compact binary-unit suffixes."""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.0f} {unit}" if value >= 10 else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.0f} TB"


def plural_suffix(count: int) -> str:
    """Return ``""`` for singular ``count == 1`` and ``"s"`` otherwise."""
    return "" if count == 1 else "s"
