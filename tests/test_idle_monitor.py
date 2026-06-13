from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

from ha_tux.idle_monitor import InputActiveWatcher, MutterIdleMonitorAsync

TIMEOUT_MS = 60_000


class _FakeWatchFired:
    def __init__(self, ids: list[int]) -> None:
        self._ids: list[int] = ids

    def catch(self) -> AsyncIterator[int]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[int]:
        for watch_id in list(self._ids):
            yield watch_id


class FakeIdleMonitor:
    """Scripts ``GetIdletime`` and replays a fixed sequence of ``WatchFired`` ids.

    Watch ids are handed out incrementally, so a test knows the idle watch is id
    1 and the first active watch is id 2 and can queue those into ``fired``."""

    def __init__(self, *, idletime_ms: int, fired: list[int]) -> None:
        self._idletime_ms: int = idletime_ms
        self.watch_fired: _FakeWatchFired = _FakeWatchFired(fired)
        self.idle_intervals: list[int] = []
        self.active_watch_count: int = 0
        self._next_id: int = 1

    async def get_idletime(self) -> int:
        return self._idletime_ms

    async def add_idle_watch(self, interval_ms: int) -> int:
        self.idle_intervals.append(interval_ms)
        return self._take_id()

    async def add_user_active_watch(self) -> int:
        self.active_watch_count += 1
        return self._take_id()

    def _take_id(self) -> int:
        watch_id = self._next_id
        self._next_id += 1
        return watch_id


class Recorder:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def __call__(self, active: bool) -> None:
        self.calls.append(active)


def _watcher(monitor: FakeIdleMonitor, recorder: Recorder) -> InputActiveWatcher:
    return InputActiveWatcher(
        monitor=cast(MutterIdleMonitorAsync, cast(object, monitor)),
        idle_timeout_ms=TIMEOUT_MS,
        on_change=recorder,
    )


def test_snapshot_maps_idle_below_threshold_to_active() -> None:
    monitor = FakeIdleMonitor(idletime_ms=59_999, fired=[])
    recorder = Recorder()

    active = asyncio.run(_watcher(monitor, recorder).snapshot())

    assert active is True
    assert recorder.calls == [True]


def test_snapshot_maps_idle_at_threshold_to_inactive() -> None:
    monitor = FakeIdleMonitor(idletime_ms=TIMEOUT_MS, fired=[])
    recorder = Recorder()

    active = asyncio.run(_watcher(monitor, recorder).snapshot())

    assert active is False
    assert recorder.calls == [False]


def test_seed_active_then_idle_then_return_emits_both_edges() -> None:
    # Active at seed, idle watch (id 1) fires, then the active watch (id 2) fires.
    monitor = FakeIdleMonitor(idletime_ms=1_000, fired=[1, 2])
    recorder = Recorder()

    asyncio.run(_watcher(monitor, recorder).run())

    assert recorder.calls == [True, False, True]
    assert monitor.idle_intervals == [TIMEOUT_MS]
    assert monitor.active_watch_count == 1


def test_seed_inactive_arms_active_watch_immediately() -> None:
    # Idle past threshold at seed, so the active watch (id 2) is armed up front.
    monitor = FakeIdleMonitor(idletime_ms=120_000, fired=[2])
    recorder = Recorder()

    asyncio.run(_watcher(monitor, recorder).run())

    assert recorder.calls == [False, True]
    assert monitor.active_watch_count == 1


def test_repeated_idle_fire_does_not_re_emit_or_double_arm() -> None:
    # Two idle fires in a row (id 1) must collapse to a single inactive edge and
    # a single active watch.
    monitor = FakeIdleMonitor(idletime_ms=0, fired=[1, 1])
    recorder = Recorder()

    asyncio.run(_watcher(monitor, recorder).run())

    assert recorder.calls == [True, False]
    assert monitor.active_watch_count == 1
