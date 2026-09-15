from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from btc_option_scanner.analytics.replay import EventTimeReplay, ReplayEvent
from btc_option_scanner.domain import (
    InstrumentSpec,
    InstrumentState,
    OptionType,
    SourceMetadata,
    SourceTransport,
    ValueState,
)
from tests.models.factories import market_snapshot

START = datetime(2026, 1, 1, tzinfo=UTC)


def _spec(name: str, state: InstrumentState = InstrumentState.OPEN) -> InstrumentSpec:
    return InstrumentSpec(
        name,
        OptionType.CALL,
        state,
        Decimal("20000"),
        1798761600000,
        Decimal("1"),
        Decimal("0.1"),
        Decimal("0.0001"),
        "test-spec@1",
        f"raw-{name}",
    )


def _market(name: str, at: datetime, index: str, iv: str = "0.5"):
    base = market_snapshot(name=name)
    source = SourceMetadata(SourceTransport.REPLAY, "test", at, at, int(at.timestamp() * 1000))
    return replace(
        base,
        source=source,
        index_price_usd_per_btc=ValueState.available(Decimal(index)),
        iv=replace(base.iv, mark_annual_decimal=ValueState.available(Decimal(iv))),
    )


def test_future_catalog_market_high_and_iv_are_not_visible() -> None:
    name, future = "BTC-OLD", "BTC-FUTURE"
    old = _market(name, START + timedelta(days=1), "100", "0.4")
    future_market = _market(future, START + timedelta(days=2), "999", "0.9")
    replay = EventTimeReplay(
        (
            ReplayEvent.catalog("catalog-old", START, _spec(name)),
            ReplayEvent.market("market-old", old),
            ReplayEvent.catalog("catalog-future", START + timedelta(days=2), _spec(future)),
            ReplayEvent.market("market-future", future_market),
        )
    )

    state = replay.state_at(START + timedelta(days=1, hours=1))
    assert state.instrument(future) is None
    assert state.market(future) is None
    assert state.rolling_high_usd(name, window=timedelta(days=10)) == Decimal("100")
    assert state.market(name).iv.mark_annual_decimal.value == Decimal("0.4")


def test_rolling_high_uses_only_observed_history_not_future_high() -> None:
    name = "BTC-OLD"
    first = _market(name, START + timedelta(days=1), "100")
    future = _market(name, START + timedelta(days=3), "200")
    replay = EventTimeReplay(
        (
            ReplayEvent.catalog("catalog", START, _spec(name)),
            ReplayEvent.market("first", first),
            ReplayEvent.market("future", future),
        )
    )
    first_state = next(state for event, state in replay.states() if event.event_id == "first")
    assert first_state.rolling_high_usd(name, window=timedelta(days=30)) == Decimal("100")
    assert replay.state_at(START + timedelta(days=3)).rolling_high_usd(
        name, window=timedelta(days=1)
    ) == Decimal("200")


def test_market_without_previously_observed_catalogue_fails_closed() -> None:
    market = _market("BTC-UNKNOWN", START, "100")
    replay = EventTimeReplay((ReplayEvent.market("market", market),))
    with pytest.raises(ValueError, match="no visible catalogue"):
        tuple(replay.states())
