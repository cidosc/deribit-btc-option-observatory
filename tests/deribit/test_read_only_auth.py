from __future__ import annotations

import asyncio

import pytest

from btc_option_scanner.deribit import (
    AuthenticationError,
    BookSubscription,
    DeribitCredentials,
    ReadOnlyWebSocketAuthenticator,
    ReconnectingTickerStream,
    validate_read_only_scope,
)


class _AuthConnection:
    def __init__(self, returned_scope: str) -> None:
        self.returned_scope = returned_scope
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def recv_json(self) -> dict:
        if len(self.sent) == 1 and self.sent[0].get("method") == "public/auth":
            return {
                "jsonrpc": "2.0",
                "id": 9001,
                "result": {
                    "access_token": "not-a-real-token",
                    "scope": self.returned_scope,
                    "enabled_features": [],
                },
            }
        return {
            "method": "subscription",
            "params": {"channel": "book.BTC-X.raw", "data": {}},
        }

    async def close(self) -> None:
        self.closed = True


def test_credentials_are_optional_but_never_partial_or_repr_exposed() -> None:
    assert DeribitCredentials.from_environment({}) is None
    with pytest.raises(AuthenticationError):
        DeribitCredentials.from_environment({"DERIBIT_CLIENT_ID": "id"})
    credentials = DeribitCredentials("id", "do-not-print")
    assert "do-not-print" not in repr(credentials)


@pytest.mark.parametrize(
    "scope",
    [
        "connection account:read trade:none wallet:none",
        "connection mainaccount account:none trade:read wallet:read",
        "connection session:collector name:opbot expires:3600 ip:127.0.0.1",
    ],
)
def test_read_only_scope_accepts_only_known_read_authority(scope: str) -> None:
    assert validate_read_only_scope(scope)


@pytest.mark.parametrize(
    "scope",
    [
        "connection trade:read_write",
        "connection wallet:write",
        "connection mystery:read",
        "connection administrator",
    ],
)
def test_read_only_scope_fails_closed_on_write_or_unknown_authority(scope: str) -> None:
    with pytest.raises(AuthenticationError):
        validate_read_only_scope(scope)


def test_authentication_precedes_raw_subscription_and_emits_no_token() -> None:
    async def scenario() -> None:
        connection = _AuthConnection("connection account:none trade:none wallet:none")
        events: list[tuple[str, dict]] = []
        stream: ReconnectingTickerStream

        async def on_message(_message: dict) -> None:
            stream.stop()

        async def on_system(name: str, detail: dict) -> None:
            events.append((name, detail))

        authenticator = ReadOnlyWebSocketAuthenticator(
            DeribitCredentials("client", "secret")
        )
        stream = ReconnectingTickerStream(
            lambda: connection,
            on_message,
            on_system,
            heartbeat_timeout_seconds=1,
            authenticator=authenticator,
        )
        stream.lifecycle_channels.clear()
        stream.set_book_subscriptions({BookSubscription("BTC-X", "raw")})
        await stream.run()
        methods = [payload["method"] for payload in connection.sent]
        assert methods == ["public/auth", "public/set_heartbeat", "public/subscribe"]
        authentication_event = next(detail for name, detail in events if name == "AUTHENTICATED")
        assert "access_token" not in authentication_event
        assert "secret" not in repr(authentication_event)

    asyncio.run(scenario())


def test_raw_book_subscription_requires_authenticator() -> None:
    stream = ReconnectingTickerStream(lambda: None, lambda _: None, lambda _a, _b: None, 1)
    with pytest.raises(ValueError, match="require read-only authentication"):
        stream.set_book_subscriptions({BookSubscription("BTC-X", "raw")})


def test_returned_write_scope_is_rejected() -> None:
    async def scenario() -> None:
        authenticator = ReadOnlyWebSocketAuthenticator(DeribitCredentials("client", "secret"))
        with pytest.raises(AuthenticationError):
            await authenticator(_AuthConnection("connection trade:read_write"))

    asyncio.run(scenario())


def test_authentication_result_redacts_key_metadata() -> None:
    async def scenario() -> None:
        authenticator = ReadOnlyWebSocketAuthenticator(DeribitCredentials("client", "secret"))
        result = await authenticator(
            _AuthConnection("name:collector connection account:read session:runtime")
        )
        assert result["scope"] == "connection account:read"

    asyncio.run(scenario())


def test_authentication_failure_is_fatal_instead_of_reconnect_loop() -> None:
    async def scenario() -> None:
        connection = _AuthConnection("connection trade:read_write")
        events: list[str] = []

        async def on_system(name: str, _detail: dict) -> None:
            events.append(name)

        stream = ReconnectingTickerStream(
            lambda: connection,
            lambda _message: None,
            on_system,
            heartbeat_timeout_seconds=1,
            authenticator=ReadOnlyWebSocketAuthenticator(
                DeribitCredentials("client", "secret")
            ),
        )
        with pytest.raises(AuthenticationError):
            await stream.run()
        assert events == ["AUTHENTICATION_FAILED"]

    asyncio.run(scenario())
