from __future__ import annotations

from pytest import MonkeyPatch

import ha_tux.ha_media as ha_media_module
from ha_mqtt_discoverable import DeviceInfo
from ha_tux.ha_media import (
    HA_TUX_MEDIA_DEVICE_CLASS,
    HA_TUX_MEDIA_NAME,
    HA_TUX_MEDIA_OBJECT_ID,
    HA_TUX_MEDIA_UNIQUE_ID,
    build_media_player_entity,
)


def test_media_player_entity_attaches_supplied_host_device() -> None:
    device = DeviceInfo(name="Linux Laptop", identifiers="ha-tux:machine-id")

    entity = build_media_player_entity(device=device)

    assert entity.name == HA_TUX_MEDIA_NAME
    assert entity.object_id == HA_TUX_MEDIA_OBJECT_ID
    assert entity.unique_id == HA_TUX_MEDIA_UNIQUE_ID
    assert entity.device_class == HA_TUX_MEDIA_DEVICE_CLASS
    assert entity.device == device


def test_media_player_entity_builds_host_device_by_default(
    monkeypatch: MonkeyPatch,
) -> None:
    device = DeviceInfo(name="Linux Laptop", identifiers="ha-tux:machine-id")

    monkeypatch.setattr(ha_media_module, "build_host_device_info", lambda: device)

    entity = build_media_player_entity()

    assert entity.device == device
