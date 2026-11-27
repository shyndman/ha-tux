from __future__ import annotations

import asyncio
from base64 import b64encode
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import aiomqtt
import pytest
from ha_mqtt_discoverable import DeviceInfo
from structlog.testing import capture_logs

from ha_tux.config import HaTuxConfig
from ha_tux.mpris import MprisBridge, PLAYERCTLD_SERVICE_NAME, run_mpris
from ha_tux.mpris.album_art import AlbumArtResolver
from ha_tux.mpris.player import (
    MICROSECONDS_PER_SECOND,
    MPRIS_PLAYER_INTERFACE,
    MetadataMap,
    MprisPlayer,
    PlayerctldNoActivePlayerError,
    PropertiesChanged,
    Variant,
)
from ha_tux.runtime import SessionContext
from tests.fakes import FakeNameOwnerSource, InMemorySession, settle

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")
TEST_CONFIG = HaTuxConfig()
TOPIC_PREFIX = "hmd/media_player/ha-tux/testbox_desktop_media"


@dataclass
class FakeMprisPlayer:
    """In-memory ``MprisPlayer`` adapter with scriptable fields and signal queues."""

    playback_status: str = "Playing"
    metadata: dict[str, Variant] = field(
        default_factory=lambda: {
            "xesam:title": ("s", "Song"),
            "xesam:artist": ("as", ["Artist"]),
            "xesam:album": ("s", "Album"),
            "mpris:length": ("x", 120 * MICROSECONDS_PER_SECOND),
            "mpris:trackid": ("o", "/track/1"),
        }
    )
    position_us: int = 7 * MICROSECONDS_PER_SECOND
    volume_level: float = 0.5
    can_play_flag: bool = True
    can_pause_flag: bool = True
    can_control_flag: bool = True
    can_go_next_flag: bool = True
    can_go_previous_flag: bool = True
    can_seek_flag: bool = True
    playback_error: Exception | None = None
    position_error: Exception | None = None
    position_reads: int = 0
    volume_writes: list[float] = field(default_factory=list)
    play_calls: int = 0
    pause_calls: int = 0
    stop_calls: int = 0
    next_calls: int = 0
    previous_calls: int = 0
    set_position_calls: list[tuple[str, int]] = field(default_factory=list)
    _properties_queue: asyncio.Queue[PropertiesChanged] = field(
        default_factory=asyncio.Queue
    )
    _seeked_queue: asyncio.Queue[int] = field(default_factory=asyncio.Queue)

    async def get_playback_status(self) -> str:
        if self.playback_error is not None:
            raise self.playback_error
        return self.playback_status

    async def get_metadata(self) -> MetadataMap:
        return self.metadata

    async def get_position(self) -> int:
        self.position_reads += 1
        if self.position_error is not None:
            raise self.position_error
        return self.position_us

    async def get_volume(self) -> float:
        return self.volume_level

    async def set_volume(self, volume: float) -> None:
        self.volume_level = volume
        self.volume_writes.append(volume)

    async def get_can_play(self) -> bool:
        return self.can_play_flag

    async def get_can_pause(self) -> bool:
        return self.can_pause_flag

    async def get_can_control(self) -> bool:
        return self.can_control_flag

    async def get_can_go_next(self) -> bool:
        return self.can_go_next_flag

    async def get_can_go_previous(self) -> bool:
        return self.can_go_previous_flag

    async def get_can_seek(self) -> bool:
        return self.can_seek_flag

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
        self.position_us = position_us

    async def properties_changed(self) -> AsyncIterator[PropertiesChanged]:
        while True:
            yield await self._properties_queue.get()

    async def seeked(self) -> AsyncIterator[int]:
        while True:
            yield await self._seeked_queue.get()

    async def emit_properties_changed(
        self, changed: dict[str, Variant] | None = None
    ) -> None:
        payload: dict[str, Variant] = (
            changed if changed is not None else {"Metadata": ("b", True)}
        )
        await self._properties_queue.put((MPRIS_PLAYER_INTERFACE, payload, []))

    async def emit_seeked(self, position_us: int) -> None:
        self.position_us = position_us
        await self._seeked_queue.put(position_us)


def _topic_values(session: InMemorySession, suffix: str) -> list[object]:
    topic = f"{TOPIC_PREFIX}/{suffix}"
    return [msg.payload for msg in session.published if msg.topic == topic]


def _build_bridge(
    *,
    initially_present: bool = True,
    players: list[FakeMprisPlayer] | None = None,
    position_poll_seconds: float = 0.01,
) -> tuple[
    MprisBridge,
    InMemorySession,
    FakeNameOwnerSource,
    list[FakeMprisPlayer],
]:
    session = InMemorySession()
    source = FakeNameOwnerSource(initially_present=initially_present)
    scripted = list(players) if players is not None else [FakeMprisPlayer()]
    created: list[FakeMprisPlayer] = []

    def factory() -> MprisPlayer:
        player = (
            scripted[len(created)] if len(created) < len(scripted) else scripted[-1]
        )
        created.append(player)
        return player

    bridge = MprisBridge(
        session=session,
        device=DEVICE,
        host_prefix="testbox",
        position_poll_seconds=position_poll_seconds,
        name_owner_source=source,
        player_factory=factory,
        album_art_resolver=AlbumArtResolver(),
    )
    return bridge, session, source, created


def test_snapshot_publishes_media_state_over_mqtt() -> None:
    async def run() -> None:
        bridge, session, _source, _created = _build_bridge(position_poll_seconds=60.0)

        task = asyncio.create_task(bridge.run())
        await settle()

        assert _topic_values(session, "availability")[-1] == "online"
        assert _topic_values(session, "state") == ["playing"]
        assert _topic_values(session, "title") == ["Song"]
        assert _topic_values(session, "artist") == ["Artist"]
        assert _topic_values(session, "album") == ["Album"]
        assert _topic_values(session, "duration") == ["120"]
        assert _topic_values(session, "position") == ["7"]
        assert _topic_values(session, "volume") == ["0.5"]
        assert _topic_values(session, "volume_mute_state") == ["false"]

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_snapshot_logs_album_art_payload_size_without_raw_data_uri(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"payload"
        art = tmp_path / "firefox-art"
        _ = art.write_bytes(png_bytes)
        player = FakeMprisPlayer()
        player.metadata["mpris:artUrl"] = ("s", art.as_uri())
        bridge, _session, _source, _created = _build_bridge(
            players=[player], position_poll_seconds=60.0
        )

        with capture_logs() as logs:
            task = asyncio.create_task(bridge.run())
            await settle()

        expected_url = f"data:image/png;base64,{b64encode(png_bytes).decode('ascii')}"
        art_logs = [
            entry for entry in logs if entry.get("event") == "mpris_album_art_published"
        ]
        assert len(art_logs) == 1
        assert cast(int, art_logs[0]["album_art_payload_bytes"]) == len(expected_url)
        assert art_logs[0]["album_art_remotely_accessible"] is True
        assert expected_url not in str(art_logs[0])

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_mute_volume_and_seek_keep_snapshot_coherent() -> None:
    async def run() -> None:
        player = FakeMprisPlayer()
        bridge, session, _source, _created = _build_bridge(
            players=[player], position_poll_seconds=60.0
        )

        task = asyncio.create_task(bridge.run())
        await settle()
        session.published.clear()

        await session.deliver(f"{TOPIC_PREFIX}/volume_mute", b"ON")
        await session.deliver(f"{TOPIC_PREFIX}/volume_mute", b"OFF")
        await session.deliver(f"{TOPIC_PREFIX}/volume_set", b"0.3")
        await session.deliver(f"{TOPIC_PREFIX}/seek", b"42")
        await player.emit_seeked(55 * MICROSECONDS_PER_SECOND)
        await settle()

        assert player.volume_writes == [0.0, 0.5, 0.3]
        assert player.set_position_calls == [("/track/1", 42 * MICROSECONDS_PER_SECOND)]
        assert _topic_values(session, "volume_mute_state") == ["true", "false", "false"]
        assert _topic_values(session, "volume") == ["0.0", "0.5", "0.3"]
        assert _topic_values(session, "position") == ["42", "55"]

        # Subsequent snapshot diff must see volume=0.3, muted=False, position=55
        # already matches and emit zero redundant updates.
        session.published.clear()
        await bridge.publish_snapshot("manual")
        assert _topic_values(session, "volume") == []
        assert _topic_values(session, "volume_mute_state") == []
        assert _topic_values(session, "position") == []

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_seek_noops_without_capability() -> None:
    async def run() -> None:
        player = FakeMprisPlayer(can_seek_flag=False)
        bridge, session, _source, _created = _build_bridge(
            players=[player], position_poll_seconds=60.0
        )

        task = asyncio.create_task(bridge.run())
        await settle()

        await session.deliver(f"{TOPIC_PREFIX}/seek", b"42")
        assert player.set_position_calls == []

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_command_callbacks_dispatch_through_mqtt_session() -> None:
    async def run() -> None:
        player = FakeMprisPlayer()
        bridge, session, _source, _created = _build_bridge(
            players=[player], position_poll_seconds=60.0
        )

        task = asyncio.create_task(bridge.run())
        await settle()

        await session.deliver(f"{TOPIC_PREFIX}/play", b"")
        assert player.play_calls == 1

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_owner_loss_and_return_recreates_player_and_signal_streams() -> None:
    async def run() -> None:
        first_player = FakeMprisPlayer()
        second_player = FakeMprisPlayer(
            metadata={
                "xesam:title": ("s", "Restarted Song"),
                "xesam:artist": ("as", ["New Artist"]),
                "xesam:album": ("s", "New Album"),
                "mpris:length": ("x", 90 * MICROSECONDS_PER_SECOND),
                "mpris:trackid": ("o", "/track/2"),
            }
        )
        bridge, session, source, created = _build_bridge(
            players=[first_player, second_player], position_poll_seconds=60.0
        )

        task = asyncio.create_task(bridge.run())
        await settle()
        assert len(created) == 1
        assert _topic_values(session, "title") == ["Song"]

        # Owner drops: entity goes offline, commands while offline are ignored.
        await source.emit(PLAYERCTLD_SERVICE_NAME, ":1.1", "")
        await settle()
        assert _topic_values(session, "availability")[-1] == "offline"
        await session.deliver(f"{TOPIC_PREFIX}/play", b"")
        assert first_player.play_calls == 0

        # Owner returns: new player proxy created and fresh signal streams attached.
        await source.emit(PLAYERCTLD_SERVICE_NAME, "", ":1.2")
        await settle()
        assert len(created) == 2
        assert _topic_values(session, "availability")[-1] == "online"
        assert _topic_values(session, "title")[-1] == "Restarted Song"

        await second_player.emit_seeked(19 * MICROSECONDS_PER_SECOND)
        await settle()
        assert _topic_values(session, "position")[-1] == "19"

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_no_active_player_snapshot_clears_state_and_stops_polling() -> None:
    async def run() -> None:
        player = FakeMprisPlayer()
        bridge, session, _source, _created = _build_bridge(
            players=[player], position_poll_seconds=0.01
        )

        with capture_logs() as logs:
            task = asyncio.create_task(bridge.run())
            await settle()

            player.playback_error = PlayerctldNoActivePlayerError("No active player")
            await bridge.publish_snapshot("properties_changed")
            player.position_reads = 0
            await asyncio.sleep(0.05)

            assert player.position_reads == 0
            assert _topic_values(session, "availability")[-1] == "offline"

            player.playback_error = None
            await bridge.publish_snapshot("properties_changed")
            assert _topic_values(session, "availability")[-1] == "online"

            _ = task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        no_player_logs = [
            entry for entry in logs if entry.get("event") == "mpris_no_active_player"
        ]
        assert len(no_player_logs) == 1
        assert no_player_logs[0].get("log_level") == "info"

    asyncio.run(run())


def test_no_active_player_during_position_poll_marks_unavailable() -> None:
    async def run() -> None:
        player = FakeMprisPlayer()
        bridge, session, _source, _created = _build_bridge(
            players=[player], position_poll_seconds=0.01
        )

        with capture_logs() as logs:
            task = asyncio.create_task(bridge.run())
            await settle()

            player.position_reads = 0
            player.position_error = PlayerctldNoActivePlayerError("No active player")
            await asyncio.sleep(0.05)

            _ = task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        assert player.position_reads == 1
        assert _topic_values(session, "availability")[-1] == "offline"
        poll_logs = [
            entry
            for entry in logs
            if entry.get("event") == "mpris_no_active_player_during_position_poll"
        ]
        assert len(poll_logs) == 1
        assert poll_logs[0].get("log_level") == "info"

    asyncio.run(run())


def test_observer_publish_failure_stops_bridge() -> None:
    async def run() -> None:
        player = FakeMprisPlayer()
        bridge, session, _source, _created = _build_bridge(
            players=[player], position_poll_seconds=60.0
        )

        task = asyncio.create_task(bridge.run())
        await settle()

        session.fail_transport(aiomqtt.MqttError("publish failed"))
        await player.emit_seeked(8 * MICROSECONDS_PER_SECOND)

        with pytest.raises(ExceptionGroup) as exc_info:
            await asyncio.wait_for(task, timeout=1)

        assert any(
            isinstance(err, aiomqtt.MqttError) and "publish failed" in str(err)
            for err in exc_info.value.exceptions
        )

    asyncio.run(run())


def test_run_mpris_wires_context_into_bridge() -> None:
    async def run() -> None:
        session = InMemorySession()
        source = FakeNameOwnerSource(initially_present=True)
        player = FakeMprisPlayer()
        ctx = SessionContext(
            session=session,
            device=DEVICE,
            hostname="testbox",
            host_prefix="testbox",
            config=TEST_CONFIG,
        )

        task = asyncio.create_task(
            run_mpris(ctx, name_owner_source=source, player_factory=lambda: player)
        )
        await settle()

        assert _topic_values(session, "availability")[-1] == "online"
        assert _topic_values(session, "title") == ["Song"]

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
