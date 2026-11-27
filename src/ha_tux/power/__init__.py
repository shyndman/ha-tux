"""Power and battery telemetry feature backed by UPower on the system bus.

Tracks ``org.freedesktop.UPower`` ownership via ``ha_tux.dbus.watch_name``,
marking the battery percentage, battery state, and AC binary sensor offline
when UPower disappears and publishing a fresh snapshot when it returns.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Final, Protocol

from ha_mqtt_discoverable.sensors import (
    BinarySensor,
    BinarySensorInfo,
    Sensor,
    SensorInfo,
)
from libsh import get_logger
from sdbus import (
    DbusInterfaceCommonAsync,
    dbus_property_async,
    sd_bus_open_system,
)
from sdbus.sd_bus_internals import SdBus

from ha_tux.dbus import NameOwnerSource, SdbusNameOwnerSource, watch_name
from ha_tux.runtime import SessionContext

LOGGER = get_logger(__name__)

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
UPOWER_STATE_UNKNOWN: Final = "unknown"

BATTERY_OBJECT_ID: Final = "battery"
BATTERY_NAME: Final = "Battery"
BATTERY_DEVICE_CLASS: Final = "battery"
BATTERY_UNIT: Final = "%"
BATTERY_STATE_CLASS: Final = "measurement"

BATTERY_STATE_OBJECT_ID: Final = "battery_state"
BATTERY_STATE_NAME: Final = "Battery State"
BATTERY_STATE_ICON: Final = "mdi:battery-charging"

AC_OBJECT_ID: Final = "ac"
AC_NAME: Final = "AC Power"
AC_DEVICE_CLASS: Final = "plug"


class UPowerDeviceAsync(
    DbusInterfaceCommonAsync,
    interface_name=UPOWER_DEVICE_INTERFACE,
):
    """Proxy for UPower's DisplayDevice properties."""

    @dbus_property_async("d")
    def percentage(self) -> float:
        """Battery charge percentage reported by UPower."""
        raise NotImplementedError

    @dbus_property_async("u")
    def state(self) -> int:
        """Numeric UPower battery state enum."""
        raise NotImplementedError


class UPowerDevice(Protocol):
    """Seam between UPower D-Bus telemetry and Home Assistant entities."""

    async def get_percentage(self) -> float:
        """Read current battery percentage."""
        ...

    async def get_state(self) -> int:
        """Read current UPower numeric state."""
        ...

    def properties_changed(self) -> AsyncIterator[object]:
        """Yield property change notifications for this device."""
        ...


class SdbusUPowerDevice:
    """Live ``UPowerDevice`` adapter over ``UPowerDeviceAsync``."""

    def __init__(self, bus: SdBus) -> None:
        """Bind a fresh DisplayDevice proxy to the system bus."""
        self._proxy: UPowerDeviceAsync = UPowerDeviceAsync.new_proxy(
            UPOWER_SERVICE_NAME,
            UPOWER_DISPLAY_DEVICE_PATH,
            bus=bus,
        )

    async def get_percentage(self) -> float:
        """Read DisplayDevice ``Percentage`` from UPower."""
        return await self._proxy.percentage

    async def get_state(self) -> int:
        """Read DisplayDevice ``State`` from UPower."""
        return await self._proxy.state

    def properties_changed(self) -> AsyncIterator[object]:
        """Subscribe to UPower ``PropertiesChanged`` signals."""
        return self._proxy.properties_changed.catch()


class _PowerEntities:
    """Owns and publishes Home Assistant's battery, battery state, and AC entities."""

    def __init__(self, ctx: SessionContext) -> None:
        host_prefix = ctx.host_prefix
        device = ctx.device

        self._battery: Sensor = Sensor(
            ctx.session,
            SensorInfo(
                device=device,
                unique_id=f"ha_tux_{host_prefix}_{BATTERY_OBJECT_ID}",
                object_id=f"{host_prefix}_{BATTERY_OBJECT_ID}",
                name=BATTERY_NAME,
                device_class=BATTERY_DEVICE_CLASS,
                unit_of_measurement=BATTERY_UNIT,
                state_class=BATTERY_STATE_CLASS,
            ),
        )
        self._battery_state: Sensor = Sensor(
            ctx.session,
            SensorInfo(
                device=device,
                unique_id=f"ha_tux_{host_prefix}_{BATTERY_STATE_OBJECT_ID}",
                object_id=f"{host_prefix}_{BATTERY_STATE_OBJECT_ID}",
                name=BATTERY_STATE_NAME,
                icon=BATTERY_STATE_ICON,
            ),
        )
        self._ac: BinarySensor = BinarySensor(
            ctx.session,
            BinarySensorInfo(
                device=device,
                unique_id=f"ha_tux_{host_prefix}_{AC_OBJECT_ID}",
                object_id=f"{host_prefix}_{AC_OBJECT_ID}",
                name=AC_NAME,
                device_class=AC_DEVICE_CLASS,
            ),
        )

    async def publish(self, percentage: int, state: int) -> None:
        """Mark all power entities available and publish updated telemetry."""
        state_name = UPOWER_STATE_NAMES.get(state, UPOWER_STATE_UNKNOWN)
        on_ac = state in UPOWER_AC_STATES

        await self._battery.set_available(True)
        await self._battery.set_state(percentage)
        await self._battery_state.set_available(True)
        await self._battery_state.set_state(state_name)
        await self._ac.set_available(True)

        if on_ac:
            await self._ac.on()
        else:
            await self._ac.off()

    async def mark_unavailable(self) -> None:
        """Mark battery, battery state, and AC entities offline."""
        await self._battery.set_available(False)
        await self._battery_state.set_available(False)
        await self._ac.set_available(False)


async def _track_power(entities: _PowerEntities, device: UPowerDevice) -> None:
    """Publish initial power reading and deduped updates while UPower is owned."""
    last: tuple[int, int] | None = None

    async def sync() -> None:
        nonlocal last
        percentage = round(await device.get_percentage())
        state = await device.get_state()
        sample = (percentage, state)

        if sample == last:
            return

        last = sample
        LOGGER.info("power_changed", percentage=percentage, state=state)
        await entities.publish(percentage, state)

    await sync()
    async for _ in device.properties_changed():
        await sync()


async def run_power(
    ctx: SessionContext,
    *,
    name_owner_source: NameOwnerSource | None = None,
    device_factory: Callable[[], UPowerDevice] | None = None,
) -> None:
    """Supervise UPower battery and AC entities across system bus restarts."""
    entities = _PowerEntities(ctx)
    bus: SdBus | None = (
        sd_bus_open_system()
        if (name_owner_source is None or device_factory is None)
        else None
    )

    if name_owner_source is not None:
        source = name_owner_source
    else:
        assert bus is not None
        source = SdbusNameOwnerSource(bus)

    if device_factory is not None:
        resolve_device = device_factory
    else:
        assert bus is not None
        system_bus = bus

        def resolve_device() -> UPowerDevice:
            return SdbusUPowerDevice(system_bus)

    async def attach() -> None:
        await _track_power(entities, resolve_device())

    async def mark_unavailable() -> None:
        LOGGER.info("power_unavailable")
        await entities.mark_unavailable()

    await watch_name(
        source=source,
        service_name=UPOWER_SERVICE_NAME,
        on_present=attach,
        on_absent=mark_unavailable,
    )
