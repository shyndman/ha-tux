"""MPRIS D-Bus adapter seam, ``sdbus`` proxy implementation, and metadata helpers.

Encapsulates all ``sdbus`` property awaits, ``set_async`` calls, and signal
subscriptions behind the typed ``MprisPlayer`` protocol.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Final, Literal, Protocol, cast

from sdbus import (
    DbusFailedError,
    DbusInterfaceCommonAsync,
    dbus_method_async,
    dbus_property_async,
    dbus_signal_async,
)

from ha_tux.config import DEFAULT_POSITION_POLL_SECONDS, PLAYERCTLD_SERVICE_NAME

MPRIS_OBJECT_PATH: Final = "/org/mpris/MediaPlayer2"
PLAYERCTLD_NO_ACTIVE_PLAYER_ERROR: Final = (
    "com.github.altdesktop.playerctld.NoActivePlayer"
)
MPRIS_PLAYER_INTERFACE: Final = "org.mpris.MediaPlayer2.Player"
MPRIS_NO_TRACK_PATH: Final = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
MICROSECONDS_PER_SECOND: Final = 1_000_000

__all__ = [
    "DEFAULT_POSITION_POLL_SECONDS",
    "MICROSECONDS_PER_SECOND",
    "MPRIS_NO_TRACK_PATH",
    "MPRIS_OBJECT_PATH",
    "MPRIS_PLAYER_INTERFACE",
    "PLAYERCTLD_NO_ACTIVE_PLAYER_ERROR",
    "PLAYERCTLD_SERVICE_NAME",
    "MediaPlayerState",
    "MetadataMap",
    "MprisPlayer",
    "MprisPlayerAsync",
    "PlayerctldNoActivePlayerError",
    "PropertiesChanged",
    "SdbusMprisPlayer",
    "Variant",
    "artist_text_from_metadata",
    "duration_seconds_from_metadata",
    "metadata_int",
    "metadata_string",
    "metadata_string_list",
    "metadata_value",
    "microseconds_to_seconds",
    "mpris_status_to_ha_state",
    "seconds_to_microseconds",
    "track_id_from_metadata",
]


class PlayerctldNoActivePlayerError(DbusFailedError):
    """Raised by ``playerctld`` when no desktop media player is active."""

    dbus_error_name: str = PLAYERCTLD_NO_ACTIVE_PLAYER_ERROR


MediaPlayerState = Literal["playing", "paused", "stopped", "idle", "off"]
Variant = tuple[str, object]
MetadataMap = Mapping[str, Variant]
PropertiesChanged = tuple[str, dict[str, Variant], list[str]]


class MprisPlayerAsync(
    DbusInterfaceCommonAsync,
    interface_name=MPRIS_PLAYER_INTERFACE,
):
    """Raw ``sdbus`` async proxy for ``org.mpris.MediaPlayer2.Player``."""

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


class MprisPlayer(Protocol):
    """Typed seam over an MPRIS player instance hiding ``sdbus`` descriptors."""

    async def get_playback_status(self) -> str:
        """Read ``PlaybackStatus`` (``Playing``, ``Paused``, or ``Stopped``)."""
        ...

    async def get_metadata(self) -> MetadataMap:
        """Read ``Metadata`` variant map for the current track."""
        ...

    async def get_position(self) -> int:
        """Read current playback position in microseconds."""
        ...

    async def get_volume(self) -> float:
        """Read normalized volume in ``[0.0, 1.0]``."""
        ...

    async def set_volume(self, volume: float) -> None:
        """Write normalized volume in ``[0.0, 1.0]``."""
        ...

    async def get_can_play(self) -> bool:
        """Return whether ``Play`` is supported."""
        ...

    async def get_can_pause(self) -> bool:
        """Return whether ``Pause`` is supported."""
        ...

    async def get_can_control(self) -> bool:
        """Return whether player control commands are supported."""
        ...

    async def get_can_go_next(self) -> bool:
        """Return whether ``Next`` is supported."""
        ...

    async def get_can_go_previous(self) -> bool:
        """Return whether ``Previous`` is supported."""
        ...

    async def get_can_seek(self) -> bool:
        """Return whether seeking is supported."""
        ...

    async def play(self) -> None:
        """Start or resume playback."""
        ...

    async def pause(self) -> None:
        """Pause playback."""
        ...

    async def stop(self) -> None:
        """Stop playback."""
        ...

    async def next(self) -> None:
        """Skip to the next track."""
        ...

    async def previous(self) -> None:
        """Skip to the previous track."""
        ...

    async def set_position(self, track_id: str, position_us: int) -> None:
        """Seek within ``track_id`` to ``position_us`` microseconds."""
        ...

    def properties_changed(self) -> AsyncIterator[PropertiesChanged]:
        """Yield ``PropertiesChanged`` tuples emitted for the player."""
        ...

    def seeked(self) -> AsyncIterator[int]:
        """Yield new playback positions in microseconds on ``Seeked``."""
        ...


class SdbusMprisPlayer:
    """Live ``MprisPlayer`` adapter backed by ``MprisPlayerAsync``."""

    def __init__(self, service_name: str = PLAYERCTLD_SERVICE_NAME) -> None:
        """Open a fresh ``sdbus`` proxy against ``service_name``."""
        self._proxy: MprisPlayerAsync = MprisPlayerAsync.new_proxy(
            service_name, MPRIS_OBJECT_PATH
        )

    async def get_playback_status(self) -> str:
        return await self._proxy.playback_status

    async def get_metadata(self) -> MetadataMap:
        return await self._proxy.metadata

    async def get_position(self) -> int:
        return await self._proxy.position

    async def get_volume(self) -> float:
        return await self._proxy.volume

    async def set_volume(self, volume: float) -> None:
        # sdbus attaches set_async dynamically on @dbus_property_async descriptors.
        setter = cast(
            Callable[[float], Awaitable[None]],
            getattr(self._proxy.volume, "set_async"),
        )
        await setter(volume)

    async def get_can_play(self) -> bool:
        return await self._proxy.can_play

    async def get_can_pause(self) -> bool:
        return await self._proxy.can_pause

    async def get_can_control(self) -> bool:
        return await self._proxy.can_control

    async def get_can_go_next(self) -> bool:
        return await self._proxy.can_go_next

    async def get_can_go_previous(self) -> bool:
        return await self._proxy.can_go_previous

    async def get_can_seek(self) -> bool:
        return await self._proxy.can_seek

    async def play(self) -> None:
        await self._proxy.play()

    async def pause(self) -> None:
        await self._proxy.pause()

    async def stop(self) -> None:
        await self._proxy.stop()

    async def next(self) -> None:
        await self._proxy.next()

    async def previous(self) -> None:
        await self._proxy.previous()

    async def set_position(self, track_id: str, position_us: int) -> None:
        await self._proxy.set_position(track_id, position_us)

    def properties_changed(self) -> AsyncIterator[PropertiesChanged]:
        return self._proxy.properties_changed.catch()

    def seeked(self) -> AsyncIterator[int]:
        return self._proxy.seeked.catch()


def mpris_status_to_ha_state(status: str) -> MediaPlayerState:
    """Map an MPRIS ``PlaybackStatus`` string to a Home Assistant state."""
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
    """Extract ``key`` from ``metadata`` only when its D-Bus signature matches."""
    value = metadata.get(key)
    if value is None:
        return None

    actual_signature, payload = value
    if actual_signature != signature:
        return None

    return payload


def metadata_string(metadata: MetadataMap, key: str) -> str | None:
    """Return a string metadata entry or ``None`` if absent/wrongly typed."""
    value = metadata_value(metadata, key, "s")
    if isinstance(value, str):
        return value

    return None


def metadata_string_list(metadata: MetadataMap, key: str) -> tuple[str, ...]:
    """Return a tuple of string metadata items or ``()`` if absent/wrongly typed."""
    value = metadata_value(metadata, key, "as")
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()

    strings: list[str] = []
    for item in value:
        if isinstance(item, str):
            strings.append(item)

    return tuple(strings)


def metadata_int(metadata: MetadataMap, key: str) -> int | None:
    """Return an ``int64`` metadata entry or ``None`` if absent/wrongly typed."""
    value = metadata_value(metadata, key, "x")
    if isinstance(value, int):
        return value

    return None


def duration_seconds_from_metadata(metadata: MetadataMap) -> int:
    """Convert ``mpris:length`` microseconds into whole seconds (0 if missing)."""
    length_us = metadata_int(metadata, "mpris:length")
    if length_us is None or length_us < 0:
        return 0

    return length_us // MICROSECONDS_PER_SECOND


def track_id_from_metadata(metadata: MetadataMap) -> str | None:
    """Extract the D-Bus object path ``mpris:trackid`` when valid and active."""
    value = metadata_value(metadata, "mpris:trackid", "o")
    if not isinstance(value, str) or value == MPRIS_NO_TRACK_PATH:
        return None

    return value


def artist_text_from_metadata(metadata: MetadataMap) -> str:
    """Join ``xesam:artist`` entries with commas."""
    return ", ".join(metadata_string_list(metadata, "xesam:artist"))


def microseconds_to_seconds(value_us: int) -> int:
    """Convert microsecond timestamps into non-negative whole seconds."""
    if value_us <= 0:
        return 0

    return value_us // MICROSECONDS_PER_SECOND


def seconds_to_microseconds(value_seconds: float) -> int:
    """Convert non-negative seconds into integer microseconds."""
    if value_seconds < 0:
        raise ValueError("Seconds must be non-negative")

    return int(value_seconds * MICROSECONDS_PER_SECOND)
