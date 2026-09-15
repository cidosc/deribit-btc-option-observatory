from decimal import Decimal

import pytest
from models.factories import AS_OF, market_snapshot

from btc_option_scanner.domain import PortfolioDefinition, PortfolioLeg, ValueState
from btc_option_scanner.portfolio import PortfolioPosition, value_portfolio


def _definition(*legs: PortfolioLeg) -> PortfolioDefinition:
    return PortfolioDefinition("portfolio-1", Decimal("1"), legs, AS_OF, "portfolio@1")


def test_multi_long_nav_cost_basis_and_greeks_are_separate() -> None:
    first_market = market_snapshot(
        name="BTC-25DEC26-20000-C", mark=Decimal("0.5"), bid=Decimal("0.4")
    )
    second_market = market_snapshot(
        name="BTC-25DEC26-30000-C", mark=Decimal("0.2"), bid=Decimal("0.1")
    )
    first = PortfolioLeg(
        first_market.instrument_name, Decimal("2"), first_market.market_snapshot_id
    )
    second = PortfolioLeg(
        second_market.instrument_name, Decimal("3"), second_market.market_snapshot_id
    )
    output = value_portfolio(
        _definition(first, second),
        (
            PortfolioPosition(first, first_market, Decimal("0.9")),
            PortfolioPosition(second, second_market, Decimal("0.8")),
        ),
        as_of_utc=AS_OF,
        usd_conversion_index_price_usd_per_btc=ValueState.available(Decimal("100000")),
        config_version="test-config",
    )

    assert output.snapshot.nav_btc_mark.value == Decimal("2.6")
    assert output.snapshot.nav_btc_liquidation.value == Decimal("2.1")
    assert output.snapshot.nav_usd_mark.value == Decimal("260000.0")
    assert output.premium_paid_btc_total == Decimal("1.7")
    assert output.greeks.standard_delta.value == Decimal("4.99075")
    assert output.greeks.gamma_standard_per_usd.value is None


def test_missing_bid_does_not_create_partial_liquidation_nav() -> None:
    market = market_snapshot(bid=None, mark=Decimal("0.5"))
    leg = PortfolioLeg(market.instrument_name, Decimal("1"), market.market_snapshot_id)
    output = value_portfolio(
        _definition(leg),
        (PortfolioPosition(leg, market, Decimal("0.6")),),
        as_of_utc=AS_OF,
        usd_conversion_index_price_usd_per_btc=ValueState.available(Decimal("100")),
        config_version="test-config",
    )

    assert output.snapshot.nav_btc_mark.value == Decimal("1.5")
    assert output.snapshot.nav_btc_liquidation.value is None
    assert output.snapshot.nav_btc_liquidation.reason.code == "NO_EXECUTABLE_BID"


def test_zero_holding_requires_no_quote_and_values_reserve() -> None:
    market = market_snapshot(bid=None, ask=None, mark=None)
    leg = PortfolioLeg(market.instrument_name, Decimal("0"), market.market_snapshot_id)
    output = value_portfolio(
        _definition(leg),
        (PortfolioPosition(leg, market, Decimal("0")),),
        as_of_utc=AS_OF,
        usd_conversion_index_price_usd_per_btc=ValueState.available(Decimal("100")),
        config_version="test-config",
    )

    assert output.snapshot.nav_btc_mark.value == Decimal("1")
    assert output.snapshot.nav_btc_liquidation.value == Decimal("1")
    assert output.option_mark_value_btc.value == Decimal("0")


def test_portfolio_rejects_market_snapshots_outside_one_time_window() -> None:
    first_market = market_snapshot(name="BTC-A", exchange_timestamp_ms=1000)
    second_market = market_snapshot(name="BTC-B", exchange_timestamp_ms=1001)
    first = PortfolioLeg("BTC-A", Decimal("1"), first_market.market_snapshot_id)
    second = PortfolioLeg("BTC-B", Decimal("1"), second_market.market_snapshot_id)
    with pytest.raises(ValueError, match="exchange-time window"):
        value_portfolio(
            _definition(first, second),
            (
                PortfolioPosition(first, first_market, Decimal("0.1")),
                PortfolioPosition(second, second_market, Decimal("0.1")),
            ),
            as_of_utc=AS_OF,
            usd_conversion_index_price_usd_per_btc=ValueState.available(Decimal("100")),
            config_version="test-config",
        )
