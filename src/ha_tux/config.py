from __future__ import annotations

import argparse
import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, Literal, NamedTuple, Protocol, cast

from libsh import get_logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from xdg_base_dirs import xdg_config_home

from ha_tux.media_player_bridge import DEFAULT_POSITION_POLL_SECONDS
from ha_tux.mpris import PLAYERCTLD_SERVICE_NAME

LOGGER_NAME: Final = "ha_tux"
DEFAULT_MQTT_HOST: Final = "homeassistant"
DEFAULT_MQTT_PORT: Final = 1883
DEFAULT_MQTT_DISCOVERY_PREFIX: Final = "homeassistant"
DEFAULT_MQTT_STATE_PREFIX: Final = "hmd"
DEFAULT_MQTT_CLIENT_NAME: Final = "ha-tux"
CONFIG_DIRECTORY_NAME: Final = "ha-tux"
CONFIG_FILE_NAME: Final = "config.toml"
REDACTED_SECRET: Final = "<redacted>"

DEFAULT_CONFIG_FILE_TEXT: Final = """# ha-tux configuration
#
# Precedence: CLI flags override environment variables; environment variables
# override this file; this file overrides built-in defaults.
# Uncomment only the settings you want to change.

#[mqtt]
#host = "homeassistant"
#port = 1883
#username = "ha_tux"
#password = "secret"
#discovery_prefix = "homeassistant"
#state_prefix = "hmd"
#client_name = "ha-tux"

#[mpris]
#service = "org.mpris.MediaPlayer2.playerctld"

#[bridge]
#position_poll_seconds = 1.0
"""

ConfigSection = Literal["mqtt", "mpris", "bridge"]


class ConfigError(ValueError):
    """Raised when file, environment, or CLI configuration is invalid."""


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


class MqttConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    host: str = DEFAULT_MQTT_HOST
    port: int = Field(default=DEFAULT_MQTT_PORT, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    discovery_prefix: str = DEFAULT_MQTT_DISCOVERY_PREFIX
    state_prefix: str = DEFAULT_MQTT_STATE_PREFIX
    client_name: str = DEFAULT_MQTT_CLIENT_NAME


class MprisConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    service: str = PLAYERCTLD_SERVICE_NAME


class BridgeRuntimeConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    position_poll_seconds: float = Field(
        default=DEFAULT_POSITION_POLL_SECONDS,
        gt=0,
    )


class AppConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    mpris: MprisConfig = Field(default_factory=MprisConfig)
    bridge: BridgeRuntimeConfig = Field(default_factory=BridgeRuntimeConfig)


class ParsedArgs(Protocol):
    once: bool
    service: str | None
    position_poll_seconds: float | None


class EnvOverride(NamedTuple):
    name: str
    section: ConfigSection
    field: str


ENV_OVERRIDES: Final = (
    EnvOverride("HA_TUX_MQTT_HOST", "mqtt", "host"),
    EnvOverride("HA_TUX_MQTT_PORT", "mqtt", "port"),
    EnvOverride("HA_TUX_MQTT_USERNAME", "mqtt", "username"),
    EnvOverride("HA_TUX_MQTT_PASSWORD", "mqtt", "password"),
    EnvOverride("HA_TUX_MQTT_DISCOVERY_PREFIX", "mqtt", "discovery_prefix"),
    EnvOverride("HA_TUX_MQTT_STATE_PREFIX", "mqtt", "state_prefix"),
    EnvOverride("HA_TUX_MQTT_CLIENT_NAME", "mqtt", "client_name"),
    EnvOverride("HA_TUX_MPRIS_SERVICE", "mpris", "service"),
    EnvOverride("HA_TUX_POSITION_POLL_SECONDS", "bridge", "position_poll_seconds"),
)


def config_file_path(config_home: Path | None = None) -> Path:
    base_path = config_home if config_home is not None else xdg_config_home()
    return base_path / CONFIG_DIRECTORY_NAME / CONFIG_FILE_NAME


def write_default_config_file(path: Path) -> bool:
    if path.exists():
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as config_file:
            _ = config_file.write(DEFAULT_CONFIG_FILE_TEXT)
    except FileExistsError:
        return False
    return True


def read_config_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}

    try:
        with path.open("rb") as config_file:
            config_data = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"Invalid TOML in {path}: {error}") from error
    except OSError as error:
        raise ConfigError(f"Could not read config file {path}: {error}") from error

    return cast(dict[str, object], config_data)


def load_app_config(
    path: Path | None = None,
    env: Mapping[str, str] = os.environ,
) -> AppConfig:
    config_path = path if path is not None else config_file_path()
    logger = get_logger(LOGGER_NAME)

    default_written = False
    template_write_failed = False
    try:
        default_written = write_default_config_file(config_path)
    except OSError as error:
        template_write_failed = True
        logger.info(
            "config_file_template_write_failed",
            path=os.fspath(config_path),
            error=str(error),
        )

    file_data = read_config_file(config_path)
    if default_written:
        logger.info("config_file_template_written", path=os.fspath(config_path))
    elif config_path.exists():
        logger.info("config_file_loaded", path=os.fspath(config_path))
    elif not template_write_failed:
        logger.info("config_file_absent", path=os.fspath(config_path))

    try:
        file_config = AppConfig.model_validate(file_data)
    except ValidationError as error:
        raise ConfigError(f"Invalid config file {config_path}: {error}") from error

    env_config_data = file_config.model_dump()
    env_names = _apply_env_overrides(env_config_data, env)
    if not env_names:
        return file_config

    try:
        return AppConfig.model_validate(env_config_data)
    except ValidationError as error:
        names = ", ".join(env_names)
        raise ConfigError(
            f"Invalid environment configuration from {names}: {error}"
        ) from error


def parse_config(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] = os.environ,
) -> BridgeConfig:
    parser = argparse.ArgumentParser(prog="ha-tux")
    _ = parser.add_argument("--once", action="store_true")
    _ = parser.add_argument("--service", default=None)
    _ = parser.add_argument("--position-poll-seconds", type=float, default=None)
    namespace = cast(ParsedArgs, cast(object, parser.parse_args(argv)))

    app_config = load_app_config(env=env)
    config = bridge_config_from_app_config(
        app_config,
        once=namespace.once,
        mpris_service=namespace.service,
        position_poll_seconds=namespace.position_poll_seconds,
    )
    get_logger(LOGGER_NAME).info(
        "startup_configuration",
        configuration=f"\n{format_config_for_log(config)}",
    )
    return config


def bridge_config_from_app_config(
    app_config: AppConfig,
    *,
    once: bool,
    mpris_service: str | None = None,
    position_poll_seconds: float | None = None,
) -> BridgeConfig:
    resolved_position_poll_seconds = (
        app_config.bridge.position_poll_seconds
        if position_poll_seconds is None
        else position_poll_seconds
    )
    if resolved_position_poll_seconds <= 0:
        raise ValueError("--position-poll-seconds must be greater than 0")

    return BridgeConfig(
        mqtt_host=app_config.mqtt.host,
        mqtt_port=app_config.mqtt.port,
        mqtt_username=app_config.mqtt.username,
        mqtt_password=app_config.mqtt.password,
        mqtt_discovery_prefix=app_config.mqtt.discovery_prefix,
        mqtt_state_prefix=app_config.mqtt.state_prefix,
        mqtt_client_name=app_config.mqtt.client_name,
        mpris_service=app_config.mpris.service
        if mpris_service is None
        else mpris_service,
        position_poll_seconds=resolved_position_poll_seconds,
        once=once,
    )


def format_config_for_log(config: BridgeConfig) -> str:
    password = REDACTED_SECRET if config.mqtt_password is not None else None
    return "\n".join(
        (
            "[mqtt]",
            f"host = {_toml_value(config.mqtt_host)}",
            f"port = {config.mqtt_port}",
            f"username = {_toml_value(config.mqtt_username)}",
            f"password = {_toml_value(password)}",
            f"discovery_prefix = {_toml_value(config.mqtt_discovery_prefix)}",
            f"state_prefix = {_toml_value(config.mqtt_state_prefix)}",
            f"client_name = {_toml_value(config.mqtt_client_name)}",
            "",
            "[mpris]",
            f"service = {_toml_value(config.mpris_service)}",
            "",
            "[bridge]",
            f"position_poll_seconds = {config.position_poll_seconds}",
        )
    )


def _apply_env_overrides(
    config_data: dict[str, object],
    env: Mapping[str, str],
) -> list[str]:
    applied_names: list[str] = []
    for override in ENV_OVERRIDES:
        value = _env_optional_str(env, override.name)
        if value is None:
            continue
        section_data = config_data.get(override.section)
        if not isinstance(section_data, dict):
            section_data = {}
            config_data[override.section] = section_data
        nested_section = cast(dict[str, object], section_data)
        nested_section[override.field] = value
        applied_names.append(override.name)
    return applied_names


def _env_optional_str(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value == "":
        return None
    return value


def _toml_value(value: str | None) -> str:
    if value is None:
        return "null"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
