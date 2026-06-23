from __future__ import annotations

from datetime import date

from ha_tux.software_update.detect import (
    SENTINEL_DATE,
    PackageUpdate,
    UpdateReport,
    entity_title,
    human_bytes,
    parse_apt_check,
    parse_apt_download_bytes,
    parse_apt_upgradable,
    parse_brew_outdated,
    release_summary,
    status_word,
    version_pair,
)

APT_UPGRADABLE = """Listing... Done
bash/stable 5.2.21-2 amd64 [upgradable from: 5.2.21-1]
curl/stable 8.5.0-1 amd64 [upgradable from: 8.4.0-1]
"""

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


def test_parse_apt_upgradable_two_packages() -> None:
    packages = parse_apt_upgradable(APT_UPGRADABLE)
    assert packages == (
        PackageUpdate(name="bash", installed="5.2.21-1", available="5.2.21-2"),
        PackageUpdate(name="curl", installed="8.4.0-1", available="8.5.0-1"),
    )


def test_parse_apt_check_returns_security_count() -> None:
    assert parse_apt_check("APT-Check noise\n12;3") == 3


def test_parse_apt_check_none_when_absent() -> None:
    assert parse_apt_check("no numbers here") is None


def test_parse_apt_download_bytes() -> None:
    out = "Inst foo\nNeed to get 84.0 MB of archives.\nAfter this..."
    assert parse_apt_download_bytes(out) == 88080384


def test_parse_apt_download_bytes_none_when_absent() -> None:
    assert parse_apt_download_bytes("0 upgraded, 0 newly installed") is None


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


def test_human_bytes() -> None:
    assert human_bytes(88080384) == "84 MB"
    assert human_bytes(512) == "512 B"
    assert human_bytes(5 * 1024 * 1024) == "5.0 MB"  # below 10 -> 1 decimal


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


def test_version_pair_clean_equal() -> None:
    today = date(2026, 6, 23)
    assert version_pair(0, None, today) == ("2026-06-23", "2026-06-23")


def test_version_pair_pending_with_old_clean_date() -> None:
    today = date(2026, 6, 23)
    last_clean = date(2026, 6, 20)
    assert version_pair(1, last_clean, today) == ("2026-06-20", "2026-06-23")


def test_version_pair_pending_same_day_or_none_uses_sentinel() -> None:
    today = date(2026, 6, 23)
    assert version_pair(1, None, today) == (SENTINEL_DATE, "2026-06-23")
    assert version_pair(1, today, today) == (SENTINEL_DATE, "2026-06-23")
