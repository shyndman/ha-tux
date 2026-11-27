from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import cast

from ha_mqtt_discoverable import DeviceInfo

from ha_tux.config import HaTuxConfig
from ha_tux.power import UPOWER_SERVICE_NAME, run_power
from ha_tux.runtime import SessionContext
from tests.fakes import FakeNameOwnerSource, InMemorySession, settle

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")
TEST_CONFIG = HaTuxConfig()


class FakeUPowerDevice:
    """In-memory ``UPowerDevice`` adapter with pushable ``(percentage, state)`` samples."""

    def __init__(self, *, percentage: float, state: int) -> None:
        self.percentage: float = percentage
        self.state: int = state
        self._events: asyncio.Queue[None] = asyncio.Queue()

    async def get_percentage(self) -> float:
        return self.percentage

    async def get_state(self) -> int:
        return self.state

    async def properties_changed(self) -> AsyncIterator[object]:
        while True:
            _ = await self._events.get()
            yield None

    async def push(self, percentage: float, state: int) -> None:
        self.percentage = percentage
        self.state = state
        await self._events.put(None)


def _ctx(session: InMemorySession) -> SessionContext:
    return SessionContext(
        session=session,
        device=DEVICE,
        hostname="testbox",
        host_prefix="testbox",
        config=TEST_CONFIG,
    )


def _states(session: InMemorySession, object_id: str) -> list[str]:
    return [
        str(msg.payload)
        for msg in session.published
        if msg.topic.endswith(f"/testbox_{object_id}/state")
    ]


def _availabilities(session: InMemorySession, unique_id: str) -> list[str]:
    avail_topic: str | None = None
    for msg in session.published:
        if msg.topic.endswith("/config") and isinstance(msg.payload, str):
            data = cast(dict[str, object], json.loads(msg.payload))
            if data.get("unique_id") == unique_id:
                avail_list = cast(list[dict[str, str]], data["availability"])
                avail_topic = avail_list[-1]["topic"]
                break

    assert avail_topic is not None
    return [str(msg.payload) for msg in session.published if msg.topic == avail_topic]


def test_power_publishes_entities_dedupes_and_handles_unknown_state() -> None:
    async def scenario() -> None:
        session = InMemorySession()
        source = FakeNameOwnerSource(initially_present=True)
        device = FakeUPowerDevice(percentage=80.4, state=5)

        task = asyncio.create_task(
            run_power(
                _ctx(session),
                name_owner_source=source,
                device_factory=lambda: device,
            )
        )
        await settle()

        assert _states(session, "battery") == ["80"]
        assert _states(session, "battery_state") == ["pending_charge"]
        assert _states(session, "ac") == ["on"]

        # Unchanged rounded percentage + state is deduped.
        await device.push(80.1, 5)
        await settle()
        assert _states(session, "battery") == ["80"]

        # Discharging turns AC off.
        await device.push(50.0, 2)
        await settle()
        assert _states(session, "battery") == ["80", "50"]
        assert _states(session, "battery_state") == ["pending_charge", "discharging"]
        assert _states(session, "ac") == ["on", "off"]

        # Unknown UPower enum falls back cleanly.
        await device.push(49.0, 99)
        await settle()
        assert _states(session, "battery_state") == [
            "pending_charge",
            "discharging",
            "unknown",
        ]
        assert _states(session, "ac") == ["on", "off", "off"]

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_power_marks_offline_on_loss_and_resyncs_on_return() -> None:
    async def scenario() -> None:
        session = InMemorySession()
        source = FakeNameOwnerSource(initially_present=False)
        devices = [FakeUPowerDevice(percentage=91.0, state=1)]

        task = asyncio.create_task(
            run_power(
                _ctx(session),
                name_owner_source=source,
                device_factory=lambda: devices[0],
            )
        )
        await settle()
        for suffix in ("battery", "battery_state", "ac"):
            assert _availabilities(session, f"ha_tux_testbox_{suffix}")[-1] == "offline"

        await source.emit(UPOWER_SERVICE_NAME, "", ":1.4")
        await settle()
        for suffix in ("battery", "battery_state", "ac"):
            assert _availabilities(session, f"ha_tux_testbox_{suffix}")[-1] == "online"
        assert _states(session, "battery") == ["91"]

        await source.emit(UPOWER_SERVICE_NAME, ":1.4", "")
        await settle()
        for suffix in ("battery", "battery_state", "ac"):
            assert _availabilities(session, f"ha_tux_testbox_{suffix}")[-1] == "offline"

        await source.emit(UPOWER_SERVICE_NAME, "", ":1.5")
        await settle()
        assert _states(session, "battery") == ["91", "91"]
        for suffix in ("battery", "battery_state", "ac"):
            assert _availabilities(session, f"ha_tux_testbox_{suffix}")[-1] == "online"

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
