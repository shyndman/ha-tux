from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from ha_tux.foundation.tasks import cancel_task, schedule_next


def test_cancel_task_handles_none_and_running_tasks() -> None:
    async def scenario() -> None:
        await cancel_task(None)

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def waiter() -> None:
            started.set()
            try:
                _ = await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task: asyncio.Task[None] = asyncio.create_task(waiter())
        _ = await started.wait()

        await cancel_task(task)
        assert cancelled.is_set() is True
        assert task.cancelled() is True

    asyncio.run(scenario())


def test_cancel_task_suppresses_stop_async_iteration() -> None:
    async def scenario() -> None:
        async def empty() -> AsyncIterator[int]:
            for item in ():
                yield item

        task = schedule_next(empty(), name="empty-next")
        await asyncio.sleep(0)
        assert task.done() is True

        await cancel_task(task)

    asyncio.run(scenario())


def test_schedule_next_yields_items_and_raises_stop_async_iteration() -> None:
    async def scenario() -> None:
        async def items() -> AsyncIterator[str]:
            yield "first"
            yield "second"

        iterator = items()
        first_task = schedule_next(iterator, name="next-item")
        assert first_task.get_name() == "next-item"
        assert await first_task == "first"

        second_task = schedule_next(iterator)
        assert await second_task == "second"

        exhausted_task = schedule_next(iterator)
        with pytest.raises(StopAsyncIteration):
            _ = await exhausted_task

    asyncio.run(scenario())
