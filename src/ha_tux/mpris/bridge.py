"""Home Assistant media-player bridge over MPRIS D-Bus and MQTT discovery.

Owns the Home Assistant ``MediaPlayer`` entity directly, tracks D-Bus service
ownership via ``ha_tux.dbus.watch_name``, keeps snapshot state coherent across
commands and seek signals, and delegates player mechanics to ``MprisPlayer``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Final, Literal

from aiomqtt import Message
from ha_mqtt_discoverable import DeviceInfo, SessionLike
from ha_mqtt_discoverable.media_player import (
    MediaPlayer,
    MediaPlayerCallbacks,
    MediaPlayerInfo,
)
from libsh import get_logger

from ha_tux.dbus import NameOwnerSource, session_name_owner_source, watch_name
from ha_tux.foundation.time import microseconds_to_seconds, seconds_to_microseconds
from ha_tux.host_device import build_host_device_info
from ha_tux.mpris.album_art import (
    AlbumArtPayload,
    AlbumArtResolver,
    youtube_thumbnail_url,
)
from ha_tux.mpris.player import (
    DEFAULT_POSITION_POLL_SECONDS,
    MPRIS_PLAYER_INTERFACE,
    PLAYERCTLD_SERVICE_NAME,
    MprisPlayer,
    PlayerctldNoActivePlayerError,
    PropertiesChanged,
    SdbusMprisPlayer,
    artist_text_from_metadata,
    duration_seconds_from_metadata,
    metadata_string,
    mpris_status_to_ha_state,
    track_id_from_metadata,
)
from ha_tux.runtime import SessionContext

LOGGER = get_logger(__name__)

HA_TUX_MEDIA_OBJECT_ID: Final = "desktop_media"
HA_TUX_MEDIA_NAME: Final = "Desktop Media"
HA_TUX_MEDIA_DEVICE_CLASS: Final = "speaker"
DEFAULT_UNMUTE_VOLUME: Final = 1.0

PublishReason = Literal[
    "initial",
    "manual",
    "properties_changed",
    "command",
]


@dataclass(frozen=True, slots=True)
class MediaSnapshot:
    """Diffable snapshot of the state last published to Home Assistant."""

    state: str
    title: str
    artist: str
    album: str
    duration: int
    position: int
    volume: float
    muted: bool
    album_art: AlbumArtPayload


def build_media_player_entity(
    device: DeviceInfo | None = None, *, host_prefix: str
) -> MediaPlayerInfo:
    """Build Home Assistant discovery metadata for the desktop media player."""
    resolved_device = device if device is not None else build_host_device_info()
    return MediaPlayerInfo(
        name=HA_TUX_MEDIA_NAME,
        object_id=f"{host_prefix}_{HA_TUX_MEDIA_OBJECT_ID}",
        unique_id=f"ha_tux_{host_prefix}_{HA_TUX_MEDIA_OBJECT_ID}",
        device=resolved_device,
        device_class=HA_TUX_MEDIA_DEVICE_CLASS,
    )


class MprisBridge:
    """Bridges an owned MPRIS D-Bus service with a Home Assistant ``MediaPlayer``."""

    def __init__(
        self,
        *,
        session: SessionLike,
        device: DeviceInfo,
        host_prefix: str,
        service_name: str = PLAYERCTLD_SERVICE_NAME,
        position_poll_seconds: float = DEFAULT_POSITION_POLL_SECONDS,
        name_owner_source: NameOwnerSource | None = None,
        player_factory: Callable[[], MprisPlayer] | None = None,
        album_art_resolver: AlbumArtResolver | None = None,
    ) -> None:
        """Create the real ``MediaPlayer`` entity and wire D-Bus/MPRIS seams."""
        info = build_media_player_entity(device, host_prefix=host_prefix)
        self._service_name: str = service_name
        self._position_poll_seconds: float = position_poll_seconds
        self._name_owner_source: NameOwnerSource = (
            name_owner_source
            if name_owner_source is not None
            else session_name_owner_source()
        )
        self._player_factory: Callable[[], MprisPlayer] = (
            player_factory
            if player_factory is not None
            else lambda: SdbusMprisPlayer(service_name)
        )
        self._album_art_resolver: AlbumArtResolver = (
            album_art_resolver if album_art_resolver is not None else AlbumArtResolver()
        )
        self._player: MprisPlayer | None = None
        self._last_snapshot: MediaSnapshot | None = None
        self._last_nonzero_volume: float | None = None
        self._media_player: MediaPlayer = MediaPlayer(session, info, self._callbacks())

    async def run(self) -> None:
        """Watch ``service_name`` ownership and supervise attached player tasks."""
        LOGGER.info(
            "Starting MPRIS media player bridge", service_name=self._service_name
        )
        await watch_name(
            source=self._name_owner_source,
            service_name=self._service_name,
            on_present=self._run_attached,
            on_absent=self._mark_unavailable,
        )

    async def _run_attached(self) -> None:
        player = self._player_factory()
        self._player = player
        try:
            await self.publish_snapshot("initial")
            async with asyncio.TaskGroup() as tasks:
                _ = tasks.create_task(
                    self._watch_properties(player), name="mpris:properties"
                )
                _ = tasks.create_task(self._watch_seeked(player), name="mpris:seeked")
                _ = tasks.create_task(
                    self._publish_position_while_playing(player),
                    name="mpris:position_poll",
                )
        finally:
            if self._player is player:
                self._player = None

    async def publish_snapshot(self, reason: PublishReason) -> None:
        """Read current MPRIS state and publish only fields that changed."""
        player = self._player
        if player is None:
            LOGGER.debug(
                "Skipping MPRIS snapshot while no D-Bus owner is attached",
                reason=reason,
            )
            return

        try:
            snapshot = await self._read_snapshot(player)
        except PlayerctldNoActivePlayerError:
            LOGGER.info("No active MPRIS player", reason=reason)
            await self._mark_unavailable()
            return
        except Exception:
            LOGGER.exception("Failed to read MPRIS player snapshot", reason=reason)
            await self._mark_unavailable()
            return

        await self._media_player.set_available(True)
        await self._publish_snapshot(snapshot)

    async def _run_playback_command(
        self,
        *,
        command: str,
        reason: str,
        is_supported: Callable[[MprisPlayer], Awaitable[bool]],
        execute: Callable[[MprisPlayer], Awaitable[None]],
    ) -> None:
        player = self._require_player(command)
        if player is None:
            return
        if not await is_supported(player):
            LOGGER.info(
                "Ignoring MPRIS command because capability is unavailable",
                command=command,
                reason=reason,
            )
            return
        await execute(player)
        await self.publish_snapshot("command")

    async def handle_play(self) -> None:
        """Forward Home Assistant ``play`` command to the active MPRIS player."""
        await self._run_playback_command(
            command="play",
            reason="cannot_play",
            is_supported=lambda player: player.get_can_play(),
            execute=lambda player: player.play(),
        )

    async def handle_pause(self) -> None:
        """Forward Home Assistant ``pause`` command to the active MPRIS player."""
        await self._run_playback_command(
            command="pause",
            reason="cannot_pause",
            is_supported=lambda player: player.get_can_pause(),
            execute=lambda player: player.pause(),
        )

    async def handle_stop(self) -> None:
        """Forward Home Assistant ``stop`` command to the active MPRIS player."""
        await self._run_playback_command(
            command="stop",
            reason="cannot_control",
            is_supported=lambda player: player.get_can_control(),
            execute=lambda player: player.stop(),
        )

    async def handle_next_track(self) -> None:
        """Forward Home Assistant ``next_track`` command to the active MPRIS player."""
        await self._run_playback_command(
            command="next_track",
            reason="cannot_go_next",
            is_supported=lambda player: player.get_can_go_next(),
            execute=lambda player: player.next(),
        )

    async def handle_previous_track(self) -> None:
        """Forward Home Assistant ``previous_track`` command to the player."""
        await self._run_playback_command(
            command="previous_track",
            reason="cannot_go_previous",
            is_supported=lambda player: player.get_can_go_previous(),
            execute=lambda player: player.previous(),
        )

    async def handle_volume_set(self, volume: float) -> None:
        """Set player volume and keep the cached snapshot consistent."""
        if not 0.0 <= volume <= 1.0:
            LOGGER.warning("Ignoring out-of-range MPRIS volume", volume=volume)
            return
        player = self._require_player("volume_set")
        if player is None:
            return

        await player.set_volume(volume)
        self._update_last_nonzero_volume(volume)
        muted = volume == 0.0
        await self._media_player.set_volume(volume)
        await self._media_player.set_muted(muted)
        if self._last_snapshot is not None:
            self._last_snapshot = replace(
                self._last_snapshot, volume=volume, muted=muted
            )

    async def handle_volume_mute(self, muted: bool) -> None:
        """Mute or restore volume and keep the cached snapshot consistent."""
        player = self._require_player("volume_mute")
        if player is None:
            return

        current_volume = await player.get_volume()
        if muted:
            self._update_last_nonzero_volume(current_volume)
            target_volume = 0.0
        else:
            target_volume = self._last_nonzero_volume or DEFAULT_UNMUTE_VOLUME

        await player.set_volume(target_volume)
        await self._media_player.set_volume(target_volume)
        await self._media_player.set_muted(muted)
        if self._last_snapshot is not None:
            self._last_snapshot = replace(
                self._last_snapshot, volume=target_volume, muted=muted
            )

    async def handle_seek(self, position_seconds: float) -> None:
        """Seek the active track and keep the cached snapshot consistent."""
        if position_seconds < 0:
            LOGGER.warning(
                "Ignoring negative MPRIS seek position",
                position_seconds=position_seconds,
            )
            return
        player = self._require_player("seek")
        if player is None:
            return
        if not await player.get_can_seek():
            LOGGER.info(
                "Ignoring MPRIS command because capability is unavailable",
                command="seek",
                reason="cannot_seek",
            )
            return

        track_id = track_id_from_metadata(await player.get_metadata())
        if track_id is None:
            LOGGER.info(
                "Ignoring MPRIS command because capability is unavailable",
                command="seek",
                reason="no_track_id",
            )
            return

        position_us = seconds_to_microseconds(position_seconds)
        position = microseconds_to_seconds(position_us)
        await player.set_position(track_id, position_us)
        await self._media_player.set_position(position)
        if self._last_snapshot is not None:
            self._last_snapshot = replace(self._last_snapshot, position=position)

    def _require_player(self, command: str) -> MprisPlayer | None:
        if self._player is None:
            LOGGER.info(
                "Ignoring MPRIS command while no D-Bus owner is attached",
                command=command,
            )
        return self._player

    def _callbacks(self) -> MediaPlayerCallbacks:
        def command(
            fn: Callable[[], Awaitable[None]],
        ) -> Callable[[MediaPlayer, Message], Awaitable[None]]:
            async def invoke(_player: MediaPlayer, _message: Message) -> None:
                await fn()

            return invoke

        def value_command[T](
            fn: Callable[[T], Awaitable[None]],
        ) -> Callable[[MediaPlayer, T, Message], Awaitable[None]]:
            async def invoke(_player: MediaPlayer, value: T, _message: Message) -> None:
                await fn(value)

            return invoke

        return {
            "play": command(self.handle_play),
            "pause": command(self.handle_pause),
            "stop": command(self.handle_stop),
            "next_track": command(self.handle_next_track),
            "previous_track": command(self.handle_previous_track),
            "volume_set": value_command(self.handle_volume_set),
            "volume_mute": value_command(self.handle_volume_mute),
            "seek": value_command(self.handle_seek),
        }

    async def _read_snapshot(self, player: MprisPlayer) -> MediaSnapshot:
        playback_status = await player.get_playback_status()
        metadata = await player.get_metadata()
        position_us = await player.get_position()
        volume = await player.get_volume()

        art_url = metadata_string(metadata, "mpris:artUrl") or youtube_thumbnail_url(
            metadata_string(metadata, "xesam:url")
        )
        album_art = self._album_art_resolver.resolve(art_url)
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
            await self._media_player.set_state(snapshot.state)
        if previous is None or previous.title != snapshot.title:
            await self._media_player.set_title(snapshot.title)
        if previous is None or previous.artist != snapshot.artist:
            await self._media_player.set_artist(snapshot.artist)
        if previous is None or previous.album != snapshot.album:
            await self._media_player.set_album(snapshot.album)
        if previous is None or previous.duration != snapshot.duration:
            await self._media_player.set_duration(snapshot.duration)
        if previous is None or previous.position != snapshot.position:
            await self._media_player.set_position(snapshot.position)
        if previous is None or previous.volume != snapshot.volume:
            await self._media_player.set_volume(snapshot.volume)
        if previous is None or previous.muted != snapshot.muted:
            await self._media_player.set_muted(snapshot.muted)
        if previous is None or previous.album_art.url != snapshot.album_art.url:
            LOGGER.info(
                "Publishing MPRIS album art payload",
                album_art_payload_bytes=len(snapshot.album_art.url),
                album_art_remotely_accessible=snapshot.album_art.remotely_accessible,
            )
            await self._media_player.set_albumart_url(snapshot.album_art.url)
        if (
            previous is None
            or previous.album_art.remotely_accessible
            != snapshot.album_art.remotely_accessible
        ):
            await self._media_player.set_media_image_remotely_accessible(
                snapshot.album_art.remotely_accessible
            )
        self._last_snapshot = snapshot

    async def _watch_properties(self, player: MprisPlayer) -> None:
        async for changed in player.properties_changed():
            await self._handle_properties_changed(changed)

    async def _watch_seeked(self, player: MprisPlayer) -> None:
        async for position_us in player.seeked():
            position = microseconds_to_seconds(position_us)
            await self._media_player.set_position(position)
            snapshot = self._last_snapshot
            if snapshot is not None:
                self._last_snapshot = replace(snapshot, position=position)

    async def _publish_position_while_playing(self, player: MprisPlayer) -> None:
        while True:
            await asyncio.sleep(self._position_poll_seconds)
            snapshot = self._last_snapshot
            if snapshot is None or snapshot.state != "playing":
                continue

            try:
                position_us = await player.get_position()
            except PlayerctldNoActivePlayerError:
                LOGGER.info("No active MPRIS player while polling playback position")
                await self._mark_unavailable()
                continue
            except Exception:
                LOGGER.exception("Failed to read MPRIS playback position")
                continue

            position = microseconds_to_seconds(position_us)
            if (
                self._last_snapshot is not None
                and position != self._last_snapshot.position
            ):
                await self._media_player.set_position(position)
                self._last_snapshot = replace(self._last_snapshot, position=position)

    async def _handle_properties_changed(self, changed: PropertiesChanged) -> None:
        interface_name, changed_properties, invalidated_properties = changed
        if interface_name != MPRIS_PLAYER_INTERFACE:
            return
        if changed_properties or invalidated_properties:
            await self.publish_snapshot("properties_changed")

    async def _mark_unavailable(self) -> None:
        LOGGER.info(
            "MPRIS player unavailable; marking media player offline",
            service_name=self._service_name,
        )
        self._last_snapshot = None
        await self._media_player.set_available(False)

    def _update_last_nonzero_volume(self, volume: float) -> None:
        if volume > 0.0:
            self._last_nonzero_volume = volume


async def run_mpris(
    ctx: SessionContext,
    *,
    name_owner_source: NameOwnerSource | None = None,
    player_factory: Callable[[], MprisPlayer] | None = None,
    album_art_resolver: AlbumArtResolver | None = None,
) -> None:
    """Run the MPRIS feature for a supervised MQTT session."""
    bridge = MprisBridge(
        session=ctx.session,
        device=ctx.device,
        host_prefix=ctx.host_prefix,
        service_name=ctx.config.mpris.service,
        position_poll_seconds=ctx.config.mpris.position_poll_seconds,
        name_owner_source=name_owner_source,
        player_factory=player_factory,
        album_art_resolver=album_art_resolver,
    )
    await bridge.run()
