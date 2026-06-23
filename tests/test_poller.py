from __future__ import annotations

import asyncio

import pytest

from ha_tux.poller import AsyncPoller


def test_poller_runs_immediately_then_per_interval() -> None:
    async def scenario() -> int:
        calls = 0

        async def poll() -> None:
            nonlocal calls
            calls += 1

        poller = AsyncPoller(name="test", interval_seconds=0.01, poll=poll)
        task = asyncio.create_task(poller.run())
        await asyncio.sleep(0.035)
        _ = task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return calls

    calls = asyncio.run(scenario())

    # Immediate call plus at least two interval ticks within ~35ms at 10ms interval.
    assert calls >= 3


def test_poller_reraises_failing_iteration() -> None:
    async def scenario() -> int:
        calls = 0

        async def poll() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("boom")

        poller = AsyncPoller(name="test", interval_seconds=0.01, poll=poll)
        with pytest.raises(RuntimeError, match="boom"):
            await poller.run()
        return calls

    calls = asyncio.run(scenario())
    assert calls == 1


def test_poller_cancellation_stops_cleanly() -> None:
    async def scenario() -> bool:
        async def poll() -> None:
            return None

        poller = AsyncPoller(name="test", interval_seconds=10.0, poll=poll)
        task = asyncio.create_task(poller.run())
        await asyncio.sleep(0.01)
        _ = task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return task.cancelled()

    assert asyncio.run(scenario()) is True
