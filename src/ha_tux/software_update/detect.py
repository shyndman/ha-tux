"""Package update detection, cooldown gating, and release formatting."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar, Final, cast

from libsh import get_logger
from pydantic import BaseModel, ConfigDict

from ha_tux.config import DEFAULT_SOFTWARE_UPDATE_POLL_SECONDS
from ha_tux.software_update.runner import CommandRunner, subprocess_runner

LOGGER = get_logger(__name__)

__all__ = [
    "APT_CHECK_PATH",
    "APT_NEVER_PHASED_OPT",
    "APT_UPGRADE_UNIT",
    "BREW_UPGRADE_UNIT",
    "DEFAULT_SOFTWARE_UPDATE_POLL_SECONDS",
    "REBOOT_REQUIRED_PATH",
    "REBOOT_REQUIRED_PKGS_PATH",
    "SYSTEMCTL_PATH",
    "SENTINEL_DATE",
    "SURFACE_COOLDOWN_DAYS",
    "PackageUpdate",
    "SurfaceDecision",
    "UpdateReport",
    "decide_surface",
    "entity_title",
    "gist_body",
    "human_bytes",
    "parse_apt_check",
    "parse_apt_download_bytes",
    "parse_apt_installable",
    "parse_apt_upgradable",
    "parse_brew_outdated",
    "query_apt",
    "query_brew",
    "read_reboot_required",
    "release_summary",
    "slugify",
    "status_word",
]

SURFACE_COOLDOWN_DAYS: Final = 2
SENTINEL_DATE: Final = "1970-01-01"
APT_CHECK_PATH: Final = "/usr/lib/update-notifier/apt-check"
SYSTEMCTL_PATH: Final = "/usr/bin/systemctl"
APT_UPGRADE_UNIT: Final = "ha-tux-apt-upgrade.service"
BREW_UPGRADE_UNIT: Final = "ha-tux-brew-upgrade.service"
REBOOT_REQUIRED_PATH: Final = "/run/reboot-required"
REBOOT_REQUIRED_PKGS_PATH: Final = "/run/reboot-required.pkgs"
# The host service runs in a systemd sandbox (ProtectHome/PrivateTmp/namespaces)
# where ischroot(1) returns true, so apt disables phased-update deferral and the
# simulation lists phased packages that the unsandboxed root `apt-get upgrade`
# (ha-tux-apt-upgrade.service) would NOT install. Forcing deferral keeps the
# simulation's Inst lines in parity with what the Install button actually does.
APT_NEVER_PHASED_OPT: Final = "APT::Get::Never-Include-Phased-Updates=true"

_UPGRADABLE_RE: Final = re.compile(
    r"^(\S+?)/\S+\s+(\S+)\s+\S+\s+\[upgradable from:\s*(\S+)\]"
)
_APT_CHECK_RE: Final = re.compile(r"(\d+);(\d+)")
_DOWNLOAD_RE: Final = re.compile(r"Need to get\s+([\d.,]+)\s*([kKMG]?B)")
_APT_INST_RE: Final = re.compile(r"^Inst\s+(\S+)", re.MULTILINE)
_SLUG_RE: Final = re.compile(r"[^a-z0-9]+")
_UNIT_FACTORS: Final = {
    "B": 1,
    "kB": 1024,
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
}


@dataclass(frozen=True, slots=True)
class PackageUpdate:
    name: str
    installed: str
    available: str
    pinned: bool = False


@dataclass(frozen=True, slots=True)
class UpdateReport:
    manager: str  # "apt" | "brew"
    label: str  # "apt" | "homebrew"
    count: int  # actionable count (drives badge/title)
    packages: tuple[PackageUpdate, ...]
    security: int | None = None  # apt only
    download_bytes: int | None = None  # apt only
    reboot_required: bool = False  # apt only
    reboot_pkg: str | None = None  # apt only
    casks: int | None = None  # brew only
    pinned: int | None = None  # brew only


def parse_apt_upgradable(stdout: str) -> tuple[PackageUpdate, ...]:
    """Parse ``apt list --upgradable`` output into ``PackageUpdate`` entries."""
    packages: list[PackageUpdate] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("Listing"):
            continue
        match = _UPGRADABLE_RE.match(line)
        if match is None:
            continue
        name, available, installed = match.group(1), match.group(2), match.group(3)
        packages.append(
            PackageUpdate(name=name, installed=installed, available=available)
        )
    return tuple(packages)


def parse_apt_check(stderr: str) -> int | None:
    """Extract security update count from ``apt-check`` stderr."""
    match = _APT_CHECK_RE.search(stderr)
    if match is None:
        return None
    return int(match.group(2))


def parse_apt_download_bytes(sim_stdout: str) -> int | None:
    """Extract estimated download size from simulated ``apt-get upgrade`` output."""
    match = _DOWNLOAD_RE.search(sim_stdout)
    if match is None:
        return None
    number = float(match.group(1).replace(",", ""))
    factor = _UNIT_FACTORS.get(match.group(2), 1)
    return int(number * factor)


def parse_apt_installable(sim_stdout: str) -> frozenset[str]:
    """Package names ``apt-get upgrade`` will actually install (its ``Inst`` lines).

    Excludes phased ("deferred due to phasing") and kept-back packages, which
    appear in ``apt list --upgradable`` but are not upgraded by plain ``upgrade``.
    """
    return frozenset(_APT_INST_RE.findall(sim_stdout))


def read_reboot_required(
    *,
    reboot_required_path: Path | None = None,
    reboot_pkgs_path: Path | None = None,
) -> tuple[bool, str | None]:
    """Read Linux ``/run/reboot-required`` marker and triggering package name."""
    flag_path = (
        reboot_required_path
        if reboot_required_path is not None
        else Path(REBOOT_REQUIRED_PATH)
    )
    if not flag_path.exists():
        return (False, None)
    pkgs_path = (
        reboot_pkgs_path
        if reboot_pkgs_path is not None
        else Path(REBOOT_REQUIRED_PKGS_PATH)
    )
    pkg: str | None = None
    try:
        lines = pkgs_path.read_text().splitlines()
    except OSError:
        lines = []
    if lines:
        pkg = lines[0].strip() or None
    return (True, pkg)


class _BrewEntry(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    name: str = ""
    installed_versions: list[str] = []
    current_version: str = ""
    pinned: bool = False


class _BrewOutdated(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    formulae: list[_BrewEntry] = []
    casks: list[_BrewEntry] = []


def _brew_package(entry: _BrewEntry, pinned: bool) -> PackageUpdate:
    return PackageUpdate(
        name=entry.name,
        installed=", ".join(entry.installed_versions),
        available=entry.current_version,
        pinned=pinned,
    )


def parse_brew_outdated(
    payload: Mapping[str, object],
) -> tuple[tuple[PackageUpdate, ...], int, int]:
    """Parse ``brew outdated --json=v2`` into packages, cask count, and pinned count."""
    data = _BrewOutdated.model_validate(payload)
    packages: list[PackageUpdate] = []
    pinned_count = 0
    for entry in data.formulae:
        pinned_count += int(entry.pinned)
        packages.append(_brew_package(entry, entry.pinned))
    for entry in data.casks:
        packages.append(_brew_package(entry, False))
    return tuple(packages), len(data.casks), pinned_count


async def query_apt(
    apt_get: str,
    *,
    runner: CommandRunner = subprocess_runner,
    apt_path: str | None = None,
    apt_check_path: Path | None = None,
    reboot_required_path: Path | None = None,
    reboot_pkgs_path: Path | None = None,
) -> UpdateReport:
    """Query pending non-phased ``apt`` updates via ``runner``."""
    apt = apt_path if apt_path is not None else (shutil.which("apt") or apt_get)
    check_path = apt_check_path if apt_check_path is not None else Path(APT_CHECK_PATH)
    env = {**os.environ, "LC_ALL": "C"}

    list_result = await runner([apt, "list", "--upgradable"], env=env)
    if list_result.returncode != 0:
        raise RuntimeError(
            f"apt list --upgradable failed: {list_result.stderr.strip()}"
        )

    packages = parse_apt_upgradable(list_result.stdout)
    security: int | None = None
    if check_path.exists():
        check_result = await runner([str(check_path)], env=env)
        security = parse_apt_check(check_result.stderr)

    sim_result = await runner(
        [apt_get, "-s", "-o", APT_NEVER_PHASED_OPT, "upgrade"], env=env
    )
    if sim_result.returncode == 0:
        installable = parse_apt_installable(sim_result.stdout)
        packages = tuple(pkg for pkg in packages if pkg.name in installable)

    reboot_required, reboot_pkg = read_reboot_required(
        reboot_required_path=reboot_required_path,
        reboot_pkgs_path=reboot_pkgs_path,
    )
    return UpdateReport(
        manager="apt",
        label="apt",
        count=len(packages),
        packages=packages,
        security=security,
        download_bytes=parse_apt_download_bytes(sim_result.stdout),
        reboot_required=reboot_required,
        reboot_pkg=reboot_pkg,
    )


async def query_brew(
    brew: str, *, runner: CommandRunner = subprocess_runner
) -> UpdateReport:
    """Refresh Homebrew metadata and query outdated formulae and casks via ``runner``."""
    update_result = await runner([brew, "update", "--quiet"])
    if update_result.returncode != 0:
        LOGGER.warning("brew_update_failed", error=update_result.stderr.strip())

    outdated_result = await runner([brew, "outdated", "--json=v2"])
    if outdated_result.returncode != 0:
        raise RuntimeError(f"brew outdated failed: {outdated_result.stderr.strip()}")

    payload = cast(Mapping[str, object], json.loads(outdated_result.stdout))
    packages, casks, pinned = parse_brew_outdated(payload)
    return UpdateReport(
        manager="brew",
        label="homebrew",
        count=len(packages) - pinned,
        packages=packages,
        casks=casks,
        pinned=pinned,
    )


def status_word(count: int) -> str:
    if count == 0:
        return "Up to date"
    if count == 1:
        return "Update available"
    return "Updates available"


def entity_title(label: str, count: int) -> str:
    return f"{label}: {status_word(count)}"


@dataclass(frozen=True, slots=True)
class SurfaceDecision:
    installed: str
    latest: str
    surfaced: bool
    notify_anchor: date | None
    last_install_date: date | None
    pending: frozenset[str]


def decide_surface(
    *,
    current: frozenset[str],
    previous: frozenset[str],
    notify_anchor: date | None,
    last_install_date: date | None,
    today: date,
    cooldown_days: int = SURFACE_COOLDOWN_DAYS,
) -> SurfaceDecision:
    """Decide whether pending updates should surface in Home Assistant today."""
    if previous - current:  # a pending package was applied
        last_install_date = today
        notify_anchor = None  # un-surface; cooldown starts

    today_iso = today.isoformat()
    if not current:  # nothing actionable pending
        return SurfaceDecision(
            today_iso, today_iso, False, None, last_install_date, current
        )

    in_cooldown = (
        last_install_date is not None
        and (today - last_install_date).days < cooldown_days
    )
    if in_cooldown:  # recently applied; stay quiet
        return SurfaceDecision(
            today_iso, today_iso, False, None, last_install_date, current
        )

    if notify_anchor is None:  # first surfacing of this batch
        notify_anchor = today

    return SurfaceDecision(
        SENTINEL_DATE,
        notify_anchor.isoformat(),
        True,
        notify_anchor,
        last_install_date,
        current,
    )


def human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.0f} {unit}" if value >= 10 else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.0f} TB"


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _apt_summary(report: UpdateReport) -> str:
    first = f"{report.count} Update{_plural(report.count)}"
    if report.security is not None and report.security > 0:
        first += f" · {report.security} Security"
    lines = [first]
    if report.download_bytes is not None:
        lines.append(f"Download: {human_bytes(report.download_bytes)}")
    if report.reboot_required:
        if report.reboot_pkg:
            lines.append(f"Reboot required ({report.reboot_pkg})")
        else:
            lines.append("Reboot required")
    return "  \n".join(lines)


def _brew_summary(report: UpdateReport) -> str:
    casks = report.casks or 0
    pinned = report.pinned or 0
    first = f"{report.count} Update{_plural(report.count)}"
    second = f"{casks} Cask{_plural(casks)} · {pinned} Pinned"
    return "  \n".join([first, second])


def release_summary(report: UpdateReport) -> str | None:
    """Format Home Assistant markdown release summary for pending updates."""
    if report.count == 0:
        return None
    if report.manager == "apt":
        return _apt_summary(report)
    return _brew_summary(report)


def _gist_summary_line(report: UpdateReport, refresh: str) -> str:
    parts = [f"{report.count} updates"]
    if report.manager == "apt":
        if report.security is not None:
            parts.append(f"{report.security} security")
    else:
        if report.casks is not None:
            parts.append(f"{report.casks} casks")
        if report.pinned is not None:
            parts.append(f"{report.pinned} pinned")
    parts.append(f"checked {refresh}")
    return "_" + " · ".join(parts) + "_"


def gist_body(report: UpdateReport, hostname: str, refresh: str) -> str:
    """Render the markdown release-notes document published to the manager's gist."""
    heading = f"# {report.label} updates on {hostname}"
    if report.count == 0:
        return f"{heading}\n\n_No updates — up to date as of {refresh}._\n"
    rows = ["| Package | Installed | Available |", "| --- | --- | --- |"]
    for pkg in report.packages:
        name = f"{pkg.name} (pinned)" if pkg.pinned else pkg.name
        rows.append(f"| {name} | {pkg.installed} | {pkg.available} |")
    table = "\n".join(rows)
    return f"{heading}\n\n{_gist_summary_line(report, refresh)}\n\n{table}\n"


def slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-")
