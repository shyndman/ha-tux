from __future__ import annotations

from typing import Final

from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable._session import SessionLike
from ha_mqtt_discoverable.sensors import BinarySensor, BinarySensorInfo

INPUT_ACTIVE_OBJECT_ID: Final = "input_active"
INPUT_ACTIVE_NAME: Final = "Input Active"
INPUT_ACTIVE_DEVICE_CLASS: Final = "occupancy"


class InputActivePublisher:
    """Publishes seat-input presence as a Home Assistant occupancy binary sensor."""

    def __init__(self, sensor: BinarySensor) -> None:
        self._sensor: BinarySensor = sensor

    async def set_active(self, active: bool) -> None:
        await self._sensor.set_available(True)
        if active:
            await self._sensor.on()
        else:
            await self._sensor.off()

    async def set_available(self, available: bool) -> None:
        await self._sensor.set_available(available)


def build_input_active_publisher(
    session: SessionLike, device: DeviceInfo, host_prefix: str
) -> InputActivePublisher:
    info = BinarySensorInfo(
        device=device,
        unique_id=f"ha_tux_{host_prefix}_{INPUT_ACTIVE_OBJECT_ID}",
        object_id=f"{host_prefix}_{INPUT_ACTIVE_OBJECT_ID}",
        name=INPUT_ACTIVE_NAME,
        device_class=INPUT_ACTIVE_DEVICE_CLASS,
    )
    return InputActivePublisher(BinarySensor(session, info))
