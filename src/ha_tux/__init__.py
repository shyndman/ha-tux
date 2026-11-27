import argparse
import asyncio
import socket
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

from ha_mqtt_discoverable import Settings
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
from ha_tux.host_device import build_host_device_info, host_slug
from ha_tux.input_active import run_input_active
from ha_tux.mpris import DEFAULT_POSITION_POLL_SECONDS, run_mpris
from ha_tux.power import run_power
from ha_tux.run_state import StateStore, state_file_path
from ha_tux.runtime import (
    Feature,
    SessionContext,
    SessionRuntime,
    mqtt_session_factory,
    run_poller,
)
from ha_tux.screen_lock import run_screen_lock
from ha_tux.smart.entity import build_smart_publisher
from ha_tux.smart.report import SMART_REPORT_PATH
from ha_tux.software_update.publisher import build_software_update_publisher
from ha_tux.zfs.entity import build_zfs_pool_publisher
from ha_tux.zfs.zpool import discover_pool_names

STARTUP_EVENT = "application_started"

__all__ = [
    "DEFAULT_MQTT_DISCOVERY_PREFIX",
    "DEFAULT_MQTT_STATE_PREFIX",
    "DEFAULT_MQTT_URL",
    "DEFAULT_POSITION_POLL_SECONDS",
    "Feature",
    "HaTuxConfig",
    "SessionContext",
    "SessionRuntime",
    "build_mqtt_settings",
    "features_for_role",
    "main",
]


async def _run_zfs(ctx: SessionContext) -> None:
    pool_names = await discover_pool_names()
    publisher = build_zfs_pool_publisher(
        ctx.session, ctx.device, pool_names, ctx.host_prefix
    )
    await run_poller(
        name="zfs",
        interval_seconds=ctx.config.zfs.poll_seconds,
        poll=publisher.publish,
    )


async def _run_software_update(ctx: SessionContext) -> None:
    state_store = StateStore.load(state_file_path())
    publisher = build_software_update_publisher(
        ctx.session, ctx.device, ctx.hostname, state_store
    )
    if publisher is None:
        return

    await run_poller(
        name="software_update",
        interval_seconds=ctx.config.software_update.poll_seconds,
        poll=publisher.publish,
    )


async def _run_smart(ctx: SessionContext) -> None:
    host_identifier = ctx.device.identifiers
    if not isinstance(host_identifier, str):
        return

    publisher = build_smart_publisher(
        ctx.session, ctx.device, host_identifier, ctx.host_prefix, SMART_REPORT_PATH
    )
    await run_poller(
        name="smart",
        interval_seconds=ctx.config.smart.poll_seconds,
        poll=publisher.publish,
    )


_SESSION_ROLES: frozenset[Role] = frozenset({"session", "all"})
_HOST_ROLES: frozenset[Role] = frozenset({"host", "all"})
_UPDATE_ROLES: frozenset[Role] = frozenset({"updates", "all"})

FEATURES: tuple[Feature, ...] = (
    Feature("mpris", _SESSION_ROLES, run_mpris),
    Feature("input_active", _SESSION_ROLES, run_input_active),
    Feature("screen_lock", _SESSION_ROLES, run_screen_lock),
    Feature("zfs", _HOST_ROLES, _run_zfs),
    Feature("software_update", _UPDATE_ROLES, _run_software_update),
    Feature("power", _HOST_ROLES, run_power),
    Feature("smart", _HOST_ROLES, _run_smart),
)


def features_for_role(role: Role) -> tuple[Feature, ...]:
    return tuple(feature for feature in FEATURES if role in feature.roles)


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
    hostname = socket.gethostname()
    host_prefix = host_slug(hostname)
    features = features_for_role(role)

    runtime = SessionRuntime(
        session_factory=mqtt_session_factory(mqtt_settings),
        features=features,
        device=device,
        hostname=hostname,
        host_prefix=host_prefix,
        config=config,
    )
    await runtime.run()


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
