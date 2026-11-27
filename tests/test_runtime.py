from __future__ import annotations

import asyncio
import time
from contextlib import suppress

import aiomqtt
import pytest
from ha_mqtt_discoverable import DeviceInfo
from ha_mqtt_discoverable.sensors import Button, ButtonInfo
from structlog.testing import capture_logs

from ha_tux.config import HaTuxConfig
from ha_tux.runtime import (
    CallbackErrorHandler,
    Feature,
    ManagedSession,
    MonotonicFn,
    RECONNECT_ERROR_GRACE_SECONDS,
    SessionContext,
    SessionRuntime,
    SleepFn,
    run_poller,
)
from tests.fakes import InMemorySession

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")
TEST_CONFIG = HaTuxConfig()


def _make_runtime(
    *,
    sessions: list[InMemorySession],
    features: list[Feature],
    connect_errors: list[Exception | None] | None = None,
    delays: list[float] | None = None,
    sleep: SleepFn | None = None,
    monotonic: MonotonicFn = time.monotonic,
    reconnect_error_grace_seconds: float = RECONNECT_ERROR_GRACE_SECONDS,
) -> SessionRuntime:
    planned_errors = list(connect_errors or [])
    recorded_delays = delays if delays is not None else []

    def factory(on_callback_error: CallbackErrorHandler) -> ManagedSession:
        connect_error = planned_errors.pop(0) if planned_errors else None
        session = InMemorySession(
            on_callback_error=on_callback_error,
            connect_error=connect_error,
        )
        sessions.append(session)
        return session

    async def fake_sleep(seconds: float) -> None:
        recorded_delays.append(seconds)
        if sleep is not None:
            await sleep(seconds)
        else:
            await asyncio.sleep(0)

    return SessionRuntime(
        session_factory=factory,
        features=features,
        device=DEVICE,
        hostname="testbox",
        host_prefix="testbox",
        config=TEST_CONFIG,
        sleep=fake_sleep,
        monotonic=monotonic,
        reconnect_error_grace_seconds=reconnect_error_grace_seconds,
    )


def test_transport_disconnect_while_idle_cancels_features_and_reconnects() -> None:
    async def scenario() -> None:
        sessions: list[InMemorySession] = []
        cancelled = asyncio.Event()
        second_connected = asyncio.Event()

        async def idle_feature(_ctx: SessionContext) -> None:
            if len(sessions) >= 2:
                second_connected.set()
            try:
                _ = await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        runtime = _make_runtime(
            sessions=sessions,
            features=[Feature("idle", frozenset({"all"}), idle_feature)],
        )

        task = asyncio.create_task(runtime.run())
        await asyncio.sleep(0)
        assert len(sessions) == 1
        assert sessions[0].entered is True

        sessions[0].fail_transport(aiomqtt.MqttError("broker dropped"))

        _ = await asyncio.wait_for(cancelled.wait(), timeout=2)
        _ = await asyncio.wait_for(second_connected.wait(), timeout=2)

        assert sessions[0].exited is True
        assert len(sessions) == 2

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_transport_failures_backoff_exponentially_and_cap_at_60s() -> None:
    async def scenario() -> None:
        sessions: list[InMemorySession] = []
        delays: list[float] = []
        reached_connected = asyncio.Event()

        async def ready_feature(_ctx: SessionContext) -> None:
            reached_connected.set()
            _ = await asyncio.Event().wait()

        connect_errors: list[Exception | None] = [
            aiomqtt.MqttError("refused 1"),
            OSError("network down"),
            aiomqtt.MqttError("refused 3"),
            aiomqtt.MqttError("refused 4"),
            aiomqtt.MqttError("refused 5"),
            aiomqtt.MqttError("refused 6"),
            aiomqtt.MqttError("refused 7"),
            aiomqtt.MqttError("refused 8"),
            None,
        ]
        runtime = _make_runtime(
            sessions=sessions,
            features=[Feature("ready", frozenset({"all"}), ready_feature)],
            connect_errors=connect_errors,
            delays=delays,
        )

        task = asyncio.create_task(runtime.run())
        _ = await asyncio.wait_for(reached_connected.wait(), timeout=2)

        assert delays == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_backoff_resets_after_clean_connect() -> None:
    async def scenario() -> None:
        sessions: list[InMemorySession] = []
        delays: list[float] = []
        connected = asyncio.Event()

        async def feature(_ctx: SessionContext) -> None:
            connected.set()
            _ = await asyncio.Event().wait()

        connect_errors: list[Exception | None] = [
            aiomqtt.MqttError("refused 1"),
            aiomqtt.MqttError("refused 2"),
            None,
            None,
        ]
        runtime = _make_runtime(
            sessions=sessions,
            features=[Feature("f", frozenset({"all"}), feature)],
            connect_errors=connect_errors,
            delays=delays,
        )

        task = asyncio.create_task(runtime.run())

        # Two connect failures ramp backoff 2s -> 4s, then attempt 3 connects cleanly.
        _ = await asyncio.wait_for(connected.wait(), timeout=2)
        connected.clear()

        # Dropping after a clean connect must restart backoff at 2s, not 8s.
        sessions[-1].fail_transport(aiomqtt.MqttError("dropped after connect"))
        _ = await asyncio.wait_for(connected.wait(), timeout=2)

        assert delays == [2.0, 4.0, 2.0]

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error",
    [
        ValueError("unexpected feature bug"),
        FileNotFoundError("post-connect os error"),
    ],
)
def test_non_mqtt_feature_failure_exits_runtime_for_systemd(error: Exception) -> None:
    async def scenario() -> None:
        sessions: list[InMemorySession] = []

        async def crashing_feature(_ctx: SessionContext) -> None:
            raise error

        runtime = _make_runtime(
            sessions=sessions,
            features=[Feature("buggy", frozenset({"all"}), crashing_feature)],
        )

        with pytest.raises(ExceptionGroup) as exc_info:
            await runtime.run()

        assert len(sessions) == 1
        assert sessions[0].exited is True
        assert any(err is error for err in exc_info.value.exceptions)

    asyncio.run(scenario())


def test_command_callback_non_mqtt_errors_are_isolated() -> None:
    async def scenario() -> None:
        sessions: list[InMemorySession] = []
        announced = asyncio.Event()
        presses = 0

        async def lock_like_feature(ctx: SessionContext) -> None:
            async def on_press(_sender: Button, _message: aiomqtt.Message) -> None:
                nonlocal presses
                presses += 1
                if presses == 1:
                    raise RuntimeError("dbus screensaver failed")
                if presses == 2:
                    raise PermissionError("polkit denied")

            button = Button(
                ctx.session,
                ButtonInfo(
                    device=ctx.device,
                    unique_id="ha_tux_test_lock",
                    object_id="test_lock",
                    name="Lock",
                ),
                on_press,
            )
            await button.set_available(True)
            announced.set()

        runtime = _make_runtime(
            sessions=sessions,
            features=[Feature("screen_lock", frozenset({"all"}), lock_like_feature)],
        )

        task = asyncio.create_task(runtime.run())
        _ = await asyncio.wait_for(announced.wait(), timeout=2)

        # Passive feature returned, yet session stays up and isolates non-MQTT errors.
        topic = sessions[0].command_topics[0]
        await sessions[0].deliver(topic)
        await sessions[0].deliver(topic)
        await sessions[0].deliver(topic)

        assert presses == 3
        assert len(sessions) == 1
        assert sessions[0].exited is False

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_command_callback_mqtt_error_triggers_reconnect() -> None:
    async def scenario() -> None:
        sessions: list[InMemorySession] = []
        ready = asyncio.Event()

        async def command_feature(ctx: SessionContext) -> None:
            async def on_press(_sender: Button, _message: aiomqtt.Message) -> None:
                raise aiomqtt.MqttError("publish failed inside command")

            button = Button(
                ctx.session,
                ButtonInfo(
                    device=ctx.device,
                    unique_id="ha_tux_test_cmd",
                    object_id="test_cmd",
                    name="Cmd",
                ),
                on_press,
            )
            await button.set_available(True)
            ready.set()

        runtime = _make_runtime(
            sessions=sessions,
            features=[Feature("cmd", frozenset({"all"}), command_feature)],
        )

        task = asyncio.create_task(runtime.run())
        _ = await asyncio.wait_for(ready.wait(), timeout=2)
        ready.clear()

        topic = sessions[0].command_topics[0]
        await sessions[0].deliver(topic)
        _ = await asyncio.wait_for(ready.wait(), timeout=2)

        assert sessions[0].exited is True
        assert len(sessions) == 2

        _ = task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_run_poller_executes_immediately_repeats_and_propagates_failure() -> None:
    async def scenario() -> None:
        calls = 0
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        async def poll() -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("poll boom")

        with pytest.raises(RuntimeError, match="poll boom"):
            await run_poller(
                name="test",
                interval_seconds=15.0,
                poll=poll,
                sleep=fake_sleep,
            )

        assert calls == 3
        assert sleeps == [15.0, 15.0]

    asyncio.run(scenario())


def test_reconnect_logs_warning_within_grace_then_error_and_resets_on_connect() -> None:
    async def scenario() -> None:
        sessions: list[InMemorySession] = []
        now = 100.0
        connected = asyncio.Event()

        async def advance_clock(seconds: float) -> None:
            nonlocal now
            now += seconds
            await asyncio.sleep(0)

        async def feature(_ctx: SessionContext) -> None:
            connected.set()
            _ = await asyncio.Event().wait()

        # Delays across failures: 2 + 4 + 8 + 16 + 32 + 60 + 60 = 182s.
        # Attempts 1-6 happen at elapsed 0, 2, 6, 14, 30, 62, 122 (< 180s -> warning).
        # Attempt 8 happens at elapsed 182s (>= 180s -> error).
        connect_errors: list[Exception | None] = [
            aiomqtt.MqttError("err 1"),
            aiomqtt.MqttError("err 2"),
            aiomqtt.MqttError("err 3"),
            aiomqtt.MqttError("err 4"),
            aiomqtt.MqttError("err 5"),
            aiomqtt.MqttError("err 6"),
            aiomqtt.MqttError("err 7"),
            aiomqtt.MqttError("err 8"),
            None,
            aiomqtt.MqttError("post-connect err"),
            None,
        ]
        runtime = _make_runtime(
            sessions=sessions,
            features=[Feature("f", frozenset({"all"}), feature)],
            connect_errors=connect_errors,
            sleep=advance_clock,
            monotonic=lambda: now,
            reconnect_error_grace_seconds=180.0,
        )

        with capture_logs() as logs:
            task = asyncio.create_task(runtime.run())
            _ = await asyncio.wait_for(connected.wait(), timeout=2)
            connected.clear()

            # Drop clean session; next failure should be warning again, not error.
            sessions[-1].fail_transport(aiomqtt.MqttError("dropped"))
            _ = await asyncio.wait_for(connected.wait(), timeout=2)

            _ = task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        reconnect_logs = [
            entry for entry in logs if entry.get("event") == "Reconnecting MQTT session"
        ]
        assert [entry.get("log_level") for entry in reconnect_logs] == [
            "warning",
            "warning",
            "warning",
            "warning",
            "warning",
            "warning",
            "warning",
            "error",
            "warning",
            "warning",
        ]
        assert reconnect_logs[7]["elapsed_seconds"] == 182.0

    asyncio.run(scenario())
