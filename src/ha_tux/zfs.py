from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast

from libsh import get_logger

LOGGER_NAME: Final = "ha_tux"
ZPOOL_LIST_COMMAND: Final = (
    "zpool",
    "list",
    "-j",
    "-p",
    "-o",
    "name,size,alloc,free,cap,frag,health",
)
DEFAULT_ZFS_POLL_SECONDS: Final = 60.0

_UNKNOWN_HEALTH: Final = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ZpoolStatus:
    name: str
    size_bytes: int | None
    allocated_bytes: int | None
    free_bytes: int | None
    capacity_percent: int | None
    fragmentation_percent: int | None
    health: str


def parse_zpool_statuses(payload: Mapping[str, object]) -> tuple[ZpoolStatus, ...]:
    pools = payload.get("pools")
    if not isinstance(pools, Mapping):
        return ()

    statuses: list[ZpoolStatus] = []
    for raw_name, raw_pool in cast(Mapping[str, object], pools).items():
        if not isinstance(raw_pool, Mapping):
            continue
        pool = cast(Mapping[str, object], raw_pool)
        properties = pool.get("properties")
        properties_map: Mapping[str, object] = (
            cast(Mapping[str, object], properties)
            if isinstance(properties, Mapping)
            else {}
        )
        statuses.append(
            ZpoolStatus(
                name=raw_name,
                size_bytes=_optional_int(properties_map, "size"),
                allocated_bytes=_optional_int(properties_map, "allocated"),
                free_bytes=_optional_int(properties_map, "free"),
                capacity_percent=_optional_int(properties_map, "capacity"),
                fragmentation_percent=_optional_int(properties_map, "fragmentation"),
                health=_health(properties_map, pool),
            )
        )
    return tuple(sorted(statuses, key=lambda status: status.name))


async def read_zpool_statuses() -> tuple[ZpoolStatus, ...]:
    proc = await asyncio.create_subprocess_exec(
        *ZPOOL_LIST_COMMAND,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"{' '.join(ZPOOL_LIST_COMMAND)} failed: {message}")

    payload = cast(object, json.loads(stdout))
    if not isinstance(payload, Mapping):
        raise ValueError("zpool list JSON output must be an object")
    return parse_zpool_statuses(cast(Mapping[str, object], payload))


async def discover_pool_names() -> tuple[str, ...]:
    try:
        statuses = await read_zpool_statuses()
    except Exception as error:
        get_logger(LOGGER_NAME).info("zfs_pool_discovery_failed", error=str(error))
        return ()
    return tuple(status.name for status in statuses)


def _optional_int(properties: Mapping[str, object], key: str) -> int | None:
    entry = properties.get(key)
    if not isinstance(entry, Mapping):
        return None
    value = cast(Mapping[str, object], entry).get("value")
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _health(properties: Mapping[str, object], pool: Mapping[str, object]) -> str:
    entry = properties.get("health")
    if isinstance(entry, Mapping):
        value = cast(Mapping[str, object], entry).get("value")
        if isinstance(value, str) and value:
            return value
    state = pool.get("state")
    if isinstance(state, str) and state:
        return state
    return _UNKNOWN_HEALTH
