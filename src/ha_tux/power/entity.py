from __future__ import annotations

from typing import Final

from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable._session import SessionLike
from ha_mqtt_discoverable.sensors import (
    BinarySensor,
    BinarySensorInfo,
    Sensor,
    SensorInfo,
)

from ha_tux.power.monitor import UPOWER_AC_STATES, UPOWER_STATE_NAMES

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

UPOWER_STATE_UNKNOWN: Final = "unknown"


class PowerPublisher:
    """Publishes UPower battery percentage, state, and AC presence to HA."""

    def __init__(
        self, battery: Sensor, battery_state: Sensor, ac: BinarySensor
    ) -> None:
        self._battery: Sensor = battery
        self._battery_state: Sensor = battery_state
        self._ac: BinarySensor = ac

    async def update(self, percentage: int, state: int) -> None:
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

    async def set_available(self, available: bool) -> None:
        await self._battery.set_available(available)
        await self._battery_state.set_available(available)
        await self._ac.set_available(available)


def build_power_publisher(
    session: SessionLike, device: DeviceInfo, host_prefix: str
) -> PowerPublisher:
    battery = Sensor(
        session,
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
    battery_state = Sensor(
        session,
        SensorInfo(
            device=device,
            unique_id=f"ha_tux_{host_prefix}_{BATTERY_STATE_OBJECT_ID}",
            object_id=f"{host_prefix}_{BATTERY_STATE_OBJECT_ID}",
            name=BATTERY_STATE_NAME,
            icon=BATTERY_STATE_ICON,
        ),
    )
    ac = BinarySensor(
        session,
        BinarySensorInfo(
            device=device,
            unique_id=f"ha_tux_{host_prefix}_{AC_OBJECT_ID}",
            object_id=f"{host_prefix}_{AC_OBJECT_ID}",
            name=AC_NAME,
            device_class=AC_DEVICE_CLASS,
        ),
    )
    return PowerPublisher(battery, battery_state, ac)
