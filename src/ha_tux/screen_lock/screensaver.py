from __future__ import annotations

from typing import Final

from sdbus import DbusInterfaceCommonAsync, dbus_method_async

GNOME_SCREENSAVER_SERVICE_NAME: Final = "org.gnome.ScreenSaver"
GNOME_SCREENSAVER_OBJECT_PATH: Final = "/org/gnome/ScreenSaver"
GNOME_SCREENSAVER_INTERFACE: Final = "org.gnome.ScreenSaver"


class GnomeScreenSaverAsync(
    DbusInterfaceCommonAsync,
    interface_name=GNOME_SCREENSAVER_INTERFACE,
):
    """Proxy for GNOME's screensaver. Only the lock action is used."""

    @dbus_method_async(method_name="Lock")
    async def lock(self) -> None:
        raise NotImplementedError


def new_screensaver_proxy() -> GnomeScreenSaverAsync:
    return GnomeScreenSaverAsync.new_proxy(
        GNOME_SCREENSAVER_SERVICE_NAME, GNOME_SCREENSAVER_OBJECT_PATH
    )
