"""Package update detection, cooldown gating, and release formatting."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar, Final, cast

from libsh import get_logger
from pydantic import BaseModel, ConfigDict

from ha_tux.config import DEFAULT_SOFTWARE_UPDATE_POLL_SECONDS
from ha_tux.foundation.strings import human_bytes, plural_suffix
from ha_tux.foundation.subprocesses import CommandRunner, subprocess_runner

LOGGER = get_logger("apt")

__all__ = [
    "APT_PROBE_SCRIPT",
    "APT_UPGRADE_UNIT",
    "BREW_UPGRADE_UNIT",
    "DEFAULT_SOFTWARE_UPDATE_POLL_SECONDS",
    "REBOOT_REQUIRED_PATH",
    "REBOOT_REQUIRED_PKGS_PATH",
    "SENTINEL_DATE",
    "SURFACE_COOLDOWN_DAYS",
    "SYSTEMCTL_PATH",
    "SYSTEM_PYTHON_PATH",
    "PackageUpdate",
    "SurfaceDecision",
    "UpdateReport",
    "decide_surface",
    "entity_title",
    "gist_body",
    "parse_apt_probe",
    "parse_brew_outdated",
    "query_apt",
    "query_brew",
    "read_reboot_required",
    "release_summary",
    "status_word",
]

SURFACE_COOLDOWN_DAYS: Final = 2
SENTINEL_DATE: Final = "1970-01-01"
SYSTEM_PYTHON_PATH: Final = "/usr/bin/python3"
SYSTEMCTL_PATH: Final = "/usr/bin/systemctl"
APT_UPGRADE_UNIT: Final = "ha-tux-apt-upgrade.service"
BREW_UPGRADE_UNIT: Final = "ha-tux-brew-upgrade.service"
REBOOT_REQUIRED_PATH: Final = "/run/reboot-required"
REBOOT_REQUIRED_PKGS_PATH: Final = "/run/reboot-required.pkgs"

# The updates service runs in a systemd sandbox (ProtectHome/PrivateTmp/namespaces)
# where ischroot(1) returns true, so apt disables phased-update deferral unless
# forced. Keeping Never-Include-Phased-Updates=true matches what the unsandboxed
# root `apt-get upgrade` unit (ha-tux-apt-upgrade.service) will actually install.
APT_PROBE_SCRIPT: Final = """\
import json, apt, apt_pkg
apt_pkg.config.set("APT::Get::Never-Include-Phased-Updates", "true")
cache = apt.Cache()
cache.upgrade(dist_upgrade=False)
packages = [
    {
        "name": pkg.name,
        "installed": pkg.installed.version,
        "available": pkg.candidate.version,
        "security": any(o.archive.endswith("-security") for o in pkg.candidate.origins),
    }
    for pkg in cache.get_changes()
    if pkg.marked_upgrade and pkg.installed is not None and pkg.candidate is not None
]
packages.sort(key=lambda p: p["name"])
print(json.dumps({"packages": packages, "download_bytes": cache.required_download}))
"""


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


class _AptPackagePayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str
    installed: str
    available: str
    security: bool


class _AptProbePayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    packages: tuple[_AptPackagePayload, ...]
    download_bytes: int


def parse_apt_probe(raw_json: str) -> tuple[tuple[PackageUpdate, ...], int, int]:
    """Validate and unpack JSON emitted by ``APT_PROBE_SCRIPT``."""
    data = _AptProbePayload.model_validate_json(raw_json)
    packages = tuple(
        PackageUpdate(
            name=pkg.name,
            installed=pkg.installed,
            available=pkg.available,
        )
        for pkg in data.packages
    )
    security = sum(1 for pkg in data.packages if pkg.security)
    return packages, security, data.download_bytes


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


class _BrewFormula(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    name: str
    installed_versions: list[str]
    current_version: str
    pinned: bool


class _BrewCask(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    name: str
    installed_versions: list[str]
    current_version: str


class _BrewOutdated(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    formulae: list[_BrewFormula]
    casks: list[_BrewCask]


def _brew_package(entry: _BrewFormula | _BrewCask, pinned: bool) -> PackageUpdate:
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
    *,
    runner: CommandRunner = subprocess_runner,
    python_path: str = SYSTEM_PYTHON_PATH,
    reboot_required_path: Path | None = None,
    reboot_pkgs_path: Path | None = None,
) -> UpdateReport:
    """Query pending non-phased ``apt`` updates via system ``python3-apt``."""
    result = await runner([python_path, "-c", APT_PROBE_SCRIPT])
    if result.returncode != 0:
        raise RuntimeError(f"python3-apt probe failed: {result.stderr.strip()}")

    packages, security, download_bytes = parse_apt_probe(result.stdout)
    reboot_required, reboot_pkg = read_reboot_required(
        reboot_required_path=reboot_required_path,
        reboot_pkgs_path=reboot_pkgs_path,
    )
    LOGGER.debug(
        "Queried pending apt updates",
        count=len(packages),
        security=security,
        download_bytes=download_bytes,
        reboot_required=reboot_required,
    )
    return UpdateReport(
        manager="apt",
        label="apt",
        count=len(packages),
        packages=packages,
        security=security,
        download_bytes=download_bytes,
        reboot_required=reboot_required,
        reboot_pkg=reboot_pkg,
    )


async def query_brew(
    brew: str, *, runner: CommandRunner = subprocess_runner
) -> UpdateReport:
    """Refresh Homebrew metadata and query outdated formulae and casks via ``runner``."""
    update_result = await runner([brew, "update", "--quiet"])
    if update_result.returncode != 0:
        LOGGER.warning(
            "Homebrew metadata refresh failed; continuing with local cache",
            error=update_result.stderr.strip(),
        )

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


def _apt_summary(report: UpdateReport) -> str:
    first = f"{report.count} Update{plural_suffix(report.count)}"
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
    first = f"{report.count} Update{plural_suffix(report.count)}"
    second = f"{casks} Cask{plural_suffix(casks)} · {pinned} Pinned"
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
