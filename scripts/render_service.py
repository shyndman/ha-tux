from __future__ import annotations

import os
from pathlib import Path

from ha_tux.systemd import SERVICE_SOURCE, render_service_unit

PROJECT_DIR_ENV = "PROJECT_DIR"
SERVICE_SOURCE_ENV = "SERVICE_SOURCE"
SERVICE_TARGET_ENV = "SERVICE_TARGET"


def main() -> None:
    project_dir = _required_path(PROJECT_DIR_ENV)
    service_source_value = os.environ.get(SERVICE_SOURCE_ENV)
    service_source = Path(
        SERVICE_SOURCE if service_source_value is None else service_source_value
    )
    service_target = _required_path(SERVICE_TARGET_ENV)

    template = service_source.read_text(encoding="utf-8")
    _ = service_target.write_text(
        render_service_unit(template, project_dir),
        encoding="utf-8",
    )


def _required_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return Path(value)


if __name__ == "__main__":
    main()
