"""Shared test doubles for D-Bus name ownership across feature test suites."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from ha_tux.dbus import NameOwnerChanged


class FakeNameOwnerSource:
    """In-memory ``NameOwnerSource`` adapter with scripted initial owner and transitions."""

    def __init__(self, *, initially_present: bool = True) -> None:
        self.initially_present: bool = initially_present
        self.subscribed: bool = False
        self.has_owner_checked_after_subscribe: bool = False
        self._queue: asyncio.Queue[NameOwnerChanged | None] = asyncio.Queue()

    async def name_has_owner(self, service_name: str) -> bool:
        del service_name
        self.has_owner_checked_after_subscribe = self.subscribed
        return self.initially_present

    @asynccontextmanager
    async def open_name_owner_changes(
        self,
    ) -> AsyncGenerator[AsyncIterator[NameOwnerChanged]]:
        self.subscribed = True

        async def iterate() -> AsyncIterator[NameOwnerChanged]:
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                yield item

        try:
            yield iterate()
        finally:
            self.subscribed = False

    async def emit(self, name: str, old_owner: str, new_owner: str) -> None:
        """Push a ``NameOwnerChanged`` tuple onto the active signal stream."""
        await self._queue.put((name, old_owner, new_owner))

    async def close(self) -> None:
        """Terminate the signal stream cleanly."""
        await self._queue.put(None)
