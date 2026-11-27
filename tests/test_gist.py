from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import final

from structlog.testing import capture_logs

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


def test_publish_gist_create_failure_returns_none_and_logs_error() -> None:
    runner = _Recorder([CommandResult(1, "", "boom")])

    with capture_logs() as logs:
        result = asyncio.run(
            publish_gist(
                manager="apt",
                body="body",
                description="desc",
                existing_gist_id=None,
                runner=runner,
            )
        )

    assert result is None
    error_logs = [entry for entry in logs if entry.get("log_level") == "error"]
    assert len(error_logs) == 1
    assert error_logs[0]["error"] == "boom"


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


def test_publish_shortlink_retry_failure_logs_error() -> None:
    runner = _Recorder(
        [
            CommandResult(0, "Site URL: https://go.don.haus\n", ""),
            CommandResult(1, "", "exists"),
            CommandResult(0, "", ""),
            CommandResult(1, "", "still failing"),
        ]
    )

    with capture_logs() as logs:
        url = asyncio.run(
            publish_shortlink(
                slug="slug",
                gist_url="https://gist.github.com/abc123",
                existing_short_url=None,
                runner=runner,
            )
        )

    assert url is None
    error_logs = [entry for entry in logs if entry.get("log_level") == "error"]
    assert len(error_logs) == 1
    assert error_logs[0]["error"] == "still failing"


def test_publish_shortlink_exception_logs_error() -> None:
    async def raising_runner(
        cmd: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
    ) -> CommandResult:
        del cmd, env, stdin
        raise FileNotFoundError("chhoto missing")

    with capture_logs() as logs:
        url = asyncio.run(
            publish_shortlink(
                slug="slug",
                gist_url="https://gist.github.com/abc123",
                existing_short_url=None,
                runner=raising_runner,
            )
        )

    assert url is None
    error_logs = [entry for entry in logs if entry.get("log_level") == "error"]
    assert len(error_logs) >= 1
    assert any("chhoto missing" in str(entry.get("error")) for entry in error_logs)
