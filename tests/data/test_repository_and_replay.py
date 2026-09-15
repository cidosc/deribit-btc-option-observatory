from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from btc_option_scanner.data import SqliteRepository
from btc_option_scanner.deribit import EventReplayer, PublicDataSpine
from btc_option_scanner.domain import SourceTransport


def _fixture(name: str) -> dict:
    return json.loads(
        (Path(__file__).parents[1] / "fixtures" / "deribit" / name).read_text(encoding="utf-8")
    )


def test_repository_is_idempotent_and_replay_does_not_duplicate_business_rows() -> None:
    repo = SqliteRepository()
    spine = PublicDataSpine(repo, stale_after_seconds=3600)
    instrument = _fixture("20260914T113335Z_mainnet_ws_get_instrument_BTC-25DEC26-20000-C.json")
    ticker = _fixture("20260914T113336Z_mainnet_ws_ticker_agg2_BTC-25DEC26-20000-C.json")
    observed = datetime.fromtimestamp(1789388015.506, UTC)
    spine.ingest_instrument(
        instrument,
        transport=SourceTransport.HTTPS_REST,
        channel="public/get_instrument",
        observed_at=observed,
    )
    first = spine.ingest_ticker(
        ticker,
        transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
        channel="ticker.BTC-25DEC26-20000-C.agg2",
        observed_at=observed,
    )
    assert (
        spine.ingest_ticker(
            ticker,
            transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
            channel="ticker.BTC-25DEC26-20000-C.agg2",
            observed_at=observed,
        ).market_snapshot_id
        == first.market_snapshot_id
    )
    assert repo.counts()["market_snapshots"] == 1

    replay = EventReplayer(repo.raw_events())
    replay.replay(lambda _event, _payload: None)
    assert repo.counts()["raw_market_events"] == 2
    repo.close()


def test_out_of_order_ticker_is_data_only_and_raw_is_retained() -> None:
    repo = SqliteRepository()
    spine = PublicDataSpine(repo, stale_after_seconds=3600)
    instrument = _fixture("20260914T113335Z_mainnet_ws_get_instrument_BTC-25DEC26-20000-C.json")
    ticker = _fixture("20260914T113336Z_mainnet_ws_ticker_agg2_BTC-25DEC26-20000-C.json")
    observed = datetime.fromtimestamp(1789388015.506, UTC)
    spine.ingest_instrument(
        instrument,
        transport=SourceTransport.HTTPS_REST,
        channel="public/get_instrument",
        observed_at=observed,
    )
    spine.ingest_ticker(
        ticker,
        transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
        channel="ticker.BTC-25DEC26-20000-C.agg2",
        observed_at=observed,
    )
    ticker["params"]["data"]["timestamp"] -= 1
    older = spine.ingest_ticker(
        ticker,
        transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
        channel="ticker.BTC-25DEC26-20000-C.agg2",
        observed_at=observed,
    )
    assert older.actionable is False
    assert "TIMESTAMP_OUT_OF_ORDER" in {flag.code.value for flag in older.quality_flags}
    assert repo.counts()["raw_market_events"] == 3
    repo.close()


def test_sparse_lifecycle_event_is_raw_first_and_versions_catalog_state() -> None:
    repo = SqliteRepository()
    spine = PublicDataSpine(repo, stale_after_seconds=3600)
    instrument = _fixture("20260914T113335Z_mainnet_ws_get_instrument_BTC-25DEC26-20000-C.json")
    observed = datetime.fromtimestamp(1789388015.506, UTC)
    original = spine.ingest_instrument(
        instrument,
        transport=SourceTransport.HTTPS_REST,
        channel="public/get_instrument",
        observed_at=observed,
    )
    lifecycle = {
        "jsonrpc": "2.0",
        "method": "subscription",
        "params": {
            "channel": "instrument.state.option.BTC",
            "data": {
                "timestamp": 1789388016506,
                "state": "delivered",
                "instrument_name": original.instrument_name,
            },
        },
    }
    updated = spine.ingest_lifecycle(lifecycle, observed_at=observed)
    assert updated.state.value == "DELIVERED"
    assert updated.instrument_spec_version != original.instrument_spec_version
    assert repo.counts()["raw_market_events"] == 2
    assert repo.counts()["instruments"] == 2
    repo.close()
