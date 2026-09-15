from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from btc_option_scanner.collector import (
    CollectorSettings,
    healthcheck,
    select_collection_universe,
    ticker_persistence_due,
)
from btc_option_scanner.domain import InstrumentState, OptionType


def _spec(name: str, expiry: datetime, strike: str, *, state=InstrumentState.OPEN):
    return SimpleNamespace(
        instrument_name=name,
        expiration_timestamp_ms=int(expiry.timestamp() * 1000),
        strike_usd_per_btc=Decimal(strike),
        option_type=OptionType.CALL,
        state=state,
    )


def test_settings_fail_before_exceeding_channel_limit_or_using_unknown_host() -> None:
    with pytest.raises(ValueError, match="500-channel"):
        CollectorSettings(max_instruments=480, max_book_instruments=20)
    with pytest.raises(ValueError, match="approved Deribit"):
        CollectorSettings(websocket_url="wss://example.invalid/ws")


def test_collection_profiles_make_high_volume_features_opt_in() -> None:
    lite = CollectorSettings.from_environment({})
    assert lite.collection_profile == "scanner-lite"
    assert lite.max_instruments == 60
    assert lite.max_book_instruments == 0
    assert lite.ticker_persist_interval_seconds == 60

    evidence = CollectorSettings.from_environment(
        {"BTC_OPTION_COLLECTION_PROFILE": "evidence"}
    )
    assert evidence.max_instruments == 180
    assert evidence.max_book_instruments == 20
    assert evidence.ticker_persist_interval_seconds == 0

    customized = CollectorSettings.from_environment(
        {
            "BTC_OPTION_COLLECTION_PROFILE": "scanner-lite",
            "BTC_OPTION_MAX_INSTRUMENTS": "10",
            "BTC_OPTION_TICKER_PERSIST_INTERVAL_SECONDS": "60",
        }
    )
    assert customized.max_instruments == 10
    assert customized.ticker_persist_interval_seconds == 60


def test_ticker_persistence_sampling_keeps_first_and_due_observations() -> None:
    last = {"BTC-X": 100.0}
    assert ticker_persistence_due(
        "BTC-NEW", now_monotonic=101, last_persisted=last, interval_seconds=20
    )
    assert not ticker_persistence_due(
        "BTC-X", now_monotonic=119.9, last_persisted=last, interval_seconds=20
    )
    assert ticker_persistence_due(
        "BTC-X", now_monotonic=120, last_persisted=last, interval_seconds=20
    )
    assert ticker_persistence_due(
        "BTC-X", now_monotonic=100.1, last_persisted=last, interval_seconds=0
    )


def test_collection_universe_is_open_call_bounded_and_deterministic() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    specs = {
        f"BTC-{index}": _spec(
            f"BTC-{index}", now + timedelta(days=150 + index), str(50_000 + index * 1_000)
        )
        for index in range(6)
    }
    specs["CLOSED"] = _spec(
        "CLOSED",
        now + timedelta(days=160),
        "50000",
        state=InstrumentState.DELIVERED,
    )
    result = select_collection_universe(
        specs,
        now=now,
        min_dte_days=Decimal("120"),
        max_dte_days=Decimal("240"),
        buffer_days=0,
        limit=3,
    )
    assert result == ("BTC-0", "BTC-2", "BTC-5")


def test_healthcheck_rejects_stale_disconnected_or_fatal_status(tmp_path) -> None:
    status = tmp_path / "status.json"
    payload = {
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "connected": True,
        "fatal_error": None,
        "seconds_since_message": 1,
    }
    status.write_text(json.dumps(payload), encoding="utf-8")
    assert healthcheck(status, max_age_seconds=10)[0] is True
    payload["connected"] = False
    status.write_text(json.dumps(payload), encoding="utf-8")
    healthy, result = healthcheck(status, max_age_seconds=10)
    assert healthy is False
    assert "disconnected" in " ".join(result["reasons"])
