from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btc_option_scanner.analytics.backtest import (
    BacktestConfig,
    FeeModel,
    IntentAction,
    StrategyId,
    StrategyIntent,
    ValuationMode,
    run_backtest,
)
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
NAME = "BTC-25DEC26-20000-C"


def _spec() -> InstrumentSpec:
    return InstrumentSpec(
        NAME,
        OptionType.CALL,
        InstrumentState.OPEN,
        Decimal("20000"),
        1798761600000,
        Decimal("1"),
        Decimal("0.1"),
        Decimal("0.0001"),
        "test-spec@1",
        "raw-spec",
    )


def _market(
    at: datetime,
    *,
    ask: Decimal | None = Decimal("1"),
    bid: Decimal | None = Decimal("0.9"),
    mark: Decimal | None = Decimal("0.95"),
):
    base = market_snapshot(name=NAME, ask=ask, bid=bid, mark=mark)
    source = SourceMetadata(SourceTransport.REPLAY, "test", at, at, int(at.timestamp() * 1000))
    return replace(
        base, source=source, index_price_usd_per_btc=ValueState.available(Decimal("100"))
    )


class BuyOnce:
    strategy_id = StrategyId.A
    strategy_version = "test-buy@1"

    def on_event(self, context):
        if context.event.event_id == "first":
            return (StrategyIntent(IntentAction.BUY, NAME, Decimal("1"), "test"),)
        return ()


class SellOnce:
    strategy_id = StrategyId.B
    strategy_version = "test-sell@1"

    def on_event(self, context):
        if context.event.event_id == "first":
            return (StrategyIntent(IntentAction.SELL, NAME, Decimal("1"), "test"),)
        return ()


def _replay(*markets):
    return EventTimeReplay(
        (ReplayEvent.catalog("catalog", START, _spec()),)
        + tuple(
            ReplayEvent.market("first" if i == 0 else f"market-{i}", market)
            for i, market in enumerate(markets)
        )
    )


def test_mark_only_can_run_but_is_never_labelled_executable() -> None:
    result = run_backtest(
        _replay(_market(START + timedelta(days=1))),
        BuyOnce(),
        BacktestConfig("test-config@1", Decimal("2"), ValuationMode.MARK_ONLY),
    )
    assert result.is_executable is False
    assert result.coverage.credibility == "MARK_ONLY_RESEARCH"
    assert result.trades[0].executed


def test_executable_mode_fails_closed_for_missing_ask_or_unverified_fee() -> None:
    missing_ask = run_backtest(
        _replay(_market(START + timedelta(days=1), ask=None)),
        BuyOnce(),
        BacktestConfig(
            "test-config@1",
            Decimal("2"),
            ValuationMode.EXECUTABLE,
            FeeModel("fee@1", Decimal("0"), True, "evidence"),
        ),
    )
    assert missing_ask.trades[0].failure_code == "NO_EXECUTABLE_ASK"
    assert missing_ask.is_executable is False
    unverified = run_backtest(
        _replay(_market(START + timedelta(days=1))),
        BuyOnce(),
        BacktestConfig("test-config@1", Decimal("2"), ValuationMode.EXECUTABLE),
    )
    assert unverified.trades[0].failure_code == "FEE_UNVERIFIED"

    missing_bid = run_backtest(
        _replay(_market(START + timedelta(days=1), bid=None)),
        SellOnce(),
        BacktestConfig(
            "test-config@1",
            Decimal("2"),
            ValuationMode.EXECUTABLE,
            FeeModel("fee@1", Decimal("0"), True, "evidence"),
        ),
    )
    assert missing_bid.trades[0].failure_code == "NO_EXECUTABLE_BID"


def test_expired_catalog_boundary_rejects_new_entry() -> None:
    expired = replace(_spec(), expiration_timestamp_ms=int(START.timestamp() * 1000))
    replay = EventTimeReplay(
        (
            ReplayEvent.catalog("catalog", START, expired),
            ReplayEvent.market("first", _market(START + timedelta(days=1))),
        )
    )
    result = run_backtest(
        replay,
        BuyOnce(),
        BacktestConfig(
            "test-config@1",
            Decimal("2"),
            ValuationMode.EXECUTABLE,
            FeeModel("fee@1", Decimal("0"), True, "evidence"),
        ),
    )
    assert result.trades[0].failure_code == "EXPIRED_INSTRUMENT_AT_EVENT_TIME"


def test_identical_event_sequence_has_identical_accounting_and_sparse_history_is_limited() -> None:
    tape = _replay(_market(START + timedelta(days=1)), _market(START + timedelta(days=2)))
    config = BacktestConfig(
        "test-config@1",
        Decimal("2"),
        ValuationMode.EXECUTABLE,
        FeeModel("fee@1", Decimal("0.01"), True, "evidence"),
    )
    first, second = run_backtest(tape, BuyOnce(), config), run_backtest(tape, BuyOnce(), config)
    assert first == second
    assert first.attribution.turnover_btc == Decimal("1")
    assert first.attribution.spread_cost_btc == Decimal("0.05")
    assert first.attribution.fees_btc == Decimal("0.01")
    assert "USD_HISTORY_INSUFFICIENT" not in first.metrics.unavailable_reasons


def test_empty_history_reports_coverage_without_fabricating_metrics() -> None:
    result = run_backtest(
        EventTimeReplay(()),
        BuyOnce(),
        BacktestConfig("test-config@1", Decimal("2"), ValuationMode.MARK_ONLY),
    )
    assert result.coverage.credibility == "NO_HISTORY"
    assert result.metrics.btc_cagr is None
    assert result.metrics.unavailable_reasons == (
        "BTC_HISTORY_INSUFFICIENT",
        "USD_HISTORY_INSUFFICIENT",
    )
