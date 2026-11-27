from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from typing import ClassVar, Final

from ha_mqtt_discoverable import DeviceInfo
from libsh import get_logger
from pydantic import BaseModel, ConfigDict
from sdbus import DbusInterfaceCommon, dbus_property, sd_bus_open_system

LOGGER_NAME: Final = "ha_tux"
HOSTNAME1_SERVICE_NAME: Final = "org.freedesktop.hostname1"
HOSTNAME1_OBJECT_PATH: Final = "/org/freedesktop/hostname1"
HOSTNAME1_INTERFACE: Final = "org.freedesktop.hostname1"
DEVICE_IDENTIFIER_DOMAIN: Final = "ha-tux"

_HOST_SLUG_PATTERN: Final = re.compile(r"[^a-z0-9]+")


def host_slug(hostname: str) -> str:
    return _HOST_SLUG_PATTERN.sub("_", hostname.lower()).strip("_")


class Hostname1Proxy(DbusInterfaceCommon, interface_name=HOSTNAME1_INTERFACE):
    """Synchronous D-Bus proxy for unprivileged ``org.freedesktop.hostname1`` properties."""

    @dbus_property("s", property_name="Hostname")
    def hostname(self) -> str:
        raise NotImplementedError

    @dbus_property("s", property_name="StaticHostname")
    def static_hostname(self) -> str:
        raise NotImplementedError

    @dbus_property("s", property_name="PrettyHostname")
    def pretty_hostname(self) -> str:
        raise NotImplementedError

    @dbus_property("ay", property_name="MachineID")
    def machine_id(self) -> bytes:
        raise NotImplementedError

    @dbus_property("s", property_name="HardwareVendor")
    def hardware_vendor(self) -> str:
        raise NotImplementedError

    @dbus_property("s", property_name="HardwareModel")
    def hardware_model(self) -> str:
        raise NotImplementedError

    @dbus_property("s", property_name="HardwareSKU")
    def hardware_sku(self) -> str:
        raise NotImplementedError

    @dbus_property("s", property_name="HardwareVersion")
    def hardware_version(self) -> str:
        raise NotImplementedError

    @dbus_property("s", property_name="OperatingSystemPrettyName")
    def operating_system_pretty_name(self) -> str:
        raise NotImplementedError

    @dbus_property("s", property_name="KernelRelease")
    def kernel_release(self) -> str:
        raise NotImplementedError

    @dbus_property("s", property_name="ChassisAssetTag")
    def chassis_asset_tag(self) -> str:
        raise NotImplementedError

    @dbus_property("s", property_name="Location")
    def location(self) -> str:
        raise NotImplementedError


class Hostname1Properties(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    hostname: str = ""
    static_hostname: str = ""
    pretty_hostname: str = ""
    machine_id: bytes = b""
    hardware_vendor: str = ""
    hardware_model: str = ""
    hardware_sku: str = ""
    hardware_version: str = ""
    operating_system_pretty_name: str = ""
    kernel_release: str = ""
    chassis_asset_tag: str = ""
    location: str = ""


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


def _read_hostname1_properties() -> Hostname1Properties:
    bus = sd_bus_open_system()
    try:
        proxy = Hostname1Proxy(
            HOSTNAME1_SERVICE_NAME,
            HOSTNAME1_OBJECT_PATH,
            bus,
        )
        raw_properties: object = proxy.properties_get_all_dict(
            on_unknown_member="ignore",
        )
    finally:
        bus.close()

    return Hostname1Properties.model_validate(raw_properties)


def load_host_device_info() -> HostDeviceInfo:
    properties = _read_hostname1_properties()
    host = host_device_info_from_hostname1_properties(properties)

    get_logger(LOGGER_NAME).debug(
        "Loaded host device metadata from org.freedesktop.hostname1",
        hostname=host.hostname,
        pretty_hostname=host.pretty_hostname,
        machine_id=host.machine_id,
    )

    return host


def host_device_info_from_hostname1_properties(
    properties: Hostname1Properties,
) -> HostDeviceInfo:
    hostname = (
        _non_empty(properties.static_hostname)
        or _non_empty(properties.hostname)
        or socket.gethostname()
    )

    pretty_hostname = _non_empty(properties.pretty_hostname)
    if pretty_hostname == hostname:
        pretty_hostname = None

    machine_id = properties.machine_id.hex() if properties.machine_id else None

    return HostDeviceInfo(
        hostname=hostname,
        pretty_hostname=pretty_hostname,
        machine_id=machine_id,
        hardware_vendor=_non_empty(properties.hardware_vendor),
        hardware_model=_non_empty(properties.hardware_model),
        hardware_sku=_non_empty(properties.hardware_sku),
        hardware_version=_non_empty(properties.hardware_version),
        operating_system_pretty_name=_non_empty(
            properties.operating_system_pretty_name,
        ),
        kernel_release=_non_empty(properties.kernel_release),
        chassis_asset_tag=_non_empty(properties.chassis_asset_tag),
        location=_non_empty(properties.location),
    )


def default_mqtt_client_name() -> str:
    try:
        host = load_host_device_info()
    except Exception as error:
        get_logger(LOGGER_NAME).info(
            "Failed to read org.freedesktop.hostname1 for MQTT client name; falling back to socket hostname",
            error=str(error),
        )
        return socket.gethostname()

    return host.pretty_hostname or host.hostname


def build_host_device_info() -> DeviceInfo:
    try:
        host = load_host_device_info()
    except Exception as error:
        get_logger(LOGGER_NAME).info(
            "Failed to read org.freedesktop.hostname1 for host device info; falling back to socket hostname",
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


def _non_empty(value: str) -> str | None:
    if not value:
        return None
    return value
