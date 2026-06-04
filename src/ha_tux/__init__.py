import asyncio
from collections.abc import Sequence

from ha_mqtt_discoverable import MqttSession, Settings
from libsh import get_logger, setup_logging_from_env

from ha_tux.config import (
    DEFAULT_MQTT_CLIENT_NAME,
    DEFAULT_MQTT_DISCOVERY_PREFIX,
    DEFAULT_MQTT_HOST,
    DEFAULT_MQTT_PORT,
    DEFAULT_MQTT_STATE_PREFIX,
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

__all__ = [
    "DEFAULT_MQTT_CLIENT_NAME",
    "DEFAULT_MQTT_DISCOVERY_PREFIX",
    "DEFAULT_MQTT_HOST",
    "DEFAULT_MQTT_PORT",
    "DEFAULT_MQTT_STATE_PREFIX",
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
    async with MqttSession(mqtt_settings) as session:
        bridge = create_bridge(
            session,
            entity,
            service_name=config.mpris_service,
            position_poll_seconds=config.position_poll_seconds,
        )
        try:
            if config.once:
                await run_bridge_once(bridge)
                return
            await run_bridge_forever(bridge)
        finally:
            await bridge.stop()


def build_mqtt_settings(config: BridgeConfig) -> Settings.MQTT:
    return Settings.MQTT(
        host=config.mqtt_host,
        port=config.mqtt_port,
        username=config.mqtt_username,
        password=config.mqtt_password,
        client_name=config.mqtt_client_name,
        discovery_prefix=config.mqtt_discovery_prefix,
        state_prefix=config.mqtt_state_prefix,
    )


async def run_bridge_once(bridge: AsyncMprisMediaPlayerBridge) -> None:
    await bridge.publish_snapshot("manual")


async def run_bridge_forever(bridge: AsyncMprisMediaPlayerBridge) -> None:
    await bridge.start()
    _ = await asyncio.Event().wait()
