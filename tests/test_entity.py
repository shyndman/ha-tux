from __future__ import annotations

from pytest import MonkeyPatch

import ha_tux.media.entity as ha_media_module
from ha_mqtt_discoverable import DeviceInfo
from ha_tux.media.entity import (
    HA_TUX_MEDIA_DEVICE_CLASS,
    HA_TUX_MEDIA_NAME,
    HA_TUX_MEDIA_OBJECT_ID,
    build_media_player_entity,
)


def test_media_player_entity_attaches_supplied_host_device() -> None:
    device = DeviceInfo(name="Linux Laptop", identifiers="ha-tux:machine-id")

    entity = build_media_player_entity(device=device, host_prefix="testbox")

    assert entity.name == HA_TUX_MEDIA_NAME
    assert entity.object_id == f"testbox_{HA_TUX_MEDIA_OBJECT_ID}"
    assert entity.unique_id == f"ha_tux_testbox_{HA_TUX_MEDIA_OBJECT_ID}"
    assert entity.device_class == HA_TUX_MEDIA_DEVICE_CLASS
    assert entity.device == device


def test_media_player_entity_builds_host_device_by_default(
    monkeypatch: MonkeyPatch,
) -> None:
    device = DeviceInfo(name="Linux Laptop", identifiers="ha-tux:machine-id")

    monkeypatch.setattr(ha_media_module, "build_host_device_info", lambda: device)

    entity = build_media_player_entity(host_prefix="testbox")

    assert entity.device == device
