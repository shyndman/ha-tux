"""Input-active presence feature backed by Mutter's D-Bus idle monitor.

Tracks GNOME Mutter ownership via ``ha_tux.dbus.watch_name``, marking the Home
Assistant occupancy binary sensor unavailable when Mutter goes away and taking a
fresh snapshot with newly armed watches when it returns.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Final, Protocol

from ha_mqtt_discoverable.sensors import BinarySensor, BinarySensorInfo
from libsh import get_logger
from sdbus import (
    DbusInterfaceCommonAsync,
    dbus_method_async,
    dbus_signal_async,
)

from ha_tux.dbus import NameOwnerSource, session_name_owner_source, watch_name
from ha_tux.foundation.time import seconds_to_milliseconds
from ha_tux.runtime import SessionContext

LOGGER = get_logger(__name__)

IDLE_MONITOR_SERVICE_NAME: Final = "org.gnome.Mutter.IdleMonitor"
IDLE_MONITOR_OBJECT_PATH: Final = "/org/gnome/Mutter/IdleMonitor/Core"
IDLE_MONITOR_INTERFACE: Final = "org.gnome.Mutter.IdleMonitor"

INPUT_ACTIVE_OBJECT_ID: Final = "input_active"
INPUT_ACTIVE_NAME: Final = "Input Active"
INPUT_ACTIVE_DEVICE_CLASS: Final = "occupancy"


class MutterIdleMonitorAsync(
    DbusInterfaceCommonAsync,
    interface_name=IDLE_MONITOR_INTERFACE,
):
    """Proxy for GNOME Mutter's seat idle monitor."""

    @dbus_method_async(result_signature="t", method_name="GetIdletime")
    async def get_idletime(self) -> int:
        """Return milliseconds since the last seat input event."""
        raise NotImplementedError

    @dbus_method_async("t", "u", method_name="AddIdleWatch")
    async def add_idle_watch(self, interval_ms: int) -> int:
        """Arm a persistent idle watch firing at ``interval_ms``."""
        del interval_ms
        raise NotImplementedError

    @dbus_method_async(result_signature="u", method_name="AddUserActiveWatch")
    async def add_user_active_watch(self) -> int:
        """Arm a one-shot watch firing on the next seat input event."""
        raise NotImplementedError

    @dbus_method_async("u", method_name="RemoveWatch")
    async def remove_watch(self, watch_id: int) -> None:
        """Remove an existing idle or user-active watch by id."""
        del watch_id
        raise NotImplementedError

    @dbus_signal_async("u", signal_name="WatchFired")
    def watch_fired(self) -> int:
        """Signal carrying the id of whichever watch fired."""
        raise NotImplementedError


class IdleMonitor(Protocol):
    """Seam between Mutter idle monitoring and the Home Assistant sensor."""

    async def get_idletime(self) -> int:
        """Return milliseconds since last user input."""
        ...

    async def add_idle_watch(self, interval_ms: int) -> int:
        """Register a persistent idle watch and return its watch id."""
        ...

    async def add_user_active_watch(self) -> int:
        """Register a one-shot active watch and return its watch id."""
        ...

    def watch_fired(self) -> AsyncIterator[int]:
        """Yield fired watch ids as edges occur."""
        ...


class SdbusIdleMonitor:
    """Live ``IdleMonitor`` adapter over ``MutterIdleMonitorAsync``."""

    def __init__(self) -> None:
        """Bind a fresh D-Bus proxy to Mutter's core idle monitor."""
        self._proxy: MutterIdleMonitorAsync = MutterIdleMonitorAsync.new_proxy(
            IDLE_MONITOR_SERVICE_NAME, IDLE_MONITOR_OBJECT_PATH
        )

    async def get_idletime(self) -> int:
        """Query Mutter's current idle duration in milliseconds."""
        return await self._proxy.get_idletime()

    async def add_idle_watch(self, interval_ms: int) -> int:
        """Arm Mutter's persistent idle watch."""
        return await self._proxy.add_idle_watch(interval_ms)

    async def add_user_active_watch(self) -> int:
        """Arm Mutter's one-shot user-active watch."""
        return await self._proxy.add_user_active_watch()

    def watch_fired(self) -> AsyncIterator[int]:
        """Subscribe to Mutter's ``WatchFired`` signal stream."""
        return self._proxy.watch_fired.catch()


class _IdleTracker:
    """Publishes edge-triggered occupancy updates for one owned Mutter instance."""

    def __init__(
        self, *, sensor: BinarySensor, monitor: IdleMonitor, idle_timeout_ms: int
    ) -> None:
        self._sensor: BinarySensor = sensor
        self._monitor: IdleMonitor = monitor
        self._idle_timeout_ms: int = idle_timeout_ms
        self._active: bool | None = None
        self._idle_watch_id: int | None = None
        self._active_watch_id: int | None = None

    async def run(self) -> None:
        """Read initial idle state, arm Mutter watches, and follow fired edges."""
        idletime_ms = await self._monitor.get_idletime()
        active = idletime_ms < self._idle_timeout_ms
        LOGGER.debug(
            "Read initial Mutter idle snapshot", idletime_ms=idletime_ms, active=active
        )

        await self._emit(active)
        self._idle_watch_id = await self._monitor.add_idle_watch(self._idle_timeout_ms)
        LOGGER.debug("Armed Mutter idle watch", watch_id=self._idle_watch_id)

        if not active:
            await self._arm_active_watch()

        async for watch_id in self._monitor.watch_fired():
            await self._handle_watch_fired(watch_id)

    async def _handle_watch_fired(self, watch_id: int) -> None:
        if watch_id == self._idle_watch_id:
            await self._emit(False)
            await self._arm_active_watch()
        elif watch_id == self._active_watch_id:
            # User-active watch is one-shot and deletes itself upon firing.
            self._active_watch_id = None
            await self._emit(True)
        else:
            LOGGER.debug("Ignoring unrecognized Mutter watch id", watch_id=watch_id)

    async def _arm_active_watch(self) -> None:
        if self._active_watch_id is not None:
            return

        self._active_watch_id = await self._monitor.add_user_active_watch()
        LOGGER.debug("Armed Mutter user-active watch", watch_id=self._active_watch_id)

    async def _emit(self, active: bool) -> None:
        if active == self._active:
            return

        self._active = active
        LOGGER.info("Seat input activity changed", active=active)
        await self._sensor.set_available(True)
        if active:
            await self._sensor.on()
        else:
            await self._sensor.off()


async def run_input_active(
    ctx: SessionContext,
    *,
    name_owner_source: NameOwnerSource | None = None,
    monitor_factory: Callable[[], IdleMonitor] = SdbusIdleMonitor,
) -> None:
    """Supervise seat-input occupancy reporting across Mutter D-Bus restarts."""
    info = BinarySensorInfo(
        device=ctx.device,
        unique_id=f"ha_tux_{ctx.host_prefix}_{INPUT_ACTIVE_OBJECT_ID}",
        object_id=f"{ctx.host_prefix}_{INPUT_ACTIVE_OBJECT_ID}",
        name=INPUT_ACTIVE_NAME,
        device_class=INPUT_ACTIVE_DEVICE_CLASS,
    )
    sensor = BinarySensor(ctx.session, info)
    idle_timeout_ms = seconds_to_milliseconds(
        ctx.config.input_active.idle_timeout_seconds
    )
    source = (
        name_owner_source
        if name_owner_source is not None
        else session_name_owner_source()
    )

    async def attach() -> None:
        tracker = _IdleTracker(
            sensor=sensor,
            monitor=monitor_factory(),
            idle_timeout_ms=idle_timeout_ms,
        )
        await tracker.run()

    async def mark_unavailable() -> None:
        LOGGER.info("Mutter idle monitor unavailable; marking input sensor offline")
        await sensor.set_available(False)

    await watch_name(
        source=source,
        service_name=IDLE_MONITOR_SERVICE_NAME,
        on_present=attach,
        on_absent=mark_unavailable,
    )
