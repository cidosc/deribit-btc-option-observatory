"""Shared offline scenario fixtures for live refresh/recommendation tests."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from typing import Any

_NOW_MS = int(time.time() * 1000)


def instrument_payload(name: str, *, strike: int, dte_days: float) -> dict[str, Any]:
    return {
        "instrument_name": name,
        "instrument_id": abs(hash(name)) % 100000,
        "option_type": "call",
        "strike": float(strike),
        "expiration_timestamp": _NOW_MS + int(dte_days * 86_400_000),
        "creation_timestamp": _NOW_MS - 30 * 86_400_000,
        "contract_size": 1.0,
        "min_trade_amount": 0.1,
        "price_index": "btc_usd",
        "base_currency": "BTC",
        "counter_currency": "USD",
        "tick_size": 0.0005,
        "tick_size_steps": [],
        "state": "open",
        "is_active": True,
        "instrument_type": "option",
    }


def ticker_result_payload(
    name: str,
    *,
    timestamp_ms: int,
    index_price: float = 76_800.0,
    delta: float = 0.85,
    gamma: float = 0.0,
    vega: float = 12.5,
    theta: float = -2.1,
    rho: float = 21.0,
    mark_price: float = 0.052,
    best_bid_price: float = 0.0485,
    best_ask_price: float = 0.05,
    best_bid_amount: float = 30.0,
    best_ask_amount: float = 25.0,
    open_interest: float = 10.0,
    state: str = "open",
) -> dict[str, Any]:
    result = {
        "timestamp": timestamp_ms,
        "state": state,
        "instrument_name": name,
        "index_price": index_price,
        "underlying_price": 77_000.0,
        "underlying_index": "BTC-26JUN27",
        "mark_price": mark_price,
        "last_price": best_ask_price,
        "open_interest": open_interest,
        "best_bid_price": best_bid_price,
        "best_bid_amount": best_bid_amount,
        "best_ask_price": best_ask_price,
        "best_ask_amount": best_ask_amount,
        "bid_iv": 90.0,
        "ask_iv": 95.0,
        "mark_iv": 92.0,
        "interest_rate": 0.0,
        "estimated_delivery_price": index_price,
        "greeks": {
            "delta": delta,
            "gamma": gamma,
            "vega": vega,
            "theta": theta,
            "rho": rho,
        },
        "stats": {
            "high": 0.051,
            "low": 0.048,
            "price_change": 0.001,
            "volume": 3.0,
            "volume_usd": 900.0,
        },
    }
    return result


def rest_ticker_envelope(
    name: str, *, timestamp_ms: int, **overrides: Any
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 7,
        "result": ticker_result_payload(name, timestamp_ms=timestamp_ms, **overrides),
    }


def ws_ticker_message(
    name: str, *, timestamp_ms: int, **overrides: Any
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "subscription",
        "params": {
            "channel": f"ticker.{name}.agg2",
            "data": ticker_result_payload(name, timestamp_ms=timestamp_ms, **overrides),
        },
    }


class ScriptedWebSocketConnection:
    """Minimal AsyncJsonConnection double driven by a script and drip schedule."""

    def __init__(
        self,
        script: list[dict[str, Any]],
        *,
        drip_schedule: list[tuple[float, dict[str, Any]]] | None = None,
    ) -> None:
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._closed = False
        self.sent: list[dict[str, Any]] = []
        self._drip_task: asyncio.Task[None] | None = None
        self._drip_schedule = list(drip_schedule or [])
        self._recv_failure_task: asyncio.Task[None] | None = None
        self._recv_failure_delay: float | None = None
        for message in script:
            self._queue.put_nowait(message)

    def schedule_recv_failure(self, delay_seconds: float) -> None:
        self._recv_failure_delay = delay_seconds

    def start(self) -> None:
        if self._drip_task is None and self._drip_schedule:
            self._drip_task = asyncio.create_task(self._drip())
        if self._recv_failure_task is None and self._recv_failure_delay is not None:
            self._recv_failure_task = asyncio.create_task(self._fail_recv_later())

    async def _fail_recv_later(self) -> None:
        await asyncio.sleep(self._recv_failure_delay or 0.0)
        self._queue.put_nowait(None)

    async def _drip(self) -> None:
        start = time.monotonic()
        for delay, message in self._drip_schedule:
            target = start + delay
            pause = target - time.monotonic()
            if pause > 0:
                await asyncio.sleep(pause)
            if self._closed:
                return
            self._queue.put_nowait(message)

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def recv_json(self) -> dict[str, Any]:
        if self._closed:
            raise ConnectionError("scripted connection closed")
        message = await self._queue.get()
        if message is None:
            raise ConnectionError("scripted connection closed")
        return message

    async def close(self) -> None:
        self._closed = True
        if self._drip_task is not None:
            self._drip_task.cancel()
        self._queue.put_nowait(None)


class FakeRefreshRestClient:
    """Injectable stand-in for ``DeribitPublicRestClient`` with scripted behavior."""

    def __init__(
        self,
        catalog_items: list[dict[str, Any]],
        ticker_builder,
        *,
        fail: bool = False,
        fail_after_calls: int | None = None,
        delay_seconds: float = 0.0,
        base_url: str = "https://www.deribit.com/api/v2",
    ) -> None:
        self.catalog_items = list(catalog_items)
        self.ticker_builder = ticker_builder
        self.fail = fail
        self.fail_after_calls = fail_after_calls
        self.delay_seconds = delay_seconds
        self.base_url = base_url
        self.calls: list[str] = []

    def get_instruments(self, currency: str = "BTC", expired: bool = False) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1, "result": [dict(item) for item in self.catalog_items]}

    def ticker(self, name: str) -> dict[str, Any]:
        self.calls.append(name)
        if self.fail_after_calls is not None and len(self.calls) > self.fail_after_calls:
            raise ConnectionError("injected refresh failure after configured calls")
        if self.fail:
            raise ConnectionError("injected refresh failure")
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        return self.ticker_builder(name)


def database_counts(path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        tables = (
            "raw_market_events",
            "instruments",
            "market_snapshots",
            "derived_entities",
            "system_events",
        )
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }
    finally:
        connection.close()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))
