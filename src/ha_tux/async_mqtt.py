import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import paho.mqtt.client as mqtt

if TYPE_CHECKING:
    from paho.mqtt.client import SocketLike
else:
    SocketLike = object

LOGGER = logging.getLogger(__name__)
DEFAULT_MQTT_KEEPALIVE_SECONDS = 60
MQTT_MISC_LOOP_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class MqttConnectionConfig:
    host: str
    port: int
    keepalive_seconds: int = DEFAULT_MQTT_KEEPALIVE_SECONDS


class AsyncioMqttClientDriver:
    def __init__(
        self,
        *,
        client: mqtt.Client,
        loop: asyncio.AbstractEventLoop,
        config: MqttConnectionConfig,
    ) -> None:
        self.client: mqtt.Client = client
        self._loop: asyncio.AbstractEventLoop = loop
        self._config: MqttConnectionConfig = config
        self._misc_task: asyncio.Task[None] | None = None
        self._reader_fd: int | None = None
        self._writer_fd: int | None = None
        self._stopping: bool = False
        self._install_socket_callbacks()

    def _install_socket_callbacks(self) -> None:
        self.client.on_socket_open = self._on_socket_open
        self.client.on_socket_close = self._on_socket_close
        self.client.on_socket_register_write = self._on_socket_register_write
        self.client.on_socket_unregister_write = self._on_socket_unregister_write

    @property
    def reader_fd(self) -> int | None:
        return self._reader_fd

    @property
    def writer_fd(self) -> int | None:
        return self._writer_fd

    async def connect(self) -> None:
        self._stopping = False
        self.client.connect_async(
            self._config.host,
            self._config.port,
            keepalive=self._config.keepalive_seconds,
        )
        result = await asyncio.to_thread(self.client.reconnect)
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT reconnect failed with error code {result}")

    async def disconnect(self) -> None:
        self._stopping = True
        _ = self.client.disconnect()
        await self.stop()

    async def stop(self) -> None:
        if self._reader_fd is not None:
            self._remove_reader(self._reader_fd)
        if self._writer_fd is not None:
            self._remove_writer(self._writer_fd)

        task = self._misc_task
        if task is not None:
            _ = task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._misc_task = None

    def _on_socket_open(
        self,
        _client: mqtt.Client,
        _userdata: object,
        sock: SocketLike,
    ) -> None:
        _ = self._loop.call_soon_threadsafe(self._register_reader, sock.fileno())

    def _on_socket_close(
        self,
        _client: mqtt.Client,
        _userdata: object,
        sock: SocketLike,
    ) -> None:
        _ = self._loop.call_soon_threadsafe(self._handle_socket_close, sock.fileno())

    def _on_socket_register_write(
        self,
        _client: mqtt.Client,
        _userdata: object,
        sock: SocketLike,
    ) -> None:
        _ = self._loop.call_soon_threadsafe(self._register_writer, sock.fileno())

    def _on_socket_unregister_write(
        self,
        _client: mqtt.Client,
        _userdata: object,
        sock: SocketLike,
    ) -> None:
        _ = self._loop.call_soon_threadsafe(self._remove_writer, sock.fileno())

    def _register_reader(self, fd: int) -> None:
        self._reader_fd = fd
        _ = self._loop.add_reader(fd, self._loop_read)
        if self._misc_task is None or self._misc_task.done():
            self._misc_task = self._loop.create_task(self._misc_loop())

    def _register_writer(self, fd: int) -> None:
        self._writer_fd = fd
        _ = self._loop.add_writer(fd, self._loop_write)

    def _remove_reader(self, fd: int) -> None:
        _ = self._loop.remove_reader(fd)
        if self._reader_fd == fd:
            self._reader_fd = None

    def _remove_writer(self, fd: int) -> None:
        _ = self._loop.remove_writer(fd)
        if self._writer_fd == fd:
            self._writer_fd = None

    def _handle_socket_close(self, fd: int) -> None:
        self._remove_reader(fd)
        self._remove_writer(fd)
        task = self._misc_task
        if task is not None:
            _ = task.cancel()
            self._misc_task = None

    def _loop_read(self) -> None:
        result = self.client.loop_read()
        if result != mqtt.MQTT_ERR_SUCCESS and not self._stopping:
            LOGGER.warning("MQTT read loop returned an error", extra={"result": result})

    def _loop_write(self) -> None:
        result = self.client.loop_write()
        if result != mqtt.MQTT_ERR_SUCCESS and not self._stopping:
            LOGGER.warning(
                "MQTT write loop returned an error", extra={"result": result}
            )

    async def _misc_loop(self) -> None:
        while not self._stopping:
            result = self.client.loop_misc()
            if result != mqtt.MQTT_ERR_SUCCESS:
                LOGGER.warning(
                    "MQTT misc loop returned an error", extra={"result": result}
                )
                return
            await asyncio.sleep(MQTT_MISC_LOOP_SECONDS)
