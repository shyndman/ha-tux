from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import final

from ha_tux.foundation.subprocesses import CommandResult
from ha_tux.software_update.gist import GistResult, publish_gist, publish_shortlink


@final
class _Recorder:
    def __init__(self, results: list[CommandResult]) -> None:
        self.calls: list[list[str]] = []
        self.stdins: list[str | None] = []
        self._results: list[CommandResult] = results
        self._index: int = 0

    async def __call__(
        self,
        cmd: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
    ) -> CommandResult:
        del env
        self.calls.append(list(cmd))
        self.stdins.append(stdin)
        result = self._results[self._index]
        self._index += 1
        return result


def test_publish_gist_create_parses_id_and_url() -> None:
    runner = _Recorder([CommandResult(0, "https://gist.github.com/me/abc123\n", "")])

    result = asyncio.run(
        publish_gist(
            manager="apt",
            body="body",
            description="desc",
            existing_gist_id=None,
            runner=runner,
        )
    )

    assert result == GistResult(
        url="https://gist.github.com/me/abc123", gist_id="abc123"
    )
    assert runner.calls == [
        [
            "gh",
            "gist",
            "create",
            "--desc",
            "desc",
            "--filename",
            "apt-updates.md",
            "-",
        ]
    ]
    assert runner.stdins == ["body"]


def test_publish_gist_edit_uses_existing_id() -> None:
    runner = _Recorder([CommandResult(0, "", "")])

    result = asyncio.run(
        publish_gist(
            manager="apt",
            body="body",
            description="desc",
            existing_gist_id="abc123",
            runner=runner,
        )
    )

    assert result == GistResult(url="https://gist.github.com/abc123", gist_id="abc123")
    assert runner.calls == [
        [
            "gh",
            "gist",
            "edit",
            "abc123",
            "--filename",
            "apt-updates.md",
            "-",
        ]
    ]
    assert runner.stdins == ["body"]


def test_publish_gist_create_failure_returns_none() -> None:
    runner = _Recorder([CommandResult(1, "", "boom")])

    assert (
        asyncio.run(
            publish_gist(
                manager="apt",
                body="body",
                description="desc",
                existing_gist_id=None,
                runner=runner,
            )
        )
        is None
    )


def test_publish_shortlink_returns_persisted_without_run() -> None:
    runner = _Recorder([])

    url = asyncio.run(
        publish_shortlink(
            slug="slug",
            gist_url="https://gist.github.com/abc123",
            existing_short_url="https://go.don.haus/keep",
            runner=runner,
        )
    )

    assert url == "https://go.don.haus/keep"
    assert runner.calls == []


def test_publish_shortlink_fresh_builds_site_slug() -> None:
    runner = _Recorder(
        [
            CommandResult(0, "Site URL: https://go.don.haus\n", ""),
            CommandResult(0, "", ""),
        ]
    )

    url = asyncio.run(
        publish_shortlink(
            slug="host-apt-updates",
            gist_url="https://gist.github.com/abc123",
            existing_short_url=None,
            runner=runner,
        )
    )

    assert url == "https://go.don.haus/host-apt-updates"
    assert runner.calls[0] == ["chhoto", "getconfig"]
    assert runner.calls[1] == [
        "chhoto",
        "new",
        "https://gist.github.com/abc123",
        "host-apt-updates",
    ]


def test_publish_shortlink_retries_after_delete() -> None:
    runner = _Recorder(
        [
            CommandResult(0, "Site URL: https://go.don.haus\n", ""),
            CommandResult(1, "", "exists"),
            CommandResult(0, "", ""),
            CommandResult(0, "", ""),
        ]
    )

    url = asyncio.run(
        publish_shortlink(
            slug="slug",
            gist_url="https://gist.github.com/abc123",
            existing_short_url=None,
            runner=runner,
        )
    )

    assert url == "https://go.don.haus/slug"
    assert runner.calls[2] == ["chhoto", "delete", "slug"]
    assert runner.calls[3] == [
        "chhoto",
        "new",
        "https://gist.github.com/abc123",
        "slug",
    ]
