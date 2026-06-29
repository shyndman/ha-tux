from __future__ import annotations

from datetime import date

from ha_tux.software_update.detect import (
    SENTINEL_DATE,
    PackageUpdate,
    SurfaceDecision,
    UpdateReport,
    decide_surface,
    entity_title,
    human_bytes,
    parse_apt_check,
    parse_apt_installable,
    parse_apt_download_bytes,
    parse_apt_upgradable,
    parse_brew_outdated,
    release_summary,
    status_word,
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


APT_SIM_ALL_HELD = """NOTE: This is only a simulation!
Calculating upgrade...
The following upgrades have been deferred due to phasing:
  gir1.2-packagekitglib-1.0 gstreamer1.0-packagekit libpackagekit-glib2-18
  packagekit
The following packages have been kept back:
  build-essential
0 upgraded, 0 newly installed, 0 to remove and 5 not upgraded.
"""

APT_SIM_TWO_INST = """NOTE: This is only a simulation!
Calculating upgrade...
Inst libc6 [2.39-0ubuntu8.3] (2.39-0ubuntu8.4 Ubuntu:24.04/noble-updates [amd64])
Inst base-files [13ubuntu10] (13ubuntu10.1 Ubuntu:24.04/noble-updates [amd64])
The following packages have been kept back:
  build-essential
Need to get 1024 kB of archives.
"""


def test_parse_apt_installable_excludes_phased_and_kept_back() -> None:
    assert parse_apt_installable(APT_SIM_ALL_HELD) == frozenset()


def test_parse_apt_installable_returns_inst_names() -> None:
    assert parse_apt_installable(APT_SIM_TWO_INST) == frozenset({"libc6", "base-files"})


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
