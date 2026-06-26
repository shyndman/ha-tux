from __future__ import annotations

import json
import re
import socket
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast

from ha_mqtt_discoverable import DeviceInfo
from libsh import get_logger

LOGGER_NAME: Final = "ha_tux"
HOSTNAMECTL_COMMAND: Final = ("hostnamectl", "--json=short")
DEVICE_IDENTIFIER_DOMAIN: Final = "ha-tux"

_HOST_SLUG_PATTERN: Final = re.compile(r"[^a-z0-9]+")


def host_slug(hostname: str) -> str:
    return _HOST_SLUG_PATTERN.sub("_", hostname.lower()).strip("_")


@dataclass(frozen=True, slots=True)
class HostDeviceInfo:
    hostname: str
    pretty_hostname: str | None
    machine_id: str | None
    hardware_vendor: str | None
    hardware_model: str | None
    hardware_sku: str | None
    hardware_version: str | None
    operating_system_pretty_name: str | None
    kernel_release: str | None
    chassis_asset_tag: str | None
    location: str | None


def load_host_device_info() -> HostDeviceInfo:
    completed = subprocess.run(
        HOSTNAMECTL_COMMAND,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = cast(object, json.loads(completed.stdout))
    if not isinstance(payload, Mapping):
        raise ValueError("hostnamectl JSON output must be an object")
    return host_device_info_from_hostnamectl_payload(
        cast(Mapping[str, object], payload)
    )


def host_device_info_from_hostnamectl_payload(
    payload: Mapping[str, object],
) -> HostDeviceInfo:
    hostname = _first_present_string(
        payload,
        "StaticHostname",
        "Hostname",
        default=socket.gethostname(),
    )
    pretty_hostname = _optional_string(payload, "PrettyHostname")
    if pretty_hostname == hostname:
        pretty_hostname = None
    return HostDeviceInfo(
        hostname=hostname,
        pretty_hostname=pretty_hostname,
        machine_id=_optional_string(payload, "MachineID"),
        hardware_vendor=_optional_string(payload, "HardwareVendor"),
        hardware_model=_optional_string(payload, "HardwareModel"),
        hardware_sku=_optional_string(payload, "HardwareSKU"),
        hardware_version=_optional_string(payload, "HardwareVersion"),
        operating_system_pretty_name=_optional_string(
            payload,
            "OperatingSystemPrettyName",
        ),
        kernel_release=_optional_string(payload, "KernelRelease"),
        chassis_asset_tag=_optional_string(payload, "ChassisAssetTag"),
        location=_optional_string(payload, "Location"),
    )


def default_mqtt_client_name() -> str:
    try:
        host = load_host_device_info()
    except Exception as error:
        get_logger(LOGGER_NAME).info(
            "hostnamectl_client_name_fallback",
            error=str(error),
        )
        return socket.gethostname()
    return host.pretty_hostname or host.hostname


def build_host_device_info() -> DeviceInfo:
    try:
        host = load_host_device_info()
    except Exception as error:
        get_logger(LOGGER_NAME).info(
            "hostnamectl_device_info_fallback",
            error=str(error),
        )
        host = HostDeviceInfo(
            hostname=socket.gethostname(),
            pretty_hostname=None,
            machine_id=None,
            hardware_vendor=None,
            hardware_model=None,
            hardware_sku=None,
            hardware_version=None,
            operating_system_pretty_name=None,
            kernel_release=None,
            chassis_asset_tag=None,
            location=None,
        )
    return build_device_info(host)


def build_device_info(host: HostDeviceInfo) -> DeviceInfo:
    return DeviceInfo(
        name=host.pretty_hostname or host.hostname,
        identifiers=f"{DEVICE_IDENTIFIER_DOMAIN}:{host.machine_id or host.hostname}",
        manufacturer=host.hardware_vendor,
        model=host.hardware_model,
        model_id=host.hardware_sku,
        sw_version=_software_version(host),
        hw_version=host.hardware_version,
        serial_number=host.chassis_asset_tag,
        suggested_area=host.location,
    )


def _software_version(host: HostDeviceInfo) -> str | None:
    if host.operating_system_pretty_name is None:
        return host.kernel_release
    if host.kernel_release is None:
        return host.operating_system_pretty_name
    return f"{host.operating_system_pretty_name} ({host.kernel_release})"


def _first_present_string(
    payload: Mapping[str, object],
    *keys: str,
    default: str,
) -> str:
    for key in keys:
        value = _optional_string(payload, key)
        if value is not None:
            return value
    return default


def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    if not value:
        return None
    return value
