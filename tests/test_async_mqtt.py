from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion, MQTTErrorCode

from ha_tux.async_mqtt import AsyncioMqttClientDriver, MqttConnectionConfig

if TYPE_CHECKING:
    from paho.mqtt.client import SocketLike
else:
    SocketLike = object


def make_client() -> mqtt.Client:
    return mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id="ha-tux-test",
    )


def test_driver_installs_socket_callbacks() -> None:
    client = make_client()

    _ = AsyncioMqttClientDriver(
        client=client,
        loop=asyncio.new_event_loop(),
        config=MqttConnectionConfig(host="localhost", port=1883),
    )

    assert client.on_socket_open is not None
    assert client.on_socket_close is not None
    assert client.on_socket_register_write is not None
    assert client.on_socket_unregister_write is not None


def test_socket_callbacks_register_and_remove_readers() -> None:
    async def run() -> None:
        client = make_client()
        loop = asyncio.get_running_loop()
        driver = AsyncioMqttClientDriver(
            client=client,
            loop=loop,
            config=MqttConnectionConfig(host="localhost", port=1883),
        )
        reader, writer = socket.socketpair()
        try:
            on_open = require_callback(client.on_socket_open)
            on_close = require_callback(client.on_socket_close)

            on_open(client, None, cast(SocketLike, cast(object, reader)))
            await asyncio.sleep(0)

            assert driver.reader_fd == reader.fileno()

            on_close(client, None, cast(SocketLike, cast(object, reader)))
            await asyncio.sleep(0)

            assert driver.reader_fd is None
        finally:
            await driver.stop()
            reader.close()
            writer.close()

    asyncio.run(run())


def test_driver_does_not_start_threaded_mqtt_loop() -> None:
    client = make_client()
    loop_start_calls = 0
    loop_forever_calls = 0

    def loop_start() -> MQTTErrorCode:
        nonlocal loop_start_calls
        loop_start_calls += 1
        return mqtt.MQTT_ERR_SUCCESS

    def loop_forever(
        timeout: float = 1.0,
        retry_first_connection: bool = False,
    ) -> MQTTErrorCode:
        del timeout, retry_first_connection
        nonlocal loop_forever_calls
        loop_forever_calls += 1
        return mqtt.MQTT_ERR_SUCCESS

    client.loop_start = loop_start
    client.loop_forever = loop_forever

    _ = AsyncioMqttClientDriver(
        client=client,
        loop=asyncio.new_event_loop(),
        config=MqttConnectionConfig(host="localhost", port=1883),
    )

    assert loop_start_calls == 0
    assert loop_forever_calls == 0


def require_callback(
    callback: Callable[[mqtt.Client, object, SocketLike], None] | None,
) -> Callable[[mqtt.Client, object, SocketLike], None]:
    assert callback is not None
    return callback
