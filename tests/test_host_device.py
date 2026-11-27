from __future__ import annotations

import socket

from pytest import MonkeyPatch

import ha_tux.host_device as host_device_module
from ha_tux.host_device import (
    HostDeviceInfo,
    Hostname1Properties,
    build_device_info,
    default_mqtt_client_name,
    host_device_info_from_hostname1_properties,
)

MACHINE_ID_HEX = "7c7698c173d94d23b9c67beaf50537ed"

HOSTNAME1_PROPERTIES = Hostname1Properties(
    hostname="runtime-host",
    static_hostname="static-host",
    pretty_hostname="Pretty Host",
    machine_id=bytes.fromhex(MACHINE_ID_HEX),
    hardware_vendor="Framework",
    hardware_model="Laptop 13 _AMD Ryzen 7040Series_",
    hardware_sku="FRANMDCP07",
    hardware_version="A7",
    operating_system_pretty_name="Ubuntu 26.04 LTS",
    kernel_release="7.0.0-22-generic",
    chassis_asset_tag="asset-tag",
    location="Office",
)


def test_hostname1_properties_map_to_host_device_info() -> None:
    host = host_device_info_from_hostname1_properties(HOSTNAME1_PROPERTIES)

    assert host == HostDeviceInfo(
        hostname="static-host",
        pretty_hostname="Pretty Host",
        machine_id=MACHINE_ID_HEX,
        hardware_vendor="Framework",
        hardware_model="Laptop 13 _AMD Ryzen 7040Series_",
        hardware_sku="FRANMDCP07",
        hardware_version="A7",
        operating_system_pretty_name="Ubuntu 26.04 LTS",
        kernel_release="7.0.0-22-generic",
        chassis_asset_tag="asset-tag",
        location="Office",
    )


def test_device_info_uses_hostname1_computer_metadata() -> None:
    host = host_device_info_from_hostname1_properties(HOSTNAME1_PROPERTIES)

    device = build_device_info(host)

    assert device.name == "Pretty Host"
    assert device.identifiers == f"ha-tux:{MACHINE_ID_HEX}"
    assert device.manufacturer == "Framework"
    assert device.model == "Laptop 13 _AMD Ryzen 7040Series_"
    assert device.model_id == "FRANMDCP07"
    assert device.sw_version == "Ubuntu 26.04 LTS (7.0.0-22-generic)"
    assert device.hw_version == "A7"
    assert device.serial_number == "asset-tag"
    assert device.suggested_area == "Office"


def test_device_info_omits_missing_optional_values() -> None:
    host = host_device_info_from_hostname1_properties(
        Hostname1Properties(
            hostname="runtime-host",
            static_hostname="static-host",
        )
    )

    device = build_device_info(host)

    assert device.name == "static-host"
    assert device.identifiers == "ha-tux:static-host"
    assert device.manufacturer is None
    assert device.model is None
    assert device.model_id is None
    assert device.sw_version is None
    assert device.hw_version is None
    assert device.serial_number is None
    assert device.suggested_area is None


def test_default_mqtt_client_name_uses_hostname1_pretty_hostname(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        host_device_module,
        "_read_hostname1_properties",
        lambda: HOSTNAME1_PROPERTIES,
    )

    assert default_mqtt_client_name() == "Pretty Host"


def test_default_mqtt_client_name_falls_back_to_socket_hostname(
    monkeypatch: MonkeyPatch,
) -> None:
    def fail_read() -> Hostname1Properties:
        raise OSError("system bus unavailable")

    monkeypatch.setattr(host_device_module, "_read_hostname1_properties", fail_read)
    monkeypatch.setattr(socket, "gethostname", lambda: "socket-host")

    assert default_mqtt_client_name() == "socket-host"
