"""Asyncio task lifecycle and iterator scheduling helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

__all__ = [
    "cancel_task",
    "schedule_next",
]


async def cancel_task(task: asyncio.Task[object] | None) -> None:
    """Cancel ``task`` if still running and await its completion cleanly."""
    if task is None:
        return

    if not task.done():
        _ = task.cancel()

    with suppress(asyncio.CancelledError, StopAsyncIteration):
        await task


def schedule_next[T](
    iterator: AsyncIterator[T],
    *,
    name: str | None = None,
) -> asyncio.Task[T]:
    """Schedule a task that awaits the next item from ``iterator``."""

    async def read_next() -> T:
        return await anext(iterator)

    return asyncio.create_task(read_next(), name=name)
