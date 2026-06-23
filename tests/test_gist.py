from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import final

import pytest

import ha_tux.gist as gist
from ha_tux.gist import publish_gist, publish_shortlink
from ha_tux.run_state import ManagerState


@final
class _Recorder:
    def __init__(self, results: list[tuple[int, str, str]]) -> None:
        self.calls: list[list[str]] = []
        self._results: list[tuple[int, str, str]] = results
        self._index: int = 0

    async def __call__(
        self, cmd: Sequence[str], *, env: Mapping[str, str] | None = None
    ) -> tuple[int, str, str]:
        self.calls.append(list(cmd))
        result = self._results[self._index]
        self._index += 1
        return result


def test_publish_gist_create_parses_id_and_url(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder([(0, "https://gist.github.com/me/abc123\n", "")])
    monkeypatch.setattr(gist, "_run", rec)
    state = ManagerState()

    url = asyncio.run(publish_gist("apt", "body", "desc", state))

    assert url == "https://gist.github.com/me/abc123"
    assert state.gist_id == "abc123"
    assert rec.calls[0][:5] == ["gh", "gist", "create", "--desc", "desc"]
    assert rec.calls[0][-1].endswith("apt-updates.md")


def test_publish_gist_edit_uses_existing_id(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder([(0, "", "")])
    monkeypatch.setattr(gist, "_run", rec)
    state = ManagerState(gist_id="abc123")

    url = asyncio.run(publish_gist("apt", "body", "desc", state))

    assert url == "https://gist.github.com/abc123"
    assert rec.calls[0][:6] == [
        "gh",
        "gist",
        "edit",
        "abc123",
        "--filename",
        "apt-updates.md",
    ]
    assert rec.calls[0][-1].endswith("apt-updates.md")


def test_publish_gist_create_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _Recorder([(1, "", "boom")])
    monkeypatch.setattr(gist, "_run", rec)
    state = ManagerState()

    assert asyncio.run(publish_gist("apt", "body", "desc", state)) is None
    assert state.gist_id is None


def test_publish_shortlink_returns_persisted_without_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _Recorder([])
    monkeypatch.setattr(gist, "_run", rec)
    state = ManagerState(short_url="https://go.don.haus/keep")

    url = asyncio.run(
        publish_shortlink("slug", "https://gist.github.com/abc123", state)
    )

    assert url == "https://go.don.haus/keep"
    assert rec.calls == []


def test_publish_shortlink_fresh_builds_site_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _Recorder(
        [
            (0, "Site URL: https://go.don.haus\n", ""),  # chhoto getconfig
            (0, "", ""),  # chhoto new
        ]
    )
    monkeypatch.setattr(gist, "_run", rec)
    state = ManagerState()

    url = asyncio.run(
        publish_shortlink("host-apt-updates", "https://gist.github.com/abc123", state)
    )

    assert url == "https://go.don.haus/host-apt-updates"
    assert state.short_url == "https://go.don.haus/host-apt-updates"
    assert rec.calls[0] == ["chhoto", "getconfig"]
    assert rec.calls[1] == [
        "chhoto",
        "new",
        "https://gist.github.com/abc123",
        "host-apt-updates",
    ]


def test_publish_shortlink_retries_after_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _Recorder(
        [
            (0, "Site URL: https://go.don.haus\n", ""),  # getconfig
            (1, "", "exists"),  # new fails
            (0, "", ""),  # delete
            (0, "", ""),  # new retry succeeds
        ]
    )
    monkeypatch.setattr(gist, "_run", rec)
    state = ManagerState()

    url = asyncio.run(
        publish_shortlink("slug", "https://gist.github.com/abc123", state)
    )

    assert url == "https://go.don.haus/slug"
    assert rec.calls[2] == ["chhoto", "delete", "slug"]
    assert rec.calls[3] == ["chhoto", "new", "https://gist.github.com/abc123", "slug"]
