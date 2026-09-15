"""Capture bounded public WebSocket evidence with reconnect and retention probes."""

from __future__ import annotations

import argparse
import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from btc_option_scanner.data import SqliteRepository
from btc_option_scanner.deribit import (
    BookSequenceTracker,
    BookSubscription,
    DeribitPublicRestClient,
    PublicDataSpine,
    ReconnectingTickerStream,
    TickerSubscription,
    connect_public_websocket,
    record_raw_event,
)
from btc_option_scanner.domain import SourceTransport


def _choose_instrument(
    items: list[dict[str, Any]], index: Decimal, now: datetime
) -> dict[str, Any]:
    cutoff_ms = int((now + timedelta(days=2)).timestamp() * 1000)
    candidates = [
        item
        for item in items
        if item.get("state") == "open"
        and item.get("option_type") == "call"
        and isinstance(item.get("expiration_timestamp"), int)
        and item["expiration_timestamp"] > cutoff_ms
        and isinstance(item.get("strike"), (int, float))
    ]
    if not candidates:
        raise RuntimeError("no suitable open BTC call found")
    nearest_expiry = min(item["expiration_timestamp"] for item in candidates)
    expiry = [item for item in candidates if item["expiration_timestamp"] == nearest_expiry]
    return min(expiry, key=lambda item: abs(Decimal(str(item["strike"])) - index))


@dataclass(slots=True)
class _InjectedReconnectConnection:
    inner: Any
    disconnect_after_messages: int
    received: int = 0

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self.inner.send_json(payload)

    async def recv_json(self) -> dict[str, Any]:
        if self.received >= self.disconnect_after_messages:
            await self.inner.close()
            raise ConnectionError("INJECTED_RECONNECT_PROBE")
        message = await self.inner.recv_json()
        self.received += 1
        return message

    async def close(self) -> None:
        await self.inner.close()


def _write_fixture(directory: Path, name: str, payload: dict[str, Any]) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return str(path.resolve())


async def collect(args: argparse.Namespace) -> int:
    if args.duration_seconds <= 0 or args.forced_reconnect_after_messages < 1:
        raise ValueError("duration and forced reconnect message count must be positive")
    started = datetime.now(UTC)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    rest = DeribitPublicRestClient()
    catalog = rest.get_instruments("BTC").get("result")
    index_result = rest.call("public/get_index_price", index_name="btc_usd").get("result")
    if not isinstance(catalog, list) or not isinstance(index_result, dict):
        raise RuntimeError("public catalog or index response has an invalid envelope")
    index = Decimal(str(index_result.get("index_price")))
    spec_payload = _choose_instrument(
        [item for item in catalog if isinstance(item, dict)], index, started
    )
    instrument_name = str(spec_payload["instrument_name"])

    repository = SqliteRepository(args.database)
    spine = PublicDataSpine(repository, stale_after_seconds=30)
    spine.ingest_instrument(
        spec_payload,
        transport=SourceTransport.HTTPS_REST,
        channel="public/get_instruments",
    )
    book = BookSequenceTracker(repository, instrument_name)
    counters = {
        "ticker": 0,
        "book_snapshot": 0,
        "book_change": 0,
        "book_gap": 0,
        "lifecycle": 0,
        "other": 0,
    }
    connection_attempts = 0
    system_events: list[dict[str, Any]] = []
    captured: dict[str, dict[str, Any]] = {}
    fixture_paths: dict[str, str] = {}

    async def factory():
        nonlocal connection_attempts
        connection_attempts += 1
        connection = await connect_public_websocket()
        if connection_attempts == 1:
            return _InjectedReconnectConnection(
                connection, args.forced_reconnect_after_messages
            )
        return connection

    async def on_system_event(name: str, detail: dict[str, Any]) -> None:
        system_events.append(
            {"name": name, "detail": detail, "at_utc": datetime.now(UTC).isoformat()}
        )

    async def on_message(message: dict[str, Any]) -> None:
        params = message.get("params")
        channel = params.get("channel") if isinstance(params, dict) else None
        if not isinstance(channel, str):
            counters["other"] += 1
            return
        if channel.startswith("ticker."):
            spine.ingest_ticker(
                message,
                transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
                channel=channel,
            )
            counters["ticker"] += 1
        elif channel.startswith("book."):
            result = book.apply(message, channel=channel)
            if result.status == "SNAPSHOT_APPLIED":
                counters["book_snapshot"] += 1
                captured.setdefault("book_snapshot", deepcopy(message))
            elif result.status == "CHANGE_APPLIED":
                counters["book_change"] += 1
                captured.setdefault("book_increment", deepcopy(message))
            elif result.status == "SEQUENCE_GAP":
                counters["book_gap"] += 1
        elif channel == "instrument.state.option.BTC":
            raw = record_raw_event(
                message,
                transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
                channel_or_method=channel,
            )
            repository.save_raw_event(raw)
            counters["lifecycle"] += 1
            captured.setdefault("lifecycle", deepcopy(message))
            data = params.get("data") if isinstance(params, dict) else None
            name = data.get("instrument_name") if isinstance(data, dict) else None
            if isinstance(name, str) and name in spine.specs:
                spine.ingest_lifecycle(message, channel=channel)
        else:
            counters["other"] += 1

    stream = ReconnectingTickerStream(
        factory,
        on_message,
        on_system_event,
        heartbeat_timeout_seconds=45,
        backoff_initial_seconds=0.25,
        backoff_max_seconds=2,
    )
    stream.set_subscriptions({TickerSubscription(instrument_name, "agg2")})
    stream.set_book_subscriptions({BookSubscription(instrument_name, "100ms")})
    task = asyncio.create_task(stream.run())
    await asyncio.sleep(args.duration_seconds)
    stream.stop()
    try:
        await asyncio.wait_for(task, timeout=5)
    except TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    if "book_snapshot" in captured:
        fixture_paths["book_snapshot"] = _write_fixture(
            args.fixtures,
            f"{stamp}_mainnet_ws_book_100ms_snapshot_{instrument_name}.json",
            captured["book_snapshot"],
        )
    if "book_increment" in captured:
        fixture_paths["book_increment"] = _write_fixture(
            args.fixtures,
            f"{stamp}_mainnet_ws_book_100ms_increment_{instrument_name}.json",
            captured["book_increment"],
        )
        gap = deepcopy(captured["book_increment"])
        data = gap["params"]["data"]
        data["prev_change_id"] = int(data["prev_change_id"]) + 1
        gap["_fixture_provenance"] = (
            "SYNTHETIC_GAP: copied from the named live increment; only prev_change_id was +1"
        )
        fixture_paths["book_gap_injected"] = _write_fixture(
            args.fixtures,
            f"{stamp}_synthetic_book_sequence_gap_{instrument_name}.json",
            gap,
        )
        gap_probe = BookSequenceTracker(repository, instrument_name)
        snapshot_probe = gap_probe.apply(
            captured["book_snapshot"], channel=f"book.{instrument_name}.100ms"
        )
        gap_result = gap_probe.apply(gap, channel=f"book.{instrument_name}.100ms")
        if snapshot_probe.status == "SNAPSHOT_APPLIED" and gap_result.status == "SEQUENCE_GAP":
            counters["book_gap"] += 1
    if "lifecycle" in captured:
        fixture_paths["lifecycle"] = _write_fixture(
            args.fixtures,
            f"{stamp}_mainnet_ws_lifecycle_option_BTC.json",
            captured["lifecycle"],
        )
    else:
        lifecycle_shape = {
            "jsonrpc": "2.0",
            "method": "subscription",
            "params": {
                "channel": "instrument.state.option.BTC",
                "data": {
                    "timestamp": int(datetime.now(UTC).timestamp() * 1000),
                    "state": spec_payload["state"],
                    "instrument_name": instrument_name,
                },
            },
            "_fixture_provenance": (
                "SYNTHETIC_SHAPE_FROM_LIVE_CATALOG: not a received lifecycle notification"
            ),
        }
        spine.ingest_lifecycle(lifecycle_shape)
        fixture_paths["lifecycle_shape_injected"] = _write_fixture(
            args.fixtures,
            f"{stamp}_synthetic_lifecycle_shape_{instrument_name}.json",
            lifecycle_shape,
        )

    counts_before_close = repository.counts()
    repository.close()
    reopened = SqliteRepository(args.database)
    counts_after_reopen = reopened.counts()
    replayable_raw_events = sum(1 for _ in reopened.raw_events())
    reopened.close()
    retention_ok = counts_before_close == counts_after_reopen and (
        replayable_raw_events == counts_after_reopen["raw_market_events"]
    )
    connected_count = sum(event["name"] == "CONNECTED" for event in system_events)
    injected_disconnect_seen = any(
        event["name"] == "DISCONNECTED"
        and "INJECTED_RECONNECT_PROBE" in str(event["detail"].get("error"))
        for event in system_events
    )
    passed = all(
        (
            counters["ticker"] > 0,
            counters["book_snapshot"] >= 2,
            counters["book_change"] > 0,
            connected_count >= 2,
            injected_disconnect_seen,
            retention_ok,
            "book_gap_injected" in fixture_paths,
            counters["book_gap"] == 1,
        )
    )
    report = {
        "status": "PASS" if passed else "FAIL",
        "public_only": True,
        "websocket_url": "wss://www.deribit.com/ws/api/v2",
        "instrument_name": instrument_name,
        "started_at_utc": started.isoformat(),
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "requested_duration_seconds": args.duration_seconds,
        "forced_reconnect_after_messages": args.forced_reconnect_after_messages,
        "connection_attempts": connection_attempts,
        "connected_events": connected_count,
        "injected_disconnect_seen": injected_disconnect_seen,
        "message_counts": counters,
        "lifecycle_observation": "LIVE" if counters["lifecycle"] else "NOT_OBSERVED",
        "lifecycle_fixture_provenance": (
            "LIVE"
            if counters["lifecycle"]
            else "SYNTHETIC_SHAPE_FROM_LIVE_CATALOG"
        ),
        "retention_ok": retention_ok,
        "database_counts": counts_after_reopen,
        "replayable_raw_events": replayable_raw_events,
        "fixtures": fixture_paths,
        "system_events": system_events,
        "database": str(args.database.resolve()),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, default=90)
    parser.add_argument("--forced-reconnect-after-messages", type=int, default=30)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    return asyncio.run(collect(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
