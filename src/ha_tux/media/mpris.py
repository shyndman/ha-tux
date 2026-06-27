import asyncio
from collections.abc import Awaitable, Mapping, Sequence
from typing import Final, Literal, Protocol, cast

from sdbus import (
    DbusInterfaceCommonAsync,
    dbus_method_async,
    dbus_property_async,
    dbus_signal_async,
)

MPRIS_OBJECT_PATH: Final = "/org/mpris/MediaPlayer2"
DBUS_DAEMON_SERVICE_NAME: Final = "org.freedesktop.DBus"
DBUS_DAEMON_OBJECT_PATH: Final = "/org/freedesktop/DBus"
PLAYERCTLD_SERVICE_NAME: Final = "org.mpris.MediaPlayer2.playerctld"
MPRIS_PLAYER_INTERFACE: Final = "org.mpris.MediaPlayer2.Player"
MPRIS_NO_TRACK_PATH: Final = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
MICROSECONDS_PER_SECOND: Final = 1_000_000

MediaPlayerState = Literal["playing", "paused", "stopped", "idle", "off"]
ToggleAction = Literal["Pause", "Play"]
Variant = tuple[str, object]
MetadataMap = Mapping[str, Variant]
PropertiesChanged = tuple[str, dict[str, Variant], list[str]]
NameOwnerChanged = tuple[str, str, str]
SupportedImageMimeType = Literal["image/png", "image/jpeg", "image/gif", "image/webp"]


class SupportsMprisToggle(Protocol):
    @property
    def playback_status(self) -> object: ...

    @property
    def can_pause(self) -> object: ...

    @property
    def can_play(self) -> object: ...

    def pause(self) -> None: ...

    def play(self) -> None: ...


class SupportsAsyncMprisToggle(Protocol):
    @property
    def playback_status(self) -> Awaitable[str]: ...

    @property
    def can_pause(self) -> Awaitable[bool]: ...

    @property
    def can_play(self) -> Awaitable[bool]: ...

    async def pause(self) -> None: ...

    async def play(self) -> None: ...


class MprisPlayerAsync(
    DbusInterfaceCommonAsync,
    interface_name=MPRIS_PLAYER_INTERFACE,
):
    @dbus_method_async(method_name="Next")
    async def next(self) -> None:
        raise NotImplementedError

    @dbus_method_async(method_name="Previous")
    async def previous(self) -> None:
        raise NotImplementedError

    @dbus_method_async(method_name="Pause")
    async def pause(self) -> None:
        raise NotImplementedError

    @dbus_method_async(method_name="Play")
    async def play(self) -> None:
        raise NotImplementedError

    @dbus_method_async(method_name="PlayPause")
    async def play_pause(self) -> None:
        raise NotImplementedError

    @dbus_method_async(method_name="Stop")
    async def stop(self) -> None:
        raise NotImplementedError

    @dbus_method_async("x", method_name="Seek")
    async def seek(self, offset_us: int) -> None:
        del offset_us
        raise NotImplementedError

    @dbus_method_async("ox", method_name="SetPosition")
    async def set_position(self, track_id: str, position_us: int) -> None:
        del track_id, position_us
        raise NotImplementedError

    @dbus_property_async("s", property_name="PlaybackStatus")
    def playback_status(self) -> str:
        raise NotImplementedError

    @dbus_property_async("a{sv}", property_name="Metadata")
    def metadata(self) -> dict[str, Variant]:
        raise NotImplementedError

    @dbus_property_async("x", property_name="Position")
    def position(self) -> int:
        raise NotImplementedError

    @dbus_property_async("d", property_name="Volume")
    def volume(self) -> float:
        raise NotImplementedError

    @volume.setter
    def volume_setter(self, new_volume: float) -> None:
        del new_volume
        raise NotImplementedError

    @dbus_property_async("d", property_name="Rate")
    def rate(self) -> float:
        raise NotImplementedError

    @dbus_property_async("b", property_name="CanPlay")
    def can_play(self) -> bool:
        raise NotImplementedError

    @dbus_property_async("b", property_name="CanPause")
    def can_pause(self) -> bool:
        raise NotImplementedError

    @dbus_property_async("b", property_name="CanGoNext")
    def can_go_next(self) -> bool:
        raise NotImplementedError

    @dbus_property_async("b", property_name="CanGoPrevious")
    def can_go_previous(self) -> bool:
        raise NotImplementedError

    @dbus_property_async("b", property_name="CanSeek")
    def can_seek(self) -> bool:
        raise NotImplementedError

    @dbus_property_async("b", property_name="CanControl")
    def can_control(self) -> bool:
        raise NotImplementedError

    @dbus_signal_async("x", signal_name="Seeked")
    def seeked(self) -> int:
        raise NotImplementedError


class DbusDaemonAsync(
    DbusInterfaceCommonAsync,
    interface_name=DBUS_DAEMON_SERVICE_NAME,
):
    @dbus_method_async("s", "b", method_name="NameHasOwner")
    async def name_has_owner(self, name: str) -> bool:
        del name
        raise NotImplementedError

    @dbus_signal_async("sss", signal_name="NameOwnerChanged")
    def name_owner_changed(self) -> NameOwnerChanged:
        raise NotImplementedError


def new_mpris_player_proxy(
    service_name: str = PLAYERCTLD_SERVICE_NAME,
) -> MprisPlayerAsync:
    return MprisPlayerAsync.new_proxy(service_name, MPRIS_OBJECT_PATH)


def new_dbus_daemon_proxy() -> DbusDaemonAsync:
    return DbusDaemonAsync.new_proxy(DBUS_DAEMON_SERVICE_NAME, DBUS_DAEMON_OBJECT_PATH)


async def get_player_async(
    service_name: str = PLAYERCTLD_SERVICE_NAME,
) -> MprisPlayerAsync:
    return new_mpris_player_proxy(service_name)


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


async def toggle_player_async(
    player: SupportsAsyncMprisToggle,
) -> tuple[str, ToggleAction]:
    playback_status = await player.playback_status
    action = select_toggle_action(playback_status)

    if action == "Pause":
        if not await player.can_pause:
            raise RuntimeError("MPRIS player reported that pause is unsupported")

        await player.pause()
        return playback_status, action

    if not await player.can_play:
        raise RuntimeError("MPRIS player reported that play is unsupported")

    await player.play()
    return playback_status, action


async def toggle_playback_async(
    service_name: str = PLAYERCTLD_SERVICE_NAME,
) -> tuple[str, ToggleAction]:
    player = cast(
        SupportsAsyncMprisToggle, cast(object, await get_player_async(service_name))
    )
    return await toggle_player_async(player)


def toggle_playback(
    service_name: str = PLAYERCTLD_SERVICE_NAME,
) -> tuple[str, ToggleAction]:
    return asyncio.run(toggle_playback_async(service_name))


def mpris_status_to_ha_state(status: str) -> MediaPlayerState:
    match status:
        case "Playing":
            return "playing"
        case "Paused":
            return "paused"
        case "Stopped":
            return "stopped"
        case _:
            raise ValueError(f"Unsupported playback status: {status}")


def metadata_value(metadata: MetadataMap, key: str, signature: str) -> object | None:
    value = metadata.get(key)
    if value is None:
        return None

    actual_signature, payload = value
    if actual_signature != signature:
        return None

    return payload


def metadata_string(metadata: MetadataMap, key: str) -> str | None:
    value = metadata_value(metadata, key, "s")
    if isinstance(value, str):
        return value

    return None


def metadata_string_list(metadata: MetadataMap, key: str) -> tuple[str, ...]:
    value = metadata_value(metadata, key, "as")
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()

    strings: list[str] = []
    for item in value:
        if isinstance(item, str):
            strings.append(item)

    return tuple(strings)


def metadata_int(metadata: MetadataMap, key: str) -> int | None:
    value = metadata_value(metadata, key, "x")
    if isinstance(value, int):
        return value

    return None


def duration_seconds_from_metadata(metadata: MetadataMap) -> int:
    length_us = metadata_int(metadata, "mpris:length")
    if length_us is None or length_us < 0:
        return 0

    return length_us // MICROSECONDS_PER_SECOND


def track_id_from_metadata(metadata: MetadataMap) -> str | None:
    value = metadata_value(metadata, "mpris:trackid", "o")
    if not isinstance(value, str) or value == MPRIS_NO_TRACK_PATH:
        return None

    return value


def artist_text_from_metadata(metadata: MetadataMap) -> str:
    return ", ".join(metadata_string_list(metadata, "xesam:artist"))


def microseconds_to_seconds(value_us: int) -> int:
    if value_us <= 0:
        return 0

    return value_us // MICROSECONDS_PER_SECOND


def seconds_to_microseconds(value_seconds: float) -> int:
    if value_seconds < 0:
        raise ValueError("Seconds must be non-negative")

    return int(value_seconds * MICROSECONDS_PER_SECOND)
