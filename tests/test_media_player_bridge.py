import asyncio
from collections.abc import AsyncIterator, Awaitable, Generator
from dataclasses import dataclass, field
from typing import Generic, TypeVar, cast, override

from aiomqtt import Message
from ha_mqtt_discoverable.media_player import MediaPlayer

from ha_tux.album_art import AlbumArtResolver
from ha_tux.media_player_bridge import AsyncMprisMediaPlayerBridge
from ha_tux.mpris import (
    MICROSECONDS_PER_SECOND,
    DbusDaemonAsync,
    MprisPlayerAsync,
    MprisRootAsync,
    PropertiesChanged,
)

T = TypeVar("T")


@dataclass
class AsyncProperty(Generic[T], Awaitable[T]):
    value: T
    writes: list[T] = field(default_factory=list)

    @override
    def __await__(self) -> Generator[None, None, T]:
        async def read() -> T:
            return self.value

        return read().__await__()

    async def set_async(self, value: T) -> None:
        self.value = value
        self.writes.append(value)


class FakeSignal(Generic[T]):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[T] = asyncio.Queue()

    def catch(self) -> AsyncIterator[T]:
        return self._iterate()

    async def emit(self, value: T) -> None:
        await self._queue.put(value)

    async def _iterate(self) -> AsyncIterator[T]:
        while True:
            yield await self._queue.get()


@dataclass
class FakePlayer:
    playback_status: AsyncProperty[str] = field(
        default_factory=lambda: AsyncProperty("Playing")
    )
    metadata: AsyncProperty[dict[str, tuple[str, object]]] = field(
        default_factory=lambda: AsyncProperty(
            {
                "xesam:title": ("s", "Song"),
                "xesam:artist": ("as", ["Artist"]),
                "xesam:album": ("s", "Album"),
                "mpris:length": ("x", 120 * MICROSECONDS_PER_SECOND),
                "mpris:trackid": ("o", "/track/1"),
            }
        )
    )
    position: AsyncProperty[int] = field(
        default_factory=lambda: AsyncProperty(7 * MICROSECONDS_PER_SECOND)
    )
    volume: AsyncProperty[float] = field(default_factory=lambda: AsyncProperty(0.5))
    can_play: AsyncProperty[bool] = field(default_factory=lambda: AsyncProperty(True))
    can_pause: AsyncProperty[bool] = field(default_factory=lambda: AsyncProperty(True))
    can_control: AsyncProperty[bool] = field(
        default_factory=lambda: AsyncProperty(True)
    )
    can_go_next: AsyncProperty[bool] = field(
        default_factory=lambda: AsyncProperty(True)
    )
    can_go_previous: AsyncProperty[bool] = field(
        default_factory=lambda: AsyncProperty(True)
    )
    can_seek: AsyncProperty[bool] = field(default_factory=lambda: AsyncProperty(True))
    properties_changed: FakeSignal[PropertiesChanged] = field(
        default_factory=FakeSignal
    )
    seeked: FakeSignal[int] = field(default_factory=FakeSignal)
    play_calls: int = 0
    pause_calls: int = 0
    stop_calls: int = 0
    next_calls: int = 0
    previous_calls: int = 0
    set_position_calls: list[tuple[str, int]] = field(default_factory=list)

    async def play(self) -> None:
        self.play_calls += 1

    async def pause(self) -> None:
        self.pause_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1

    async def next(self) -> None:
        self.next_calls += 1

    async def previous(self) -> None:
        self.previous_calls += 1

    async def set_position(self, track_id: str, position_us: int) -> None:
        self.set_position_calls.append((track_id, position_us))


@dataclass
class FakeRoot:
    identity: AsyncProperty[str] = field(
        default_factory=lambda: AsyncProperty("Chrome")
    )


@dataclass
class FakeDbus:
    name_owner_changed: FakeSignal[tuple[str, str, str]] = field(
        default_factory=FakeSignal
    )


@dataclass
class FakeMediaPlayer:
    calls: list[tuple[str, object]] = field(default_factory=list)

    async def set_available(self, available: bool) -> None:
        self.calls.append(("available", available))

    async def set_state(self, state: str) -> None:
        self.calls.append(("state", state))

    async def set_title(self, title: str) -> None:
        self.calls.append(("title", title))

    async def set_artist(self, artist: str) -> None:
        self.calls.append(("artist", artist))

    async def set_album(self, album: str) -> None:
        self.calls.append(("album", album))

    async def set_duration(self, duration: int) -> None:
        self.calls.append(("duration", duration))

    async def set_position(self, position: int) -> None:
        self.calls.append(("position", position))

    async def set_volume(self, volume: float) -> None:
        self.calls.append(("volume", volume))

    async def set_muted(self, muted: bool) -> None:
        self.calls.append(("muted", muted))

    async def set_albumart_url(self, url: str) -> None:
        self.calls.append(("albumart", url))

    async def set_media_image_remotely_accessible(self, accessible: bool) -> None:
        self.calls.append(("remote_art", accessible))


def build_bridge() -> tuple[
    AsyncMprisMediaPlayerBridge, FakePlayer, FakeMediaPlayer, FakeDbus
]:
    player = FakePlayer()
    media = FakeMediaPlayer()
    dbus = FakeDbus()
    bridge = AsyncMprisMediaPlayerBridge(
        player=cast(MprisPlayerAsync, cast(object, player)),
        root=cast(MprisRootAsync, cast(object, FakeRoot())),
        dbus=cast(DbusDaemonAsync, cast(object, dbus)),
        media_player=media,
        album_art_resolver=AlbumArtResolver(),
        position_poll_seconds=0.01,
    )
    return bridge, player, media, dbus


def test_snapshot_publishes_media_state() -> None:
    async def run() -> None:
        bridge, _player, media, _dbus = build_bridge()

        await bridge.publish_snapshot("manual")

        assert ("available", True) in media.calls
        assert ("state", "playing") in media.calls
        assert ("title", "Song") in media.calls
        assert ("artist", "Artist") in media.calls
        assert ("album", "Album") in media.calls
        assert ("duration", 120) in media.calls
        assert ("position", 7) in media.calls
        assert ("volume", 0.5) in media.calls
        assert ("muted", False) in media.calls

    asyncio.run(run())


def test_mute_layers_on_top_of_volume() -> None:
    async def run() -> None:
        bridge, player, media, _dbus = build_bridge()

        await bridge.handle_volume_mute(True)
        await bridge.handle_volume_mute(False)

        assert player.volume.writes == [0.0, 0.5]
        assert ("muted", True) in media.calls
        assert ("muted", False) in media.calls

    asyncio.run(run())


def test_seek_uses_absolute_mpris_set_position() -> None:
    async def run() -> None:
        bridge, player, media, _dbus = build_bridge()

        await bridge.handle_seek(42)

        assert player.set_position_calls == [("/track/1", 42 * MICROSECONDS_PER_SECOND)]
        assert ("position", 42) in media.calls

    asyncio.run(run())


def test_seek_noops_without_capability() -> None:
    async def run() -> None:
        bridge, player, _media, _dbus = build_bridge()
        player.can_seek.value = False

        await bridge.handle_seek(42)

        assert player.set_position_calls == []

    asyncio.run(run())


def test_command_callbacks_enqueue_async_work() -> None:
    async def run() -> None:
        bridge, player, _media, _dbus = build_bridge()
        callbacks = bridge.callbacks()
        play = callbacks.get("play")
        assert play is not None

        await play(
            cast(MediaPlayer, cast(object, None)),
            cast(Message, cast(object, None)),
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert player.play_calls == 1

    asyncio.run(run())
