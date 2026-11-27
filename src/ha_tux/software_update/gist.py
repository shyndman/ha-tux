"""Best-effort secret Gist and ``chhoto`` shortlink publication."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from libsh import get_logger

from ha_tux.foundation.subprocesses import CommandRunner, subprocess_runner

LOGGER = get_logger("apt")

CHHOTO_SITE_FALLBACK: Final = "https://go.don.haus"

_GIST_URL_PATTERN: Final = re.compile(
    r"https://gist\.github\.com/(?:[^/\s]+/)?([0-9a-f]+)"
)
_SITE_URL_PATTERN: Final = re.compile(r"^Site URL:\s*(\S+)", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class GistResult:
    """Published Gist URL paired with its canonical id."""

    url: str
    gist_id: str


async def _create_gist(
    *,
    manager: str,
    description: str,
    filename: str,
    body: str,
    runner: CommandRunner,
) -> GistResult | None:
    result = await runner(
        ["gh", "gist", "create", "--desc", description, "--filename", filename, "-"],
        stdin=body,
    )
    if result.returncode != 0:
        LOGGER.error(
            "Failed to create update summary gist",
            manager=manager,
            error=result.stderr.strip(),
        )
        return None

    match = _GIST_URL_PATTERN.search(result.stdout)
    if match is None:
        LOGGER.error(
            "GitHub CLI did not return a gist URL",
            manager=manager,
            output=result.stdout.strip(),
        )
        return None

    return GistResult(url=match.group(0), gist_id=match.group(1))


async def _edit_gist(
    *,
    manager: str,
    gist_id: str,
    filename: str,
    body: str,
    runner: CommandRunner,
) -> GistResult | None:
    result = await runner(
        ["gh", "gist", "edit", gist_id, "--filename", filename, "-"],
        stdin=body,
    )
    if result.returncode != 0:
        LOGGER.error(
            "Failed to update existing gist",
            manager=manager,
            gist_id=gist_id,
            error=result.stderr.strip(),
        )
        return None

    return GistResult(url=f"https://gist.github.com/{gist_id}", gist_id=gist_id)


async def publish_gist(
    *,
    manager: str,
    body: str,
    description: str,
    existing_gist_id: str | None,
    runner: CommandRunner = subprocess_runner,
) -> GistResult | None:
    """Create or update the per-manager secret gist. Returns ``None`` on failure."""
    filename = f"{manager}-updates.md"
    try:
        if existing_gist_id is None:
            return await _create_gist(
                manager=manager,
                description=description,
                filename=filename,
                body=body,
                runner=runner,
            )

        return await _edit_gist(
            manager=manager,
            gist_id=existing_gist_id,
            filename=filename,
            body=body,
            runner=runner,
        )
    except Exception as error:
        LOGGER.error(
            "Unexpected error while publishing update gist",
            manager=manager,
            error=str(error),
        )
        return None


async def chhoto_site_url(*, runner: CommandRunner = subprocess_runner) -> str | None:
    """Parse the ``Site URL:`` line of ``chhoto getconfig``; ``None`` on failure."""
    try:
        result = await runner(["chhoto", "getconfig"])
        if result.returncode != 0:
            return None

        match = _SITE_URL_PATTERN.search(result.stdout)
        if match is None:
            return None

        return match.group(1)
    except Exception as error:
        LOGGER.error("Failed to read chhoto configuration", error=str(error))
        return None


async def publish_shortlink(
    *,
    slug: str,
    gist_url: str,
    existing_short_url: str | None,
    runner: CommandRunner = subprocess_runner,
) -> str | None:
    """Create a once-persisted ``chhoto`` shortlink for ``gist_url``; ``None`` on failure."""
    if existing_short_url is not None:
        return existing_short_url

    try:
        base = await chhoto_site_url(runner=runner) or CHHOTO_SITE_FALLBACK
        result = await runner(["chhoto", "new", gist_url, slug])
        if result.returncode != 0:
            # Slug may already exist from lost state pointing at a stale gist.
            _ = await runner(["chhoto", "delete", slug])
            result = await runner(["chhoto", "new", gist_url, slug])
            if result.returncode != 0:
                LOGGER.error(
                    "Failed to create chhoto shortlink after retry",
                    slug=slug,
                    error=result.stderr.strip(),
                )
                return None

        return f"{base}/{slug}"
    except Exception as error:
        LOGGER.error(
            "Unexpected error while publishing chhoto shortlink",
            slug=slug,
            error=str(error),
        )
        return None
