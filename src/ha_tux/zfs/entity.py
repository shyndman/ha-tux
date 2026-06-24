from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable._session import SessionLike
from ha_mqtt_discoverable.sensors import Sensor, SensorInfo

from ha_tux.zfs.zpool import ZpoolStatus, read_zpool_statuses

LOGGER = logging.getLogger(__name__)

ENTITY_CATEGORY_DIAGNOSTIC: Final = "diagnostic"
BYTES_UNIT: Final = "B"
GIBIBYTES_UNIT: Final = "GiB"
TEBIBYTES_UNIT: Final = "TiB"
PERCENT_UNIT: Final = "%"
DATA_SIZE_DEVICE_CLASS: Final = "data_size"
MEASUREMENT_STATE_CLASS: Final = "measurement"

HEALTH_KEY: Final = "health"
HEALTH_LABEL: Final = "Health"

_SLUG_PATTERN: Final = re.compile(r"[^a-z0-9_]+")


class ZfsSensorInfo(SensorInfo):
    """Sensor discovery info that also carries a Home Assistant display-unit hint.

    ``ha_mqtt_discoverable``'s ``SensorInfo`` does not model
    ``suggested_unit_of_measurement``, and pydantic drops unknown kwargs, so the
    field must be declared here to reach the discovery payload."""

    suggested_unit_of_measurement: str | None = None


@dataclass(frozen=True, slots=True)
class ZfsMetric:
    key: str
    label: str
    unit: str
    device_class: str | None
    suggested_unit: str | None
    extract: Callable[[ZpoolStatus], int | None]


ZFS_NUMERIC_METRICS: Final = (
    ZfsMetric(
        "size",
        "Size",
        BYTES_UNIT,
        DATA_SIZE_DEVICE_CLASS,
        TEBIBYTES_UNIT,
        lambda s: s.size_bytes,
    ),
    ZfsMetric(
        "allocated",
        "Allocated",
        BYTES_UNIT,
        DATA_SIZE_DEVICE_CLASS,
        GIBIBYTES_UNIT,
        lambda s: s.allocated_bytes,
    ),
    ZfsMetric(
        "free",
        "Free",
        BYTES_UNIT,
        DATA_SIZE_DEVICE_CLASS,
        GIBIBYTES_UNIT,
        lambda s: s.free_bytes,
    ),
    ZfsMetric("used", "Used", PERCENT_UNIT, None, None, lambda s: s.capacity_percent),
    ZfsMetric(
        "fragmentation",
        "Fragmentation",
        PERCENT_UNIT,
        None,
        None,
        lambda s: s.fragmentation_percent,
    ),
)


def pool_slug(pool_name: str) -> str:
    return _SLUG_PATTERN.sub("_", pool_name.lower())


@dataclass(frozen=True, slots=True)
class _PoolSensors:
    health: Sensor
    metrics: tuple[tuple[ZfsMetric, Sensor], ...]


class ZfsPoolPublisher:
    def __init__(self, *, pools: Mapping[str, _PoolSensors]) -> None:
        self._pools: Mapping[str, _PoolSensors] = pools

    async def publish(self) -> None:
        try:
            statuses = await read_zpool_statuses()
        except Exception:
            LOGGER.exception("zfs_status_read_failed")
            await self._set_all_unavailable()
            return

        by_name = {status.name: status for status in statuses}
        for pool_name, sensors in self._pools.items():
            status = by_name.get(pool_name)
            if status is None:
                await self._set_pool_unavailable(sensors)
                continue

            await sensors.health.set_available(True)
            await sensors.health.set_state(status.health)
            for metric, sensor in sensors.metrics:
                value = metric.extract(status)
                if value is None:
                    await sensor.set_available(False)
                else:
                    await sensor.set_available(True)
                    await sensor.set_state(value)

    async def _set_all_unavailable(self) -> None:
        for sensors in self._pools.values():
            await self._set_pool_unavailable(sensors)

    async def _set_pool_unavailable(self, sensors: _PoolSensors) -> None:
        await sensors.health.set_available(False)
        for _, sensor in sensors.metrics:
            await sensor.set_available(False)


def build_zfs_pool_publisher(
    session: SessionLike,
    device: DeviceInfo,
    pool_names: Sequence[str],
) -> ZfsPoolPublisher:
    pools: dict[str, _PoolSensors] = {}
    for pool_name in pool_names:
        slug = pool_slug(pool_name)
        health_info = SensorInfo(
            device=device,
            unique_id=f"ha_tux_zfs_{slug}_{HEALTH_KEY}",
            object_id=f"zfs_{slug}_{HEALTH_KEY}",
            name=f"ZFS {pool_name} {HEALTH_LABEL}",
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        )
        metrics: list[tuple[ZfsMetric, Sensor]] = []
        for metric in ZFS_NUMERIC_METRICS:
            info = ZfsSensorInfo(
                device=device,
                unique_id=f"ha_tux_zfs_{slug}_{metric.key}",
                object_id=f"zfs_{slug}_{metric.key}",
                name=f"ZFS {pool_name} {metric.label}",
                unit_of_measurement=metric.unit,
                device_class=metric.device_class,
                state_class=MEASUREMENT_STATE_CLASS,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
                suggested_unit_of_measurement=metric.suggested_unit,
            )
            metrics.append((metric, Sensor(session, info)))
        pools[pool_name] = _PoolSensors(
            health=Sensor(session, health_info),
            metrics=tuple(metrics),
        )
    return ZfsPoolPublisher(pools=pools)
