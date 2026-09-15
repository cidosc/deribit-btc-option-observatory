from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from btc_option_scanner.deribit import (
    InstrumentLifecycle,
    normalize_instrument,
    normalize_ticker,
    record_raw_event,
)
from btc_option_scanner.domain import SourceTransport


def _fixture(name: str) -> dict:
    root = Path(__file__).parents[1] / "fixtures" / "deribit"
    return json.loads((root / name).read_text(encoding="utf-8"))


def test_open_ticker_maps_canonical_quotes_and_iv() -> None:
    instrument = _fixture("20260914T113335Z_mainnet_ws_get_instrument_BTC-25DEC26-20000-C.json")
    ticker = _fixture("20260914T113336Z_mainnet_ws_ticker_agg2_BTC-25DEC26-20000-C.json")
    time = datetime(2026, 9, 14, tzinfo=UTC)
    raw_spec = record_raw_event(
        instrument,
        transport=SourceTransport.WEBSOCKET_RPC,
        channel_or_method="public/get_instrument",
        observed_at=time,
    )
    spec = normalize_instrument(instrument, raw_spec)
    raw_ticker = record_raw_event(
        ticker,
        transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
        channel_or_method="ticker.BTC-25DEC26-20000-C.agg2",
        observed_at=time,
    )
    snapshot = normalize_ticker(ticker, raw_ticker, spec)
    assert snapshot.bid.price_btc_per_contract.value is not None
    assert snapshot.ask.price_btc_per_contract.value is not None
    assert snapshot.iv.mark_annual_decimal.value == snapshot.iv.mark_raw.value / 100
    assert snapshot.greeks.gamma_standard_per_usd.value is None
    assert snapshot.source.exchange_timestamp_ms == 1789388015506


def test_delivered_zero_quotes_are_not_executable() -> None:
    instrument = _fixture(
        "20260914T113731Z_mainnet_https_get_instrument_delivered_BTC-14SEP26-68000-C.json"
    )
    ticker = _fixture("20260914T113730Z_mainnet_https_ticker_delivered_BTC-14SEP26-68000-C.json")
    time = datetime(2026, 9, 14, tzinfo=UTC)
    raw_spec = record_raw_event(
        instrument,
        transport=SourceTransport.HTTPS_REST,
        channel_or_method="public/get_instrument",
        observed_at=time,
    )
    spec = normalize_instrument(instrument, raw_spec)
    raw_ticker = record_raw_event(
        ticker,
        transport=SourceTransport.HTTPS_REST,
        channel_or_method="public/ticker",
        observed_at=time,
    )
    snapshot = normalize_ticker(ticker, raw_ticker, spec)
    codes = {flag.code.value for flag in snapshot.quality_flags}
    assert snapshot.actionable is False
    assert snapshot.bid.price_btc_per_contract.value is None
    assert snapshot.ask.price_btc_per_contract.value is None
    assert snapshot.estimated_delivery_price_usd_per_btc.value is None
    assert {"ZERO_QUOTE_SENTINEL", "DELIVERED_FINAL_STATE", "NON_OPEN_STATE"} <= codes


def test_lifecycle_state_update_removes_terminal_instrument_from_active_set() -> None:
    lifecycle = InstrumentLifecycle()
    opened = _fixture(
        "20260914T113335Z_mainnet_ws_get_instrument_BTC-25DEC26-20000-C.json"
    )
    original = lifecycle.apply(opened, channel="public/get_instrument")
    sparse_state = {
        "jsonrpc": "2.0",
        "method": "subscription",
        "params": {
            "channel": "instrument.state.option.BTC",
            "data": {
                "timestamp": 1790000000000,
                "state": "delivered",
                "instrument_name": original.instrument_name,
            },
        },
    }
    updated = lifecycle.apply(sparse_state, channel="instrument.state.option.BTC")
    assert updated.state.value == "DELIVERED"
    assert updated.raw_event_id != original.raw_event_id
    assert original.instrument_name not in lifecycle.active_names()
    assert original.instrument_name in lifecycle.terminal
