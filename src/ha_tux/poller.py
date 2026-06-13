from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

LOGGER = logging.getLogger(__name__)


class AsyncPoller:
    """Drives an async ``poll`` callable immediately, then on a fixed interval.

    One failing iteration is logged and swallowed so the loop survives transient
    errors; cancellation stops it cleanly."""

    def __init__(
        self,
        *,
        name: str,
        interval_seconds: float,
        poll: Callable[[], Awaitable[None]],
    ) -> None:
        self._name: str = name
        self._interval_seconds: float = interval_seconds
        self._poll: Callable[[], Awaitable[None]] = poll

    async def run(self) -> None:
        while True:
            try:
                await self._poll()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "poller_iteration_failed", extra={"poller": self._name}
                )
            await asyncio.sleep(self._interval_seconds)
