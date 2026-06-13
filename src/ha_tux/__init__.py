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
from ha_tux.media_player_bridge import (
    DEFAULT_POSITION_POLL_SECONDS,
    AsyncMprisMediaPlayerBridge,
    create_bridge,
)
from ha_tux.poller import AsyncPoller
from ha_tux.zfs import discover_pool_names

STARTUP_EVENT = "application_started"
MQTT_RECONNECT_DELAY_SECONDS = 2.0

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
            try:
                await run_once(bridge, zfs)
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
                try:
                    await run_bridge_and_poller_forever(bridge, poller)
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


async def run_once(bridge: AsyncMprisMediaPlayerBridge, zfs: ZfsPoolPublisher) -> None:
    await bridge.publish_snapshot("manual")
    await zfs.publish()


async def run_bridge_and_poller_forever(
    bridge: AsyncMprisMediaPlayerBridge,
    poller: AsyncPoller,
) -> None:
    await bridge.start()
    poll_task = asyncio.create_task(poller.run())
    try:
        await bridge.wait_until_stopped_or_failed()
    finally:
        _ = poll_task.cancel()
        with suppress(asyncio.CancelledError):
            await poll_task
