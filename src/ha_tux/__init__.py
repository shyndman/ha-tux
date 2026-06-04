import asyncio
from collections.abc import Sequence

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
from ha_tux.media_player_bridge import (
    DEFAULT_POSITION_POLL_SECONDS,
    AsyncMprisMediaPlayerBridge,
    create_bridge,
)

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
    entity = build_media_player_entity()
    if config.once:
        async with MqttSession(mqtt_settings) as session:
            bridge = create_bridge(
                session,
                entity,
                service_name=config.mpris_service,
                position_poll_seconds=config.position_poll_seconds,
            )
            try:
                await run_bridge_once(bridge)
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
                try:
                    await run_bridge_forever(bridge)
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


async def run_bridge_once(bridge: AsyncMprisMediaPlayerBridge) -> None:
    await bridge.publish_snapshot("manual")


async def run_bridge_forever(bridge: AsyncMprisMediaPlayerBridge) -> None:
    await bridge.start()
    await bridge.wait_until_stopped_or_failed()
