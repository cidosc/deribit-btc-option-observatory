"""Cross-module failure closure evidence for non-actionable public data."""

from decimal import Decimal

from btc_option_scanner.analytics.backtest import (
    BacktestConfig,
    StrategyId,
    ValuationMode,
    run_backtest,
)
from btc_option_scanner.analytics.replay import EventTimeReplay
from btc_option_scanner.domain import (
    DecisionState,
    OptionType,
    PortfolioDefinition,
    PortfolioLeg,
    ValueState,
)
from btc_option_scanner.models import build_metric_bundle
from btc_option_scanner.portfolio import PortfolioPosition, value_portfolio
from btc_option_scanner.scanner import (
    Bucket,
    CohortPolicy,
    ScanInput,
    ScannerPolicy,
    scan_candidates,
)
from tests.models.factories import AS_OF, market_snapshot


def _policy() -> ScannerPolicy:
    return ScannerPolicy(
        "integration-failure-closure@1",
        "integration-universe@1",
        Decimal("90"),
        Decimal("200"),
        Decimal("1"),
        Decimal("0.1"),
        CohortPolicy(
            "integration-cohort@1",
            (Bucket("delta", Decimal("0.9"), Decimal("1.01")),),
            (Bucket("dte", Decimal("90"), Decimal("200")),),
            1,
        ),
    )


def test_stale_empty_quotes_and_no_history_fail_closed_across_services() -> None:
    stale = market_snapshot(name="BTC-STALE", stale=True)
    empty = market_snapshot(name="BTC-EMPTY", ask=None, bid=None, mark=None)
    stale_metric = build_metric_bundle(stale, as_of_utc=AS_OF, config_version="integration@1").core
    empty_metric = build_metric_bundle(empty, as_of_utc=AS_OF, config_version="integration@1").core
    results = scan_candidates(
        (
            ScanInput(OptionType.CALL, stale, stale_metric),
            ScanInput(OptionType.CALL, empty, empty_metric),
        ),
        _policy(),
    )
    assert [result.decision_state for result in results] == [
        DecisionState.DATA_STALE,
        DecisionState.NO_EXECUTABLE_ASK,
    ]
    assert not any(result.eligible for result in results)

    leg = PortfolioLeg(empty.instrument_name, Decimal("1"), empty.market_snapshot_id)
    portfolio = value_portfolio(
        PortfolioDefinition("empty-quote", Decimal("1"), (leg,), AS_OF, "portfolio@1"),
        (PortfolioPosition(leg, empty, Decimal("0.5")),),
        as_of_utc=AS_OF,
        usd_conversion_index_price_usd_per_btc=ValueState.available(Decimal("100")),
        config_version="integration@1",
    )
    assert portfolio.snapshot.nav_btc_mark.reason.code == "NO_MARK"
    assert portfolio.snapshot.nav_btc_liquidation.reason.code == "NO_EXECUTABLE_BID"

    class HoldOnly:
        strategy_id = StrategyId.A
        strategy_version = "integration-hold@1"

        def on_event(self, context):
            return ()

    no_history = run_backtest(
        EventTimeReplay(()),
        HoldOnly(),
        BacktestConfig("integration@1", Decimal("1"), ValuationMode.MARK_ONLY),
    )
    assert no_history.coverage.credibility == "NO_HISTORY"
    assert no_history.metrics.btc_cagr is None
