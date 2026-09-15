from __future__ import annotations

import asyncio
import json

from btc_option_scanner.deribit import (
    BookSubscription,
    PublicWebSocketConnection,
    ReconnectingTickerStream,
    TickerSubscription,
)


class _Connection:
    def __init__(self, message: dict) -> None:
        self.message = message
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def recv_json(self) -> dict:
        return self.message

    async def close(self) -> None:
        return None


def test_reconnects_and_resubscribes_after_connect_failure() -> None:
    async def scenario() -> None:
        attempts = 0
        events: list[str] = []
        stream: ReconnectingTickerStream
        connection: _Connection | None = None

        async def factory():
            nonlocal attempts, connection
            attempts += 1
            if attempts == 1:
                raise ConnectionError("temporary")
            connection = _Connection(
                {"method": "subscription", "params": {"channel": "ticker.BTC-X.agg2", "data": {}}}
            )
            return connection

        async def message(_payload: dict) -> None:
            stream.stop()

        async def system(name: str, _detail: dict) -> None:
            events.append(name)

        stream = ReconnectingTickerStream(
            factory,
            message,
            system,
            heartbeat_timeout_seconds=1,
            backoff_initial_seconds=0.001,
            backoff_max_seconds=0.002,
        )
        stream.set_subscriptions({TickerSubscription("BTC-X")})
        stream.set_book_subscriptions({BookSubscription("BTC-X")})
        await stream.run()
        assert attempts == 2
        assert "DISCONNECTED" in events and "CONNECTED" in events
        assert connection is not None
        subscribed = next(
            item for item in connection.sent if item.get("method") == "public/subscribe"
        )
        assert "book.BTC-X.100ms" in subscribed["params"]["channels"]

    asyncio.run(scenario())


def test_public_websocket_connection_encodes_and_validates_json() -> None:
    class Socket:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.closed = False

        async def send(self, value: str) -> None:
            self.sent.append(value)

        async def recv(self) -> str:
            return '{"jsonrpc":"2.0","result":"ok"}'

        async def close(self) -> None:
            self.closed = True

    async def scenario() -> None:
        socket = Socket()
        connection = PublicWebSocketConnection(socket)
        await connection.send_json({"jsonrpc": "2.0", "method": "public/test"})
        assert json.loads(socket.sent[0])["method"] == "public/test"
        assert (await connection.recv_json())["result"] == "ok"
        await connection.close()
        assert socket.closed

    asyncio.run(scenario())


def test_test_request_heartbeat_gets_public_test_response() -> None:
    class Messages(_Connection):
        def __init__(self) -> None:
            super().__init__({})
            self.messages = [
                {"method": "heartbeat", "params": {"type": "test_request"}},
                {"method": "subscription", "params": {"channel": "ticker.BTC-X.agg2"}},
            ]

        async def recv_json(self) -> dict:
            return self.messages.pop(0)

    async def scenario() -> None:
        connection = Messages()
        stream: ReconnectingTickerStream

        async def on_message(_payload: dict) -> None:
            stream.stop()

        async def on_system(_name: str, _detail: dict) -> None:
            return None

        stream = ReconnectingTickerStream(lambda: connection, on_message, on_system, 1)
        stream.lifecycle_channels.clear()
        await stream.run()
        assert any(item.get("method") == "public/test" for item in connection.sent)

    asyncio.run(scenario())
