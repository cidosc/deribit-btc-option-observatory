import json
from decimal import Decimal
from pathlib import Path

import pytest

from btc_option_scanner.models.pricing import (
    Black76Inputs,
    black76_call,
    deribit_observed_diagnostic,
)


def inputs(**overrides: Decimal) -> Black76Inputs:
    base = dict(
        forward_usd_per_btc=Decimal("80000"),
        strike_usd_per_btc=Decimal("70000"),
        time_to_expiry_years=Decimal("0.5"),
        iv_annual_decimal=Decimal("0.6"),
        risk_free_rate_annual_decimal=Decimal("0.02"),
        index_price_usd_per_btc=Decimal("79000"),
    )
    base.update(overrides)
    return Black76Inputs(**base)


def test_black76_respects_call_bounds_and_deep_moneyness() -> None:
    result = black76_call(inputs())
    lower = Decimal("0.5") * Decimal(str(__import__("math").exp(-0.02 * 0.5))) * Decimal("10000")
    assert result.price_usd_per_contract >= lower
    assert result.price_usd_per_contract <= Decimal("80000")
    deep_itm = black76_call(inputs(strike_usd_per_btc=Decimal("1")))
    deep_otm = black76_call(inputs(strike_usd_per_btc=Decimal("10000000")))
    assert deep_itm.greeks.delta_per_forward_usd > Decimal("0.99")
    assert deep_otm.greeks.delta_per_forward_usd < Decimal("0.001")
    assert deep_otm.price_usd_per_contract < deep_itm.price_usd_per_contract


def test_zero_vol_and_expiry_are_defined_without_division_by_zero() -> None:
    zero_vol = black76_call(inputs(iv_annual_decimal=Decimal("0")))
    assert zero_vol.price_usd_per_contract > 0
    assert zero_vol.greeks.gamma_per_forward_usd == 0
    expired = black76_call(
        inputs(
            time_to_expiry_years=Decimal("0"), settlement_or_reference_usd_per_btc=Decimal("90000")
        )
    )
    assert expired.expired is True
    assert expired.price_usd_per_contract == Decimal("20000.0")
    with pytest.raises(ValueError, match="settlement"):
        black76_call(inputs(time_to_expiry_years=Decimal("0")))


def test_fixture_backed_diagnostic_keeps_local_and_exchange_domains_distinct() -> None:
    fixture_root = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "deribit"
    ticker = json.loads(
        (fixture_root / "20260914T113335Z_mainnet_ws_ticker_BTC-25DEC26-20000-C.json").read_text()
    )["result"]
    instrument = json.loads(
        (
            fixture_root / "20260914T113335Z_mainnet_ws_get_instrument_BTC-25DEC26-20000-C.json"
        ).read_text()
    )["result"]
    time_to_expiry = Decimal(instrument["expiration_timestamp"] - ticker["timestamp"]) / Decimal(
        365 * 24 * 60 * 60 * 1000
    )
    diagnostic = deribit_observed_diagnostic(
        underlying_price_usd_per_btc=Decimal(str(ticker["underlying_price"])),
        strike_usd_per_btc=Decimal(str(instrument["strike"])),
        time_to_expiry_years=time_to_expiry,
        mark_iv_annual_decimal=Decimal(str(ticker["mark_iv"])) / Decimal("100"),
        interest_rate_annual_decimal=Decimal(str(ticker["interest_rate"])),
    )
    # These remain local diagnostic values, not replacement Deribit fields.
    assert abs(
        diagnostic.greeks.delta_per_forward_usd - Decimal(str(ticker["greeks"]["delta"]))
    ) < Decimal("0.00001")
    assert abs(
        diagnostic.greeks.vega_usd_per_vol_point - Decimal(str(ticker["greeks"]["vega"]))
    ) < Decimal("0.001")
    assert abs(
        diagnostic.greeks.theta_usd_per_day - Decimal(str(ticker["greeks"]["theta"]))
    ) < Decimal("0.001")
    assert abs(
        diagnostic.greeks.rho_usd_per_rate_point - Decimal(str(ticker["greeks"]["rho"]))
    ) < Decimal("0.001")
