import argparse
import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from ha_mqtt_discoverable import Settings
from ha_mqtt_discoverable.media_player import MediaPlayerInfo
from libsh import get_logger, setup_logging_from_env

from ha_tux.async_mqtt import AsyncioMqttClientDriver, MqttConnectionConfig
from ha_tux.ha_media import build_media_player_settings
from ha_tux.media_player_bridge import (
    DEFAULT_POSITION_POLL_SECONDS,
    AsyncMprisMediaPlayerBridge,
    create_bridge,
)
from ha_tux.mpris import PLAYERCTLD_SERVICE_NAME

LOGGER_NAME = "ha_tux"
STARTUP_EVENT = "application_started"
DEFAULT_MQTT_HOST = "homeassistant"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_DISCOVERY_PREFIX = "homeassistant"
DEFAULT_MQTT_STATE_PREFIX = "hmd"
DEFAULT_MQTT_CLIENT_NAME = "ha-tux"


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_discovery_prefix: str
    mqtt_state_prefix: str
    mqtt_client_name: str
    mpris_service: str
    position_poll_seconds: float
    once: bool


class ParsedArgs(Protocol):
    once: bool
    service: str
    position_poll_seconds: float


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


def parse_config(argv: Sequence[str] | None = None) -> BridgeConfig:
    parser = argparse.ArgumentParser(prog="ha-tux")
    _ = parser.add_argument("--once", action="store_true")
    _ = parser.add_argument(
        "--service", default=_env_str("HA_TUX_MPRIS_SERVICE", PLAYERCTLD_SERVICE_NAME)
    )
    _ = parser.add_argument(
        "--position-poll-seconds",
        type=float,
        default=_env_float(
            "HA_TUX_POSITION_POLL_SECONDS", DEFAULT_POSITION_POLL_SECONDS
        ),
    )
    namespace = cast(ParsedArgs, cast(object, parser.parse_args(argv)))
    position_poll_seconds = namespace.position_poll_seconds
    if position_poll_seconds <= 0:
        raise ValueError("--position-poll-seconds must be greater than 0")

    return BridgeConfig(
        mqtt_host=_env_str("HA_TUX_MQTT_HOST", DEFAULT_MQTT_HOST),
        mqtt_port=_env_int("HA_TUX_MQTT_PORT", DEFAULT_MQTT_PORT),
        mqtt_username=_env_optional_str("HA_TUX_MQTT_USERNAME"),
        mqtt_password=_env_optional_str("HA_TUX_MQTT_PASSWORD"),
        mqtt_discovery_prefix=_env_str(
            "HA_TUX_MQTT_DISCOVERY_PREFIX",
            DEFAULT_MQTT_DISCOVERY_PREFIX,
        ),
        mqtt_state_prefix=_env_str(
            "HA_TUX_MQTT_STATE_PREFIX", DEFAULT_MQTT_STATE_PREFIX
        ),
        mqtt_client_name=_env_str("HA_TUX_MQTT_CLIENT_NAME", DEFAULT_MQTT_CLIENT_NAME),
        mpris_service=namespace.service,
        position_poll_seconds=position_poll_seconds,
        once=namespace.once,
    )


def _env_optional_str(name: str) -> str | None:
    value = os.environ.get(name)
    if value == "":
        return None
    return value


def _env_str(name: str, default: str) -> str:
    return _env_optional_str(name) or default


def _env_int(name: str, default: int) -> int:
    value = _env_optional_str(name)
    if value is None:
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = _env_optional_str(name)
    if value is None:
        return default
    return float(value)
