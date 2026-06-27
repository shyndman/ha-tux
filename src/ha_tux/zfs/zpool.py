from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Final, cast

from libsh import get_logger
from pydantic import BaseModel, ConfigDict

LOGGER_NAME: Final = "ha_tux"
ZPOOL_LIST_COMMAND: Final = (
    "zpool",
    "list",
    "-j",
    "-p",
    "-o",
    "name,size,alloc,free,cap,frag,health",
)
ZFS_SNAPSHOT_LIST_COMMAND: Final = (
    "zfs",
    "list",
    "-t",
    "snapshot",
    "-H",
    "-p",
    "-o",
    "name,creation",
)
DEFAULT_ZFS_POLL_SECONDS: Final = 1800.0

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


class _PropEntry(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    value: str | None = None


class _Pool(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    state: str | None = None
    properties: dict[str, _PropEntry] = {}

    def optional_int(self, key: str) -> int | None:
        entry = self.properties.get(key)
        if entry is not None and entry.value is not None and entry.value.isdigit():
            return int(entry.value)
        return None

    def health(self) -> str:
        health = self.properties.get("health")
        if health is not None and health.value:
            return health.value
        if self.state:
            return self.state
        return _UNKNOWN_HEALTH


class _ZpoolPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    pools: dict[str, _Pool] = {}


def parse_zpool_statuses(payload: Mapping[str, object]) -> tuple[ZpoolStatus, ...]:
    parsed = _ZpoolPayload.model_validate(payload)
    statuses = [
        ZpoolStatus(
            name=name,
            size_bytes=pool.optional_int("size"),
            allocated_bytes=pool.optional_int("allocated"),
            free_bytes=pool.optional_int("free"),
            capacity_percent=pool.optional_int("capacity"),
            fragmentation_percent=pool.optional_int("fragmentation"),
            health=pool.health(),
        )
        for name, pool in parsed.pools.items()
    ]
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


@dataclass(frozen=True, slots=True)
class PoolSnapshots:
    count: int
    latest_epoch: int | None


def parse_pool_snapshots(output: str) -> dict[str, PoolSnapshots]:
    accumulated: dict[str, PoolSnapshots] = {}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            continue
        name, creation = fields
        if not creation.isdigit():
            continue
        pool = name.split("@", 1)[0].split("/", 1)[0]
        epoch = int(creation)
        existing = accumulated.get(pool)
        if existing is None:
            accumulated[pool] = PoolSnapshots(count=1, latest_epoch=epoch)
        else:
            latest = existing.latest_epoch
            accumulated[pool] = PoolSnapshots(
                count=existing.count + 1,
                latest_epoch=epoch if latest is None else max(latest, epoch),
            )
    return accumulated


async def read_pool_snapshots() -> dict[str, PoolSnapshots]:
    proc = await asyncio.create_subprocess_exec(
        *ZFS_SNAPSHOT_LIST_COMMAND,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"{' '.join(ZFS_SNAPSHOT_LIST_COMMAND)} failed: {message}")
    return parse_pool_snapshots(stdout.decode(errors="replace"))


async def discover_pool_names() -> tuple[str, ...]:
    try:
        statuses = await read_zpool_statuses()
    except Exception as error:
        get_logger(LOGGER_NAME).info("zfs_pool_discovery_failed", error=str(error))
        return ()
    return tuple(status.name for status in statuses)
