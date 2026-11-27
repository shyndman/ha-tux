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
from contextlib import AbstractAsyncContextManager, asynccontextmanager, closing
from typing import Final, Protocol, cast

from libsh import get_logger
from sdbus import (
    get_default_bus,
    sd_bus_open_system,
)
from sdbus.sd_bus_internals import SdBus, SdBusMessage
from sdbus_async.dbus_daemon import FreedesktopDbus

from ha_tux.foundation.tasks import cancel_task, schedule_next

LOGGER = get_logger(__name__)

DBUS_DAEMON_SERVICE_NAME: Final = "org.freedesktop.DBus"
DBUS_DAEMON_OBJECT_PATH: Final = "/org/freedesktop/DBus"
NAME_OWNER_CHANGED_SIGNAL: Final = "NameOwnerChanged"

NameOwnerChanged = tuple[str, str, str]
OnPresent = Callable[[], Coroutine[object, object, None]]
OnAbsent = Callable[[], Awaitable[None]]


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
        self._daemon: FreedesktopDbus = FreedesktopDbus(self._bus)

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
            "D-Bus name owner lost",
            service_name=service_name,
            old_owner=old_owner,
        )
        await cancel_task(active_task)
        await on_absent()
        return None

    message = (
        "D-Bus name owner acquired" if old_owner == "" else "D-Bus name owner replaced"
    )
    LOGGER.info(
        message,
        service_name=service_name,
        old_owner=old_owner,
        new_owner=new_owner,
    )
    await cancel_task(active_task)
    return asyncio.create_task(on_present(), name=f"dbus:{service_name}")


def _reap_attachment(
    active_task: asyncio.Task[None] | None,
    done: set[asyncio.Task[object]],
    service_name: str,
) -> asyncio.Task[None] | None:
    """Propagate failures if the active attachment finished; retain it if still running."""
    if active_task is None or active_task not in done:
        return active_task

    # Propagate unhandled feature/transport exceptions (such as MqttError) immediately
    # instead of letting them sit unobserved until the next ownership signal; passive
    # features that return cleanly remain watched until the owner drops.
    active_task.result()
    LOGGER.debug(
        "D-Bus name attachment finished cleanly; continuing to watch ownership",
        service_name=service_name,
    )
    return None


async def _wait_for_race(
    next_signal: asyncio.Task[NameOwnerChanged],
    active_task: asyncio.Task[None] | None,
    service_name: str,
) -> tuple[bool, asyncio.Task[None] | None]:
    """Wait until either the signal read or active attachment settles."""
    waiters: list[asyncio.Task[object]] = (
        [next_signal, active_task] if active_task is not None else [next_signal]
    )
    done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    updated_active = _reap_attachment(active_task, done, service_name)
    return next_signal in done, updated_active


async def _consume_events(
    *,
    changes: AsyncIterator[NameOwnerChanged],
    service_name: str,
    active_task: asyncio.Task[None] | None,
    on_present: OnPresent,
    on_absent: OnAbsent,
) -> None:
    """Coordinate the D-Bus ownership signal stream alongside the active attachment task.

    Two concurrent systems run independently while a D-Bus name is watched:

    1. The D-Bus daemon's ``NameOwnerChanged`` signal stream (``next_signal``), which
       wakes whenever any bus name is acquired, replaced, or dropped.
    2. The feature's active attachment task (``active_task``, running ``on_present()``),
       which holds D-Bus watches/proxies and publishes state to Home Assistant.

    A plain ``async for`` loop over ``changes`` would block waiting on D-Bus signals and
    leave an unhandled exception from ``active_task`` (for instance an ``aiomqtt.MqttError``
    when the broker drops) undetected until some unrelated bus name changed. Racing both
    tasks with ``asyncio.wait(..., return_when=FIRST_COMPLETED)`` lets either system wake
    this loop immediately: attachment crashes re-raise right away to unwind Session
    Runtime, while signal arrivals tear down and re-create the attachment as ownership
    transitions.
    """
    signal_task_name = f"dbus_signal:{service_name}"
    next_signal: asyncio.Task[NameOwnerChanged] | None = None

    try:
        next_signal = schedule_next(changes, name=signal_task_name)

        while True:
            signal_ready, active_task = await _wait_for_race(
                next_signal, active_task, service_name
            )
            if not signal_ready:
                continue

            changed_name, old_owner, new_owner = next_signal.result()
            next_signal = schedule_next(changes, name=signal_task_name)
            if changed_name == service_name:
                active_task = await _apply_owner_change(
                    service_name=service_name,
                    old_owner=old_owner,
                    new_owner=new_owner,
                    active_task=active_task,
                    on_present=on_present,
                    on_absent=on_absent,
                )
    except StopAsyncIteration:
        LOGGER.debug("D-Bus name-owner signal stream ended", service_name=service_name)
    finally:
        await cancel_task(next_signal)
        await cancel_task(active_task)


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
            LOGGER.info(
                "D-Bus name already has an owner at startup", service_name=service_name
            )
            active_task = asyncio.create_task(on_present(), name=f"dbus:{service_name}")
        else:
            LOGGER.info("D-Bus name has no owner at startup", service_name=service_name)
            await on_absent()

        await _consume_events(
            changes=changes,
            service_name=service_name,
            active_task=active_task,
            on_present=on_present,
            on_absent=on_absent,
        )
