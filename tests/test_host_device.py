from __future__ import annotations

import json
import socket
import subprocess

from pytest import MonkeyPatch

from ha_tux.host_device import (
    HOSTNAMECTL_COMMAND,
    HostDeviceInfo,
    build_device_info,
    default_mqtt_client_name,
    host_device_info_from_hostnamectl_payload,
)

HOSTNAMECTL_PAYLOAD: dict[str, object] = {
    "Hostname": "runtime-host",
    "StaticHostname": "static-host",
    "PrettyHostname": "Pretty Host",
    "IconName": "computer-laptop",
    "Chassis": "laptop",
    "ChassisAssetTag": "asset-tag",
    "KernelName": "Linux",
    "KernelRelease": "7.0.0-22-generic",
    "KernelVersion": "#22-Ubuntu SMP PREEMPT_DYNAMIC Mon May 25 15:54:34 UTC 2026",
    "OperatingSystemPrettyName": "Ubuntu 26.04 LTS",
    "OperatingSystemHomeURL": "https://www.ubuntu.com/",
    "HardwareVendor": "Framework",
    "HardwareModel": "Laptop 13 _AMD Ryzen 7040Series_",
    "HardwareSKU": "FRANMDCP07",
    "HardwareVersion": "A7",
    "FirmwareVersion": "03.18",
    "FirmwareVendor": "INSYDE Corp.",
    "MachineID": "7c7698c173d94d23b9c67beaf50537ed",
    "Location": "Office",
}


def test_hostnamectl_payload_maps_to_host_device_info() -> None:
    host = host_device_info_from_hostnamectl_payload(HOSTNAMECTL_PAYLOAD)

    assert host == HostDeviceInfo(
        hostname="static-host",
        pretty_hostname="Pretty Host",
        machine_id="7c7698c173d94d23b9c67beaf50537ed",
        hardware_vendor="Framework",
        hardware_model="Laptop 13 _AMD Ryzen 7040Series_",
        hardware_sku="FRANMDCP07",
        hardware_version="A7",
        operating_system_pretty_name="Ubuntu 26.04 LTS",
        kernel_release="7.0.0-22-generic",
        chassis_asset_tag="asset-tag",
        location="Office",
    )


def test_device_info_uses_hostnamectl_computer_metadata() -> None:
    host = host_device_info_from_hostnamectl_payload(HOSTNAMECTL_PAYLOAD)

    device = build_device_info(host)

    assert device.name == "Pretty Host"
    assert device.identifiers == "ha-tux:7c7698c173d94d23b9c67beaf50537ed"
    assert device.manufacturer == "Framework"
    assert device.model == "Laptop 13 _AMD Ryzen 7040Series_"
    assert device.model_id == "FRANMDCP07"
    assert device.sw_version == "Ubuntu 26.04 LTS (7.0.0-22-generic)"
    assert device.hw_version == "A7"
    assert device.serial_number == "asset-tag"
    assert device.suggested_area == "Office"


def test_device_info_omits_missing_optional_values() -> None:
    host = host_device_info_from_hostnamectl_payload(
        {
            "Hostname": "runtime-host",
            "StaticHostname": "static-host",
        }
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


def test_default_mqtt_client_name_uses_hostnamectl_pretty_hostname(
    monkeypatch: MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess[str](
        HOSTNAMECTL_COMMAND,
        0,
        stdout=json.dumps(HOSTNAMECTL_PAYLOAD),
        stderr="",
    )

    def run_hostnamectl(
        args: tuple[str, str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert args == HOSTNAMECTL_COMMAND
        assert check is True
        assert capture_output is True
        assert text is True
        return completed

    monkeypatch.setattr(subprocess, "run", run_hostnamectl)

    assert default_mqtt_client_name() == "Pretty Host"


def test_default_mqtt_client_name_falls_back_to_socket_hostname(
    monkeypatch: MonkeyPatch,
) -> None:
    def run_hostnamectl(
        args: tuple[str, str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del args, check, capture_output, text
        raise FileNotFoundError("hostnamectl")

    monkeypatch.setattr(subprocess, "run", run_hostnamectl)
    monkeypatch.setattr(socket, "gethostname", lambda: "socket-host")

    assert default_mqtt_client_name() == "socket-host"
