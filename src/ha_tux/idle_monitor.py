from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Final

from sdbus import (
    DbusInterfaceCommonAsync,
    dbus_method_async,
    dbus_signal_async,
)

LOGGER = logging.getLogger(__name__)

IDLE_MONITOR_SERVICE_NAME: Final = "org.gnome.Mutter.IdleMonitor"
IDLE_MONITOR_OBJECT_PATH: Final = "/org/gnome/Mutter/IdleMonitor/Core"
IDLE_MONITOR_INTERFACE: Final = "org.gnome.Mutter.IdleMonitor"

# "Input active" is true while the seat has seen input within this window. The
# threshold is baked in rather than published, so Home Assistant consumes a clean
# binary state instead of an ever-climbing idle counter.
INPUT_ACTIVE_IDLE_TIMEOUT_MS: Final = 60_000

OnInputActiveChange = Callable[[bool], Awaitable[None]]


class MutterIdleMonitorAsync(
    DbusInterfaceCommonAsync,
    interface_name=IDLE_MONITOR_INTERFACE,
):
    """Proxy for GNOME Mutter's seat idle monitor.

    ``GetIdletime`` reports milliseconds since the last seat input. The watch
    methods are edge-triggered: an idle watch fires once when idle time crosses
    its interval upward, and a user-active watch fires once when input resumes
    (then deletes itself). ``WatchFired`` carries the id of whichever watch
    tripped."""

    @dbus_method_async(result_signature="t", method_name="GetIdletime")
    async def get_idletime(self) -> int:
        raise NotImplementedError

    @dbus_method_async("t", "u", method_name="AddIdleWatch")
    async def add_idle_watch(self, interval_ms: int) -> int:
        del interval_ms
        raise NotImplementedError

    @dbus_method_async(result_signature="u", method_name="AddUserActiveWatch")
    async def add_user_active_watch(self) -> int:
        raise NotImplementedError

    @dbus_method_async("u", method_name="RemoveWatch")
    async def remove_watch(self, watch_id: int) -> None:
        del watch_id
        raise NotImplementedError

    @dbus_signal_async("u", signal_name="WatchFired")
    def watch_fired(self) -> int:
        raise NotImplementedError


def new_idle_monitor_proxy() -> MutterIdleMonitorAsync:
    return MutterIdleMonitorAsync.new_proxy(
        IDLE_MONITOR_SERVICE_NAME, IDLE_MONITOR_OBJECT_PATH
    )


class InputActiveWatcher:
    """Tracks seat-input presence via Mutter idle/active watches.

    Holds a persistent idle watch (fires on going idle) and re-arms a one-shot
    user-active watch after every idle period (fires on return). State changes
    are forwarded to ``on_change`` only on real edges, so the consumer sees two
    transitions per idle/active cycle rather than a stream."""

    def __init__(
        self,
        *,
        monitor: MutterIdleMonitorAsync,
        idle_timeout_ms: int,
        on_change: OnInputActiveChange,
    ) -> None:
        self._monitor: MutterIdleMonitorAsync = monitor
        self._idle_timeout_ms: int = idle_timeout_ms
        self._on_change: OnInputActiveChange = on_change
        self._active: bool | None = None
        self._idle_watch_id: int | None = None
        self._active_watch_id: int | None = None

    async def snapshot(self) -> bool:
        """Read the current idle time once and publish the derived active state."""
        idletime_ms = await self._monitor.get_idletime()
        active = idletime_ms < self._idle_timeout_ms
        LOGGER.debug(
            "input_active_snapshot",
            extra={"idletime_ms": idletime_ms, "active": active},
        )
        await self._emit(active)
        return active

    async def run(self) -> None:
        """Seed from the current idle time, arm watches, then track edges."""
        self._idle_watch_id = None
        self._active_watch_id = None
        active = await self.snapshot()
        self._idle_watch_id = await self._monitor.add_idle_watch(self._idle_timeout_ms)
        LOGGER.debug("input_active_idle_watch_armed", extra={"id": self._idle_watch_id})
        if not active:
            await self._arm_active_watch()
        async for watch_id in self._monitor.watch_fired.catch():
            await self._handle_watch_fired(watch_id)

    async def _handle_watch_fired(self, watch_id: int) -> None:
        if watch_id == self._idle_watch_id:
            # Idle watch is persistent and auto-re-arms; arm a fresh active watch
            # to catch the return to input.
            await self._emit(False)
            await self._arm_active_watch()
        elif watch_id == self._active_watch_id:
            # User-active watch is one-shot; it has now deleted itself.
            self._active_watch_id = None
            await self._emit(True)
        else:
            LOGGER.debug("input_active_watch_fired_unknown", extra={"id": watch_id})

    async def _arm_active_watch(self) -> None:
        if self._active_watch_id is not None:
            return
        self._active_watch_id = await self._monitor.add_user_active_watch()
        LOGGER.debug(
            "input_active_active_watch_armed", extra={"id": self._active_watch_id}
        )

    async def _emit(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        LOGGER.info("input_active_changed", extra={"active": active})
        await self._on_change(active)
