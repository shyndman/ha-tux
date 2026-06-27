from ha_tux import features_for_role


def test_session_role_activates_session_features() -> None:
    assert {f.name for f in features_for_role("session")} == {
        "media",
        "input_active",
        "lock",
    }


def test_host_role_activates_host_features() -> None:
    assert {f.name for f in features_for_role("host")} == {
        "zfs",
        "software_update",
        "power",
        "smart",
    }


def test_all_role_activates_every_feature() -> None:
    assert {f.name for f in features_for_role("all")} == {
        "media",
        "input_active",
        "lock",
        "zfs",
        "software_update",
        "power",
        "smart",
    }
