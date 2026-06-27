from __future__ import annotations

import logging
from typing import Final

import aiomqtt
from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable._session import SessionLike
from ha_mqtt_discoverable.sensors import Button, ButtonInfo

from ha_tux.lock.screensaver import GnomeScreenSaverAsync, new_screensaver_proxy

LOGGER = logging.getLogger(__name__)

LOCK_OBJECT_ID: Final = "lock"
LOCK_NAME: Final = "Lock"
LOCK_ICON: Final = "mdi:lock"


class LockPublisher:
    """Locks the GNOME session when its Home Assistant button is pressed."""

    def __init__(
        self, session: SessionLike, device: DeviceInfo, host_prefix: str
    ) -> None:
        self._proxy: GnomeScreenSaverAsync = new_screensaver_proxy()
        info = ButtonInfo(
            device=device,
            unique_id=f"ha_tux_{host_prefix}_{LOCK_OBJECT_ID}",
            object_id=f"{host_prefix}_{LOCK_OBJECT_ID}",
            name=LOCK_NAME,
            icon=LOCK_ICON,
        )
        self._button: Button = Button(session, info, self.on_press)

    async def announce(self) -> None:
        # First set_available writes discovery config (see Discoverable.set_available).
        await self._button.set_available(True)

    async def on_press(self, _sender: Button, _message: aiomqtt.Message) -> None:
        try:
            await self._proxy.lock()
            LOGGER.info("screen_lock_requested")
        except Exception:
            LOGGER.exception("screen_lock_failed")


def build_lock_publisher(
    session: SessionLike, device: DeviceInfo, host_prefix: str
) -> LockPublisher:
    return LockPublisher(session, device, host_prefix)
