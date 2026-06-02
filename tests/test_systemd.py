from __future__ import annotations

from pathlib import Path

from ha_tux.systemd import build_exec_start, render_service_unit


def test_build_exec_start_runs_project_entrypoint_with_uv(tmp_path: Path) -> None:
    assert build_exec_start(tmp_path) == "/usr/bin/env uv run ha-tux"


def test_render_service_unit_replaces_project_dir_and_exec_start(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    template = """[Service]
WorkingDirectory=__PROJECT_DIR__
ExecStart=__EXEC_START__
"""

    rendered = render_service_unit(template, project_dir)

    assert (
        rendered
        == f"""[Service]
WorkingDirectory={project_dir.resolve().as_posix()}
ExecStart=/usr/bin/env uv run ha-tux
"""
    )
