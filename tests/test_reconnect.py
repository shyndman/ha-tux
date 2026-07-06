from __future__ import annotations

import asyncio
from contextlib import suppress
from types import TracebackType

import aiomqtt
from pytest import MonkeyPatch

import ha_tux
from ha_tux import Activation, Feature
from ha_tux.config import Role, load_config


class FakeSession:
    connects: int = 0

    def __init__(self, settings: object) -> None:
        del settings

    async def __aenter__(self) -> FakeSession:
        FakeSession.connects += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb


async def _boom() -> None:
    raise aiomqtt.MqttError("poison")


def test_child_failure_triggers_reconnect(monkeypatch: MonkeyPatch) -> None:
    FakeSession.connects = 0
    reached_second = asyncio.Event()

    async def activate(act: Activation) -> None:
        if FakeSession.connects == 1:
            _ = act.tasks.create_task(_boom())
        else:
            reached_second.set()
        _ = await asyncio.Event().wait()

    feature = Feature(name="t", roles=frozenset({"all"}), activate=activate)

    monkeypatch.setattr(ha_tux, "MqttSession", FakeSession)

    def _features(role: Role) -> tuple[Feature, ...]:
        del role
        return (feature,)

    monkeypatch.setattr(ha_tux, "features_for_role", _features)
    monkeypatch.setattr(ha_tux, "MQTT_RECONNECT_DELAY_SECONDS", 0)

    async def run() -> None:
        config = load_config(path=None)
        task = asyncio.create_task(ha_tux.async_main(config, "all"))
        _ = await asyncio.wait_for(reached_second.wait(), timeout=5)
        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert FakeSession.connects >= 2
