"""Shared D-Bus name-ownership watching for desktop and system services.

Provides the ``NameOwnerSource`` seam, an ``sdbus`` adapter that subscribes to
``NameOwnerChanged`` before querying ``NameHasOwner`` so name transitions are
never missed, and ``watch_name`` to attach, tear down, and re-attach features
across owner restarts.
"""

from __future__ import annotations

import asyncio
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
)
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
    closing,
    suppress,
)
from typing import Final, Protocol, cast

from libsh import get_logger
from sdbus import (
    DbusInterfaceCommonAsync,
    dbus_method_async,
    dbus_signal_async,
    get_default_bus,
    sd_bus_open_system,
)
from sdbus.sd_bus_internals import SdBus, SdBusMessage

LOGGER = get_logger(__name__)

DBUS_DAEMON_SERVICE_NAME: Final = "org.freedesktop.DBus"
DBUS_DAEMON_OBJECT_PATH: Final = "/org/freedesktop/DBus"
NAME_OWNER_CHANGED_SIGNAL: Final = "NameOwnerChanged"

NameOwnerChanged = tuple[str, str, str]
OnPresent = Callable[[], Coroutine[object, object, None]]
OnAbsent = Callable[[], Awaitable[None]]


class DbusDaemonAsync(
    DbusInterfaceCommonAsync,
    interface_name=DBUS_DAEMON_SERVICE_NAME,
):
    """Proxy for org.freedesktop.DBus ownership queries and change signals."""

    @dbus_method_async("s", "b", method_name="NameHasOwner")
    async def name_has_owner(self, name: str) -> bool:
        """Return whether ``name`` currently has an active bus owner."""
        del name
        raise NotImplementedError

    @dbus_signal_async("sss", signal_name=NAME_OWNER_CHANGED_SIGNAL)
    def name_owner_changed(self) -> NameOwnerChanged:
        """Signal emitted with ``(name, old_owner, new_owner)`` on ownership change."""
        raise NotImplementedError


def new_dbus_daemon_proxy(bus: SdBus | None = None) -> DbusDaemonAsync:
    """Create a ``DbusDaemonAsync`` proxy bound to ``bus`` or the session default."""
    resolved_bus = bus if bus is not None else get_default_bus()
    return DbusDaemonAsync.new_proxy(
        DBUS_DAEMON_SERVICE_NAME,
        DBUS_DAEMON_OBJECT_PATH,
        bus=resolved_bus,
    )


class NameOwnerSource(Protocol):
    """Seam for querying and streaming D-Bus name ownership transitions."""

    async def name_has_owner(self, service_name: str) -> bool:
        """Return True if ``service_name`` currently has a D-Bus owner."""
        ...

    def open_name_owner_changes(
        self,
    ) -> AbstractAsyncContextManager[AsyncIterator[NameOwnerChanged]]:
        """Subscribe to ``NameOwnerChanged`` before yielding the signal stream."""
        ...


class SdbusNameOwnerSource:
    """Live ``NameOwnerSource`` adapter backed by ``python-sdbus``."""

    def __init__(self, bus: SdBus | None = None) -> None:
        """Bind ownership queries and signal matches to ``bus``."""
        self._bus: SdBus = bus if bus is not None else get_default_bus()
        self._daemon: DbusDaemonAsync = new_dbus_daemon_proxy(self._bus)

    async def name_has_owner(self, service_name: str) -> bool:
        """Query ``org.freedesktop.DBus.NameHasOwner`` on the bound bus."""
        return await self._daemon.name_has_owner(service_name)

    @asynccontextmanager
    async def open_name_owner_changes(
        self,
    ) -> AsyncGenerator[AsyncIterator[NameOwnerChanged]]:
        """Install the ``NameOwnerChanged`` match rule, then stream signal tuples."""
        queue: asyncio.Queue[SdBusMessage] = asyncio.Queue()
        match_slot = await self._bus.match_signal_async(
            DBUS_DAEMON_SERVICE_NAME,
            DBUS_DAEMON_OBJECT_PATH,
            DBUS_DAEMON_SERVICE_NAME,
            NAME_OWNER_CHANGED_SIGNAL,
            queue.put_nowait,
        )

        async def iterate() -> AsyncIterator[NameOwnerChanged]:
            while True:
                message = await queue.get()
                yield cast(NameOwnerChanged, message.get_contents())

        with closing(match_slot):
            yield iterate()


def session_name_owner_source() -> NameOwnerSource:
    """Create a ``NameOwnerSource`` on the default user session bus."""
    return SdbusNameOwnerSource()


def system_name_owner_source() -> NameOwnerSource:
    """Create a ``NameOwnerSource`` on the host system bus."""
    return SdbusNameOwnerSource(sd_bus_open_system())


async def _cancel_task(task: asyncio.Task[object] | None) -> None:
    """Cancel ``task`` if running and wait until it settles."""
    if task is None:
        return

    if not task.done():
        _ = task.cancel()

    with suppress(asyncio.CancelledError, StopAsyncIteration):
        await task


async def _apply_owner_change(
    *,
    service_name: str,
    old_owner: str,
    new_owner: str,
    active_task: asyncio.Task[None] | None,
    on_present: OnPresent,
    on_absent: OnAbsent,
) -> asyncio.Task[None] | None:
    """Apply a ``NameOwnerChanged`` transition and return the updated attachment task."""
    if new_owner == "":
        LOGGER.info(
            "dbus_name_owner_lost",
            service_name=service_name,
            old_owner=old_owner,
        )
        await _cancel_task(active_task)
        await on_absent()
        return None

    event = (
        "dbus_name_owner_acquired" if old_owner == "" else "dbus_name_owner_replaced"
    )
    LOGGER.info(
        event,
        service_name=service_name,
        old_owner=old_owner,
        new_owner=new_owner,
    )
    await _cancel_task(active_task)
    return asyncio.create_task(on_present(), name=f"dbus:{service_name}")


def _schedule_signal(
    changes: AsyncIterator[NameOwnerChanged], service_name: str
) -> asyncio.Task[NameOwnerChanged]:
    """Schedule the next ``NameOwnerChanged`` read task."""

    async def read_next() -> NameOwnerChanged:
        return await anext(changes)

    return asyncio.create_task(read_next(), name=f"dbus_signal:{service_name}")


async def _consume_events(
    *,
    changes: AsyncIterator[NameOwnerChanged],
    service_name: str,
    active_task: asyncio.Task[None] | None,
    on_present: OnPresent,
    on_absent: OnAbsent,
) -> None:
    """Race incoming ownership signals against the active attachment task."""
    next_signal: asyncio.Task[NameOwnerChanged] | None = None

    try:
        next_signal = _schedule_signal(changes, service_name)

        while True:
            waiters: list[asyncio.Task[object]] = (
                [next_signal, active_task] if active_task is not None else [next_signal]
            )
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)

            if active_task is not None and active_task in done:
                finished, active_task = active_task, None
                # Surface unhandled feature failures immediately; passive features
                # that return cleanly stay attached until the owner drops.
                finished.result()
                LOGGER.debug(
                    "dbus_name_attachment_completed", service_name=service_name
                )

            if next_signal not in done:
                continue

            try:
                changed_name, old_owner, new_owner = next_signal.result()
            except StopAsyncIteration:
                LOGGER.debug("dbus_name_signal_stream_ended", service_name=service_name)
                return

            next_signal = _schedule_signal(changes, service_name)
            if changed_name != service_name:
                continue

            active_task = await _apply_owner_change(
                service_name=service_name,
                old_owner=old_owner,
                new_owner=new_owner,
                active_task=active_task,
                on_present=on_present,
                on_absent=on_absent,
            )
    finally:
        await _cancel_task(next_signal)
        await _cancel_task(active_task)


async def watch_name(
    *,
    source: NameOwnerSource,
    service_name: str,
    on_present: OnPresent,
    on_absent: OnAbsent,
) -> None:
    """Track ``service_name`` ownership and run ``on_present`` while it is owned.

    Subscribes to ``NameOwnerChanged`` before calling ``NameHasOwner`` so transitions
    around startup are never dropped. Unhandled exceptions raised by ``on_present``
    propagate immediately to the caller.
    """
    async with source.open_name_owner_changes() as changes:
        active_task: asyncio.Task[None] | None = None

        if await source.name_has_owner(service_name):
            LOGGER.info("dbus_name_owner_present", service_name=service_name)
            active_task = asyncio.create_task(on_present(), name=f"dbus:{service_name}")
        else:
            LOGGER.info("dbus_name_owner_absent", service_name=service_name)
            await on_absent()

        await _consume_events(
            changes=changes,
            service_name=service_name,
            active_task=active_task,
            on_present=on_present,
            on_absent=on_absent,
        )
