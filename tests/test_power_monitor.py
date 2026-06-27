from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

from ha_tux.power.monitor import PowerWatcher, UPowerDeviceAsync


class FakePropertiesChanged:
    def __init__(self, fire_count: int) -> None:
        self._fire_count: int = fire_count

    async def catch(self) -> AsyncIterator[None]:
        for _ in range(self._fire_count):
            yield None


class FakeDevice:
    """Scriptable UPower device: each (percentage, state) read pops the next
    scripted sample; properties_changed fires len(samples) - 1 times (one seed
    read happens before the loop)."""

    def __init__(self, samples: list[tuple[int, int]]) -> None:
        self._samples: list[tuple[int, int]] = samples
        self._index: int = 0
        self.properties_changed: FakePropertiesChanged = FakePropertiesChanged(
            len(samples) - 1
        )

    def _advance(self) -> tuple[int, int]:
        sample = self._samples[min(self._index, len(self._samples) - 1)]
        return sample

    @property
    async def percentage(self) -> float:
        return float(self._advance()[0])

    @property
    async def state(self) -> int:
        sample = self._advance()
        self._index += 1
        return sample[1]


def test_emit_dedupes_unchanged_samples() -> None:
    samples = [(80, 5), (80, 5), (80, 5), (79, 5)]
    fired: list[tuple[int, int]] = []

    async def on_change(percentage: int, state: int) -> None:
        fired.append((percentage, state))

    device = FakeDevice(samples)
    watcher = PowerWatcher(
        device=cast(UPowerDeviceAsync, cast(object, device)),
        on_change=on_change,
    )
    asyncio.run(watcher.run())
    assert fired == [(80, 5), (79, 5)]
