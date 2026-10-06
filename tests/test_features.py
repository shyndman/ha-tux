from ha_tux import features_for_role


def test_session_role_activates_session_features() -> None:
    assert {f.name for f in features_for_role("session")} == {
        "mpris",
        "input_active",
        "screen_lock",
    }


def test_host_role_activates_host_features() -> None:
    assert {f.name for f in features_for_role("host")} == {
        "zfs",
        "power",
        "smart",
    }


def test_updates_role_activates_only_package_updates() -> None:
    assert {f.name for f in features_for_role("updates")} == {"software_update"}


def test_all_role_activates_every_feature() -> None:
    assert {f.name for f in features_for_role("all")} == {
        "mpris",
        "input_active",
        "screen_lock",
        "zfs",
        "software_update",
        "power",
        "smart",
    }
