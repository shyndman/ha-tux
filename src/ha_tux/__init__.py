import argparse
import asyncio
import socket
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Protocol, cast

from ha_mqtt_discoverable import MqttSession, Settings
from libsh import get_logger, setup_logging_from_env

from ha_tux.config import (
    DEFAULT_MQTT_DISCOVERY_PREFIX,
    DEFAULT_MQTT_STATE_PREFIX,
    DEFAULT_MQTT_URL,
    LOGGER_NAME,
    HaTuxConfig,
    Role,
    load_config,
    parse_role,
)
from ha_tux.ha_media import build_media_player_entity
from ha_tux.ha_zfs import build_zfs_pool_publisher
from ha_tux.host_device import build_host_device_info
from ha_tux.ha_input_active import (
    InputActivePublisher,
    build_input_active_publisher,
)
from ha_tux.idle_monitor import (
    MILLISECONDS_PER_SECOND,
    InputActiveWatcher,
    new_idle_monitor_proxy,
)
from ha_tux.media_player_bridge import (
    DEFAULT_POSITION_POLL_SECONDS,
    AsyncMprisMediaPlayerBridge,
    create_bridge,
)
from ha_tux.poller import AsyncPoller
from ha_tux.run_state import StateStore, state_file_path
from ha_tux.software_update.publisher import build_software_update_publisher
from ha_tux.zfs import discover_pool_names

STARTUP_EVENT = "application_started"
MQTT_RECONNECT_DELAY_SECONDS = 2.0
INPUT_ACTIVE_RETRY_SECONDS = 5.0

__all__ = [
    "DEFAULT_MQTT_DISCOVERY_PREFIX",
    "DEFAULT_MQTT_STATE_PREFIX",
    "DEFAULT_MQTT_URL",
    "DEFAULT_POSITION_POLL_SECONDS",
    "HaTuxConfig",
    "build_mqtt_settings",
    "main",
]


class CliArgs(Protocol):
    config: Path | None


def main(argv: Sequence[str] | None = None) -> None:
    setup_logging_from_env()
    logger = get_logger(LOGGER_NAME)
    logger.info(STARTUP_EVENT)
    parser = argparse.ArgumentParser(prog="ha-tux")
    _ = parser.add_argument("--config", type=Path, default=None)
    namespace = cast(CliArgs, cast(object, parser.parse_args(argv)))
    config = load_config(path=namespace.config)
    role = parse_role()
    try:
        asyncio.run(async_main(config, role))
    except KeyboardInterrupt:
        logger.info("application_interrupted")


async def async_main(config: HaTuxConfig, role: Role) -> None:
    mqtt_settings = build_mqtt_settings(config, role)
    device = build_host_device_info()
    wants_session = role in ("session", "all")
    wants_host = role in ("host", "all")

    entity = build_media_player_entity(device) if wants_session else None
    pool_names = await discover_pool_names() if wants_host else ()
    state_store = StateStore.load(state_file_path()) if wants_host else None
    hostname = socket.gethostname() if wants_host else ""

    logger = get_logger(LOGGER_NAME)
    while True:
        try:
            async with MqttSession(mqtt_settings) as session:
                bridge: AsyncMprisMediaPlayerBridge | None = None
                input_active: InputActivePublisher | None = None
                input_active_watcher: InputActiveWatcher | None = None
                pollers: list[AsyncPoller] = []
                if entity is not None:
                    bridge = create_bridge(
                        session,
                        entity,
                        service_name=config.mpris.service,
                        position_poll_seconds=config.mpris.position_poll_seconds,
                    )
                    input_active = build_input_active_publisher(session, device)
                    input_active_watcher = build_input_active_watcher(
                        input_active, config.input_active.idle_timeout_seconds
                    )
                if state_store is not None:
                    zfs = build_zfs_pool_publisher(session, device, pool_names)
                    pollers.append(
                        AsyncPoller(
                            name="zfs",
                            interval_seconds=config.zfs.poll_seconds,
                            poll=zfs.publish,
                        )
                    )
                    software_update = build_software_update_publisher(
                        session, device, hostname, state_store
                    )
                    if software_update is not None:
                        pollers.append(
                            AsyncPoller(
                                name="software_update",
                                interval_seconds=config.software_update.poll_seconds,
                                poll=software_update.publish,
                            )
                        )
                try:
                    await run(
                        bridge=bridge,
                        pollers=pollers,
                        input_active_watcher=input_active_watcher,
                        input_active=input_active,
                    )
                finally:
                    if bridge is not None:
                        await bridge.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("session_failed_reconnecting")
            await asyncio.sleep(MQTT_RECONNECT_DELAY_SECONDS)


def build_mqtt_settings(config: HaTuxConfig, role: Role) -> Settings.MQTT:
    client_name = (
        config.mqtt.client_name
        if role == "all"
        else f"{config.mqtt.client_name}-{role}"
    )
    return Settings.MQTT(
        url=config.mqtt.url,
        client_name=client_name,
        discovery_prefix=config.mqtt.discovery_prefix,
        state_prefix=config.mqtt.state_prefix,
    )


def build_input_active_watcher(
    publisher: InputActivePublisher,
    idle_timeout_seconds: float,
) -> InputActiveWatcher:
    return InputActiveWatcher(
        monitor=new_idle_monitor_proxy(),
        idle_timeout_ms=int(idle_timeout_seconds * MILLISECONDS_PER_SECOND),
        on_change=publisher.set_active,
    )


async def run(
    *,
    bridge: AsyncMprisMediaPlayerBridge | None,
    pollers: Sequence[AsyncPoller],
    input_active_watcher: InputActiveWatcher | None,
    input_active: InputActivePublisher | None,
) -> None:
    if bridge is not None:
        await bridge.start()
    poll_tasks = [asyncio.create_task(p.run()) for p in pollers]
    input_active_task: asyncio.Task[None] | None = None
    if input_active_watcher is not None and input_active is not None:
        input_active_task = asyncio.create_task(
            run_input_active_forever(input_active_watcher, input_active)
        )
    bridge_task: asyncio.Task[None] | None = None
    supervised: list[asyncio.Task[None]] = list(poll_tasks)
    if bridge is not None:
        bridge_task = asyncio.create_task(bridge.wait_until_stopped_or_failed())
        supervised.append(bridge_task)
    try:
        done, _pending = await asyncio.wait(
            supervised, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            await task
    finally:
        for task in (*poll_tasks, input_active_task, bridge_task):
            if task is not None:
                _ = task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def run_input_active_forever(
    watcher: InputActiveWatcher,
    publisher: InputActivePublisher,
) -> None:
    logger = get_logger(LOGGER_NAME)
    while True:
        try:
            await watcher.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("input_active_watch_failed")
            with suppress(Exception):
                await publisher.set_available(False)
            await asyncio.sleep(INPUT_ACTIVE_RETRY_SECONDS)
