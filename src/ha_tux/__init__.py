import asyncio
from collections.abc import Sequence
from contextlib import suppress

from ha_mqtt_discoverable import MqttSession, Settings
from libsh import get_logger, setup_logging_from_env

from ha_tux.config import (
    DEFAULT_MQTT_DISCOVERY_PREFIX,
    DEFAULT_MQTT_STATE_PREFIX,
    DEFAULT_MQTT_URL,
    LOGGER_NAME,
    BridgeConfig,
    parse_config,
)
from ha_tux.ha_media import build_media_player_entity
from ha_tux.ha_zfs import ZfsPoolPublisher, build_zfs_pool_publisher
from ha_tux.host_device import build_host_device_info
from ha_tux.ha_input_active import (
    InputActivePublisher,
    build_input_active_publisher,
)
from ha_tux.idle_monitor import (
    INPUT_ACTIVE_IDLE_TIMEOUT_MS,
    InputActiveWatcher,
    new_idle_monitor_proxy,
)
from ha_tux.media_player_bridge import (
    DEFAULT_POSITION_POLL_SECONDS,
    AsyncMprisMediaPlayerBridge,
    create_bridge,
)
from ha_tux.poller import AsyncPoller
from ha_tux.zfs import discover_pool_names

STARTUP_EVENT = "application_started"
MQTT_RECONNECT_DELAY_SECONDS = 2.0
INPUT_ACTIVE_RETRY_SECONDS = 5.0

__all__ = [
    "DEFAULT_MQTT_DISCOVERY_PREFIX",
    "DEFAULT_MQTT_STATE_PREFIX",
    "DEFAULT_MQTT_URL",
    "DEFAULT_POSITION_POLL_SECONDS",
    "BridgeConfig",
    "build_mqtt_settings",
    "main",
    "parse_config",
]


def main(argv: Sequence[str] | None = None) -> None:
    setup_logging_from_env()
    logger = get_logger(LOGGER_NAME)
    logger.info(STARTUP_EVENT)
    config = parse_config(argv)
    try:
        asyncio.run(async_main(config))
    except KeyboardInterrupt:
        logger.info("application_interrupted")


async def async_main(config: BridgeConfig) -> None:
    mqtt_settings = build_mqtt_settings(config)
    device = build_host_device_info()
    entity = build_media_player_entity(device)
    pool_names = await discover_pool_names()
    if config.once:
        async with MqttSession(mqtt_settings) as session:
            bridge = create_bridge(
                session,
                entity,
                service_name=config.mpris_service,
                position_poll_seconds=config.position_poll_seconds,
            )
            zfs = build_zfs_pool_publisher(session, device, pool_names)
            input_active = build_input_active_publisher(session, device)
            input_active_watcher = build_input_active_watcher(input_active)
            try:
                await run_once(bridge, zfs, input_active_watcher)
            finally:
                await bridge.stop()
        return

    logger = get_logger(LOGGER_NAME)
    while True:
        try:
            async with MqttSession(mqtt_settings) as session:
                bridge = create_bridge(
                    session,
                    entity,
                    service_name=config.mpris_service,
                    position_poll_seconds=config.position_poll_seconds,
                )
                zfs = build_zfs_pool_publisher(session, device, pool_names)
                poller = AsyncPoller(
                    name="zfs",
                    interval_seconds=config.zfs_poll_seconds,
                    poll=zfs.publish,
                )
                input_active = build_input_active_publisher(session, device)
                input_active_watcher = build_input_active_watcher(input_active)
                try:
                    await run_bridge_and_poller_forever(
                        bridge, poller, input_active_watcher, input_active
                    )
                finally:
                    await bridge.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("bridge_session_failed_reconnecting")
            await asyncio.sleep(MQTT_RECONNECT_DELAY_SECONDS)


def build_mqtt_settings(config: BridgeConfig) -> Settings.MQTT:
    return Settings.MQTT(
        url=config.mqtt_url,
        client_name=config.mqtt_client_name,
        discovery_prefix=config.mqtt_discovery_prefix,
        state_prefix=config.mqtt_state_prefix,
    )


def build_input_active_watcher(
    publisher: InputActivePublisher,
) -> InputActiveWatcher:
    return InputActiveWatcher(
        monitor=new_idle_monitor_proxy(),
        idle_timeout_ms=INPUT_ACTIVE_IDLE_TIMEOUT_MS,
        on_change=publisher.set_active,
    )


async def run_once(
    bridge: AsyncMprisMediaPlayerBridge,
    zfs: ZfsPoolPublisher,
    input_active_watcher: InputActiveWatcher,
) -> None:
    await bridge.publish_snapshot("manual")
    await zfs.publish()
    _ = await input_active_watcher.snapshot()


async def run_bridge_and_poller_forever(
    bridge: AsyncMprisMediaPlayerBridge,
    poller: AsyncPoller,
    input_active_watcher: InputActiveWatcher,
    input_active: InputActivePublisher,
) -> None:
    await bridge.start()
    poll_task = asyncio.create_task(poller.run())
    input_active_task = asyncio.create_task(
        run_input_active_forever(input_active_watcher, input_active)
    )
    try:
        await bridge.wait_until_stopped_or_failed()
    finally:
        for task in (poll_task, input_active_task):
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
