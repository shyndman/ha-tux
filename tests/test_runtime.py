from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from types import TracebackType
from typing import cast, overload

import aiomqtt
import pytest
from ha_mqtt_discoverable import (
    CommandCallback,
    CommandPayloadParser,
    DeviceInfo,
    ParsedCommandCallback,
)
from ha_mqtt_discoverable.sensors import Button, ButtonInfo

from ha_tux.config import HaTuxConfig
from ha_tux.runtime import (
    CallbackErrorHandler,
    Feature,
    ManagedSession,
    SessionContext,
    SessionRuntime,
    run_poller,
)

DEVICE = DeviceInfo(name="ha-tux", identifiers="ha-tux-test")
TEST_CONFIG = HaTuxConfig()


@dataclass(frozen=True, slots=True)
class PublishedMessage:
    topic: str
    payload: str | bytes | int | float | None
    retain: bool
    qos: int


class InMemorySession:
    """Test adapter satisfying ManagedSession with controllable transport failure
    and inbound command delivery."""

    def __init__(
        self,
        *,
        on_callback_error: CallbackErrorHandler,
        connect_error: Exception | None = None,
    ) -> None:
        self._on_callback_error: CallbackErrorHandler = on_callback_error
        self._connect_error: Exception | None = connect_error
        self._closed_event: asyncio.Event = asyncio.Event()
        self._failure: Exception | None = None
        self._commands: dict[str, Callable[[aiomqtt.Message], Awaitable[None]]] = {}
        self.published: list[PublishedMessage] = []
        self.entered: bool = False
        self.exited: bool = False

    @property
    def discovery_prefix(self) -> str:
        return "homeassistant"

    @property
    def state_prefix(self) -> str:
        return "hmd"

    @property
    def status_topic(self) -> str:
        return "hmd/ha-tux-test/status"

    async def __aenter__(self) -> InMemorySession:
        if self._connect_error is not None:
            raise self._connect_error

        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        self.exited = True
        self._closed_event.set()

    async def wait_closed(self) -> None:
        if self._failure is not None:
            raise self._failure

        _ = await self._closed_event.wait()
        if self._failure is not None:
            raise self._failure

    async def publish(
        self,
        topic: str,
        payload: str | bytes | int | float | None,
        *,
        retain: bool = False,
        qos: int = 0,
    ) -> None:
        if self._failure is not None:
            raise self._failure

        self.published.append(PublishedMessage(topic, payload, retain, qos))

    @overload
    def register_command[SenderT](
        self,
        topic: str,
        sender: SenderT,
        callback: CommandCallback[SenderT],
        *,
        qos: int = 1,
        command_name: str | None = None,
    ) -> None: ...

    @overload
    def register_command[SenderT, ParsedT](
        self,
        topic: str,
        sender: SenderT,
        callback: ParsedCommandCallback[SenderT, ParsedT],
        *,
        parser: CommandPayloadParser[ParsedT],
        qos: int = 1,
        command_name: str | None = None,
    ) -> None: ...

    def register_command[SenderT, ParsedT](
        self,
        topic: str,
        sender: SenderT,
        callback: CommandCallback[SenderT] | ParsedCommandCallback[SenderT, ParsedT],
        *,
        parser: CommandPayloadParser[ParsedT] | None = None,
        qos: int = 1,
        command_name: str | None = None,
    ) -> None:
        del qos, command_name
        dispatch = cast(Callable[..., Awaitable[None]], callback)

        async def handle(message: aiomqtt.Message) -> None:
            if parser is not None:
                await dispatch(sender, parser(message), message)
            else:
                await dispatch(sender, message)

        self._commands[topic] = handle

    @property
    def command_topics(self) -> tuple[str, ...]:
        return tuple(self._commands)

    def fail_transport(self, error: Exception) -> None:
        if self._failure is None:
            self._failure = error
        self._closed_event.set()

    async def deliver(self, topic: str, payload: bytes = b"PRESS") -> None:
        handler = self._commands[topic]
        message = aiomqtt.Message(
            topic=topic, payload=payload, qos=1, retain=False, mid=1, properties=None
        )
        try:
            await handler(message)
        except Exception as error:
            try:
                await self._on_callback_error(error, message)
            except Exception as propagated:
                self.fail_transport(propagated)


def _make_runtime(
    *,
    sessions: list[InMemorySession],
    features: list[Feature],
    connect_errors: list[Exception | None] | None = None,
    delays: list[float] | None = None,
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
        await asyncio.sleep(0)

    return SessionRuntime(
        session_factory=factory,
        features=features,
        device=DEVICE,
        hostname="testbox",
        host_prefix="testbox",
        config=TEST_CONFIG,
        sleep=fake_sleep,
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
