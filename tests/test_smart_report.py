from __future__ import annotations

import json
from pathlib import Path

import pytest

from ha_tux.smart.report import DriveSmart, read_smart_reports

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
    "smart_status": {"passed": True},
    "ata_smart_attributes": {
        "table": [
            {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": 3}},
            {"id": 197, "name": "Current_Pending_Sector", "raw": {"value": 0}},
        ]
    },
}


def _write_report(path: Path, drives: list[dict[str, object]]) -> None:
    _ = path.write_text(
        json.dumps({"generated": "2026-06-27T00:00:00+00:00", "drives": drives})
    )


def test_reads_nvme_drive(tmp_path: Path) -> None:
    path = tmp_path / "smart.json"
    _write_report(path, [NVME_DRIVE])

    drives = read_smart_reports(path)

    assert drives == {
        "S7KGNJ0X157882Y": DriveSmart(
            serial="S7KGNJ0X157882Y",
            model="Samsung SSD 990 PRO 4TB",
            firmware="4B2QJXD7",
            protocol="NVMe",
            passed=True,
            percentage_used=0,
            reallocated_sectors=None,
            pending_sectors=None,
        )
    }


def test_reads_sata_attributes(tmp_path: Path) -> None:
    path = tmp_path / "smart.json"
    _write_report(path, [SATA_DRIVE])

    drive = read_smart_reports(path)["WD-XYZ"]

    assert drive.protocol == "ATA"
    assert drive.reallocated_sectors == 3
    assert drive.pending_sectors == 0
    assert drive.percentage_used is None


def test_missing_smart_status_yields_none(tmp_path: Path) -> None:
    drive_obj = {k: v for k, v in NVME_DRIVE.items() if k != "smart_status"}
    path = tmp_path / "smart.json"
    _write_report(path, [drive_obj])

    assert read_smart_reports(path)["S7KGNJ0X157882Y"].passed is None


def test_drive_without_serial_is_skipped(tmp_path: Path) -> None:
    drive_obj = {k: v for k, v in NVME_DRIVE.items() if k != "serial_number"}
    path = tmp_path / "smart.json"
    _write_report(path, [drive_obj, SATA_DRIVE])

    drives = read_smart_reports(path)

    assert set(drives) == {"WD-XYZ"}


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _ = read_smart_reports(tmp_path / "absent.json")
