from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, cast

LOGGER = logging.getLogger(__name__)

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
    "PackageUpdate",
    "UpdateReport",
    "_run",
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
    "version_pair",
]

DEFAULT_SOFTWARE_UPDATE_POLL_SECONDS: Final = 172800.0  # 2 days
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


async def _run(
    cmd: Sequence[str], *, env: Mapping[str, str] | None = None
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(env) if env is not None else None,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode if proc.returncode is not None else -1,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


def parse_apt_upgradable(stdout: str) -> tuple[PackageUpdate, ...]:
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
    match = _APT_CHECK_RE.search(stderr)
    if match is None:
        return None
    return int(match.group(2))


def parse_apt_download_bytes(sim_stdout: str) -> int | None:
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


def read_reboot_required() -> tuple[bool, str | None]:
    if not Path(REBOOT_REQUIRED_PATH).exists():
        return (False, None)
    pkg: str | None = None
    try:
        lines = Path(REBOOT_REQUIRED_PKGS_PATH).read_text().splitlines()
    except OSError:
        lines = []
    if lines:
        pkg = lines[0].strip() or None
    return (True, pkg)


def _brew_package(entry: Mapping[str, object], pinned: bool) -> PackageUpdate:
    installed_raw = entry.get("installed_versions")
    installed = (
        ", ".join(str(v) for v in cast(list[object], installed_raw))
        if isinstance(installed_raw, list)
        else ""
    )
    return PackageUpdate(
        name=str(entry.get("name", "")),
        installed=installed,
        available=str(entry.get("current_version", "")),
        pinned=pinned,
    )


def parse_brew_outdated(
    payload: Mapping[str, object],
) -> tuple[tuple[PackageUpdate, ...], int, int]:
    packages: list[PackageUpdate] = []
    pinned_count = 0
    formulae = payload.get("formulae")
    if isinstance(formulae, list):
        for raw in cast(list[object], formulae):
            if not isinstance(raw, Mapping):
                continue
            entry = cast(Mapping[str, object], raw)
            pinned = bool(entry.get("pinned", False))
            pinned_count += int(pinned)
            packages.append(_brew_package(entry, pinned))
    casks_count = 0
    casks = payload.get("casks")
    if isinstance(casks, list):
        for raw in cast(list[object], casks):
            if not isinstance(raw, Mapping):
                continue
            casks_count += 1
            packages.append(_brew_package(cast(Mapping[str, object], raw), False))
    return tuple(packages), casks_count, pinned_count


async def query_apt(apt_get: str) -> UpdateReport:
    apt = shutil.which("apt") or apt_get
    env = {**os.environ, "LC_ALL": "C"}
    rc, out, err = await _run([apt, "list", "--upgradable"], env=env)
    if rc != 0:
        raise RuntimeError(f"apt list --upgradable failed: {err.strip()}")
    packages = parse_apt_upgradable(out)
    security: int | None = None
    if Path(APT_CHECK_PATH).exists():
        _, _, check_err = await _run([APT_CHECK_PATH], env=env)
        security = parse_apt_check(check_err)
    sim_rc, sim_out, _ = await _run(
        [apt_get, "-s", "-o", APT_NEVER_PHASED_OPT, "upgrade"], env=env
    )
    if sim_rc == 0:
        installable = parse_apt_installable(sim_out)
        packages = tuple(pkg for pkg in packages if pkg.name in installable)
    reboot_required, reboot_pkg = read_reboot_required()
    return UpdateReport(
        manager="apt",
        label="apt",
        count=len(packages),
        packages=packages,
        security=security,
        download_bytes=parse_apt_download_bytes(sim_out),
        reboot_required=reboot_required,
        reboot_pkg=reboot_pkg,
    )


async def query_brew(brew: str) -> UpdateReport:
    rc, _, err = await _run([brew, "update", "--quiet"])
    if rc != 0:
        LOGGER.warning("brew update failed: %s", err.strip())
    rc, out, err = await _run([brew, "outdated", "--json=v2"])
    if rc != 0:
        raise RuntimeError(f"brew outdated failed: {err.strip()}")
    payload = cast(Mapping[str, object], json.loads(out))
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


def version_pair(count: int, last_clean: date | None, today: date) -> tuple[str, str]:
    today_iso = today.isoformat()
    if count == 0:
        return (today_iso, today_iso)
    if last_clean is not None and last_clean < today:
        return (last_clean.isoformat(), today_iso)
    return (SENTINEL_DATE, today_iso)


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
