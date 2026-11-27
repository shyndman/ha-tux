from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from ha_tux.dbus import watch_name
from tests.fakes import FakeNameOwnerSource

SERVICE = "org.example.Service"


def test_watch_name_subscribes_before_querying_initial_owner() -> None:
    async def scenario() -> None:
        source = FakeNameOwnerSource(initially_present=True)
        present_runs = 0
        absent_calls = 0
        started = asyncio.Event()

        async def on_present() -> None:
            nonlocal present_runs
            present_runs += 1
            started.set()
            _ = await asyncio.Event().wait()

        async def on_absent() -> None:
            nonlocal absent_calls
            absent_calls += 1

        task = asyncio.create_task(
            watch_name(
                source=source,
                service_name=SERVICE,
                on_present=on_present,
                on_absent=on_absent,
            )
        )
        _ = await asyncio.wait_for(started.wait(), timeout=2)

        assert source.has_owner_checked_after_subscribe is True
        assert present_runs == 1
        assert absent_calls == 0

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_watch_name_starts_absent_then_attaches_on_acquisition() -> None:
    async def scenario() -> None:
        source = FakeNameOwnerSource(initially_present=False)
        events: list[str] = []
        attached = asyncio.Event()

        async def on_present() -> None:
            events.append("present")
            attached.set()
            _ = await asyncio.Event().wait()

        async def on_absent() -> None:
            events.append("absent")

        task = asyncio.create_task(
            watch_name(
                source=source,
                service_name=SERVICE,
                on_present=on_present,
                on_absent=on_absent,
            )
        )
        await asyncio.sleep(0)
        assert events == ["absent"]

        await source.emit(SERVICE, "", ":1.10")
        _ = await asyncio.wait_for(attached.wait(), timeout=2)
        assert events == ["absent", "present"]

        await source.close()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_watch_name_cancels_on_loss_and_reattaches_on_return() -> None:
    async def scenario() -> None:
        source = FakeNameOwnerSource(initially_present=True)
        events: list[str] = []
        attached = asyncio.Event()
        cancelled = asyncio.Event()

        async def on_present() -> None:
            run_number = len([e for e in events if e.startswith("start:")]) + 1
            events.append(f"start:{run_number}")
            attached.set()
            try:
                _ = await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append(f"cancel:{run_number}")
                cancelled.set()
                raise

        async def on_absent() -> None:
            events.append("absent")

        task = asyncio.create_task(
            watch_name(
                source=source,
                service_name=SERVICE,
                on_present=on_present,
                on_absent=on_absent,
            )
        )
        _ = await asyncio.wait_for(attached.wait(), timeout=2)
        attached.clear()

        await source.emit("org.other.Service", ":1.1", "")
        await asyncio.sleep(0)
        assert events == ["start:1"]

        await source.emit(SERVICE, ":1.1", "")
        _ = await asyncio.wait_for(cancelled.wait(), timeout=2)
        await asyncio.sleep(0)
        assert events == ["start:1", "cancel:1", "absent"]

        await source.emit(SERVICE, "", ":1.2")
        _ = await asyncio.wait_for(attached.wait(), timeout=2)
        assert events == ["start:1", "cancel:1", "absent", "start:2"]

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_watch_name_replaces_owner_without_absent_flap() -> None:
    async def scenario() -> None:
        source = FakeNameOwnerSource(initially_present=True)
        events: list[str] = []
        attached = asyncio.Event()

        async def on_present() -> None:
            run_number = len([e for e in events if e.startswith("start:")]) + 1
            events.append(f"start:{run_number}")
            attached.set()
            try:
                _ = await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append(f"cancel:{run_number}")
                raise

        async def on_absent() -> None:
            events.append("absent")

        task = asyncio.create_task(
            watch_name(
                source=source,
                service_name=SERVICE,
                on_present=on_present,
                on_absent=on_absent,
            )
        )
        _ = await asyncio.wait_for(attached.wait(), timeout=2)
        attached.clear()

        await source.emit(SERVICE, ":1.1", ":1.2")
        _ = await asyncio.wait_for(attached.wait(), timeout=2)
        assert events == ["start:1", "cancel:1", "start:2"]

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_watch_name_keeps_watching_after_passive_on_present_returns() -> None:
    async def scenario() -> None:
        source = FakeNameOwnerSource(initially_present=False)
        events: list[str] = []

        async def on_present() -> None:
            events.append("available")

        async def on_absent() -> None:
            events.append("unavailable")

        task = asyncio.create_task(
            watch_name(
                source=source,
                service_name=SERVICE,
                on_present=on_present,
                on_absent=on_absent,
            )
        )
        await asyncio.sleep(0)
        await source.emit(SERVICE, "", ":1.5")
        await asyncio.sleep(0)
        await source.emit(SERVICE, ":1.5", "")
        await asyncio.sleep(0)
        await source.close()
        await asyncio.wait_for(task, timeout=2)

        assert events == ["unavailable", "available", "unavailable"]

    asyncio.run(scenario())


def test_watch_name_propagates_on_present_exception_immediately() -> None:
    async def scenario() -> None:
        source = FakeNameOwnerSource(initially_present=True)

        async def failing_present() -> None:
            raise RuntimeError("boom inside on_present")

        async def on_absent() -> None:
            pass

        with pytest.raises(RuntimeError, match="boom inside on_present"):
            await asyncio.wait_for(
                watch_name(
                    source=source,
                    service_name=SERVICE,
                    on_present=failing_present,
                    on_absent=on_absent,
                ),
                timeout=2,
            )

    asyncio.run(scenario())
