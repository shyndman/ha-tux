"""Session Runtime for supervising an MQTT session and its active features.

Owns connection retries with bounded exponential backoff, concurrent execution
of every feature active for a role, and isolation of non-MQTT command errors.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Protocol

import aiomqtt
from ha_mqtt_discoverable import DeviceInfo, MqttSession, SessionLike, Settings
from libsh import get_logger

from ha_tux.config import LOGGER_NAME, HaTuxConfig, Role

LOGGER = get_logger(LOGGER_NAME)

INITIAL_RECONNECT_DELAY_SECONDS: Final[float] = 2.0
MAX_RECONNECT_DELAY_SECONDS: Final[float] = 60.0
BACKOFF_MULTIPLIER: Final[float] = 2.0
RECONNECT_ERROR_GRACE_SECONDS: Final[float] = 180.0

_CONNECT_ERRORS: Final[tuple[type[Exception], ...]] = (aiomqtt.MqttError, OSError)

CallbackErrorHandler = Callable[[Exception, aiomqtt.Message], Awaitable[None]]
SleepFn = Callable[[float], Awaitable[None]]
MonotonicFn = Callable[[], float]


class ManagedSession(SessionLike, Protocol):
    """Runtime-facing seam for a supervisable MQTT session adapter."""

    async def __aenter__(self) -> ManagedSession:
        """Connect to the broker and publish the session's online status."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Drain background tasks, publish offline status, and disconnect."""
        ...

    async def wait_closed(self) -> None:
        """Block until clean shutdown or raise immediately on transport failure."""
        ...


SessionFactory = Callable[[CallbackErrorHandler], ManagedSession]


def mqtt_session_factory(settings: Settings.MQTT) -> SessionFactory:
    """Build a factory that opens live ``MqttSession`` instances with ``settings``."""

    def create(on_callback_error: CallbackErrorHandler) -> ManagedSession:
        return MqttSession(settings, on_callback_error=on_callback_error)

    return create


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Per-session dependencies handed to each active feature's ``run`` coroutine."""

    session: SessionLike
    device: DeviceInfo
    hostname: str
    host_prefix: str
    config: HaTuxConfig


FeatureRun = Callable[[SessionContext], Coroutine[object, object, None]]


@dataclass(frozen=True, slots=True)
class Feature:
    """Descriptor binding a feature's name and active roles to its lifecycle coroutine."""

    name: str
    roles: frozenset[Role]
    run: FeatureRun


async def run_poller(
    *,
    name: str,
    interval_seconds: float,
    poll: Callable[[], Awaitable[None]],
    sleep: SleepFn = asyncio.sleep,
) -> None:
    """Invoke ``poll`` immediately, then repeat on a fixed interval.

    Logs and re-raises any failing iteration so the supervising runtime unwinds.
    """
    LOGGER.debug("Started poller", poller=name, interval_seconds=interval_seconds)

    while True:
        try:
            await poll()
        except Exception:
            LOGGER.exception("Poller iteration failed", poller=name)
            raise

        await sleep(interval_seconds)


class SessionRuntime:
    """Supervises one MQTT session and all active features for a role.

    Connect-time transport errors (``MqttError`` / ``OSError``) and post-connect
    ``MqttError`` failures trigger in-process reconnect with bounded exponential
    backoff (2s -> 60s cap, holding at 60s indefinitely and resetting once a
    session connects cleanly). Non-MQTT command errors are isolated to logs;
    unhandled non-MQTT feature failures unwind the session and propagate so
    systemd can restart the process.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        features: Sequence[Feature],
        device: DeviceInfo,
        hostname: str,
        host_prefix: str,
        config: HaTuxConfig,
        sleep: SleepFn = asyncio.sleep,
        monotonic: MonotonicFn = time.monotonic,
        reconnect_error_grace_seconds: float = RECONNECT_ERROR_GRACE_SECONDS,
    ) -> None:
        """Configure the runtime with its session seam, features, and host metadata."""
        self._session_factory: SessionFactory = session_factory
        self._features: tuple[Feature, ...] = tuple(features)
        self._device: DeviceInfo = device
        self._hostname: str = hostname
        self._host_prefix: str = host_prefix
        self._config: HaTuxConfig = config
        self._sleep: SleepFn = sleep
        self._monotonic: MonotonicFn = monotonic
        self._reconnect_error_grace_seconds: float = reconnect_error_grace_seconds
        self._delay_seconds: float = INITIAL_RECONNECT_DELAY_SECONDS
        self._disconnect_started_at: float | None = None

    async def run(self) -> None:
        """Run the reconnect loop until cancelled, cleanly closed, or fatally failed."""
        feature_names = [feature.name for feature in self._features]
        LOGGER.info("Started session runtime", features=feature_names)
        self._delay_seconds = INITIAL_RECONNECT_DELAY_SECONDS
        self._disconnect_started_at = None

        while True:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                LOGGER.info("Session runtime cancelled")
                raise
            except _TransportReconnectError as reconnect:
                delay = self._delay_seconds
                self._log_reconnect_failure(delay=delay, cause=reconnect.cause)
                await self._sleep(delay)
                self._delay_seconds = min(
                    delay * BACKOFF_MULTIPLIER, MAX_RECONNECT_DELAY_SECONDS
                )
            except Exception:
                LOGGER.exception("Session runtime encountered a fatal error")
                raise
            else:
                LOGGER.info("MQTT session closed cleanly")
                return

    def _log_reconnect_failure(self, *, delay: float, cause: Exception) -> None:
        now = self._monotonic()
        if self._disconnect_started_at is None:
            self._disconnect_started_at = now

        elapsed = now - self._disconnect_started_at
        if elapsed >= self._reconnect_error_grace_seconds:
            LOGGER.error(
                "Reconnecting MQTT session",
                delay_seconds=delay,
                elapsed_seconds=round(elapsed, 1),
                error=str(cause),
                error_type=type(cause).__name__,
            )
            return

        LOGGER.warning(
            "Reconnecting MQTT session",
            delay_seconds=delay,
            error=str(cause),
            error_type=type(cause).__name__,
        )

    async def _run_once(self) -> None:
        """Open one session and supervise transport alongside all active features."""
        session = self._session_factory(self._on_callback_error)

        try:
            entered = await session.__aenter__()
        except _CONNECT_ERRORS as error:
            raise _TransportReconnectError(error) from error

        self._disconnect_started_at = None

        if self._delay_seconds != INITIAL_RECONNECT_DELAY_SECONDS:
            LOGGER.info(
                "Reset MQTT reconnect backoff",
                previous_delay_seconds=self._delay_seconds,
                delay_seconds=INITIAL_RECONNECT_DELAY_SECONDS,
            )
            self._delay_seconds = INITIAL_RECONNECT_DELAY_SECONDS

        LOGGER.info(
            "Connected MQTT session",
            features=[feature.name for feature in self._features],
        )

        ctx = SessionContext(
            session=entered,
            device=self._device,
            hostname=self._hostname,
            host_prefix=self._host_prefix,
            config=self._config,
        )

        try:
            async with asyncio.TaskGroup() as tasks:
                _ = tasks.create_task(entered.wait_closed(), name="mqtt_transport")

                for feature in self._features:
                    LOGGER.debug("Starting feature", feature=feature.name)
                    _ = tasks.create_task(
                        feature.run(ctx),
                        name=f"feature:{feature.name}",
                    )
        except BaseException as error:
            await session.__aexit__(type(error), error, error.__traceback__)
            mqtt_error = _extract_mqtt_error(error)
            if mqtt_error is not None:
                raise _TransportReconnectError(mqtt_error) from mqtt_error
            raise
        else:
            await session.__aexit__(None, None, None)

    async def _on_callback_error(
        self, error: Exception, message: aiomqtt.Message
    ) -> None:
        """Isolate non-MQTT command callback errors; propagate ``MqttError`` to reconnect."""
        topic = str(message.topic)

        if isinstance(error, aiomqtt.MqttError):
            LOGGER.warning(
                "MQTT transport failed inside command callback",
                topic=topic,
                error=str(error),
                error_type=type(error).__name__,
            )
            raise error

        LOGGER.warning(
            "Isolated non-MQTT error in command callback",
            topic=topic,
            error=str(error),
            error_type=type(error).__name__,
        )


class _TransportReconnectError(Exception):
    """Internal signal separating recoverable transport failures from fatal crashes."""

    def __init__(self, cause: Exception) -> None:
        """Wrap the underlying transport exception that triggered a reconnect."""
        super().__init__(str(cause))
        self.cause: Exception = cause


def _extract_mqtt_error(error: BaseException) -> Exception | None:
    """Return ``error`` if every leaf exception is an ``aiomqtt.MqttError``."""
    if isinstance(error, aiomqtt.MqttError):
        return error

    if isinstance(error, ExceptionGroup):
        matched, rest = error.split(aiomqtt.MqttError)
        if matched is not None and rest is None:
            return matched

    return None
