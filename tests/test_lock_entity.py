from __future__ import annotations

import asyncio
from typing import cast

import aiomqtt
import pytest
from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable._session import (
    CommandCallback,
    PublishPayload,
    SessionLike,
)
from ha_mqtt_discoverable.sensors import Button

import ha_tux.lock.entity as lock_module
from ha_tux.lock.entity import LockPublisher
from ha_tux.lock.screensaver import GnomeScreenSaverAsync

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")


class FakeProxy:
    def __init__(self, error: Exception | None = None) -> None:
        self.locks: int = 0
        self._error: Exception | None = error

    async def lock(self) -> None:
        self.locks += 1
        if self._error is not None:
            raise self._error


class FakeSession:
    @property
    def discovery_prefix(self) -> str:
        return "homeassistant"

    @property
    def state_prefix(self) -> str:
        return "hmd"

    @property
    def status_topic(self) -> str:
        return "hmd/ha-tux/status"

    async def publish(
        self,
        topic: str,
        payload: PublishPayload,
        *,
        retain: bool = False,
        qos: int = 0,
    ) -> None:
        del topic, payload, retain, qos

    def register_command[SenderT](
        self,
        topic: str,
        sender: SenderT,
        callback: CommandCallback[SenderT],
        *,
        qos: int = 1,
        command_name: str | None = None,
    ) -> None:
        del topic, sender, callback, qos, command_name


def _publisher(proxy: FakeProxy, monkeypatch: pytest.MonkeyPatch) -> LockPublisher:
    monkeypatch.setattr(
        lock_module,
        "new_screensaver_proxy",
        lambda: cast(GnomeScreenSaverAsync, cast(object, proxy)),
    )
    session = cast(SessionLike, cast(object, FakeSession()))
    return LockPublisher(session, DEVICE, "testbox")


def _press_args() -> tuple[Button, aiomqtt.Message]:
    return cast(Button, object()), cast(aiomqtt.Message, object())


def test_press_triggers_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = FakeProxy()
    publisher = _publisher(proxy, monkeypatch)

    sender, message = _press_args()
    asyncio.run(publisher.on_press(sender, message))

    assert proxy.locks == 1


def test_press_swallows_lock_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = FakeProxy(error=RuntimeError("no session"))
    publisher = _publisher(proxy, monkeypatch)

    sender, message = _press_args()
    asyncio.run(publisher.on_press(sender, message))

    assert proxy.locks == 1
