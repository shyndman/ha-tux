from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import cast

from ha_mqtt_discoverable import DeviceInfo

from ha_tux.config import HaTuxConfig
from ha_tux.input_active import IDLE_MONITOR_SERVICE_NAME, run_input_active
from ha_tux.runtime import SessionContext
from tests.fakes import FakeNameOwnerSource, InMemorySession, settle

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")
TEST_CONFIG = HaTuxConfig()
TIMEOUT_MS = 60_000


class FakeIdleMonitor:
    """In-memory ``IdleMonitor`` adapter with a controllable ``WatchFired`` queue."""

    def __init__(self, *, idletime_ms: int) -> None:
        self.idletime_ms: int = idletime_ms
        self.idle_intervals: list[int] = []
        self.active_watch_count: int = 0
        self._next_id: int = 1
        self._fires: asyncio.Queue[int | None] = asyncio.Queue()

    async def get_idletime(self) -> int:
        return self.idletime_ms

    async def add_idle_watch(self, interval_ms: int) -> int:
        self.idle_intervals.append(interval_ms)
        watch_id = self._next_id
        self._next_id += 1
        return watch_id

    async def add_user_active_watch(self) -> int:
        self.active_watch_count += 1
        watch_id = self._next_id
        self._next_id += 1
        return watch_id

    async def watch_fired(self) -> AsyncIterator[int]:
        while True:
            watch_id = await self._fires.get()
            if watch_id is None:
                return
            yield watch_id

    async def fire(self, watch_id: int) -> None:
        await self._fires.put(watch_id)


def _ctx(session: InMemorySession) -> SessionContext:
    return SessionContext(
        session=session,
        device=DEVICE,
        hostname="testbox",
        host_prefix="testbox",
        config=TEST_CONFIG,
    )


def _states(session: InMemorySession) -> list[str]:
    return [
        str(msg.payload)
        for msg in session.published
        if msg.topic.endswith("/testbox_input_active/state")
    ]


def _availabilities(session: InMemorySession) -> list[str]:
    avail_topics = {
        cast(dict[str, str], json.loads(msg.payload)["availability"][-1])["topic"]
        for msg in session.published
        if msg.topic.endswith("/config")
        and isinstance(msg.payload, str)
        and "input_active" in msg.topic
    }
    assert avail_topics
    topic = next(iter(avail_topics))
    return [str(msg.payload) for msg in session.published if msg.topic == topic]


def _discovery_config(session: InMemorySession) -> dict[str, object]:
    for msg in session.published:
        if msg.topic.endswith("/config") and isinstance(msg.payload, str):
            data = cast(dict[str, object], json.loads(msg.payload))
            if data.get("unique_id") == "ha_tux_testbox_input_active":
                return data
    raise AssertionError("No input_active discovery config published")


def test_input_active_publishes_discovery_and_tracks_edges() -> None:
    async def scenario() -> None:
        session = InMemorySession()
        source = FakeNameOwnerSource(initially_present=True)
        monitor = FakeIdleMonitor(idletime_ms=1_000)

        task = asyncio.create_task(
            run_input_active(
                _ctx(session),
                name_owner_source=source,
                monitor_factory=lambda: monitor,
            )
        )
        await settle()

        config = _discovery_config(session)
        assert config["device_class"] == "occupancy"
        assert config["name"] == "Input Active"
        assert config["default_entity_id"] == "binary_sensor.testbox_input_active"
        assert monitor.idle_intervals == [TIMEOUT_MS]
        assert _states(session) == ["on"]

        # Duplicate idle fires collapse to one transition and one active watch.
        await monitor.fire(1)
        await monitor.fire(1)
        await settle()
        assert monitor.active_watch_count == 1
        assert _states(session) == ["on", "off"]

        # Active watch (id 2) fires when input resumes.
        await monitor.fire(2)
        await settle()
        assert _states(session) == ["on", "off", "on"]

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_input_active_inactive_seed_arms_user_active_watch_immediately() -> None:
    async def scenario() -> None:
        session = InMemorySession()
        source = FakeNameOwnerSource(initially_present=True)
        monitor = FakeIdleMonitor(idletime_ms=TIMEOUT_MS)

        task = asyncio.create_task(
            run_input_active(
                _ctx(session),
                name_owner_source=source,
                monitor_factory=lambda: monitor,
            )
        )
        await settle()

        assert _states(session) == ["off"]
        assert monitor.idle_intervals == [TIMEOUT_MS]
        assert monitor.active_watch_count == 1

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_input_active_recovers_after_dbus_owner_loss_and_return() -> None:
    async def scenario() -> None:
        session = InMemorySession()
        source = FakeNameOwnerSource(initially_present=True)
        monitors = [
            FakeIdleMonitor(idletime_ms=1_000),
            FakeIdleMonitor(idletime_ms=120_000),
        ]
        created: list[FakeIdleMonitor] = []

        def factory() -> FakeIdleMonitor:
            mon = monitors[len(created)]
            created.append(mon)
            return mon

        task = asyncio.create_task(
            run_input_active(
                _ctx(session),
                name_owner_source=source,
                monitor_factory=factory,
            )
        )
        await settle()
        assert len(created) == 1
        assert _states(session) == ["on"]

        await source.emit(IDLE_MONITOR_SERVICE_NAME, ":1.1", "")
        await settle()
        assert _availabilities(session)[-1] == "offline"

        await source.emit(IDLE_MONITOR_SERVICE_NAME, "", ":1.2")
        await settle()
        assert len(created) == 2
        assert created[1].idle_intervals == [TIMEOUT_MS]
        assert created[1].active_watch_count == 1
        assert _availabilities(session)[-1] == "online"
        assert _states(session) == ["on", "off"]

        # New watch on replacement monitor still drives updates.
        await created[1].fire(2)
        await settle()
        assert _states(session) == ["on", "off", "on"]

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
