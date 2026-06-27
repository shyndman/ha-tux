from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import ClassVar, Final, cast

from libsh import get_logger, setup_logging_from_env
from pydantic import BaseModel, ConfigDict

from ha_tux.config import LOGGER_NAME
from ha_tux.smart.report import SMART_REPORT_PATH

LOGGER = logging.getLogger(__name__)

SMART_SCAN_COMMAND: Final = ("smartctl", "--scan-open", "-j")
REPORT_FILE_MODE: Final = 0o644


def _device_command(name: str, dev_type: str) -> tuple[str, ...]:
    return ("smartctl", "-a", "-j", "-d", dev_type, name)


class _ScanDevice(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    name: str
    type: str


class _ScanResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    devices: list[_ScanDevice] = []


def collect_reports(
    devices: Sequence[_ScanDevice],
    read_device: Callable[[_ScanDevice], dict[str, object]],
) -> list[dict[str, object]]:
    drives: list[dict[str, object]] = []
    for dev in devices:
        obj = read_device(dev)
        if isinstance(obj.get("serial_number"), str):
            drives.append(obj)
        else:
            LOGGER.warning("smart_scan_no_serial", extra={"device": dev.name})
    return drives


def _read_device(dev: _ScanDevice) -> dict[str, object]:
    # smartctl exit status is a bitmask where nonzero is normal (e.g. bit 3 =
    # failing-now), so ignore returncode and parse stdout JSON regardless.
    completed = subprocess.run(
        _device_command(dev.name, dev.type),
        check=False,
        capture_output=True,
        text=True,
    )
    return cast(dict[str, object], json.loads(completed.stdout))


def main() -> None:
    setup_logging_from_env()
    logger = get_logger(LOGGER_NAME)
    completed = subprocess.run(
        SMART_SCAN_COMMAND,
        check=False,
        capture_output=True,
        text=True,
    )
    payload = cast(object, json.loads(completed.stdout))
    result = _ScanResult.model_validate(payload)
    drives = collect_reports(result.devices, _read_device)
    report = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "drives": drives,
    }
    SMART_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ = SMART_REPORT_PATH.write_text(json.dumps(report))
    SMART_REPORT_PATH.chmod(REPORT_FILE_MODE)
    logger.info("smart_scan_complete", extra={"drives": len(drives)})
