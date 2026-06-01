import asyncio
from collections.abc import Sequence

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from ha_mqtt_discoverable import Settings
from ha_mqtt_discoverable.media_player import MediaPlayerInfo
from libsh import get_logger, setup_logging_from_env

from ha_tux.async_mqtt import AsyncioMqttClientDriver, MqttConnectionConfig
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
from ha_tux.ha_media import build_media_player_settings
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
    "build_mqtt_client",
    "build_settings",
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
    loop = asyncio.get_running_loop()
    client = build_mqtt_client(config)
    settings = build_settings(config, client)
    bridge = create_bridge(
        settings,
        loop=loop,
        service_name=config.mpris_service,
        position_poll_seconds=config.position_poll_seconds,
    )
    driver = AsyncioMqttClientDriver(
        client=client,
        loop=loop,
        config=MqttConnectionConfig(host=config.mqtt_host, port=config.mqtt_port),
    )
    await driver.connect()
    try:
        if config.once:
            await run_bridge_once(bridge)
            return
        await run_bridge_forever(bridge)
    finally:
        await bridge.stop()
        await driver.disconnect()


def build_mqtt_client(config: BridgeConfig) -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=config.mqtt_client_name,
    )
    if config.mqtt_username is not None:
        client.username_pw_set(config.mqtt_username, config.mqtt_password)
    return client


def build_settings(
    config: BridgeConfig,
    client: mqtt.Client,
) -> Settings[MediaPlayerInfo]:
    mqtt_settings = Settings.MQTT(
        host=config.mqtt_host,
        port=config.mqtt_port,
        username=config.mqtt_username,
        password=config.mqtt_password,
        client_name=config.mqtt_client_name,
        discovery_prefix=config.mqtt_discovery_prefix,
        state_prefix=config.mqtt_state_prefix,
        client=client,
    )
    return build_media_player_settings(mqtt=mqtt_settings)


async def run_bridge_once(bridge: AsyncMprisMediaPlayerBridge) -> None:
    await bridge.publish_snapshot("manual")


async def run_bridge_forever(bridge: AsyncMprisMediaPlayerBridge) -> None:
    await bridge.start()
    _ = await asyncio.Event().wait()
