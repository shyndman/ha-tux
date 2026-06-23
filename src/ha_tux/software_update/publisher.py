from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from datetime import date

import aiomqtt
from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable._session import SessionLike
from ha_mqtt_discoverable.sensors import Update, UpdateInfo

from ha_tux.gist import publish_gist, publish_shortlink
from ha_tux.run_state import StateStore
from ha_tux.software_update.detect import (
    SENTINEL_DATE,
    UpdateReport,
    _run,
    entity_title,
    gist_body,
    query_apt,
    query_brew,
    release_summary,
    slugify,
    version_pair,
)

LOGGER = logging.getLogger(__name__)


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class ManagerPublisher:
    def __init__(
        self,
        session: SessionLike,
        device: DeviceInfo,
        *,
        manager: str,
        label: str,
        query: Callable[[], Awaitable[UpdateReport]],
        state_store: StateStore,
        hostname: str,
        slug: str,
        description: str,
        brew: str | None = None,
    ) -> None:
        self.manager: str = manager
        self.label: str = label
        self.query: Callable[[], Awaitable[UpdateReport]] = query
        self.state_store: StateStore = state_store
        self.hostname: str = hostname
        self.slug: str = slug
        self.description: str = description
        self.brew: str | None = brew
        self._lock: asyncio.Lock = asyncio.Lock()
        self._installed: str = SENTINEL_DATE
        self._latest: str = SENTINEL_DATE

        info = UpdateInfo(
            device=device,
            unique_id=f"ha_tux_software_update_{manager}",
            object_id=f"software_update_{manager}",
            name=f"{label.capitalize()} updates",
        )
        # brew is user-owned, so it gets an Install button; apt is read-only.
        callback = self.on_install if brew is not None else None
        self.entity: Update = Update(session, info, callback)

    async def publish(self) -> None:
        async with self._lock:
            await self._publish_unlocked()

    async def _publish_unlocked(self) -> None:
        try:
            report = await self.query()
        except Exception:
            LOGGER.exception(
                "software_update_query_failed", extra={"manager": self.manager}
            )
            await self.entity.set_available(False)
            return

        today = date.today()
        refresh = today.isoformat()
        st = self.state_store.get(self.manager)
        last_clean = _parse_date(st.last_clean_date)
        installed, latest = version_pair(report.count, last_clean, today)
        self._installed, self._latest = installed, latest

        if report.count == 0:
            self.state_store.update(self.manager, last_clean_date=refresh)

        body = gist_body(report, self.hostname, refresh)
        gist_url = await publish_gist(self.manager, body, self.description, st)
        short = (
            await publish_shortlink(self.slug, gist_url, st)
            if gist_url
            else st.short_url
        )

        self.state_store.update(
            self.manager,
            gist_id=st.gist_id,
            short_url=short or st.short_url,
        )
        self.state_store.save()

        await self.entity.set_available(True)
        await self.entity.set_state(
            installed=installed,
            latest=latest,
            title=entity_title(self.label, report.count),
            release_summary=release_summary(report),
            release_url=short,
        )

    async def on_install(self, sender: Update, _message: aiomqtt.Message) -> None:
        async with self._lock:
            if self.brew is None:
                return
            await sender.set_state(
                installed=self._installed, latest=self._latest, in_progress=True
            )
            rc, out, err = await _run([self.brew, "upgrade"])
            LOGGER.info(
                "software_update_install_finished",
                extra={"manager": self.manager, "returncode": rc},
            )
            if rc != 0:
                LOGGER.warning("brew upgrade failed: %s", (err or out).strip())
            await self._publish_unlocked()


class SoftwareUpdatePublisher:
    def __init__(self, managers: tuple[ManagerPublisher, ...]) -> None:
        self._managers: tuple[ManagerPublisher, ...] = managers

    async def publish(self) -> None:
        for manager in self._managers:
            await manager.publish()


def build_software_update_publisher(
    session: SessionLike,
    device: DeviceInfo,
    hostname: str,
    state_store: StateStore,
) -> SoftwareUpdatePublisher | None:
    host_slug = slugify(hostname)
    managers: list[ManagerPublisher] = []

    apt_get = shutil.which("apt-get")
    if apt_get is not None:
        managers.append(
            ManagerPublisher(
                session,
                device,
                manager="apt",
                label="apt",
                query=lambda apt_get=apt_get: query_apt(apt_get),
                state_store=state_store,
                hostname=hostname,
                slug=f"{host_slug}-apt-updates",
                description=f"ha-tux apt updates on {hostname}",
            )
        )

    brew = shutil.which("brew")
    if brew is not None:
        managers.append(
            ManagerPublisher(
                session,
                device,
                manager="brew",
                label="homebrew",
                query=lambda brew=brew: query_brew(brew),
                state_store=state_store,
                hostname=hostname,
                slug=f"{host_slug}-brew-updates",
                description=f"ha-tux homebrew updates on {hostname}",
                brew=brew,
            )
        )

    if not managers:
        return None
    return SoftwareUpdatePublisher(tuple(managers))
