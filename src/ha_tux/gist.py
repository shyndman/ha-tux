from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Final

from ha_tux.run_state import ManagerState
from ha_tux.software_update.detect import _run

LOGGER = logging.getLogger(__name__)

CHHOTO_SITE_FALLBACK: Final = "https://go.don.haus"

_GIST_URL_PATTERN: Final = re.compile(
    r"https://gist\.github\.com/(?:[^/\s]+/)?([0-9a-f]+)"
)
_SITE_URL_PATTERN: Final = re.compile(r"^Site URL:\s*(\S+)", re.MULTILINE)


async def publish_gist(
    manager: str, body: str, description: str, state: ManagerState
) -> str | None:
    """Create or update the per-manager secret gist. Best-effort: None on failure."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = f"{manager}-updates.md"
            tmpfile = Path(tmpdir) / filename
            _ = tmpfile.write_text(body)
            if state.gist_id is None:
                rc, out, err = await _run(
                    ["gh", "gist", "create", "--desc", description, str(tmpfile)]
                )
                if rc != 0:
                    LOGGER.info("gist create failed for %s: %s", manager, err.strip())
                    return None
                match = _GIST_URL_PATTERN.search(out)
                if match is None:
                    LOGGER.info(
                        "gist create returned no url for %s: %s", manager, out.strip()
                    )
                    return None
                state.gist_id = match.group(1)
                return match.group(0)
            rc, _out, err = await _run(
                [
                    "gh",
                    "gist",
                    "edit",
                    state.gist_id,
                    "--filename",
                    filename,
                    str(tmpfile),
                ]
            )
            if rc != 0:
                LOGGER.info("gist edit failed for %s: %s", manager, err.strip())
                return None
            return f"https://gist.github.com/{state.gist_id}"
    except Exception as error:
        LOGGER.info("gist publish error for %s: %s", manager, error)
        return None


async def chhoto_site_url() -> str | None:
    """Parse the ``Site URL:`` line of ``chhoto getconfig``; None on failure."""
    try:
        rc, out, _err = await _run(["chhoto", "getconfig"])
        if rc != 0:
            return None
        match = _SITE_URL_PATTERN.search(out)
        if match is None:
            return None
        return match.group(1)
    except Exception as error:
        LOGGER.info("chhoto getconfig error: %s", error)
        return None


async def publish_shortlink(
    slug: str, gist_url: str, state: ManagerState
) -> str | None:
    """Create-once chhoto shortlink for the gist. Best-effort: None on failure."""
    if state.short_url is not None:
        return state.short_url
    try:
        base = await chhoto_site_url() or CHHOTO_SITE_FALLBACK
        rc, _out, _err = await _run(["chhoto", "new", gist_url, slug])
        if rc != 0:
            # Slug may already exist from lost state pointing at a stale gist.
            _ = await _run(["chhoto", "delete", slug])
            rc, _out, _err = await _run(["chhoto", "new", gist_url, slug])
            if rc != 0:
                LOGGER.info("chhoto new failed for %s: %s", slug, _err.strip())
                return None
        state.short_url = f"{base}/{slug}"
        return state.short_url
    except Exception as error:
        LOGGER.info("chhoto publish error for %s: %s", slug, error)
        return None
