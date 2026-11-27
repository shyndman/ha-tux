from __future__ import annotations

import asyncio
import sys

from ha_tux.foundation.subprocesses import subprocess_runner


def test_subprocess_runner_captures_stdout_without_stdin() -> None:
    result = asyncio.run(subprocess_runner([sys.executable, "-c", "print('hello')"]))

    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""


def test_subprocess_runner_streams_stdin() -> None:
    result = asyncio.run(
        subprocess_runner(
            [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
            stdin="from stdin",
        )
    )

    assert result.returncode == 0
    assert result.stdout == "FROM STDIN\n"
