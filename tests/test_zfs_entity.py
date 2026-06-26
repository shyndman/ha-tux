from __future__ import annotations

import asyncio
import json
from typing import cast

from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable._session import PublishPayload, SessionLike
from pytest import MonkeyPatch

import ha_tux.zfs.entity as ha_zfs
from ha_tux.zfs.entity import build_zfs_pool_publisher
from ha_tux.zfs.zpool import PoolSnapshots, ZpoolStatus

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")

RPOOL_STATUS = ZpoolStatus(
    name="rpool",
    size_bytes=500107862016,
    allocated_bytes=123456789,
    free_bytes=499984405227,
    capacity_percent=3,
    fragmentation_percent=11,
    health="ONLINE",
)


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


def _session() -> tuple[FakeSession, SessionLike]:
    fake = FakeSession()
    return fake, cast(SessionLike, cast(object, fake))


def _configs_by_unique_id(fake: FakeSession) -> dict[str, dict[str, object]]:
    configs: dict[str, dict[str, object]] = {}
    for topic, payload, _ in fake.published:
        if topic.endswith("/config") and isinstance(payload, str):
            config = cast(dict[str, object], json.loads(payload))
            configs[cast(str, config["unique_id"])] = config
    return configs


def test_builds_eight_sensors_per_pool_with_expected_metadata(
    monkeypatch: MonkeyPatch,
) -> None:
    fake, session = _session()
    monkeypatch.setattr(
        ha_zfs,
        "read_zpool_statuses",
        lambda: _async_return((RPOOL_STATUS,)),
    )
    monkeypatch.setattr(
        ha_zfs,
        "read_pool_snapshots",
        lambda: _async_snapshots({}),
    )
    publisher = build_zfs_pool_publisher(session, DEVICE, ["rpool"])

    asyncio.run(publisher.publish())

    configs = _configs_by_unique_id(fake)
    assert len(configs) == 8

    size = configs["ha_tux_zfs_rpool_size"]
    assert size["default_entity_id"] == "sensor.zfs_rpool_size"
    assert size["name"] == "ZFS rpool Size"
    assert size["unit_of_measurement"] == "B"
    assert size["device_class"] == "data_size"
    assert size["state_class"] == "measurement"
    assert size["entity_category"] == "diagnostic"
    assert size["suggested_unit_of_measurement"] == "TiB"

    assert configs["ha_tux_zfs_rpool_allocated"]["suggested_unit_of_measurement"] == (
        "GiB"
    )
    assert configs["ha_tux_zfs_rpool_free"]["suggested_unit_of_measurement"] == "GiB"

    used = configs["ha_tux_zfs_rpool_used"]
    assert used["default_entity_id"] == "sensor.zfs_rpool_used"
    assert used["name"] == "ZFS rpool Used"
    assert used["unit_of_measurement"] == "%"
    assert "device_class" not in used
    assert used["state_class"] == "measurement"
    assert "suggested_unit_of_measurement" not in used

    health = configs["ha_tux_zfs_rpool_health"]
    assert health["default_entity_id"] == "sensor.zfs_rpool_health"
    assert health["name"] == "ZFS rpool Health"
    assert "unit_of_measurement" not in health
    assert "state_class" not in health
    assert health["entity_category"] == "diagnostic"

    snapshots = configs["ha_tux_zfs_rpool_snapshots"]
    assert snapshots["default_entity_id"] == "sensor.zfs_rpool_snapshots"
    assert snapshots["name"] == "ZFS rpool Snapshots"
    assert snapshots["state_class"] == "measurement"
    assert "unit_of_measurement" not in snapshots
    assert "device_class" not in snapshots
    assert snapshots["entity_category"] == "diagnostic"

    latest = configs["ha_tux_zfs_rpool_latest_snapshot"]
    assert latest["default_entity_id"] == "sensor.zfs_rpool_latest_snapshot"
    assert latest["name"] == "ZFS rpool Latest snapshot"
    assert latest["device_class"] == "timestamp"
    assert "state_class" not in latest
    assert "unit_of_measurement" not in latest
    assert latest["entity_category"] == "diagnostic"


def test_publish_emits_state_for_each_metric_and_health(
    monkeypatch: MonkeyPatch,
) -> None:
    fake, session = _session()
    monkeypatch.setattr(
        ha_zfs,
        "read_zpool_statuses",
        lambda: _async_return((RPOOL_STATUS,)),
    )
    monkeypatch.setattr(
        ha_zfs,
        "read_pool_snapshots",
        lambda: _async_snapshots(
            {"rpool": PoolSnapshots(count=4, latest_epoch=1782471601)}
        ),
    )
    publisher = build_zfs_pool_publisher(session, DEVICE, ["rpool"])

    asyncio.run(publisher.publish())

    states = _states(fake)
    assert states["zfs_rpool_size"] == "500107862016"
    assert states["zfs_rpool_allocated"] == "123456789"
    assert states["zfs_rpool_free"] == "499984405227"
    assert states["zfs_rpool_used"] == "3"
    assert states["zfs_rpool_fragmentation"] == "11"
    assert states["zfs_rpool_health"] == "ONLINE"
    assert states["zfs_rpool_snapshots"] == "4"
    assert states["zfs_rpool_latest_snapshot"] == "2026-06-26T11:00:01+00:00"


def test_missing_pool_marks_all_its_sensors_offline(
    monkeypatch: MonkeyPatch,
) -> None:
    fake, session = _session()
    monkeypatch.setattr(ha_zfs, "read_zpool_statuses", lambda: _async_return(()))
    monkeypatch.setattr(ha_zfs, "read_pool_snapshots", lambda: _async_snapshots({}))
    publisher = build_zfs_pool_publisher(session, DEVICE, ["rpool"])

    asyncio.run(publisher.publish())

    availability = _availability(fake)
    assert len(availability) == 8
    assert set(availability.values()) == {"offline"}
    assert not _states(fake)


def test_read_failure_marks_all_sensors_offline(monkeypatch: MonkeyPatch) -> None:
    fake, session = _session()

    def _raise() -> object:
        raise RuntimeError("zpool unavailable")

    monkeypatch.setattr(ha_zfs, "read_zpool_statuses", _raise)
    monkeypatch.setattr(ha_zfs, "read_pool_snapshots", lambda: _async_snapshots({}))
    publisher = build_zfs_pool_publisher(session, DEVICE, ["rpool"])

    asyncio.run(publisher.publish())

    availability = _availability(fake)
    assert len(availability) == 8
    assert set(availability.values()) == {"offline"}


def test_empty_pool_names_publishes_nothing(monkeypatch: MonkeyPatch) -> None:
    fake, session = _session()
    monkeypatch.setattr(ha_zfs, "read_zpool_statuses", lambda: _async_return(()))
    monkeypatch.setattr(ha_zfs, "read_pool_snapshots", lambda: _async_snapshots({}))
    publisher = build_zfs_pool_publisher(session, DEVICE, [])

    asyncio.run(publisher.publish())

    assert fake.published == []


async def _async_return(value: tuple[ZpoolStatus, ...]) -> tuple[ZpoolStatus, ...]:
    return value


async def _async_snapshots(
    value: dict[str, PoolSnapshots],
) -> dict[str, PoolSnapshots]:
    return value


def _states(fake: FakeSession) -> dict[str, str]:
    states: dict[str, str] = {}
    for topic, payload, _ in fake.published:
        if topic.endswith("/state") and isinstance(payload, str):
            states[topic.split("/")[-2]] = payload
    return states


def _availability(fake: FakeSession) -> dict[str, str]:
    availability: dict[str, str] = {}
    for topic, payload, _ in fake.published:
        if topic.endswith("/availability") and isinstance(payload, str):
            availability[topic.split("/")[-2]] = payload
    return availability


def test_pool_with_no_snapshots_reports_zero_and_offline_timestamp(
    monkeypatch: MonkeyPatch,
) -> None:
    fake, session = _session()
    monkeypatch.setattr(
        ha_zfs,
        "read_zpool_statuses",
        lambda: _async_return((RPOOL_STATUS,)),
    )
    monkeypatch.setattr(ha_zfs, "read_pool_snapshots", lambda: _async_snapshots({}))
    publisher = build_zfs_pool_publisher(session, DEVICE, ["rpool"])

    asyncio.run(publisher.publish())

    states = _states(fake)
    assert states["zfs_rpool_snapshots"] == "0"
    assert "zfs_rpool_latest_snapshot" not in states
    assert _availability(fake)["zfs_rpool_latest_snapshot"] == "offline"


def test_snapshot_read_failure_keeps_other_sensors(
    monkeypatch: MonkeyPatch,
) -> None:
    fake, session = _session()
    monkeypatch.setattr(
        ha_zfs,
        "read_zpool_statuses",
        lambda: _async_return((RPOOL_STATUS,)),
    )

    def _raise() -> object:
        raise RuntimeError("zfs unavailable")

    monkeypatch.setattr(ha_zfs, "read_pool_snapshots", _raise)
    publisher = build_zfs_pool_publisher(session, DEVICE, ["rpool"])

    asyncio.run(publisher.publish())

    states = _states(fake)
    assert states["zfs_rpool_size"] == "500107862016"
    assert states["zfs_rpool_health"] == "ONLINE"
    availability = _availability(fake)
    assert availability["zfs_rpool_snapshots"] == "offline"
    assert availability["zfs_rpool_latest_snapshot"] == "offline"
