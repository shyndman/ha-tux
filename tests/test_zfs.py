from __future__ import annotations

from ha_tux.zfs import ZpoolStatus, parse_zpool_statuses


def _property(value: str) -> dict[str, object]:
    return {"value": value, "source": {"type": "NONE", "data": "-"}}


def _online_pool(
    name: str,
    *,
    size: str,
    allocated: str,
    free: str,
    capacity: str,
    fragmentation: str,
) -> dict[str, object]:
    return {
        "name": name,
        "type": "POOL",
        "state": "ONLINE",
        "properties": {
            "size": _property(size),
            "allocated": _property(allocated),
            "free": _property(free),
            "capacity": _property(capacity),
            "fragmentation": _property(fragmentation),
            "health": _property("ONLINE"),
        },
    }


PAYLOAD: dict[str, object] = {
    "output_version": {"command": "zpool list", "vers_major": 0, "vers_minor": 1},
    "pools": {
        "rpool": _online_pool(
            "rpool",
            size="500107862016",
            allocated="123456789",
            free="499984405227",
            capacity="3",
            fragmentation="11",
        ),
        "bpool": _online_pool(
            "bpool",
            size="2013265920",
            allocated="1040515072",
            free="972750848",
            capacity="51",
            fragmentation="28",
        ),
    },
}


def test_parse_orders_pools_by_name_and_coerces_integers() -> None:
    statuses = parse_zpool_statuses(PAYLOAD)

    assert statuses == (
        ZpoolStatus(
            name="bpool",
            size_bytes=2013265920,
            allocated_bytes=1040515072,
            free_bytes=972750848,
            capacity_percent=51,
            fragmentation_percent=28,
            health="ONLINE",
        ),
        ZpoolStatus(
            name="rpool",
            size_bytes=500107862016,
            allocated_bytes=123456789,
            free_bytes=499984405227,
            capacity_percent=3,
            fragmentation_percent=11,
            health="ONLINE",
        ),
    )


def test_parse_treats_dash_values_as_none_for_degraded_pool() -> None:
    payload: dict[str, object] = {
        "pools": {
            "rpool": {
                "name": "rpool",
                "state": "FAULTED",
                "properties": {
                    "size": _property("-"),
                    "allocated": _property("-"),
                    "free": _property("-"),
                    "capacity": _property("-"),
                    "fragmentation": _property("-"),
                    "health": _property("FAULTED"),
                },
            }
        }
    }

    statuses = parse_zpool_statuses(payload)

    assert statuses == (
        ZpoolStatus(
            name="rpool",
            size_bytes=None,
            allocated_bytes=None,
            free_bytes=None,
            capacity_percent=None,
            fragmentation_percent=None,
            health="FAULTED",
        ),
    )


def test_parse_falls_back_to_pool_state_when_health_missing() -> None:
    payload: dict[str, object] = {
        "pools": {
            "rpool": {
                "name": "rpool",
                "state": "DEGRADED",
                "properties": {"size": _property("100")},
            }
        }
    }

    (status,) = parse_zpool_statuses(payload)

    assert status.size_bytes == 100
    assert status.health == "DEGRADED"


def test_parse_returns_empty_when_no_pools() -> None:
    assert parse_zpool_statuses({"pools": {}}) == ()
    assert parse_zpool_statuses({}) == ()
