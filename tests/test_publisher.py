from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import cast

from ha_mqtt_discoverable import DeviceInfo

from ha_tux.foundation.subprocesses import CommandResult
from ha_tux.software_update.detect import PackageUpdate, UpdateReport
from ha_tux.software_update.publisher import ManagerPublisher
from ha_tux.software_update.state import StateStore
from tests.fakes import InMemorySession

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")


class FakeRunner:
    """In-memory ``CommandRunner`` handling ``gh``, ``chhoto``, and ``systemctl`` calls."""

    def __init__(self, *, install_rc: int = 0) -> None:
        self.commands: list[list[str]] = []
        self._install_rc: int = install_rc

    async def __call__(
        self, cmd: Sequence[str], *, env: Mapping[str, str] | None = None
    ) -> CommandResult:
        del env
        args = list(cmd)
        self.commands.append(args)

        if args[:3] == ["gh", "gist", "create"]:
            return CommandResult(0, "https://gist.github.com/me/abc123\n", "")
        if args[:3] == ["gh", "gist", "edit"]:
            return CommandResult(0, "", "")
        if args[:2] == ["chhoto", "getconfig"]:
            return CommandResult(0, "Site URL: https://go.don.haus\n", "")
        if args[:2] == ["chhoto", "new"]:
            return CommandResult(0, "", "")
        if len(args) >= 2 and args[1] == "start":
            return CommandResult(self._install_rc, "", "")
        raise AssertionError(f"Unexpected command: {args}")


def _state_payloads(session: InMemorySession, manager: str) -> list[dict[str, object]]:
    topic = f"hmd/update/ha-tux/testbox_software_update_{manager}/state"
    return [
        cast(dict[str, object], json.loads(msg.payload))
        for msg in session.published
        if msg.topic == topic and isinstance(msg.payload, str)
    ]


def _availabilities(session: InMemorySession, manager: str) -> list[str]:
    topic = f"hmd/update/ha-tux/testbox_software_update_{manager}/availability"
    return [str(msg.payload) for msg in session.published if msg.topic == topic]


def _make_publisher(
    tmp_path: Path,
    *,
    query: Callable[[], Awaitable[UpdateReport]],
    today: Callable[[], date],
    runner: FakeRunner | None = None,
    manager: str = "apt",
    label: str = "apt",
    install_cmd: Sequence[str] | None = None,
) -> tuple[ManagerPublisher, InMemorySession, FakeRunner, StateStore, Path]:
    session = InMemorySession()
    used_runner = runner if runner is not None else FakeRunner()
    state_path = tmp_path / "state.toml"
    store = StateStore.load(state_path)
    publisher = ManagerPublisher(
        session,
        DEVICE,
        manager=manager,
        label=label,
        query=query,
        state_store=store,
        hostname="host",
        host_prefix="testbox",
        slug=f"host-{manager}-updates",
        description=f"updates for {manager}",
        install_cmd=install_cmd,
        runner=used_runner,
        today=today,
    )
    return publisher, session, used_runner, store, state_path


def test_pending_apt_publishes_mqtt_state_and_persists_state_file(
    tmp_path: Path,
) -> None:
    report = UpdateReport(
        manager="apt",
        label="apt",
        count=2,
        packages=(PackageUpdate("a", "1", "2"), PackageUpdate("b", "1", "2")),
        security=1,
        download_bytes=88080384,
    )

    async def query() -> UpdateReport:
        return report

    fixed_today = date(2026, 6, 23)
    publisher, session, _runner, _store, state_path = _make_publisher(
        tmp_path, query=query, today=lambda: fixed_today
    )

    asyncio.run(publisher.publish())

    assert _availabilities(session, "apt") == ["online"]
    states = _state_payloads(session, "apt")
    assert len(states) == 1
    assert states[0]["title"] == "apt: Updates available"
    assert states[0]["installed_version"] == "1970-01-01"
    assert states[0]["latest_version"] == "2026-06-23"
    assert states[0]["release_summary"] is not None
    assert states[0]["release_url"] == "https://go.don.haus/host-apt-updates"
    assert states[0]["in_progress"] is False

    reloaded = StateStore.load(state_path).get("apt")
    assert reloaded.gist_id == "abc123"
    assert reloaded.short_url == "https://go.don.haus/host-apt-updates"
    assert reloaded.notify_anchor == "2026-06-23"
    assert reloaded.pending_set == ("a", "b")
    assert reloaded.last_install_date is None


def test_query_failure_marks_real_entity_offline(tmp_path: Path) -> None:
    async def failing_query() -> UpdateReport:
        raise RuntimeError("apt exploded")

    fixed_today = date(2026, 6, 23)
    publisher, session, _runner, _store, state_path = _make_publisher(
        tmp_path, query=failing_query, today=lambda: fixed_today
    )

    asyncio.run(publisher.publish())

    assert _availabilities(session, "apt") == ["offline"]
    assert _state_payloads(session, "apt") == []
    assert not state_path.exists()


def test_cooldown_suppresses_then_resurfaces_after_two_days(tmp_path: Path) -> None:
    current_day = date(2026, 6, 23)
    current_report = UpdateReport(
        manager="apt",
        label="apt",
        count=2,
        packages=(PackageUpdate("a", "1", "2"), PackageUpdate("b", "1", "2")),
    )

    async def query() -> UpdateReport:
        return current_report

    publisher, session, _runner, _store, _path = _make_publisher(
        tmp_path, query=query, today=lambda: current_day
    )

    async def scenario() -> None:
        nonlocal current_day, current_report
        await publisher.publish()

        # Package "a" was applied on day+1 while "b" remains -> triggers cooldown.
        current_day = current_day + timedelta(days=1)
        current_report = UpdateReport(
            manager="apt",
            label="apt",
            count=1,
            packages=(PackageUpdate("b", "1", "2"),),
        )
        await publisher.publish()

        # Day+2 is still within 2-day cooldown; day+3 resurfaces.
        current_day = current_day + timedelta(days=2)
        await publisher.publish()

    asyncio.run(scenario())

    states = _state_payloads(session, "apt")
    assert [s["title"] for s in states] == [
        "apt: Updates available",
        "apt: Up to date",
        "apt: Update available",
    ]


def test_install_command_via_mqtt_marks_in_progress_then_resolves(
    tmp_path: Path,
) -> None:
    fixed_today = date(2026, 6, 25)
    reports = [
        UpdateReport(
            manager="brew",
            label="homebrew",
            count=1,
            packages=(PackageUpdate("ripgrep", "14.0", "14.1"),),
            casks=0,
            pinned=0,
        ),
        UpdateReport(
            manager="brew", label="homebrew", count=0, packages=(), casks=0, pinned=0
        ),
    ]

    async def query() -> UpdateReport:
        return reports.pop(0) if len(reports) > 1 else reports[0]

    install_cmd = (
        "/usr/bin/systemctl",
        "start",
        "--wait",
        "ha-tux-brew-upgrade.service",
    )
    publisher, session, runner, _store, _path = _make_publisher(
        tmp_path,
        query=query,
        today=lambda: fixed_today,
        manager="brew",
        label="homebrew",
        install_cmd=install_cmd,
    )

    async def scenario() -> None:
        await publisher.publish()
        session.published.clear()
        await session.deliver(session.command_topics[0], b"INSTALL")

    asyncio.run(scenario())

    assert [cmd for cmd in runner.commands if cmd[0] == "/usr/bin/systemctl"] == [
        list(install_cmd)
    ]
    states = _state_payloads(session, "brew")
    assert len(states) == 2
    assert states[0]["in_progress"] is True
    assert states[1]["in_progress"] is False
    assert states[1]["title"] == "homebrew: Up to date"
    assert states[1]["installed_version"] == "2026-06-25"
    assert states[1]["latest_version"] == "2026-06-25"
