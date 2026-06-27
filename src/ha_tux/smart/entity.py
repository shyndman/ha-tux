from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable._session import SessionLike
from ha_mqtt_discoverable.sensors import (
    BinarySensor,
    BinarySensorInfo,
    Sensor,
    SensorInfo,
)
from pydantic import ValidationError

from ha_tux.host_device import DEVICE_IDENTIFIER_DOMAIN, host_slug
from ha_tux.smart.report import (
    PROTOCOL_ATA,
    PROTOCOL_NVME,
    DriveSmart,
    read_smart_reports,
)

LOGGER = logging.getLogger(__name__)

ENTITY_CATEGORY_DIAGNOSTIC: Final = "diagnostic"
MEASUREMENT_STATE_CLASS: Final = "measurement"
PERCENT_UNIT: Final = "%"
HEALTH_DEVICE_CLASS: Final = "problem"
HEALTH_KEY: Final = "health"
HEALTH_LABEL: Final = "Health"
PERCENTAGE_USED_KEY: Final = "percentage_used"
PERCENTAGE_USED_LABEL: Final = "Percentage used"
REALLOCATED_KEY: Final = "reallocated_sectors"
REALLOCATED_LABEL: Final = "Reallocated sector count"
PENDING_KEY: Final = "pending_sectors"
PENDING_LABEL: Final = "Current pending sector"


@dataclass(frozen=True, slots=True)
class _DriveSensors:
    serial: str
    health: BinarySensor
    percentage_used: Sensor | None
    reallocated_sectors: Sensor | None
    pending_sectors: Sensor | None


class SmartPublisher:
    def __init__(
        self, *, drives: Mapping[str, _DriveSensors], report_path: Path
    ) -> None:
        self._drives: Mapping[str, _DriveSensors] = drives
        self._report_path: Path = report_path

    async def publish(self) -> None:
        try:
            reports = read_smart_reports(self._report_path)
        except (FileNotFoundError, json.JSONDecodeError, ValidationError):
            LOGGER.exception("smart_report_read_failed")
            await self._set_all_unavailable()
            return
        for serial, sensors in self._drives.items():
            drive = reports.get(serial)
            if drive is None:
                await self._set_drive_unavailable(sensors)
                continue
            await self._publish_drive(drive, sensors)

    async def _publish_drive(self, drive: DriveSmart, sensors: _DriveSensors) -> None:
        if drive.passed is None:
            await sensors.health.set_available(False)
        else:
            await sensors.health.set_available(True)
            if drive.passed:
                await sensors.health.off()
            else:
                await sensors.health.on()
        await self._publish_metric(sensors.percentage_used, drive.percentage_used)
        await self._publish_metric(
            sensors.reallocated_sectors, drive.reallocated_sectors
        )
        await self._publish_metric(sensors.pending_sectors, drive.pending_sectors)

    async def _publish_metric(self, sensor: Sensor | None, value: int | None) -> None:
        if sensor is None:
            return
        if value is None:
            await sensor.set_available(False)
        else:
            await sensor.set_available(True)
            await sensor.set_state(value)

    async def _set_all_unavailable(self) -> None:
        for sensors in self._drives.values():
            await self._set_drive_unavailable(sensors)

    async def _set_drive_unavailable(self, sensors: _DriveSensors) -> None:
        await sensors.health.set_available(False)
        for sensor in (
            sensors.percentage_used,
            sensors.reallocated_sectors,
            sensors.pending_sectors,
        ):
            if sensor is not None:
                await sensor.set_available(False)


def build_smart_publisher(
    session: SessionLike,
    host_device: DeviceInfo,
    host_identifier: str,
    host_prefix: str,
    report_path: Path,
) -> SmartPublisher:
    del host_device
    try:
        drives = read_smart_reports(report_path)
    except (FileNotFoundError, json.JSONDecodeError, ValidationError):
        LOGGER.warning("smart_report_unavailable")
        drives = {}
    sensors_by_serial: dict[str, _DriveSensors] = {}
    for serial, drive in drives.items():
        slug = host_slug(drive.serial)
        drive_device = DeviceInfo(
            name=drive.model,
            identifiers=f"{DEVICE_IDENTIFIER_DOMAIN}:smart:{drive.serial}",
            model=drive.model,
            serial_number=drive.serial,
            sw_version=drive.firmware,
            via_device=host_identifier,
        )
        health = BinarySensor(
            session,
            BinarySensorInfo(
                device=drive_device,
                unique_id=f"ha_tux_{host_prefix}_smart_{slug}_{HEALTH_KEY}",
                object_id=f"{host_prefix}_smart_{slug}_{HEALTH_KEY}",
                name=HEALTH_LABEL,
                device_class=HEALTH_DEVICE_CLASS,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
        )
        percentage_used: Sensor | None = None
        reallocated_sectors: Sensor | None = None
        pending_sectors: Sensor | None = None
        if drive.protocol == PROTOCOL_NVME:
            percentage_used = Sensor(
                session,
                SensorInfo(
                    device=drive_device,
                    unique_id=f"ha_tux_{host_prefix}_smart_{slug}_{PERCENTAGE_USED_KEY}",
                    object_id=f"{host_prefix}_smart_{slug}_{PERCENTAGE_USED_KEY}",
                    name=PERCENTAGE_USED_LABEL,
                    unit_of_measurement=PERCENT_UNIT,
                    state_class=MEASUREMENT_STATE_CLASS,
                    entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                ),
            )
        elif drive.protocol == PROTOCOL_ATA:
            reallocated_sectors = Sensor(
                session,
                SensorInfo(
                    device=drive_device,
                    unique_id=f"ha_tux_{host_prefix}_smart_{slug}_{REALLOCATED_KEY}",
                    object_id=f"{host_prefix}_smart_{slug}_{REALLOCATED_KEY}",
                    name=REALLOCATED_LABEL,
                    state_class=MEASUREMENT_STATE_CLASS,
                    entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                ),
            )
            pending_sectors = Sensor(
                session,
                SensorInfo(
                    device=drive_device,
                    unique_id=f"ha_tux_{host_prefix}_smart_{slug}_{PENDING_KEY}",
                    object_id=f"{host_prefix}_smart_{slug}_{PENDING_KEY}",
                    name=PENDING_LABEL,
                    state_class=MEASUREMENT_STATE_CLASS,
                    entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                ),
            )
        sensors_by_serial[serial] = _DriveSensors(
            serial=serial,
            health=health,
            percentage_used=percentage_used,
            reallocated_sectors=reallocated_sectors,
            pending_sectors=pending_sectors,
        )
    return SmartPublisher(drives=sensors_by_serial, report_path=report_path)
