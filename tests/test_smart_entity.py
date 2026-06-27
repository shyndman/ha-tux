from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable._session import PublishPayload, SessionLike

from ha_tux.smart.entity import build_smart_publisher

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")
HOST_IDENTIFIER = "ha-tux-test"
HOST_PREFIX = "testbox"

NVME_SLUG = "s7kgnj0x157882y"
SATA_SLUG = "wd_xyz"

NVME_DRIVE: dict[str, object] = {
    "model_name": "Samsung SSD 990 PRO 4TB",
    "serial_number": "S7KGNJ0X157882Y",
    "firmware_version": "4B2QJXD7",
    "device": {"protocol": "NVMe"},
    "smart_status": {"passed": True},
    "nvme_smart_health_information_log": {"percentage_used": 0},
}

SATA_DRIVE: dict[str, object] = {
    "model_name": "WDC WD40",
    "serial_number": "WD-XYZ",
    "firmware_version": "01.0",
    "device": {"protocol": "ATA"},
    "smart_status": {"passed": False},
    "ata_smart_attributes": {
        "table": [
            {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": 3}},
            {"id": 197, "name": "Current_Pending_Sector", "raw": {"value": 2}},
        ]
    },
}


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


def _write_report(path: Path, drives: list[dict[str, object]]) -> None:
    _ = path.write_text(
        json.dumps({"generated": "2026-06-27T00:00:00+00:00", "drives": drives})
    )


def test_builds_expected_entities_per_drive(tmp_path: Path) -> None:
    fake, session = _session()
    path = tmp_path / "smart.json"
    _write_report(path, [NVME_DRIVE, SATA_DRIVE])

    publisher = build_smart_publisher(
        session, DEVICE, HOST_IDENTIFIER, HOST_PREFIX, path
    )
    asyncio.run(publisher.publish())
    configs = _configs_by_unique_id(fake)
    assert set(configs) == {
        f"ha_tux_testbox_smart_{NVME_SLUG}_health",
        f"ha_tux_testbox_smart_{NVME_SLUG}_percentage_used",
        f"ha_tux_testbox_smart_{SATA_SLUG}_health",
        f"ha_tux_testbox_smart_{SATA_SLUG}_reallocated_sectors",
        f"ha_tux_testbox_smart_{SATA_SLUG}_pending_sectors",
    }

    health = configs[f"ha_tux_testbox_smart_{NVME_SLUG}_health"]
    device = cast(dict[str, object], health["device"])
    assert device["via_device"] == "ha-tux-test"
    assert "ha-tux:smart:S7KGNJ0X157882Y" in cast(list[str], device["identifiers"])
    assert device["serial_number"] == "S7KGNJ0X157882Y"
    assert device["model"] == "Samsung SSD 990 PRO 4TB"
    assert device["sw_version"] == "4B2QJXD7"
    assert health["name"] == "Health"
    assert health["device_class"] == "problem"
    assert health["entity_category"] == "diagnostic"

    pct = configs[f"ha_tux_testbox_smart_{NVME_SLUG}_percentage_used"]
    assert pct["name"] == "Percentage used"
    assert pct["unit_of_measurement"] == "%"
    assert pct["entity_category"] == "diagnostic"

    realloc = configs[f"ha_tux_testbox_smart_{SATA_SLUG}_reallocated_sectors"]
    assert realloc["name"] == "Reallocated sector count"
    assert "unit_of_measurement" not in realloc
    pending = configs[f"ha_tux_testbox_smart_{SATA_SLUG}_pending_sectors"]
    assert pending["name"] == "Current pending sector"


def test_publish_emits_states(tmp_path: Path) -> None:
    fake, session = _session()
    path = tmp_path / "smart.json"
    _write_report(path, [NVME_DRIVE, SATA_DRIVE])
    publisher = build_smart_publisher(
        session, DEVICE, HOST_IDENTIFIER, HOST_PREFIX, path
    )

    asyncio.run(publisher.publish())

    states = _states(fake)
    availability = _availability(fake)
    assert availability[f"testbox_smart_{NVME_SLUG}_health"] == "online"
    assert states[f"testbox_smart_{NVME_SLUG}_health"] == "off"
    assert states[f"testbox_smart_{NVME_SLUG}_percentage_used"] == "0"
    assert states[f"testbox_smart_{SATA_SLUG}_health"] == "on"
    assert states[f"testbox_smart_{SATA_SLUG}_reallocated_sectors"] == "3"
    assert states[f"testbox_smart_{SATA_SLUG}_pending_sectors"] == "2"


def test_drive_absent_from_later_report_goes_offline(tmp_path: Path) -> None:
    fake, session = _session()
    path = tmp_path / "smart.json"
    _write_report(path, [NVME_DRIVE])
    publisher = build_smart_publisher(
        session, DEVICE, HOST_IDENTIFIER, HOST_PREFIX, path
    )
    _write_report(path, [])

    asyncio.run(publisher.publish())

    assert _availability(fake)[f"testbox_smart_{NVME_SLUG}_health"] == "offline"
    assert not _states(fake)


def test_unreadable_report_marks_all_offline(tmp_path: Path) -> None:
    fake, session = _session()
    path = tmp_path / "smart.json"
    _write_report(path, [NVME_DRIVE, SATA_DRIVE])
    publisher = build_smart_publisher(
        session, DEVICE, HOST_IDENTIFIER, HOST_PREFIX, path
    )
    path.unlink()

    asyncio.run(publisher.publish())

    availability = _availability(fake)
    assert set(availability.values()) == {"offline"}
    assert not _states(fake)
