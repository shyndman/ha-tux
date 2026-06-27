import argparse
import asyncio
import socket
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from ha_mqtt_discoverable import DeviceInfo, MqttSession, Settings
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
from ha_tux.media.entity import build_media_player_entity
from ha_tux.zfs.entity import build_zfs_pool_publisher
from ha_tux.host_device import build_host_device_info, host_slug
from ha_tux.presence.entity import (
    InputActivePublisher,
    build_input_active_publisher,
)
from ha_tux.presence.monitor import (
    MILLISECONDS_PER_SECOND,
    InputActiveWatcher,
    new_idle_monitor_proxy,
)
from ha_tux.lock.entity import LockPublisher, build_lock_publisher
from ha_tux.power.entity import PowerPublisher, build_power_publisher
from ha_tux.power.monitor import PowerWatcher, new_display_device_proxy
from ha_tux.media.bridge import (
    DEFAULT_POSITION_POLL_SECONDS,
    AsyncMprisMediaPlayerBridge,
    create_bridge,
)
from ha_tux.poller import AsyncPoller
from ha_tux.run_state import StateStore, state_file_path
from ha_tux.software_update.publisher import build_software_update_publisher
from ha_tux.zfs.zpool import discover_pool_names

STARTUP_EVENT = "application_started"
MQTT_RECONNECT_DELAY_SECONDS = 2.0
INPUT_ACTIVE_RETRY_SECONDS = 5.0
POWER_RETRY_SECONDS = 5.0

__all__ = [
    "DEFAULT_MQTT_DISCOVERY_PREFIX",
    "DEFAULT_MQTT_STATE_PREFIX",
    "DEFAULT_MQTT_URL",
    "DEFAULT_POSITION_POLL_SECONDS",
    "HaTuxConfig",
    "build_mqtt_settings",
    "main",
]


@dataclass(frozen=True, slots=True)
class Activation:
    session: MqttSession
    device: DeviceInfo
    hostname: str
    host_prefix: str
    config: HaTuxConfig
    tasks: asyncio.TaskGroup
    cleanup: AsyncExitStack


FeatureActivate = Callable[[Activation], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Feature:
    name: str
    roles: frozenset[Role]
    activate: FeatureActivate


async def _activate_media(act: Activation) -> None:
    entity = build_media_player_entity(act.device, host_prefix=act.host_prefix)
    bridge: AsyncMprisMediaPlayerBridge = create_bridge(
        act.session,
        entity,
        service_name=act.config.mpris.service,
        position_poll_seconds=act.config.mpris.position_poll_seconds,
    )
    await bridge.start()
    _ = act.cleanup.push_async_callback(bridge.stop)
    _ = act.tasks.create_task(bridge.wait_until_stopped_or_failed())


async def _activate_input_active(act: Activation) -> None:
    publisher: InputActivePublisher = build_input_active_publisher(
        act.session, act.device, act.host_prefix
    )
    watcher = InputActiveWatcher(
        monitor=new_idle_monitor_proxy(),
        idle_timeout_ms=int(
            act.config.input_active.idle_timeout_seconds * MILLISECONDS_PER_SECOND
        ),
        on_change=publisher.set_active,
    )
    _ = act.tasks.create_task(run_input_active_forever(watcher, publisher))


async def _activate_lock(act: Activation) -> None:
    lock: LockPublisher = build_lock_publisher(act.session, act.device, act.host_prefix)
    await lock.announce()


async def _activate_zfs(act: Activation) -> None:
    pool_names = await discover_pool_names()
    publisher = build_zfs_pool_publisher(
        act.session, act.device, pool_names, act.host_prefix
    )
    _ = act.tasks.create_task(
        AsyncPoller(
            name="zfs",
            interval_seconds=act.config.zfs.poll_seconds,
            poll=publisher.publish,
        ).run()
    )


async def _activate_software_update(act: Activation) -> None:
    state_store = StateStore.load(state_file_path())
    publisher = build_software_update_publisher(
        act.session, act.device, act.hostname, state_store
    )
    if publisher is None:
        return
    _ = act.tasks.create_task(
        AsyncPoller(
            name="software_update",
            interval_seconds=act.config.software_update.poll_seconds,
            poll=publisher.publish,
        ).run()
    )


async def _activate_power(act: Activation) -> None:
    publisher: PowerPublisher = build_power_publisher(
        act.session, act.device, act.host_prefix
    )
    watcher = PowerWatcher(
        device=new_display_device_proxy(),
        on_change=publisher.update,
    )
    _ = act.tasks.create_task(run_power_forever(watcher, publisher))


_SESSION_ROLES: frozenset[Role] = frozenset({"session", "all"})
_HOST_ROLES: frozenset[Role] = frozenset({"host", "all"})

FEATURES: tuple[Feature, ...] = (
    Feature("media", _SESSION_ROLES, _activate_media),
    Feature("input_active", _SESSION_ROLES, _activate_input_active),
    Feature("lock", _SESSION_ROLES, _activate_lock),
    Feature("zfs", _HOST_ROLES, _activate_zfs),
    Feature("software_update", _HOST_ROLES, _activate_software_update),
    Feature("power", _HOST_ROLES, _activate_power),
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
    logger = get_logger(LOGGER_NAME)
    while True:
        try:
            async with MqttSession(mqtt_settings) as session:
                async with AsyncExitStack() as cleanup:
                    async with asyncio.TaskGroup() as tasks:
                        act = Activation(
                            session=session,
                            device=device,
                            hostname=hostname,
                            host_prefix=host_prefix,
                            config=config,
                            tasks=tasks,
                            cleanup=cleanup,
                        )
                        for feature in features:
                            await feature.activate(act)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("session_failed_reconnecting")
            await asyncio.sleep(MQTT_RECONNECT_DELAY_SECONDS)


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


async def run_power_forever(
    watcher: PowerWatcher,
    publisher: PowerPublisher,
) -> None:
    logger = get_logger(LOGGER_NAME)
    while True:
        try:
            await watcher.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("power_watch_failed")
            with suppress(Exception):
                await publisher.set_available(False)
            await asyncio.sleep(POWER_RETRY_SECONDS)
