from __future__ import annotations

import pytest

from ha_tux.foundation.time import seconds_to_milliseconds


def test_seconds_to_milliseconds_converts_positive_values_and_rejects_negative() -> (
    None
):
    assert seconds_to_milliseconds(0) == 0
    assert seconds_to_milliseconds(1.25) == 1_250
    with pytest.raises(ValueError, match="Seconds must be non-negative"):
        _ = seconds_to_milliseconds(-0.1)
