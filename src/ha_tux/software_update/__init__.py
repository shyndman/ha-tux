"""Software-update polling feature package."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ha_tux.foundation.subprocesses import CommandRunner, subprocess_runner
from ha_tux.runtime import SessionContext, run_poller
from ha_tux.software_update.detect import DEFAULT_SOFTWARE_UPDATE_POLL_SECONDS
from ha_tux.software_update.publisher import (
    TodayFn,
    build_software_update_publisher,
)
from ha_tux.software_update.state import StateStore, state_file_path

__all__ = [
    "DEFAULT_SOFTWARE_UPDATE_POLL_SECONDS",
    "run_software_update",
]


async def run_software_update(
    ctx: SessionContext,
    *,
    runner: CommandRunner = subprocess_runner,
    today: TodayFn = date.today,
    state_path: Path | None = None,
) -> None:
    """Load persisted update state and periodically poll available package managers."""
    resolved_path = state_path if state_path is not None else state_file_path()
    state_store = StateStore.load(resolved_path)
    publisher = build_software_update_publisher(
        ctx.session,
        ctx.device,
        ctx.hostname,
        state_store,
        runner=runner,
        today=today,
    )
    if publisher is None:
        return

    await run_poller(
        name="software_update",
        interval_seconds=ctx.config.software_update.poll_seconds,
        poll=publisher.publish,
    )
