from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, cast

from pydantic import BaseModel, ConfigDict, ValidationError

LOGGER = logging.getLogger(__name__)

SMART_REPORT_PATH: Final = Path("/var/lib/ha-tux/smart.json")
DEFAULT_SMART_POLL_SECONDS: Final = 86400.0
PROTOCOL_NVME: Final = "NVMe"
PROTOCOL_ATA: Final = "ATA"
REALLOCATED_SECTOR_ATTR_ID: Final = 5
CURRENT_PENDING_SECTOR_ATTR_ID: Final = 197


class _Device(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    protocol: str


class _SmartStatus(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    passed: bool


class _NvmeLog(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    percentage_used: int | None = None


class _AtaRaw(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    value: int


class _AtaAttr(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    id: int
    raw: _AtaRaw


class _AtaAttrs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    table: list[_AtaAttr] = []


class _DriveReport(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    model_name: str | None = None
    serial_number: str
    firmware_version: str | None = None
    device: _Device
    smart_status: _SmartStatus | None = None
    nvme_smart_health_information_log: _NvmeLog | None = None
    ata_smart_attributes: _AtaAttrs | None = None


class _ReportFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    drives: list[dict[str, object]] = []


@dataclass(frozen=True, slots=True)
class DriveSmart:
    serial: str
    model: str
    firmware: str | None
    protocol: str
    passed: bool | None
    percentage_used: int | None
    reallocated_sectors: int | None
    pending_sectors: int | None


def _to_drive_smart(report: _DriveReport) -> DriveSmart:
    passed = report.smart_status.passed if report.smart_status else None
    percentage_used = (
        report.nvme_smart_health_information_log.percentage_used
        if report.nvme_smart_health_information_log is not None
        else None
    )
    attrs: dict[int, int] = (
        {attr.id: attr.raw.value for attr in report.ata_smart_attributes.table}
        if report.ata_smart_attributes is not None
        else {}
    )
    return DriveSmart(
        serial=report.serial_number,
        model=report.model_name or report.serial_number,
        firmware=report.firmware_version,
        protocol=report.device.protocol,
        passed=passed,
        percentage_used=percentage_used,
        reallocated_sectors=attrs.get(REALLOCATED_SECTOR_ATTR_ID),
        pending_sectors=attrs.get(CURRENT_PENDING_SECTOR_ATTR_ID),
    )


def read_smart_reports(path: Path) -> dict[str, DriveSmart]:
    raw = cast(object, json.loads(path.read_text()))
    file = _ReportFile.model_validate(raw)
    result: dict[str, DriveSmart] = {}
    for entry in file.drives:
        try:
            report = _DriveReport.model_validate(entry)
        except ValidationError:
            LOGGER.warning("smart_drive_skipped_invalid")
            continue
        result[report.serial_number] = _to_drive_smart(report)
    return result
