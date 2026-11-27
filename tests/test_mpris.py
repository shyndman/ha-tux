import pytest

from ha_tux.mpris.player import (
    MICROSECONDS_PER_SECOND,
    MPRIS_NO_TRACK_PATH,
    MetadataMap,
    artist_text_from_metadata,
    duration_seconds_from_metadata,
    metadata_int,
    metadata_string,
    metadata_string_list,
    microseconds_to_seconds,
    mpris_status_to_ha_state,
    seconds_to_microseconds,
    track_id_from_metadata,
)


@pytest.mark.parametrize(
    ("mpris_status", "ha_state"),
    [("Playing", "playing"), ("Paused", "paused"), ("Stopped", "stopped")],
)
def test_mpris_status_to_ha_state(mpris_status: str, ha_state: str) -> None:
    assert mpris_status_to_ha_state(mpris_status) == ha_state


def test_mpris_status_to_ha_state_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="Unsupported playback status"):
        _ = mpris_status_to_ha_state("Buffering")


def test_metadata_helpers_extract_supported_values() -> None:
    metadata: MetadataMap = {
        "xesam:title": ("s", "Song"),
        "xesam:artist": ("as", ["One", "Two"]),
        "mpris:length": ("x", 123 * MICROSECONDS_PER_SECOND),
        "mpris:trackid": ("o", "/track/1"),
    }

    assert metadata_string(metadata, "xesam:title") == "Song"
    assert metadata_string_list(metadata, "xesam:artist") == ("One", "Two")
    assert artist_text_from_metadata(metadata) == "One, Two"
    assert metadata_int(metadata, "mpris:length") == 123 * MICROSECONDS_PER_SECOND
    assert duration_seconds_from_metadata(metadata) == 123
    assert track_id_from_metadata(metadata) == "/track/1"


def test_metadata_helpers_reject_wrong_signatures() -> None:
    metadata: MetadataMap = {
        "xesam:title": ("as", ["not", "a", "string"]),
        "xesam:artist": ("s", "not a list"),
        "mpris:length": ("s", "not an int"),
        "mpris:trackid": ("o", MPRIS_NO_TRACK_PATH),
    }

    assert metadata_string(metadata, "xesam:title") is None
    assert metadata_string_list(metadata, "xesam:artist") == ()
    assert metadata_int(metadata, "mpris:length") is None
    assert duration_seconds_from_metadata(metadata) == 0
    assert track_id_from_metadata(metadata) is None


def test_time_conversion_helpers() -> None:
    assert microseconds_to_seconds(1_999_999) == 1
    assert microseconds_to_seconds(-1) == 0
    assert seconds_to_microseconds(1.25) == 1_250_000
    with pytest.raises(ValueError, match="Seconds must be non-negative"):
        _ = seconds_to_microseconds(-1)
