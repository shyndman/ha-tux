from typing import Final, Literal, Protocol, cast

from sdbus import DbusInterfaceCommon, dbus_method, dbus_property

MPRIS_OBJECT_PATH: Final = "/org/mpris/MediaPlayer2"
PLAYERCTLD_SERVICE_NAME: Final = "org.mpris.MediaPlayer2.playerctld"

PlaybackStatus = Literal["Playing", "Paused", "Stopped"]
ToggleAction = Literal["Pause", "Play"]


class SupportsMprisToggle(Protocol):
    @property
    def playback_status(self) -> object: ...

    @property
    def can_pause(self) -> object: ...

    @property
    def can_play(self) -> object: ...

    def pause(self) -> None: ...

    def play(self) -> None: ...


class MprisPlayer(DbusInterfaceCommon, interface_name="org.mpris.MediaPlayer2.Player"):
    @dbus_method(method_name="Pause")
    def pause(self) -> None:
        raise NotImplementedError

    @dbus_method(method_name="Play")
    def play(self) -> None:
        raise NotImplementedError

    @dbus_property("s", property_name="PlaybackStatus")
    def playback_status(self) -> str:
        raise NotImplementedError

    @dbus_property("b", property_name="CanPause")
    def can_pause(self) -> bool:
        raise NotImplementedError

    @dbus_property("b", property_name="CanPlay")
    def can_play(self) -> bool:
        raise NotImplementedError


def get_player(service_name: str = PLAYERCTLD_SERVICE_NAME) -> MprisPlayer:
    return MprisPlayer(service_name=service_name, object_path=MPRIS_OBJECT_PATH)


def select_toggle_action(playback_status: str) -> ToggleAction:
    if playback_status == "Playing":
        return "Pause"

    if playback_status in {"Paused", "Stopped"}:
        return "Play"

    raise ValueError(f"Unsupported playback status: {playback_status}")


def toggle_player(player: SupportsMprisToggle) -> tuple[str, ToggleAction]:
    playback_status = cast(str, player.playback_status)
    action = select_toggle_action(playback_status)

    if action == "Pause":
        if not cast(bool, player.can_pause):
            raise RuntimeError("MPRIS player reported that pause is unsupported")

        player.pause()
        return playback_status, action

    if not cast(bool, player.can_play):
        raise RuntimeError("MPRIS player reported that play is unsupported")

    player.play()
    return playback_status, action


def toggle_playback(
    service_name: str = PLAYERCTLD_SERVICE_NAME,
) -> tuple[str, ToggleAction]:
    return toggle_player(get_player(service_name))
