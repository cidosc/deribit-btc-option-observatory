"""Read-only JSON WebSocket adapter and reconnecting market-data stream."""

from __future__ import annotations

import asyncio
import inspect
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .auth import AuthenticationError


class AsyncJsonConnection(Protocol):
    async def send_json(self, payload: dict[str, Any]) -> None: ...
    async def recv_json(self) -> dict[str, Any]: ...
    async def close(self) -> None: ...


DERIBIT_PUBLIC_WS_URL = "wss://www.deribit.com/ws/api/v2"


@dataclass(slots=True)
class PublicWebSocketConnection:
    """JSON framing over an already-open public Deribit WebSocket."""

    websocket: Any

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self.websocket.send(json.dumps(payload, separators=(",", ":")))

    async def recv_json(self) -> dict[str, Any]:
        frame = await self.websocket.recv()
        try:
            decoded = json.loads(frame)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise ValueError("Deribit WebSocket frame is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("Deribit WebSocket JSON-RPC envelope must be an object")
        return decoded

    async def close(self) -> None:
        await self.websocket.close()


async def connect_public_websocket(
    url: str = DERIBIT_PUBLIC_WS_URL, *, open_timeout_seconds: float = 10
) -> PublicWebSocketConnection:
    """Open one unauthenticated TLS connection to an approved Deribit host."""
    if url not in {
        "wss://www.deribit.com/ws/api/v2",
        "wss://test.deribit.com/ws/api/v2",
    }:
        raise ValueError("public WebSocket URL must be an approved Deribit WSS endpoint")
    if open_timeout_seconds <= 0:
        raise ValueError("open_timeout_seconds must be positive")
    try:
        from websockets.asyncio.client import connect
    except ImportError as exc:
        raise RuntimeError("install the collector optional dependencies to use WebSocket") from exc
    websocket = await connect(
        url,
        open_timeout=open_timeout_seconds,
        ping_interval=20,
        ping_timeout=20,
        max_size=8 * 1024 * 1024,
    )
    return PublicWebSocketConnection(websocket)


@dataclass(frozen=True, slots=True)
class TickerSubscription:
    instrument_name: str
    interval: str = "agg2"

    def __post_init__(self) -> None:
        if self.interval not in {"agg2", "100ms"}:
            raise ValueError("ticker interval must be agg2 or 100ms")

    @property
    def channel(self) -> str:
        return f"ticker.{self.instrument_name}.{self.interval}"


@dataclass(frozen=True, slots=True)
class BookSubscription:
    instrument_name: str
    interval: str = "100ms"

    def __post_init__(self) -> None:
        if self.interval not in {"agg2", "100ms", "raw"}:
            raise ValueError("book interval must be agg2, 100ms, or authenticated raw")

    @property
    def channel(self) -> str:
        return f"book.{self.instrument_name}.{self.interval}"


@dataclass(slots=True)
class ReconnectingTickerStream:
    connection_factory: Callable[[], Awaitable[AsyncJsonConnection] | AsyncJsonConnection]
    on_message: Callable[[dict[str, Any]], Awaitable[None] | None]
    on_system_event: Callable[[str, dict[str, Any]], Awaitable[None] | None]
    heartbeat_timeout_seconds: float
    authenticator: Callable[[AsyncJsonConnection], Awaitable[dict[str, Any]]] | None = None
    subscriptions: set[TickerSubscription] = field(default_factory=set)
    book_subscriptions: set[BookSubscription] = field(default_factory=set)
    lifecycle_channels: set[str] = field(default_factory=lambda: {"instrument.state.option.BTC"})
    backoff_initial_seconds: float = 0.25
    backoff_max_seconds: float = 15.0
    _stopped: bool = False
    _last_message_monotonic: float | None = None
    _reconnect_event: asyncio.Event = field(default_factory=asyncio.Event)
    _reconnect_reason: str = "requested"

    async def _emit(self, name: str, detail: dict[str, Any]) -> None:
        result = self.on_system_event(name, detail)
        if inspect.isawaitable(result):
            await result

    async def _deliver(self, message: dict[str, Any]) -> None:
        result = self.on_message(message)
        if inspect.isawaitable(result):
            await result

    def set_subscriptions(self, subscriptions: set[TickerSubscription]) -> None:
        self.subscriptions = set(subscriptions)

    def set_book_subscriptions(self, subscriptions: set[BookSubscription]) -> None:
        if self.authenticator is None and any(item.interval == "raw" for item in subscriptions):
            raise ValueError("raw book subscriptions require read-only authentication")
        self.book_subscriptions = set(subscriptions)

    def request_reconnect(self, reason: str) -> None:
        self._reconnect_reason = reason
        self._reconnect_event.set()

    @property
    def seconds_since_message(self) -> float | None:
        if self._last_message_monotonic is None:
            return None
        return max(0.0, time.monotonic() - self._last_message_monotonic)

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        backoff = self.backoff_initial_seconds
        while not self._stopped:
            connection: AsyncJsonConnection | None = None
            try:
                maybe_connection = self.connection_factory()
                connection = (
                    await maybe_connection
                    if inspect.isawaitable(maybe_connection)
                    else maybe_connection
                )
                self._last_message_monotonic = time.monotonic()
                if self.authenticator is not None:
                    authentication = await self.authenticator(connection)
                    await self._emit("AUTHENTICATED", authentication)
                await connection.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "public/set_heartbeat",
                        "params": {"interval": 10},
                    }
                )
                await self._subscribe(connection)
                await self._emit("CONNECTED", {"channels": self._channels()})
                backoff = self.backoff_initial_seconds
                while not self._stopped:
                    try:
                        message = await self._receive_or_reconnect(connection)
                    except TimeoutError as exc:
                        await self._emit(
                            "HEARTBEAT_TIMEOUT", {"timeout_seconds": self.heartbeat_timeout_seconds}
                        )
                        raise ConnectionError("heartbeat timeout") from exc
                    self._last_message_monotonic = time.monotonic()
                    if message.get("method") == "heartbeat":
                        params = message.get("params")
                        if isinstance(params, dict) and params.get("type") == "test_request":
                            await connection.send_json(
                                {
                                    "jsonrpc": "2.0",
                                    "id": 2,
                                    "method": "public/test",
                                    "params": {},
                                }
                            )
                        continue
                    await self._deliver(message)
            except asyncio.CancelledError:
                raise
            except AuthenticationError as exc:
                await self._emit("AUTHENTICATION_FAILED", {"error": str(exc)})
                raise
            except Exception as exc:
                if not self._stopped:
                    await self._emit(
                        "DISCONNECTED", {"error": str(exc), "retry_in_seconds": backoff}
                    )
                    await asyncio.sleep(backoff + random.uniform(0, backoff * 0.2))
                    backoff = min(self.backoff_max_seconds, backoff * 2)
            finally:
                if connection is not None:
                    try:
                        await connection.close()
                    except Exception:
                        pass

    async def _receive_or_reconnect(
        self, connection: AsyncJsonConnection
    ) -> dict[str, Any]:
        receive_task = asyncio.create_task(connection.recv_json())
        reconnect_task = asyncio.create_task(self._reconnect_event.wait())
        try:
            done, _ = await asyncio.wait(
                {receive_task, reconnect_task},
                timeout=self.heartbeat_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError
            if reconnect_task in done and self._reconnect_event.is_set():
                self._reconnect_event.clear()
                raise ConnectionError(
                    f"read-only stream reconnect requested: {self._reconnect_reason}"
                )
            return receive_task.result()
        finally:
            for task in (receive_task, reconnect_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(receive_task, reconnect_task, return_exceptions=True)

    async def _subscribe(self, connection: AsyncJsonConnection) -> None:
        channels = self._channels()
        if channels:
            await connection.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "public/subscribe",
                    "params": {"channels": channels},
                }
            )

    def _channels(self) -> list[str]:
        return sorted(
            {subscription.channel for subscription in self.subscriptions}
            | {subscription.channel for subscription in self.book_subscriptions}
            | self.lifecycle_channels
        )
