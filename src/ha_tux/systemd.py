from __future__ import annotations

from pathlib import Path
from typing import Final

SERVICE_NAME: Final = "ha-tux.service"
SERVICE_SOURCE: Final = "systemd/ha-tux.service"
USER_SYSTEMD_DIR: Final = "{{.HOME}}/.config/systemd/user"
PROJECT_DIR_PLACEHOLDER: Final = "__PROJECT_DIR__"
EXEC_START_PLACEHOLDER: Final = "__EXEC_START__"


def build_exec_start(_project_dir: Path) -> str:
    return "/usr/bin/env uv run ha-tux"


def render_service_unit(template: str, project_dir: Path) -> str:
    resolved_project_dir = project_dir.resolve()
    return template.replace(
        PROJECT_DIR_PLACEHOLDER,
        resolved_project_dir.as_posix(),
    ).replace(
        EXEC_START_PLACEHOLDER,
        build_exec_start(resolved_project_dir),
    )
