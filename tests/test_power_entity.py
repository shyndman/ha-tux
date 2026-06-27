from __future__ import annotations

import asyncio
from typing import cast

from ha_mqtt_discoverable.sensors import BinarySensor, Sensor

from ha_tux.power.entity import PowerPublisher


class FakeSensor:
    def __init__(self) -> None:
        self.state: str | int | float | None = None
        self.available: bool | None = None

    async def set_available(self, available: bool) -> None:
        self.available = available

    async def set_state(
        self, state: str | int | float, last_reset: str | None = None
    ) -> None:
        del last_reset
        self.state = state


class FakeBinarySensor:
    def __init__(self) -> None:
        self.is_on: bool | None = None

    async def set_available(self, available: bool) -> None:
        del available

    async def on(self) -> None:
        self.is_on = True

    async def off(self) -> None:
        self.is_on = False


def _publisher() -> tuple[FakeSensor, FakeSensor, FakeBinarySensor, PowerPublisher]:
    battery = FakeSensor()
    state = FakeSensor()
    ac = FakeBinarySensor()
    publisher = PowerPublisher(
        cast(Sensor, cast(object, battery)),
        cast(Sensor, cast(object, state)),
        cast(BinarySensor, cast(object, ac)),
    )
    return battery, state, ac, publisher


def test_update_charging_sets_state_and_ac_on() -> None:
    battery, state, ac, publisher = _publisher()
    asyncio.run(publisher.update(80, 5))
    assert battery.state == 80
    assert state.state == "pending_charge"
    assert ac.is_on is True


def test_update_discharging_sets_state_and_ac_off() -> None:
    _battery, state, ac, publisher = _publisher()
    asyncio.run(publisher.update(50, 2))
    assert state.state == "discharging"
    assert ac.is_on is False


def test_update_unknown_state_falls_back() -> None:
    _battery, state, ac, publisher = _publisher()
    asyncio.run(publisher.update(73, 99))
    assert state.state == "unknown"
    assert ac.is_on is False
