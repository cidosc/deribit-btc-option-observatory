from decimal import Decimal

import pytest
from models.factories import AS_OF, market_snapshot

from btc_option_scanner.domain import PortfolioDefinition, PortfolioLeg
from btc_option_scanner.strategy import (
    ForwardPolicy,
    SmilePolicy,
    StressPosition,
    StressScenario,
    stress_portfolio,
)


def test_stress_crossing_expiry_is_conditional_and_keeps_btc_reserve() -> None:
    market = market_snapshot()
    leg = PortfolioLeg(market.instrument_name, Decimal("1"), market.market_snapshot_id)
    definition = PortfolioDefinition("portfolio-1", Decimal("2"), (leg,), AS_OF, "portfolio@1")
    scenario = StressScenario(
        "cross-expiry",
        Decimal("-0.1"),
        Decimal("200"),
        Decimal("-20"),
        Decimal("0"),
        ForwardPolicy.AT_EXPIRY_DELIVERY,
        SmilePolicy.STICKY_STRIKE_PARALLEL,
        "rate@1",
        "forward@1",
        "smile@1",
    )

    output = stress_portfolio(
        definition,
        (StressPosition(leg, market),),
        portfolio_snapshot_id="portfolio-snapshot-1",
        as_of_utc=AS_OF,
        scenarios=(scenario,),
        config_version="test",
    )

    result = output.results[0]
    assert result.conditional_label == "MODEL_CONDITIONAL_NOT_EXECUTABLE_REPRICE"
    assert result.leg_results[0].expired_or_crossed_expiry is True
    assert result.stressed_nav_btc.value is not None
    assert (
        result.stressed_nav_usd.value
        == result.stressed_nav_btc.value * result.stressed_index_price_usd_per_btc
    )


def test_stress_rejects_mixed_market_snapshot_times() -> None:
    first_market = market_snapshot(name="BTC-A", exchange_timestamp_ms=1000)
    second_market = market_snapshot(name="BTC-B", exchange_timestamp_ms=1001)
    first = PortfolioLeg("BTC-A", Decimal("1"), first_market.market_snapshot_id)
    second = PortfolioLeg("BTC-B", Decimal("1"), second_market.market_snapshot_id)
    definition = PortfolioDefinition(
        "portfolio-mixed-time", Decimal("1"), (first, second), AS_OF, "portfolio@1"
    )
    scenario = StressScenario(
        "mixed-time",
        Decimal("0"),
        Decimal("1"),
        Decimal("0"),
        Decimal("0"),
        ForwardPolicy.PRESERVE_BASIS_RATIO,
        SmilePolicy.FLAT_IV,
        "rate@1",
        "forward@1",
        "smile@1",
    )
    with pytest.raises(ValueError, match="exchange-time window"):
        stress_portfolio(
            definition,
            (StressPosition(first, first_market), StressPosition(second, second_market)),
            portfolio_snapshot_id="portfolio-snapshot-1",
            as_of_utc=AS_OF,
            scenarios=(scenario,),
            config_version="test",
        )
