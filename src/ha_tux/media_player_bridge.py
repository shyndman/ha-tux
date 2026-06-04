import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Literal, cast

from aiomqtt import Message
from ha_mqtt_discoverable import MqttSession
from ha_mqtt_discoverable.media_player import (
    MediaPlayer,
    MediaPlayerCallbacks,
    MediaPlayerInfo,
)

from ha_tux.album_art import AlbumArtPayload, AlbumArtResolver
from ha_tux.ha_media import (
    MediaPlayerPublisher,
    PlaceholderPublisher,
)
from ha_tux.mpris import (
    MPRIS_PLAYER_INTERFACE,
    PLAYERCTLD_SERVICE_NAME,
    DbusDaemonAsync,
    MetadataMap,
    MprisPlayerAsync,
    MprisRootAsync,
    PropertiesChanged,
    artist_text_from_metadata,
    duration_seconds_from_metadata,
    metadata_string,
    microseconds_to_seconds,
    mpris_status_to_ha_state,
    new_dbus_daemon_proxy,
    new_mpris_player_proxy,
    new_mpris_root_proxy,
    seconds_to_microseconds,
    track_id_from_metadata,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_UNMUTE_VOLUME = 1.0
DEFAULT_POSITION_POLL_SECONDS = 1.0
PublishReason = Literal[
    "initial",
    "manual",
    "properties_changed",
    "name_owner_changed",
    "command",
]


@dataclass(frozen=True, slots=True)
class MediaSnapshot:
    state: str
    title: str
    artist: str
    album: str
    duration: int
    position: int
    volume: float
    muted: bool
    album_art: AlbumArtPayload


class AsyncMprisMediaPlayerBridge:
    def __init__(
        self,
        *,
        player: MprisPlayerAsync,
        root: MprisRootAsync,
        dbus: DbusDaemonAsync,
        media_player: MediaPlayerPublisher,
        album_art_resolver: AlbumArtResolver,
        service_name: str = PLAYERCTLD_SERVICE_NAME,
        position_poll_seconds: float = DEFAULT_POSITION_POLL_SECONDS,
    ) -> None:
        self.player: MprisPlayerAsync = player
        self.root: MprisRootAsync = root
        self.dbus: DbusDaemonAsync = dbus
        self.media_player: MediaPlayerPublisher = media_player
        self.album_art_resolver: AlbumArtResolver = album_art_resolver
        self.service_name: str = service_name
        self.position_poll_seconds: float = position_poll_seconds
        self._last_snapshot: MediaSnapshot | None = None
        self._last_nonzero_volume: float | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._command_tasks: set[asyncio.Task[None]] = set()
        self._failure_event: asyncio.Event = asyncio.Event()
        self._failure: Exception | None = None
        self._stopped: bool = False

    async def start(self) -> None:
        self._stopped = False
        await self.publish_snapshot("initial")
        self._tasks.add(self._track_task(self._watch_properties()))
        self._tasks.add(self._track_task(self._watch_seeked()))
        self._tasks.add(self._track_task(self._watch_name_owner()))
        self._tasks.add(self._track_task(self._publish_position_while_playing()))

    async def stop(self) -> None:
        self._stopped = True
        _ = self._failure_event.set()
        for task in tuple(self._tasks | self._command_tasks):
            _ = task.cancel()
        for task in tuple(self._tasks | self._command_tasks):
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        self._command_tasks.clear()

    async def wait_until_stopped_or_failed(self) -> None:
        _ = await self._failure_event.wait()
        if self._failure is not None:
            raise self._failure

    async def publish_snapshot(self, reason: PublishReason) -> None:
        try:
            snapshot = await self._read_snapshot()
        except Exception:
            LOGGER.exception("Unable to read MPRIS snapshot", extra={"reason": reason})
            await self.media_player.set_available(False)
            return

        await self.media_player.set_available(True)
        await self._publish_snapshot(snapshot)

    async def handle_play(self) -> None:
        if not await _await_property(self.player.can_play):
            LOGGER.info("Ignoring play command because MPRIS CanPlay is false")
            return
        await self.player.play()
        await self.publish_snapshot("command")

    async def handle_pause(self) -> None:
        if not await _await_property(self.player.can_pause):
            LOGGER.info("Ignoring pause command because MPRIS CanPause is false")
            return
        await self.player.pause()
        await self.publish_snapshot("command")

    async def handle_stop(self) -> None:
        if not await _await_property(self.player.can_control):
            LOGGER.info("Ignoring stop command because MPRIS CanControl is false")
            return
        await self.player.stop()
        await self.publish_snapshot("command")

    async def handle_next_track(self) -> None:
        if not await _await_property(self.player.can_go_next):
            LOGGER.info("Ignoring next command because MPRIS CanGoNext is false")
            return
        await self.player.next()
        await self.publish_snapshot("command")

    async def handle_previous_track(self) -> None:
        if not await _await_property(self.player.can_go_previous):
            LOGGER.info(
                "Ignoring previous command because MPRIS CanGoPrevious is false"
            )
            return
        await self.player.previous()
        await self.publish_snapshot("command")

    async def handle_volume_set(self, volume: float) -> None:
        if not 0.0 <= volume <= 1.0:
            LOGGER.warning("Ignoring out-of-range volume", extra={"volume": volume})
            return
        await _set_property(self.player.volume, volume)
        self._update_last_nonzero_volume(volume)
        await self.media_player.set_volume(volume)
        await self.media_player.set_muted(volume == 0.0)

    async def handle_volume_mute(self, muted: bool) -> None:
        current_volume = cast(float, await _await_property(self.player.volume))
        if muted:
            self._update_last_nonzero_volume(current_volume)
            await _set_property(self.player.volume, 0.0)
            await self.media_player.set_volume(0.0)
            await self.media_player.set_muted(True)
            return

        restore_volume = self._last_nonzero_volume or DEFAULT_UNMUTE_VOLUME
        await _set_property(self.player.volume, restore_volume)
        await self.media_player.set_volume(restore_volume)
        await self.media_player.set_muted(False)

    async def handle_seek(self, position_seconds: float) -> None:
        if position_seconds < 0:
            LOGGER.warning(
                "Ignoring negative seek", extra={"position_seconds": position_seconds}
            )
            return
        if not await _await_property(self.player.can_seek):
            LOGGER.info("Ignoring seek command because MPRIS CanSeek is false")
            return

        metadata = cast(MetadataMap, await _await_property(self.player.metadata))
        track_id = track_id_from_metadata(metadata)
        if track_id is None:
            LOGGER.info("Ignoring seek command because MPRIS metadata has no track id")
            return

        position_us = seconds_to_microseconds(position_seconds)
        await self.player.set_position(track_id, position_us)
        await self.media_player.set_position(microseconds_to_seconds(position_us))

    def callbacks(self) -> MediaPlayerCallbacks:
        return {
            "play": self._play_callback,
            "pause": self._pause_callback,
            "stop": self._stop_callback,
            "next_track": self._next_callback,
            "previous_track": self._previous_callback,
            "volume_set": self._volume_set_callback,
            "volume_mute": self._volume_mute_callback,
            "seek": self._seek_callback,
        }

    async def _play_callback(self, _player: MediaPlayer, _message: Message) -> None:
        self._schedule_command(self.handle_play())

    async def _pause_callback(self, _player: MediaPlayer, _message: Message) -> None:
        self._schedule_command(self.handle_pause())

    async def _stop_callback(self, _player: MediaPlayer, _message: Message) -> None:
        self._schedule_command(self.handle_stop())

    async def _next_callback(self, _player: MediaPlayer, _message: Message) -> None:
        self._schedule_command(self.handle_next_track())

    async def _previous_callback(self, _player: MediaPlayer, _message: Message) -> None:
        self._schedule_command(self.handle_previous_track())

    async def _volume_set_callback(
        self, _player: MediaPlayer, volume: float, _message: Message
    ) -> None:
        self._schedule_command(self.handle_volume_set(volume))

    async def _volume_mute_callback(
        self, _player: MediaPlayer, muted: bool, _message: Message
    ) -> None:
        self._schedule_command(self.handle_volume_mute(muted))

    async def _seek_callback(
        self, _player: MediaPlayer, position_seconds: float, _message: Message
    ) -> None:
        self._schedule_command(self.handle_seek(position_seconds))

    async def _read_snapshot(self) -> MediaSnapshot:
        playback_status = cast(str, await _await_property(self.player.playback_status))
        metadata = cast(MetadataMap, await _await_property(self.player.metadata))
        position_us = cast(int, await _await_property(self.player.position))
        volume = cast(float, await _await_property(self.player.volume))
        art_url = metadata_string(metadata, "mpris:artUrl")
        album_art = self.album_art_resolver.resolve(art_url)
        self._update_last_nonzero_volume(volume)
        return MediaSnapshot(
            state=mpris_status_to_ha_state(playback_status),
            title=metadata_string(metadata, "xesam:title") or "",
            artist=artist_text_from_metadata(metadata),
            album=metadata_string(metadata, "xesam:album") or "",
            duration=duration_seconds_from_metadata(metadata),
            position=microseconds_to_seconds(position_us),
            volume=volume,
            muted=volume == 0.0,
            album_art=album_art,
        )

    async def _publish_snapshot(self, snapshot: MediaSnapshot) -> None:
        previous = self._last_snapshot
        if previous is None or previous.state != snapshot.state:
            await self.media_player.set_state(snapshot.state)
        if previous is None or previous.title != snapshot.title:
            await self.media_player.set_title(snapshot.title)
        if previous is None or previous.artist != snapshot.artist:
            await self.media_player.set_artist(snapshot.artist)
        if previous is None or previous.album != snapshot.album:
            await self.media_player.set_album(snapshot.album)
        if previous is None or previous.duration != snapshot.duration:
            await self.media_player.set_duration(snapshot.duration)
        if previous is None or previous.position != snapshot.position:
            await self.media_player.set_position(snapshot.position)
        if previous is None or previous.volume != snapshot.volume:
            await self.media_player.set_volume(snapshot.volume)
        if previous is None or previous.muted != snapshot.muted:
            await self.media_player.set_muted(snapshot.muted)
        if previous is None or previous.album_art.url != snapshot.album_art.url:
            await self.media_player.set_albumart_url(snapshot.album_art.url)
        if (
            previous is None
            or previous.album_art.remotely_accessible
            != snapshot.album_art.remotely_accessible
        ):
            await self.media_player.set_media_image_remotely_accessible(
                snapshot.album_art.remotely_accessible
            )
        self._last_snapshot = snapshot

    async def _watch_properties(self) -> None:
        async for changed in self.player.properties_changed.catch():
            await self._handle_properties_changed(changed)

    async def _watch_seeked(self) -> None:
        async for position_us in self.player.seeked.catch():
            await self.media_player.set_position(microseconds_to_seconds(position_us))

    async def _watch_name_owner(self) -> None:
        async for name, _old_owner, new_owner in self.dbus.name_owner_changed.catch():
            if name != self.service_name:
                continue
            await self._handle_name_owner_changed(new_owner)

    async def _publish_position_while_playing(self) -> None:
        while not self._stopped:
            await asyncio.sleep(self.position_poll_seconds)
            snapshot = self._last_snapshot
            if snapshot is None or snapshot.state != "playing":
                continue
            try:
                position_us = cast(int, await _await_property(self.player.position))
            except Exception:
                LOGGER.exception("Unable to read MPRIS position")
                continue
            position = microseconds_to_seconds(position_us)
            if (
                self._last_snapshot is not None
                and position != self._last_snapshot.position
            ):
                await self.media_player.set_position(position)
                self._last_snapshot = _replace_snapshot_position(
                    self._last_snapshot, position
                )

    async def _handle_properties_changed(self, changed: PropertiesChanged) -> None:
        interface_name, changed_properties, invalidated_properties = changed
        if interface_name != MPRIS_PLAYER_INTERFACE:
            return
        if changed_properties or invalidated_properties:
            await self.publish_snapshot("properties_changed")

    async def _handle_name_owner_changed(self, new_owner: str) -> None:
        if new_owner == "":
            await self.media_player.set_available(False)
            self._last_snapshot = None
            return
        self.player = new_mpris_player_proxy(self.service_name)
        self.root = new_mpris_root_proxy(self.service_name)
        await self.publish_snapshot("name_owner_changed")

    def _schedule_command(self, awaitable: Coroutine[object, object, None]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(awaitable)
        self._command_tasks.add(task)
        task.add_done_callback(self._command_task_done)

    def _command_task_done(self, task: asyncio.Task[None]) -> None:
        self._command_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self._mark_failed(error)
            LOGGER.exception("MQTT command task failed")

    def _track_task(
        self, awaitable: Coroutine[object, object, None]
    ) -> asyncio.Task[None]:
        task: asyncio.Task[None] = asyncio.create_task(awaitable)
        task.add_done_callback(self._observer_task_done)
        return task

    def _observer_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self._mark_failed(error)
            LOGGER.exception("MPRIS observer task failed")

    def _mark_failed(self, error: Exception) -> None:
        if self._failure is None:
            self._failure = error
        _ = self._failure_event.set()

    def _update_last_nonzero_volume(self, volume: float) -> None:
        if volume > 0.0:
            self._last_nonzero_volume = volume


def create_bridge(
    session: MqttSession,
    entity: MediaPlayerInfo,
    *,
    service_name: str = PLAYERCTLD_SERVICE_NAME,
    position_poll_seconds: float = DEFAULT_POSITION_POLL_SECONDS,
) -> AsyncMprisMediaPlayerBridge:
    player = new_mpris_player_proxy(service_name)
    root = new_mpris_root_proxy(service_name)
    dbus = new_dbus_daemon_proxy()
    resolver = AlbumArtResolver()
    placeholder = PlaceholderPublisher()
    bridge = AsyncMprisMediaPlayerBridge(
        player=player,
        root=root,
        dbus=dbus,
        media_player=placeholder,
        album_art_resolver=resolver,
        service_name=service_name,
        position_poll_seconds=position_poll_seconds,
    )
    media_player = MediaPlayer(session, entity, bridge.callbacks())
    bridge.media_player = media_player
    return bridge


async def _await_property(value: object) -> object:
    return await cast(Awaitable[object], value)


async def _set_property(property_proxy: object, value: object) -> None:
    set_async = cast(
        Callable[[object], Awaitable[None]], getattr(property_proxy, "set_async")
    )
    await set_async(value)


def _replace_snapshot_position(snapshot: MediaSnapshot, position: int) -> MediaSnapshot:
    return MediaSnapshot(
        state=snapshot.state,
        title=snapshot.title,
        artist=snapshot.artist,
        album=snapshot.album,
        duration=snapshot.duration,
        position=position,
        volume=snapshot.volume,
        muted=snapshot.muted,
        album_art=snapshot.album_art,
    )
