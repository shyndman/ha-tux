"""Software-update Home Assistant entity publishing and command execution."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date

import aiomqtt
from ha_mqtt_discoverable import DeviceInfo, SessionLike
from ha_mqtt_discoverable.sensors import Update, UpdateInfo
from libsh import get_logger

from ha_tux.foundation.strings import slugify
from ha_tux.foundation.subprocesses import CommandRunner, subprocess_runner
from ha_tux.foundation.time import parse_iso_date
from ha_tux.host_device import host_slug
from ha_tux.software_update.detect import (
    APT_UPGRADE_UNIT,
    BREW_UPGRADE_UNIT,
    SENTINEL_DATE,
    SYSTEMCTL_PATH,
    SurfaceDecision,
    UpdateReport,
    decide_surface,
    entity_title,
    gist_body,
    query_apt,
    query_brew,
    release_summary,
)
from ha_tux.software_update.gist import publish_gist, publish_shortlink
from ha_tux.software_update.state import ManagerState, StateStore

LOGGER = get_logger(__name__)

TodayFn = Callable[[], date]


def _next_state(
    *,
    previous: ManagerState,
    decision: SurfaceDecision,
    gist_id: str | None,
    short_url: str | None,
) -> ManagerState:
    return ManagerState(
        gist_id=gist_id if gist_id is not None else previous.gist_id,
        short_url=short_url if short_url is not None else previous.short_url,
        notify_anchor=(
            decision.notify_anchor.isoformat() if decision.notify_anchor else None
        ),
        last_install_date=(
            decision.last_install_date.isoformat()
            if decision.last_install_date
            else None
        ),
        pending_set=tuple(sorted(decision.pending)),
    )


class ManagerPublisher:
    """Publishes one package manager's ``Update`` entity and runs upgrades on demand."""

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
        host_prefix: str,
        slug: str,
        description: str,
        install_cmd: Sequence[str] | None = None,
        runner: CommandRunner = subprocess_runner,
        today: TodayFn = date.today,
    ) -> None:
        self.manager: str = manager
        self.label: str = label
        self.query: Callable[[], Awaitable[UpdateReport]] = query
        self.state_store: StateStore = state_store
        self.hostname: str = hostname
        self.slug: str = slug
        self.description: str = description
        self.install_cmd: tuple[str, ...] | None = (
            tuple(install_cmd) if install_cmd is not None else None
        )
        self._runner: CommandRunner = runner
        self._today: TodayFn = today
        self._lock: asyncio.Lock = asyncio.Lock()
        self._installed: str = SENTINEL_DATE
        self._latest: str = SENTINEL_DATE

        info = UpdateInfo(
            device=device,
            unique_id=f"ha_tux_{host_prefix}_software_update_{manager}",
            object_id=f"{host_prefix}_software_update_{manager}",
            name=f"{label.capitalize()} updates",
        )
        # An install command means an Install button; read-only entities pass None.
        callback = self.on_install if install_cmd is not None else None
        self.entity: Update = Update(session, info, callback)

    async def publish(self) -> None:
        """Query the package manager under lock and publish updated entity state."""
        async with self._lock:
            await self._publish_unlocked()

    async def _publish_links(
        self, report: UpdateReport, today: date, previous: ManagerState
    ) -> tuple[str | None, str | None]:
        gist_result = await publish_gist(
            manager=self.manager,
            body=gist_body(report, self.hostname, today.isoformat()),
            description=self.description,
            existing_gist_id=previous.gist_id,
            runner=self._runner,
        )
        if gist_result is None:
            return (None, previous.short_url)

        short = await publish_shortlink(
            slug=self.slug,
            gist_url=gist_result.url,
            existing_short_url=previous.short_url,
            runner=self._runner,
        )
        return (gist_result.gist_id, short)

    async def _publish_unlocked(self) -> None:
        try:
            report = await self.query()
        except Exception:
            LOGGER.exception(
                "Failed to query pending software updates", manager=self.manager
            )
            await self.entity.set_available(False)
            return

        today = self._today()
        previous = self.state_store.get(self.manager)
        current = frozenset(p.name for p in report.packages if not p.pinned)
        decision = decide_surface(
            current=current,
            previous=frozenset(previous.pending_set),
            notify_anchor=parse_iso_date(previous.notify_anchor),
            last_install_date=parse_iso_date(previous.last_install_date),
            today=today,
        )
        self._installed, self._latest = decision.installed, decision.latest

        gist_id, short = await self._publish_links(report, today, previous)
        next_state = _next_state(
            previous=previous,
            decision=decision,
            gist_id=gist_id,
            short_url=short,
        )
        self.state_store.set(self.manager, next_state)
        self.state_store.save()

        await self.entity.set_available(True)
        await self.entity.set_state(
            installed=decision.installed,
            latest=decision.latest,
            title=entity_title(self.label, report.count if decision.surfaced else 0),
            release_summary=release_summary(report) if decision.surfaced else None,
            release_url=next_state.short_url,
        )

    async def on_install(self, sender: Update, _message: aiomqtt.Message) -> None:
        """Execute the configured upgrade command and republish fresh state."""
        async with self._lock:
            if self.install_cmd is None:
                return

            await sender.set_state(
                installed=self._installed, latest=self._latest, in_progress=True
            )
            result = await self._runner(self.install_cmd)
            LOGGER.info(
                "Software update install command finished",
                manager=self.manager,
                returncode=result.returncode,
            )
            if result.returncode != 0:
                LOGGER.warning(
                    "Software update install command exited with error",
                    manager=self.manager,
                    error=(result.stderr or result.stdout).strip(),
                )
            await self._publish_unlocked()


class SoftwareUpdatePublisher:
    """Publishes updates across every detected package manager on the host."""

    def __init__(self, managers: tuple[ManagerPublisher, ...]) -> None:
        self._managers: tuple[ManagerPublisher, ...] = managers

    async def publish(self) -> None:
        """Poll and publish each configured package manager sequentially."""
        for manager in self._managers:
            await manager.publish()


@dataclass(frozen=True, slots=True)
class _ManagerSpec:
    binary: str
    manager: str
    label: str
    upgrade_unit: str
    query: Callable[[str, CommandRunner], Awaitable[UpdateReport]]


_MANAGER_SPECS: tuple[_ManagerSpec, ...] = (
    _ManagerSpec(
        "apt-get",
        "apt",
        "apt",
        APT_UPGRADE_UNIT,
        lambda _exe, runner: query_apt(runner=runner),
    ),
    _ManagerSpec(
        "brew",
        "brew",
        "homebrew",
        BREW_UPGRADE_UNIT,
        lambda exe, runner: query_brew(exe, runner=runner),
    ),
)


def build_software_update_publisher(
    session: SessionLike,
    device: DeviceInfo,
    hostname: str,
    state_store: StateStore,
    *,
    runner: CommandRunner = subprocess_runner,
    today: TodayFn = date.today,
    which: Callable[[str], str | None] = shutil.which,
) -> SoftwareUpdatePublisher | None:
    """Construct publishers for every available package manager, or ``None`` if none."""
    host_prefix = host_slug(hostname)
    topic_host_slug = slugify(hostname)
    managers: list[ManagerPublisher] = []

    for spec in _MANAGER_SPECS:
        executable = which(spec.binary)
        if executable is None:
            continue

        managers.append(
            ManagerPublisher(
                session,
                device,
                manager=spec.manager,
                label=spec.label,
                query=lambda exe=executable, fn=spec.query: fn(exe, runner),
                state_store=state_store,
                hostname=hostname,
                host_prefix=host_prefix,
                slug=f"{topic_host_slug}-{spec.manager}-updates",
                description=f"ha-tux {spec.label} updates on {hostname}",
                install_cmd=(SYSTEMCTL_PATH, "start", "--wait", spec.upgrade_unit),
                runner=runner,
                today=today,
            )
        )

    if not managers:
        LOGGER.error(
            "No supported package managers found on PATH",
            expected=[spec.binary for spec in _MANAGER_SPECS],
        )
        return None
    return SoftwareUpdatePublisher(tuple(managers))
