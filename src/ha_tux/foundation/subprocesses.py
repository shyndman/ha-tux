"""Async subprocess command runner seam and live adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "CommandResult",
    "CommandRunner",
    "subprocess_runner",
]


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Exit status and decoded output from one external command invocation."""

    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Seam for executing external subprocesses and capturing output."""

    async def __call__(
        self,
        cmd: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
    ) -> CommandResult:
        """Run ``cmd`` with optional environment variables and stdin input."""
        ...


async def subprocess_runner(
    cmd: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    stdin: str | None = None,
) -> CommandResult:
    """Live ``CommandRunner`` backed by ``asyncio.create_subprocess_exec``."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(env) if env is not None else None,
    )
    stdout, stderr = await proc.communicate(
        stdin.encode() if stdin is not None else None
    )
    return CommandResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )
