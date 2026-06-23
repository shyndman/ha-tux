from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import date
from pathlib import Path
from typing import cast

import aiomqtt
from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable._session import PublishPayload, SessionLike
from ha_mqtt_discoverable.sensors import Update
from pytest import MonkeyPatch

import ha_tux.software_update.publisher as publisher
from ha_tux.run_state import StateStore
from ha_tux.software_update.detect import PackageUpdate, UpdateReport
from ha_tux.software_update.publisher import ManagerPublisher

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")


class FakeSession:
    @property
    def discovery_prefix(self) -> str:
        return "homeassistant"

    @property
    def state_prefix(self) -> str:
        return "hmd"

    @property
    def status_topic(self) -> str:
        return "hmd/ha-tux/status"

    async def publish(
        self,
        topic: str,
        payload: PublishPayload,
        *,
        retain: bool = False,
        qos: int = 0,
    ) -> None:
        del topic, payload, retain, qos

    def register_command(
        self,
        topic: str,
        sender: object,
        callback: object,
        *,
        qos: int = 1,
        command_name: str | None = None,
    ) -> None:
        del topic, sender, callback, qos, command_name


def _session() -> SessionLike:
    return cast(SessionLike, cast(object, FakeSession()))


class StubUpdate:
    def __init__(self) -> None:
        self.available: list[bool] = []
        self.states: list[dict[str, object]] = []
        self.progress: list[int] = []

    async def set_available(self, available: bool) -> None:
        self.available.append(available)

    async def set_progress(self, progress: int) -> None:
        self.progress.append(progress)

    async def set_state(
        self,
        *,
        installed: str,
        latest: str | None = None,
        in_progress: bool = False,
        progress: int | None = None,
        title: str | None = None,
        release_summary: str | None = None,
        release_url: str | None = None,
        entity_picture: str | None = None,
    ) -> None:
        del progress, entity_picture
        self.states.append(
            {
                "installed": installed,
                "latest": latest,
                "in_progress": in_progress,
                "title": title,
                "release_summary": release_summary,
                "release_url": release_url,
            }
        )


def _areturn(report: UpdateReport) -> Awaitable[UpdateReport]:
    async def _inner() -> UpdateReport:
        return report

    return _inner()


def _make(
    tmp_path: Path,
    report: UpdateReport,
    *,
    manager: str = "apt",
    label: str = "apt",
    brew: str | None = None,
) -> tuple[ManagerPublisher, StubUpdate]:
    store = StateStore.load(tmp_path / "state.toml")
    mp = ManagerPublisher(
        _session(),
        DEVICE,
        manager=manager,
        label=label,
        query=lambda: _areturn(report),
        state_store=store,
        hostname="host",
        slug=f"host-{manager}-updates",
        description="desc",
        brew=brew,
    )
    stub = StubUpdate()
    mp.entity = cast(Update, cast(object, stub))
    return mp, stub


def _patch_publishers(monkeypatch: MonkeyPatch, short: str | None) -> None:
    async def fake_gist(
        manager: str, body: str, description: str, state: object
    ) -> str | None:
        del manager, body, description, state
        return "https://gist.github.com/abc123"

    async def fake_short(slug: str, gist_url: str, state: object) -> str | None:
        del slug, gist_url, state
        return short

    monkeypatch.setattr(publisher, "publish_gist", fake_gist)
    monkeypatch.setattr(publisher, "publish_shortlink", fake_short)


def test_pending_apt_publishes_state(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _patch_publishers(monkeypatch, "https://go.don.haus/host-apt-updates")
    report = UpdateReport(
        manager="apt",
        label="apt",
        count=2,
        packages=(PackageUpdate("a", "1", "2"), PackageUpdate("b", "1", "2")),
        security=1,
        download_bytes=88080384,
    )
    mp, stub = _make(tmp_path, report)

    asyncio.run(mp.publish())

    assert stub.available == [True]
    assert len(stub.states) == 1
    state = stub.states[0]
    assert state["title"] == "apt: Updates available"
    assert state["latest"] == date.today().isoformat()
    assert state["installed"] == "1970-01-01"
    assert state["release_summary"] is not None
    assert state["release_url"] == "https://go.don.haus/host-apt-updates"


def test_query_failure_marks_unavailable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _patch_publishers(monkeypatch, "https://go.don.haus/host-apt-updates")
    report = UpdateReport(manager="apt", label="apt", count=0, packages=())
    mp, stub = _make(tmp_path, report)

    async def _boom() -> UpdateReport:
        raise RuntimeError("apt exploded")

    mp.query = _boom

    asyncio.run(mp.publish())

    assert stub.available == [False]
    assert stub.states == []


def test_brew_install_marks_in_progress_then_resolves(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _patch_publishers(monkeypatch, "https://go.don.haus/host-brew-updates")
    report = UpdateReport(
        manager="brew", label="homebrew", count=0, packages=(), casks=0, pinned=0
    )
    mp, stub = _make(
        tmp_path, report, manager="brew", label="homebrew", brew="/usr/bin/brew"
    )

    captured: list[list[str]] = []

    async def fake_run(cmd: object, *, env: object = None) -> tuple[int, str, str]:
        del env
        captured.append(list(cast(list[str], cmd)))
        return (0, "", "")

    monkeypatch.setattr(publisher, "_run", fake_run)

    message = cast(aiomqtt.Message, object())
    asyncio.run(mp.on_install(cast(Update, cast(object, stub)), message))

    assert stub.progress == []
    assert captured == [["/usr/bin/brew", "upgrade"]]
    assert len(stub.states) == 2
    assert stub.states[0]["in_progress"] is True
    assert stub.states[1]["title"] == "homebrew: Up to date"
    assert stub.states[1]["in_progress"] is False
    assert (
        stub.states[1]["installed"]
        == stub.states[1]["latest"]
        == date.today().isoformat()
    )
