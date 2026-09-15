"""Optional Deribit authentication with a fail-closed read-only scope policy."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class AuthenticationError(RuntimeError):
    """Authentication failed or returned a scope that is unsafe for this project."""


_PERMISSION_FAMILIES = {
    "account",
    "block_rfq",
    "block_trade",
    "custody",
    "trade",
    "wallet",
}
_SAFE_PERMISSION_LEVELS = {"none", "read"}
_SAFE_BARE_SCOPES = {"connection", "mainaccount"}
_SAFE_METADATA_PREFIXES = {"expires", "ip", "name", "session"}


def validate_read_only_scope(scope: str) -> tuple[str, ...]:
    """Return normalized scope tokens, rejecting write or unknown authority."""
    if not isinstance(scope, str) or not scope.strip():
        raise AuthenticationError("Deribit authentication scope is empty")
    tokens = tuple(scope.split())
    for token in tokens:
        if token in _SAFE_BARE_SCOPES:
            continue
        if ":" not in token:
            raise AuthenticationError(f"unknown Deribit scope token: {token}")
        family, value = token.split(":", 1)
        if family in _PERMISSION_FAMILIES:
            if value not in _SAFE_PERMISSION_LEVELS:
                raise AuthenticationError(f"write or unknown Deribit permission refused: {token}")
            continue
        if family in _SAFE_METADATA_PREFIXES and value:
            continue
        raise AuthenticationError(f"unknown Deribit scope token: {token}")
    return tokens


@dataclass(frozen=True, slots=True)
class DeribitCredentials:
    client_id: str
    client_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.client_id.strip() or not self.client_secret.strip():
            raise AuthenticationError("Deribit client id and secret must both be non-empty")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> DeribitCredentials | None:
        source = environment if environment is not None else os.environ
        client_id = source.get("DERIBIT_CLIENT_ID", "").strip()
        client_secret = source.get("DERIBIT_CLIENT_SECRET", "").strip()
        if not client_id and not client_secret:
            return None
        if not client_id or not client_secret:
            raise AuthenticationError(
                "DERIBIT_CLIENT_ID and DERIBIT_CLIENT_SECRET must be set together"
            )
        return cls(client_id, client_secret)


@dataclass(frozen=True, slots=True)
class ReadOnlyWebSocketAuthenticator:
    """Authenticate a WebSocket connection without exposing an access token."""

    credentials: DeribitCredentials = field(repr=False)
    requested_scope: str = "connection account:none trade:none wallet:none"
    timeout_seconds: float = 10.0
    request_id: int = 9001

    def __post_init__(self) -> None:
        validate_read_only_scope(self.requested_scope)
        if self.timeout_seconds <= 0:
            raise ValueError("authentication timeout must be positive")

    async def __call__(self, connection: Any) -> dict[str, Any]:
        await connection.send_json(
            {
                "jsonrpc": "2.0",
                "id": self.request_id,
                "method": "public/auth",
                "params": {
                    "grant_type": "client_credentials",
                    "client_id": self.credentials.client_id,
                    "client_secret": self.credentials.client_secret,
                    "scope": self.requested_scope,
                },
            }
        )
        try:
            response = await asyncio.wait_for(
                connection.recv_json(), timeout=self.timeout_seconds
            )
        except TimeoutError as exc:
            raise AuthenticationError("Deribit authentication timed out") from exc
        if response.get("id") != self.request_id:
            raise AuthenticationError("unexpected response while authenticating to Deribit")
        if "error" in response:
            raise AuthenticationError("Deribit rejected the supplied credentials or scope")
        result = response.get("result")
        if not isinstance(result, dict):
            raise AuthenticationError("Deribit authentication result is missing")
        returned_scope = result.get("scope")
        if not isinstance(returned_scope, str):
            raise AuthenticationError("Deribit authentication returned no effective scope")
        returned_tokens = validate_read_only_scope(returned_scope)
        if not isinstance(result.get("access_token"), str):
            raise AuthenticationError("Deribit authentication returned no access token")
        return {
            "scope": " ".join(
                token
                for token in returned_tokens
                if token.split(":", 1)[0] not in _SAFE_METADATA_PREFIXES
            ),
            "enabled_features": tuple(result.get("enabled_features") or ()),
        }
