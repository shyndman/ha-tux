from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from ha_tux.foundation.subprocesses import CommandResult
from ha_tux.software_update.detect import (
    APT_PROBE_SCRIPT,
    SENTINEL_DATE,
    SYSTEM_PYTHON_PATH,
    PackageUpdate,
    SurfaceDecision,
    UpdateReport,
    decide_surface,
    entity_title,
    parse_apt_probe,
    parse_brew_outdated,
    query_apt,
    query_brew,
    release_summary,
    status_word,
)

APT_PROBE_JSON = json.dumps(
    {
        "packages": [
            {
                "name": "bash",
                "installed": "5.2.21-1",
                "available": "5.2.21-2",
                "security": False,
            },
            {
                "name": "curl",
                "installed": "8.4.0-1",
                "available": "8.5.0-1",
                "security": True,
            },
        ],
        "download_bytes": 2097152,
    }
)

BREW_PAYLOAD: dict[str, object] = {
    "formulae": [
        {
            "name": "ripgrep",
            "installed_versions": ["14.0.0"],
            "current_version": "14.1.0",
            "pinned": False,
        },
        {
            "name": "node",
            "installed_versions": ["20.0.0"],
            "current_version": "22.0.0",
            "pinned": True,
        },
    ],
    "casks": [
        {
            "name": "firefox",
            "installed_versions": ["120.0"],
            "current_version": "121.0",
        }
    ],
}


def test_parse_apt_probe_extracts_packages_security_and_bytes() -> None:
    packages, security, download_bytes = parse_apt_probe(APT_PROBE_JSON)
    assert packages == (
        PackageUpdate(name="bash", installed="5.2.21-1", available="5.2.21-2"),
        PackageUpdate(name="curl", installed="8.4.0-1", available="8.5.0-1"),
    )
    assert security == 1
    assert download_bytes == 2097152


def test_parse_apt_probe_rejects_missing_or_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _ = parse_apt_probe('{"packages": []}')

    with pytest.raises(ValidationError):
        _ = parse_apt_probe(
            json.dumps(
                {
                    "packages": [
                        {
                            "name": "bash",
                            "installed": "1",
                            "available": "2",
                            "security": False,
                            "unexpected": 1,
                        }
                    ],
                    "download_bytes": 0,
                }
            )
        )


def test_parse_brew_outdated_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        _ = parse_brew_outdated({"formulae": [{"name": "ripgrep"}], "casks": []})


def test_parse_brew_outdated_excludes_pinned() -> None:
    packages, casks, pinned = parse_brew_outdated(BREW_PAYLOAD)
    assert casks == 1
    assert pinned == 1
    assert len(packages) == 3  # 2 formulae + 1 cask
    node = next(p for p in packages if p.name == "node")
    assert node.pinned is True
    firefox = next(p for p in packages if p.name == "firefox")
    assert firefox.pinned is False
    # count = len(packages) - pinned excludes the pinned formula
    assert len(packages) - pinned == 2


def test_status_word() -> None:
    assert status_word(0) == "Up to date"
    assert status_word(1) == "Update available"
    assert status_word(2) == "Updates available"


def test_entity_title() -> None:
    assert entity_title("apt", 0) == "apt: Up to date"
    assert entity_title("homebrew", 1) == "homebrew: Update available"
    assert entity_title("apt", 2) == "apt: Updates available"


def test_release_summary_clean_is_none() -> None:
    report = UpdateReport(manager="apt", label="apt", count=0, packages=())
    assert release_summary(report) is None


def test_release_summary_apt_pending_exact() -> None:
    report = UpdateReport(
        manager="apt",
        label="apt",
        count=2,
        packages=(),
        security=3,
        download_bytes=88080384,
        reboot_required=True,
        reboot_pkg="linux-image-6.0",
    )
    assert release_summary(report) == (
        "2 Updates · 3 Security  \nDownload: 84 MB  \nReboot required (linux-image-6.0)"
    )


def test_release_summary_brew_pending() -> None:
    report = UpdateReport(
        manager="brew",
        label="homebrew",
        count=2,
        packages=(),
        casks=1,
        pinned=3,
    )
    assert release_summary(report) == "2 Updates  \n1 Cask · 3 Pinned"


def test_decide_surface_clean() -> None:
    today = date(2026, 6, 23)
    d = decide_surface(
        current=frozenset(),
        previous=frozenset(),
        notify_anchor=None,
        last_install_date=None,
        today=today,
    )
    assert d.installed == today.isoformat()
    assert d.latest == today.isoformat()
    assert d.surfaced is False


def test_decide_surface_first_surface() -> None:
    today = date(2026, 6, 23)
    d = decide_surface(
        current=frozenset({"a"}),
        previous=frozenset(),
        notify_anchor=None,
        last_install_date=None,
        today=today,
    )
    assert d.installed == SENTINEL_DATE
    assert d.latest == today.isoformat()
    assert d.surfaced is True
    assert d.notify_anchor == today


def test_decide_surface_freezes_latest_across_days() -> None:
    d = decide_surface(
        current=frozenset({"a", "b"}),
        previous=frozenset({"a", "b"}),
        notify_anchor=date(2026, 6, 20),
        last_install_date=None,
        today=date(2026, 6, 25),
    )
    assert d.latest == "2026-06-20"
    assert d.surfaced is True


def test_decide_surface_reduction_arms_cooldown() -> None:
    today = date(2026, 6, 25)
    d = decide_surface(
        current=frozenset({"b"}),
        previous=frozenset({"a", "b"}),
        notify_anchor=date(2026, 6, 20),
        last_install_date=None,
        today=today,
    )
    assert d.surfaced is False
    assert d.last_install_date == today
    assert d.notify_anchor is None
    assert d.installed == today.isoformat()
    assert d.latest == today.isoformat()


def test_decide_surface_set_diff_flat_count() -> None:
    today = date(2026, 6, 25)
    d = decide_surface(
        current=frozenset({"b", "c"}),
        previous=frozenset({"a", "b"}),
        notify_anchor=None,
        last_install_date=None,
        today=today,
    )
    assert d.surfaced is False
    assert d.last_install_date == today


def test_decide_surface_in_cooldown_suppresses() -> None:
    today = date(2026, 6, 25)
    d = decide_surface(
        current=frozenset({"c"}),
        previous=frozenset({"c"}),
        notify_anchor=None,
        last_install_date=date(2026, 6, 24),
        today=today,
    )
    assert d.surfaced is False
    assert d.installed == today.isoformat()
    assert d.latest == today.isoformat()


def test_decide_surface_resurfaces_after_cooldown() -> None:
    today = date(2026, 6, 26)
    d = decide_surface(
        current=frozenset({"c"}),
        previous=frozenset({"c"}),
        notify_anchor=None,
        last_install_date=date(2026, 6, 24),
        today=today,
    )
    assert d.surfaced is True
    assert d.notify_anchor == today
    assert d.installed == SENTINEL_DATE
    assert isinstance(d, SurfaceDecision)


def test_query_apt_parses_probe_and_reads_reboot(tmp_path: Path) -> None:
    reboot_flag = tmp_path / "reboot-required"
    reboot_pkgs = tmp_path / "reboot-required.pkgs"
    _ = reboot_flag.write_text("*** Restart required ***\n")
    _ = reboot_pkgs.write_text("linux-image-generic\n")
    recorded_cmds: list[list[str]] = []

    async def runner(
        cmd: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
    ) -> CommandResult:
        del env, stdin
        recorded_cmds.append(list(cmd))
        return CommandResult(0, APT_PROBE_JSON, "")

    report = asyncio.run(
        query_apt(
            runner=runner,
            reboot_required_path=reboot_flag,
            reboot_pkgs_path=reboot_pkgs,
        )
    )

    assert recorded_cmds == [[SYSTEM_PYTHON_PATH, "-c", APT_PROBE_SCRIPT]]
    assert report.count == 2
    assert report.packages == (
        PackageUpdate("bash", "5.2.21-1", "5.2.21-2"),
        PackageUpdate("curl", "8.4.0-1", "8.5.0-1"),
    )
    assert report.security == 1
    assert report.download_bytes == 2097152
    assert report.reboot_required is True
    assert report.reboot_pkg == "linux-image-generic"


def test_query_brew_excludes_pinned_count() -> None:
    async def runner(
        cmd: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
    ) -> CommandResult:
        del env, stdin
        if cmd[1] == "update":
            return CommandResult(0, "", "")
        if cmd[1] == "outdated":
            return CommandResult(0, json.dumps(BREW_PAYLOAD), "")
        raise AssertionError(f"Unexpected command: {cmd}")

    report = asyncio.run(
        query_brew("/home/linuxbrew/.linuxbrew/bin/brew", runner=runner)
    )

    assert report.manager == "brew"
    assert report.count == 2
    assert report.casks == 1
    assert report.pinned == 1
