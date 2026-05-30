from dataclasses import dataclass

import pytest

from ha_tux.mpris import select_toggle_action, toggle_player


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


def test_select_toggle_action_pauses_when_playing() -> None:
    assert select_toggle_action("Playing") == "Pause"


@pytest.mark.parametrize("playback_status", ["Paused", "Stopped"])
def test_select_toggle_action_plays_when_not_playing(playback_status: str) -> None:
    assert select_toggle_action(playback_status) == "Play"


def test_select_toggle_action_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="Unsupported playback status"):
        _ = select_toggle_action("Buffering")


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
