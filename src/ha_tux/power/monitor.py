from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Final

from sdbus import DbusInterfaceCommonAsync, dbus_property_async, sd_bus_open_system

LOGGER = logging.getLogger(__name__)

UPOWER_SERVICE_NAME: Final = "org.freedesktop.UPower"
UPOWER_DISPLAY_DEVICE_PATH: Final = "/org/freedesktop/UPower/devices/DisplayDevice"
UPOWER_DEVICE_INTERFACE: Final = "org.freedesktop.UPower.Device"

UPOWER_STATE_NAMES: Final[dict[int, str]] = {
    0: "unknown",
    1: "charging",
    2: "discharging",
    3: "empty",
    4: "full",
    5: "pending_charge",
    6: "pending_discharge",
}
UPOWER_AC_STATES: Final[frozenset[int]] = frozenset({1, 4, 5})

# (percentage rounded to int, state enum) — callback fires only when this changes.
OnPowerChange = Callable[[int, int], Awaitable[None]]


class UPowerDeviceAsync(
    DbusInterfaceCommonAsync,
    interface_name=UPOWER_DEVICE_INTERFACE,
):
    """Proxy for a UPower device; only battery percentage and state are read."""

    @dbus_property_async("d")
    def percentage(self) -> float:
        raise NotImplementedError

    @dbus_property_async("u")
    def state(self) -> int:
        raise NotImplementedError


def new_display_device_proxy() -> UPowerDeviceAsync:
    return UPowerDeviceAsync.new_proxy(
        UPOWER_SERVICE_NAME, UPOWER_DISPLAY_DEVICE_PATH, bus=sd_bus_open_system()
    )


class PowerWatcher:
    """Pushes (percentage, state) to ``on_change`` on every real change.

    Reads both from UPower's DisplayDevice and re-reads on each
    PropertiesChanged signal (which also fires for noisy props like EnergyRate,
    hence the dedupe)."""

    def __init__(
        self,
        *,
        device: UPowerDeviceAsync,
        on_change: OnPowerChange,
    ) -> None:
        self._device: UPowerDeviceAsync = device
        self._on_change: OnPowerChange = on_change
        self._last: tuple[int, int] | None = None

    async def run(self) -> None:
        await self._emit()
        async for _ in self._device.properties_changed.catch():
            await self._emit()

    async def _emit(self) -> None:
        percentage = round(await self._device.percentage)
        state = await self._device.state
        key = (percentage, state)
        if key == self._last:
            return
        self._last = key
        LOGGER.info("power_changed", extra={"percentage": percentage, "state": state})
        await self._on_change(percentage, state)
