from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Final, Literal, NamedTuple, cast
from urllib.parse import urlsplit, urlunsplit

from libsh import get_logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from xdg_base_dirs import xdg_config_home

from ha_tux.host_device import default_mqtt_client_name
from ha_tux.idle_monitor import DEFAULT_INPUT_ACTIVE_IDLE_TIMEOUT_SECONDS
from ha_tux.media_player_bridge import DEFAULT_POSITION_POLL_SECONDS
from ha_tux.mpris import PLAYERCTLD_SERVICE_NAME
from ha_tux.zfs import DEFAULT_ZFS_POLL_SECONDS

LOGGER_NAME: Final = "ha_tux"
DEFAULT_MQTT_URL: Final = "mqtt://homeassistant:1883"
DEFAULT_MQTT_DISCOVERY_PREFIX: Final = "homeassistant"
DEFAULT_MQTT_STATE_PREFIX: Final = "hmd"
CONFIG_DIRECTORY_NAME: Final = "ha-tux"
CONFIG_FILE_NAME: Final = "config.toml"
REDACTED_SECRET: Final = "<redacted>"

DEFAULT_CONFIG_FILE_TEXT: Final = """# ha-tux configuration
#
# Precedence: CLI flags override environment variables; environment variables
# override this file; this file overrides built-in defaults.
# Uncomment only the settings you want to change.

#[mqtt]
#url = "mqtt://homeassistant:1883"
# Credentials are part of the URL when needed:
#url = "mqtt://ha_tux:secret@homeassistant:1883"
#discovery_prefix = "homeassistant"
#state_prefix = "hmd"
# client_name defaults to this computer's hostname.
#client_name = "desktop"

#[mpris]
#service = "org.mpris.MediaPlayer2.playerctld"
#position_poll_seconds = 1.0

#[zfs]
#poll_seconds = 60.0

#[input_active]
#idle_timeout_seconds = 60.0
"""

ConfigSection = Literal["mqtt", "mpris", "zfs", "input_active"]


class ConfigError(ValueError):
    """Raised when file, environment, or CLI configuration is invalid."""


class MqttConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    url: str = DEFAULT_MQTT_URL
    discovery_prefix: str = DEFAULT_MQTT_DISCOVERY_PREFIX
    state_prefix: str = DEFAULT_MQTT_STATE_PREFIX
    client_name: str = Field(default_factory=lambda: default_mqtt_client_name())

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"mqtt", "mqtts", "ws", "wss"}:
            raise ValueError("MQTT URL scheme must be mqtt, mqtts, ws, or wss")
        if parsed.hostname is None:
            raise ValueError("MQTT URL must include a host")
        try:
            _ = parsed.port
        except ValueError as error:
            raise ValueError("MQTT URL port must be valid") from error
        return value


class MprisConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    service: str = PLAYERCTLD_SERVICE_NAME
    position_poll_seconds: float = Field(
        default=DEFAULT_POSITION_POLL_SECONDS,
        gt=0,
    )


class ZfsConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    poll_seconds: float = Field(
        default=DEFAULT_ZFS_POLL_SECONDS,
        gt=0,
    )


class InputActiveConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    idle_timeout_seconds: float = Field(
        default=DEFAULT_INPUT_ACTIVE_IDLE_TIMEOUT_SECONDS,
        gt=0,
    )


class HaTuxConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    mpris: MprisConfig = Field(default_factory=MprisConfig)
    zfs: ZfsConfig = Field(default_factory=ZfsConfig)
    input_active: InputActiveConfig = Field(default_factory=InputActiveConfig)


class EnvOverride(NamedTuple):
    name: str
    section: ConfigSection
    field: str


ENV_OVERRIDES: Final = (
    EnvOverride("HA_TUX_MQTT_URL", "mqtt", "url"),
    EnvOverride("HA_TUX_MQTT_DISCOVERY_PREFIX", "mqtt", "discovery_prefix"),
    EnvOverride("HA_TUX_MQTT_STATE_PREFIX", "mqtt", "state_prefix"),
    EnvOverride("HA_TUX_MQTT_CLIENT_NAME", "mqtt", "client_name"),
    EnvOverride("HA_TUX_MPRIS_SERVICE", "mpris", "service"),
    EnvOverride("HA_TUX_POSITION_POLL_SECONDS", "mpris", "position_poll_seconds"),
    EnvOverride("HA_TUX_ZFS_POLL_SECONDS", "zfs", "poll_seconds"),
    EnvOverride(
        "HA_TUX_INPUT_ACTIVE_IDLE_TIMEOUT_SECONDS",
        "input_active",
        "idle_timeout_seconds",
    ),
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


def load_config(
    path: Path | None = None,
    env: Mapping[str, str] = os.environ,
) -> HaTuxConfig:
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
        file_config = HaTuxConfig.model_validate(file_data)
    except ValidationError as error:
        raise ConfigError(f"Invalid config file {config_path}: {error}") from error

    env_config_data = file_config.model_dump()
    env_names = _apply_env_overrides(env_config_data, env)
    if not env_names:
        config = file_config
    else:
        try:
            config = HaTuxConfig.model_validate(env_config_data)
        except ValidationError as error:
            names = ", ".join(env_names)
            raise ConfigError(
                f"Invalid environment configuration from {names}: {error}"
            ) from error

    logger.info(
        "startup_configuration",
        configuration=f"\n{format_config_for_log(config)}",
    )
    return config


def format_config_for_log(config: HaTuxConfig) -> str:
    return "\n".join(
        (
            "[mqtt]",
            f"url = {_toml_value(_redact_url_userinfo(config.mqtt.url))}",
            f"discovery_prefix = {_toml_value(config.mqtt.discovery_prefix)}",
            f"state_prefix = {_toml_value(config.mqtt.state_prefix)}",
            f"client_name = {_toml_value(config.mqtt.client_name)}",
            "",
            "[mpris]",
            f"service = {_toml_value(config.mpris.service)}",
            f"position_poll_seconds = {config.mpris.position_poll_seconds}",
            "",
            "[zfs]",
            f"poll_seconds = {config.zfs.poll_seconds}",
            "",
            "[input_active]",
            f"idle_timeout_seconds = {config.input_active.idle_timeout_seconds}",
        )
    )


def _redact_url_userinfo(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url

    if parsed.username is None and parsed.password is None:
        return url
    if "@" not in parsed.netloc:
        return url

    host_port = parsed.netloc.rsplit("@", maxsplit=1)[1]
    userinfo = REDACTED_SECRET
    if parsed.password is not None:
        userinfo = f"{REDACTED_SECRET}:{REDACTED_SECRET}"
    return urlunsplit(parsed._replace(netloc=f"{userinfo}@{host_port}"))


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
