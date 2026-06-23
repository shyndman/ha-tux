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
    load_config,
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
    try:
        asyncio.run(async_main(config))
    except KeyboardInterrupt:
        logger.info("application_interrupted")


async def async_main(config: HaTuxConfig) -> None:
    mqtt_settings = build_mqtt_settings(config)
    device = build_host_device_info()
    entity = build_media_player_entity(device)
    pool_names = await discover_pool_names()
    state_store = StateStore.load(state_file_path())
    hostname = socket.gethostname()

    logger = get_logger(LOGGER_NAME)
    while True:
        try:
            async with MqttSession(mqtt_settings) as session:
                bridge = create_bridge(
                    session,
                    entity,
                    service_name=config.mpris.service,
                    position_poll_seconds=config.mpris.position_poll_seconds,
                )
                zfs = build_zfs_pool_publisher(session, device, pool_names)
                poller = AsyncPoller(
                    name="zfs",
                    interval_seconds=config.zfs.poll_seconds,
                    poll=zfs.publish,
                )
                input_active = build_input_active_publisher(session, device)
                input_active_watcher = build_input_active_watcher(
                    input_active, config.input_active.idle_timeout_seconds
                )
                software_update = build_software_update_publisher(
                    session, device, hostname, state_store
                )
                pollers = [poller]
                if software_update is not None:
                    pollers.append(
                        AsyncPoller(
                            name="software_update",
                            interval_seconds=config.software_update.poll_seconds,
                            poll=software_update.publish,
                        )
                    )
                try:
                    await run(bridge, pollers, input_active_watcher, input_active)
                finally:
                    await bridge.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("session_failed_reconnecting")
            await asyncio.sleep(MQTT_RECONNECT_DELAY_SECONDS)


def build_mqtt_settings(config: HaTuxConfig) -> Settings.MQTT:
    return Settings.MQTT(
        url=config.mqtt.url,
        client_name=config.mqtt.client_name,
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
    bridge: AsyncMprisMediaPlayerBridge,
    pollers: Sequence[AsyncPoller],
    input_active_watcher: InputActiveWatcher,
    input_active: InputActivePublisher,
) -> None:
    await bridge.start()
    poll_tasks = [asyncio.create_task(p.run()) for p in pollers]
    input_active_task = asyncio.create_task(
        run_input_active_forever(input_active_watcher, input_active)
    )
    try:
        await bridge.wait_until_stopped_or_failed()
    finally:
        for task in (*poll_tasks, input_active_task):
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
