from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import cast

from ha_mqtt_discoverable import DeviceInfo

from ha_tux.config import HaTuxConfig
from ha_tux.runtime import SessionContext
from ha_tux.screen_lock import GNOME_SCREENSAVER_SERVICE_NAME, run_screen_lock
from tests.fakes import FakeNameOwnerSource, InMemorySession, settle

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")
TEST_CONFIG = HaTuxConfig()


class FakeScreenSaver:
    def __init__(self, error: Exception | None = None) -> None:
        self.locks: int = 0
        self._error: Exception | None = error

    async def lock(self) -> None:
        self.locks += 1
        if self._error is not None:
            raise self._error


def _ctx(session: InMemorySession) -> SessionContext:
    return SessionContext(
        session=session,
        device=DEVICE,
        hostname="testbox",
        host_prefix="testbox",
        config=TEST_CONFIG,
    )


def _availabilities(session: InMemorySession) -> list[str]:
    avail_topic: str | None = None
    for msg in session.published:
        if msg.topic.endswith("/config") and isinstance(msg.payload, str):
            data = cast(dict[str, object], json.loads(msg.payload))
            if data.get("unique_id") == "ha_tux_testbox_lock":
                avail_list = cast(list[dict[str, str]], data["availability"])
                avail_topic = avail_list[-1]["topic"]
                break

    assert avail_topic is not None
    return [str(msg.payload) for msg in session.published if msg.topic == avail_topic]


def test_screen_lock_triggers_lock_and_isolates_failure() -> None:
    async def scenario() -> None:
        session = InMemorySession()
        source = FakeNameOwnerSource(initially_present=True)
        proxies = [
            FakeScreenSaver(),
            FakeScreenSaver(error=RuntimeError("no session")),
        ]
        created: list[FakeScreenSaver] = []

        def factory() -> FakeScreenSaver:
            proxy = proxies[len(created)]
            created.append(proxy)
            return proxy

        task = asyncio.create_task(
            run_screen_lock(
                _ctx(session),
                name_owner_source=source,
                screensaver_factory=factory,
            )
        )
        await settle()

        topic = session.command_topics[0]
        await session.deliver(topic)
        assert created[0].locks == 1

        # Owner replacement swaps in a fresh proxy; lock failure does not crash.
        await source.emit(GNOME_SCREENSAVER_SERVICE_NAME, ":1.1", ":1.2")
        await settle()
        assert len(created) == 2

        await session.deliver(topic)
        assert created[1].locks == 1
        assert not task.done()

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_screen_lock_tracks_gnome_screensaver_availability() -> None:
    async def scenario() -> None:
        session = InMemorySession()
        source = FakeNameOwnerSource(initially_present=False)
        created: list[FakeScreenSaver] = []

        def factory() -> FakeScreenSaver:
            proxy = FakeScreenSaver()
            created.append(proxy)
            return proxy

        task = asyncio.create_task(
            run_screen_lock(
                _ctx(session),
                name_owner_source=source,
                screensaver_factory=factory,
            )
        )
        await settle()
        assert _availabilities(session)[-1] == "offline"

        # Pressing while GNOME ScreenSaver is absent does nothing.
        topic = session.command_topics[0]
        await session.deliver(topic)
        assert created == []

        await source.emit(GNOME_SCREENSAVER_SERVICE_NAME, "", ":1.9")
        await settle()
        assert _availabilities(session)[-1] == "online"

        await session.deliver(topic)
        assert created[0].locks == 1

        await source.emit(GNOME_SCREENSAVER_SERVICE_NAME, ":1.9", "")
        await settle()
        assert _availabilities(session)[-1] == "offline"

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
