"""Screen-lock command feature backed by GNOME ScreenSaver on the session bus.

Tracks ``org.gnome.ScreenSaver`` ownership via ``ha_tux.dbus.watch_name`` so the
Home Assistant Lock button reflects live GNOME session availability and binds
to a fresh proxy whenever the owner restarts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol

import aiomqtt
from ha_mqtt_discoverable.sensors import Button, ButtonInfo
from libsh import get_logger
from sdbus import DbusInterfaceCommonAsync, dbus_method_async

from ha_tux.dbus import NameOwnerSource, session_name_owner_source, watch_name
from ha_tux.runtime import SessionContext

LOGGER = get_logger(__name__)

GNOME_SCREENSAVER_SERVICE_NAME: Final = "org.gnome.ScreenSaver"
GNOME_SCREENSAVER_OBJECT_PATH: Final = "/org/gnome/ScreenSaver"
GNOME_SCREENSAVER_INTERFACE: Final = "org.gnome.ScreenSaver"

LOCK_OBJECT_ID: Final = "lock"
LOCK_NAME: Final = "Lock"
LOCK_ICON: Final = "mdi:lock"


class GnomeScreenSaverAsync(
    DbusInterfaceCommonAsync,
    interface_name=GNOME_SCREENSAVER_INTERFACE,
):
    """Proxy for GNOME's screensaver; only the Lock action is used."""

    @dbus_method_async(method_name="Lock")
    async def lock(self) -> None:
        """Lock the active GNOME desktop session."""
        raise NotImplementedError


class ScreenSaver(Protocol):
    """Seam between screen-lock button commands and D-Bus execution."""

    async def lock(self) -> None:
        """Trigger a screen lock."""
        ...


class SdbusScreenSaver:
    """Live ``ScreenSaver`` adapter over ``GnomeScreenSaverAsync``."""

    def __init__(self) -> None:
        """Bind a fresh D-Bus proxy to GNOME ScreenSaver."""
        self._proxy: GnomeScreenSaverAsync = GnomeScreenSaverAsync.new_proxy(
            GNOME_SCREENSAVER_SERVICE_NAME, GNOME_SCREENSAVER_OBJECT_PATH
        )

    async def lock(self) -> None:
        """Invoke ``org.gnome.ScreenSaver.Lock`` on the session bus."""
        await self._proxy.lock()


async def run_screen_lock(
    ctx: SessionContext,
    *,
    name_owner_source: NameOwnerSource | None = None,
    screensaver_factory: Callable[[], ScreenSaver] = SdbusScreenSaver,
) -> None:
    """Expose the Home Assistant Lock button tracked against GNOME ScreenSaver."""
    active_screensaver: ScreenSaver | None = None

    async def on_press(_sender: Button, _message: aiomqtt.Message) -> None:
        if active_screensaver is None:
            LOGGER.warning("screen_lock_ignored_unavailable")
            return

        try:
            await active_screensaver.lock()
            LOGGER.info("screen_lock_requested")
        except Exception:
            LOGGER.exception("screen_lock_failed")

    info = ButtonInfo(
        device=ctx.device,
        unique_id=f"ha_tux_{ctx.host_prefix}_{LOCK_OBJECT_ID}",
        object_id=f"{ctx.host_prefix}_{LOCK_OBJECT_ID}",
        name=LOCK_NAME,
        icon=LOCK_ICON,
    )
    button = Button(ctx.session, info, on_press)
    source = (
        name_owner_source
        if name_owner_source is not None
        else session_name_owner_source()
    )

    async def attach() -> None:
        nonlocal active_screensaver
        active_screensaver = screensaver_factory()
        LOGGER.info("screen_lock_available")
        await button.set_available(True)

    async def mark_unavailable() -> None:
        nonlocal active_screensaver
        active_screensaver = None
        LOGGER.info("screen_lock_unavailable")
        await button.set_available(False)

    await watch_name(
        source=source,
        service_name=GNOME_SCREENSAVER_SERVICE_NAME,
        on_present=attach,
        on_absent=mark_unavailable,
    )
