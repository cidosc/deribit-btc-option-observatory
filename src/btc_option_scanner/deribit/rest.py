"""Explicitly rate-limited public HTTPS JSON-RPC client; no private methods exist here."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PUBLIC_METHODS = frozenset(
    {
        "public/get_instruments",
        "public/get_instrument",
        "public/ticker",
        "public/get_order_book",
        "public/get_index_price",
    }
)


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    requests_per_second: float = 5.0
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.requests_per_second <= 0 or self.timeout_seconds <= 0:
            raise ValueError("rate limit and timeout must be positive")


class DeribitPublicRestClient:
    """A small injectable client using only Deribit's public endpoint namespace."""

    def __init__(
        self,
        base_url: str = "https://www.deribit.com/api/v2",
        config: RateLimitConfig | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.config = config or RateLimitConfig()
        self._lock = Lock()
        self._next_request_at = 0.0

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        if method not in PUBLIC_METHODS:
            raise ValueError("only explicitly whitelisted public Deribit methods are permitted")
        with self._lock:
            delay = self._next_request_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._next_request_at = time.monotonic() + 1 / self.config.requests_per_second
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url}/{method}" + (f"?{query}" if query else "")
        request = Request(
            url, headers={"Accept": "application/json", "User-Agent": "btc-option-scanner/0.1"}
        )
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:  # nosec B310: fixed HTTPS public API
                body = response.read()
        except OSError as exc:
            raise ConnectionError(f"public Deribit request failed: {method}: {exc}") from exc
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Deribit returned invalid JSON for {method}") from exc
        if not isinstance(decoded, dict):
            raise ValueError("Deribit response envelope must be an object")
        if "error" in decoded:
            raise RuntimeError(f"Deribit public error for {method}: {decoded['error']}")
        return decoded

    def get_instruments(self, currency: str = "BTC", expired: bool = False) -> dict[str, Any]:
        return self.call(
            "public/get_instruments", currency=currency, kind="option", expired=str(expired).lower()
        )

    def get_instrument(self, instrument_name: str) -> dict[str, Any]:
        return self.call("public/get_instrument", instrument_name=instrument_name)

    def ticker(self, instrument_name: str) -> dict[str, Any]:
        return self.call("public/ticker", instrument_name=instrument_name)
