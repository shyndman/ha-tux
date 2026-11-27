"""Shared test doubles for D-Bus name ownership and in-memory MQTT sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast, overload

import aiomqtt
from ha_mqtt_discoverable import (
    CommandCallback,
    CommandPayloadParser,
    ParsedCommandCallback,
)

from ha_tux.dbus import NameOwnerChanged
from ha_tux.runtime import CallbackErrorHandler


async def settle(turns: int = 12) -> None:
    """Yield across event-loop turns so scheduled tasks and watchers settle."""
    for _ in range(turns):
        await asyncio.sleep(0)


async def _noop_callback_error(error: Exception, message: aiomqtt.Message) -> None:
    del error, message


class FakeNameOwnerSource:
    """In-memory ``NameOwnerSource`` adapter with scripted initial owner and transitions."""

    def __init__(self, *, initially_present: bool = True) -> None:
        self.initially_present: bool = initially_present
        self.subscribed: bool = False
        self.has_owner_checked_after_subscribe: bool = False
        self._queue: asyncio.Queue[NameOwnerChanged | None] = asyncio.Queue()

    async def name_has_owner(self, service_name: str) -> bool:
        del service_name
        self.has_owner_checked_after_subscribe = self.subscribed
        return self.initially_present

    @asynccontextmanager
    async def open_name_owner_changes(
        self,
    ) -> AsyncGenerator[AsyncIterator[NameOwnerChanged]]:
        self.subscribed = True

        async def iterate() -> AsyncIterator[NameOwnerChanged]:
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                yield item

        try:
            yield iterate()
        finally:
            self.subscribed = False

    async def emit(self, name: str, old_owner: str, new_owner: str) -> None:
        """Push a ``NameOwnerChanged`` tuple onto the active signal stream."""
        await self._queue.put((name, old_owner, new_owner))

    async def close(self) -> None:
        """Terminate the signal stream cleanly."""
        await self._queue.put(None)


@dataclass(frozen=True, slots=True)
class PublishedMessage:
    topic: str
    payload: str | bytes | int | float | None
    retain: bool
    qos: int


class InMemorySession:
    """Test adapter satisfying ``ManagedSession`` with controllable transport failure
    and inbound command delivery."""

    def __init__(
        self,
        *,
        on_callback_error: CallbackErrorHandler | None = None,
        connect_error: Exception | None = None,
    ) -> None:
        self._on_callback_error: CallbackErrorHandler = (
            on_callback_error if on_callback_error is not None else _noop_callback_error
        )
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
        exc_type: object,
        exc: BaseException | None,
        tb: object,
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
