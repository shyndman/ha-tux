from __future__ import annotations

import asyncio
import json
from typing import cast

from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable._session import PublishPayload, SessionLike
from ha_mqtt_discoverable.sensors import BinarySensor

from ha_tux.ha_input_active import InputActivePublisher, build_input_active_publisher

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")


class FakeBinarySensor:
    def __init__(self) -> None:
        self.events: list[tuple[str, bool | None]] = []

    async def set_available(self, available: bool) -> None:
        self.events.append(("available", available))

    async def on(self) -> None:
        self.events.append(("on", None))

    async def off(self) -> None:
        self.events.append(("off", None))


class FakeSession:
    def __init__(self) -> None:
        self.published: list[tuple[str, PublishPayload, bool]] = []

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
        del qos
        self.published.append((topic, payload, retain))


def _publisher() -> tuple[FakeBinarySensor, InputActivePublisher]:
    fake = FakeBinarySensor()
    return fake, InputActivePublisher(cast(BinarySensor, cast(object, fake)))


def test_set_active_true_marks_available_and_on() -> None:
    fake, publisher = _publisher()

    asyncio.run(publisher.set_active(True))

    assert fake.events == [("available", True), ("on", None)]


def test_set_active_false_marks_available_and_off() -> None:
    fake, publisher = _publisher()

    asyncio.run(publisher.set_active(False))

    assert fake.events == [("available", True), ("off", None)]


def test_set_unavailable_does_not_touch_state() -> None:
    fake, publisher = _publisher()

    asyncio.run(publisher.set_available(False))

    assert fake.events == [("available", False)]


def test_build_publishes_occupancy_discovery() -> None:
    fake = FakeSession()
    session = cast(SessionLike, cast(object, fake))

    publisher = build_input_active_publisher(session, DEVICE)
    asyncio.run(publisher.set_active(True))

    configs = {
        cast(str, json.loads(payload)["unique_id"]): cast(
            dict[str, object], json.loads(payload)
        )
        for topic, payload, _ in fake.published
        if topic.endswith("/config") and isinstance(payload, str)
    }
    config = configs["ha_tux_input_active"]
    assert config["device_class"] == "occupancy"
    assert config["name"] == "Input Active"
    assert config["default_entity_id"] == "binary_sensor.input_active"
