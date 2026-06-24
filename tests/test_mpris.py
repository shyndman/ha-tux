import asyncio
from collections.abc import Awaitable, Generator
from dataclasses import dataclass
from typing import Generic, TypeVar, override

import pytest

from ha_tux.media.mpris import (
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
    select_toggle_action,
    toggle_player,
    toggle_player_async,
    track_id_from_metadata,
)

T = TypeVar("T")


@dataclass
class FakePlayer:
    playback_status: str
    can_pause: bool = True
    can_play: bool = True
    pause_calls: int = 0
    play_calls: int = 0

    def pause(self) -> None:
        self.pause_calls += 1

    def play(self) -> None:
        self.play_calls += 1


@dataclass
class AsyncValue(Generic[T], Awaitable[T]):
    value: T

    @override
    def __await__(self) -> Generator[None, None, T]:
        async def read() -> T:
            return self.value

        return read().__await__()


@dataclass
class FakeAsyncPlayer:
    status: str
    pause_supported: bool = True
    play_supported: bool = True
    pause_calls: int = 0
    play_calls: int = 0

    @property
    def playback_status(self) -> Awaitable[str]:
        return AsyncValue(self.status)

    @property
    def can_pause(self) -> Awaitable[bool]:
        return AsyncValue(self.pause_supported)

    @property
    def can_play(self) -> Awaitable[bool]:
        return AsyncValue(self.play_supported)

    async def pause(self) -> None:
        self.pause_calls += 1

    async def play(self) -> None:
        self.play_calls += 1


def test_select_toggle_action_pauses_when_playing() -> None:
    assert select_toggle_action("Playing") == "Pause"


@pytest.mark.parametrize("playback_status", ["Paused", "Stopped"])
def test_select_toggle_action_plays_when_not_playing(playback_status: str) -> None:
    assert select_toggle_action(playback_status) == "Play"


def test_select_toggle_action_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="Unsupported playback status"):
        _ = select_toggle_action("Buffering")


@pytest.mark.parametrize(
    ("mpris_status", "ha_state"),
    [("Playing", "playing"), ("Paused", "paused"), ("Stopped", "stopped")],
)
def test_mpris_status_to_ha_state(mpris_status: str, ha_state: str) -> None:
    assert mpris_status_to_ha_state(mpris_status) == ha_state


def test_mpris_status_to_ha_state_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="Unsupported playback status"):
        _ = mpris_status_to_ha_state("Buffering")


def test_toggle_player_pauses_active_playback() -> None:
    player = FakePlayer(playback_status="Playing")

    previous_status, action = toggle_player(player)

    assert previous_status == "Playing"
    assert action == "Pause"
    assert player.pause_calls == 1
    assert player.play_calls == 0


def test_toggle_player_plays_paused_playback() -> None:
    player = FakePlayer(playback_status="Paused")

    previous_status, action = toggle_player(player)

    assert previous_status == "Paused"
    assert action == "Play"
    assert player.pause_calls == 0
    assert player.play_calls == 1


def test_toggle_player_rejects_unpausable_player() -> None:
    player = FakePlayer(playback_status="Playing", can_pause=False)

    with pytest.raises(RuntimeError, match="pause is unsupported"):
        _ = toggle_player(player)

    assert player.pause_calls == 0
    assert player.play_calls == 0


def test_toggle_player_rejects_unplayable_player() -> None:
    player = FakePlayer(playback_status="Paused", can_play=False)

    with pytest.raises(RuntimeError, match="play is unsupported"):
        _ = toggle_player(player)

    assert player.pause_calls == 0
    assert player.play_calls == 0


def test_toggle_player_async_pauses_active_playback() -> None:
    player = FakeAsyncPlayer(status="Playing")

    previous_status, action = asyncio.run(toggle_player_async(player))

    assert previous_status == "Playing"
    assert action == "Pause"
    assert player.pause_calls == 1
    assert player.play_calls == 0


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
