"""Numerically stable Black-76 Call diagnostics with explicit units and conversion basis.

The generic function reports USD PV and a *reporting* BTC equivalent using the
explicit index supplied by the caller.  It is not an executable price and it
does not replace a Deribit mark.  ``deribit_observed_diagnostic`` is the
separately named, fixture-backed regression adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import erf, exp, log, pi, sqrt

BLACK76_MODEL_VERSION = "black76-reporting@1.0.0"
DERIBIT_DIAGNOSTIC_MODEL_VERSION = "deribit-bs-observed@1.0.0-diagnostic"


def _finite_positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a finite positive Decimal")


def _finite_nonnegative(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Decimal")


def _decimal(value: float) -> Decimal:
    # String conversion avoids importing a binary float approximation as Decimal.
    return Decimal(str(value))


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _normal_pdf(value: float) -> float:
    return exp(-0.5 * value * value) / sqrt(2.0 * pi)


@dataclass(frozen=True, slots=True)
class Black76Inputs:
    """Black-76 inputs, all per contract except where a unit is in the name."""

    forward_usd_per_btc: Decimal
    strike_usd_per_btc: Decimal
    time_to_expiry_years: Decimal
    iv_annual_decimal: Decimal
    risk_free_rate_annual_decimal: Decimal
    index_price_usd_per_btc: Decimal
    contract_multiplier_btc: Decimal = Decimal("1")
    settlement_or_reference_usd_per_btc: Decimal | None = None

    def __post_init__(self) -> None:
        _finite_positive(self.forward_usd_per_btc, "forward_usd_per_btc")
        _finite_positive(self.strike_usd_per_btc, "strike_usd_per_btc")
        _finite_nonnegative(self.time_to_expiry_years, "time_to_expiry_years")
        _finite_nonnegative(self.iv_annual_decimal, "iv_annual_decimal")
        if (
            not isinstance(self.risk_free_rate_annual_decimal, Decimal)
            or not self.risk_free_rate_annual_decimal.is_finite()
        ):
            raise ValueError("risk_free_rate_annual_decimal must be a finite Decimal")
        _finite_positive(self.index_price_usd_per_btc, "index_price_usd_per_btc")
        _finite_positive(self.contract_multiplier_btc, "contract_multiplier_btc")
        if self.settlement_or_reference_usd_per_btc is not None:
            _finite_positive(
                self.settlement_or_reference_usd_per_btc,
                "settlement_or_reference_usd_per_btc",
            )


@dataclass(frozen=True, slots=True)
class LocalGreeks:
    """Model Greeks with stated bases; never overwrite exchange Greek fields."""

    delta_per_forward_usd: Decimal
    gamma_per_forward_usd: Decimal
    vega_usd_per_vol_point: Decimal
    theta_usd_per_day: Decimal
    rho_usd_per_rate_point: Decimal


@dataclass(frozen=True, slots=True)
class Black76Result:
    model_version: str
    price_usd_per_contract: Decimal
    price_btc_per_contract_at_conversion_reference: Decimal
    conversion_reference_usd_per_btc: Decimal
    conversion_reference_label: str
    greeks: LocalGreeks
    expired: bool


def black76_call(inputs: Black76Inputs) -> Black76Result:
    """Price a European Call and its analytic Black-76 Greeks.

    At expiry the supplied settlement/reference is required; it is deliberately
    not silently substituted by forward or index.  At zero volatility before
    expiry this uses the deterministic discounted-forward limit.
    """
    f = float(inputs.forward_usd_per_btc)
    k = float(inputs.strike_usd_per_btc)
    t = float(inputs.time_to_expiry_years)
    sigma = float(inputs.iv_annual_decimal)
    rate = float(inputs.risk_free_rate_annual_decimal)
    index = float(inputs.index_price_usd_per_btc)
    multiplier = float(inputs.contract_multiplier_btc)

    if t == 0.0:
        if inputs.settlement_or_reference_usd_per_btc is None:
            raise ValueError("settlement_or_reference_usd_per_btc is required at expiry")
        settlement = float(inputs.settlement_or_reference_usd_per_btc)
        price_usd = multiplier * max(settlement - k, 0.0)
        zero = Decimal("0")
        return Black76Result(
            BLACK76_MODEL_VERSION,
            _decimal(price_usd),
            _decimal(price_usd / index),
            inputs.index_price_usd_per_btc,
            "index_price_usd_per_btc",
            LocalGreeks(zero, zero, zero, zero, zero),
            True,
        )

    discount = exp(-rate * t)
    if sigma == 0.0:
        price_usd = multiplier * discount * max(f - k, 0.0)
        # The derivative at F == K is discontinuous; zero is an explicit,
        # deterministic convention for the point-value limit.
        delta = multiplier * discount if f > k else 0.0
        zero = Decimal("0")
        return Black76Result(
            BLACK76_MODEL_VERSION,
            _decimal(price_usd),
            _decimal(price_usd / index),
            inputs.index_price_usd_per_btc,
            "index_price_usd_per_btc",
            LocalGreeks(_decimal(delta), zero, zero, zero, zero),
            False,
        )

    sqrt_t = sqrt(t)
    vol_sqrt_t = sigma * sqrt_t
    d1 = (log(f / k) + 0.5 * sigma * sigma * t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    n1, n2, pdf1 = _normal_cdf(d1), _normal_cdf(d2), _normal_pdf(d1)
    price_usd = multiplier * discount * (f * n1 - k * n2)
    delta = multiplier * discount * n1
    gamma = multiplier * discount * pdf1 / (f * vol_sqrt_t)
    vega = multiplier * discount * f * pdf1 * sqrt_t / 100.0
    # dV/d(calendar time) under Black-76 with F held fixed.
    theta = (
        -multiplier * discount * f * pdf1 * sigma / (2.0 * sqrt_t)
        + multiplier * rate * discount * (f * n1 - k * n2)
    ) / 365.0
    rho = -multiplier * t * discount * (f * n1 - k * n2) / 100.0
    return Black76Result(
        BLACK76_MODEL_VERSION,
        _decimal(price_usd),
        _decimal(price_usd / index),
        inputs.index_price_usd_per_btc,
        "index_price_usd_per_btc",
        LocalGreeks(
            _decimal(delta), _decimal(gamma), _decimal(vega), _decimal(theta), _decimal(rho)
        ),
        False,
    )


def deribit_observed_diagnostic(
    *,
    underlying_price_usd_per_btc: Decimal,
    strike_usd_per_btc: Decimal,
    time_to_expiry_years: Decimal,
    mark_iv_annual_decimal: Decimal,
    interest_rate_annual_decimal: Decimal,
    contract_multiplier_btc: Decimal = Decimal("1"),
) -> Black76Result:
    """Fixture-backed BS-equivalent diagnostic, not a production BTC convention.

    The observed 2026-09-14 fixture regresses when the supplied Deribit
    underlying is used as both forward and conversion reference.  Keeping this
    conversion in a separately named adapter prevents it being mistaken for
    index-based reporting or an executable model price.
    """
    # This is the Black-Scholes equivalent of Black-76 with F=S*exp(rT).
    # Its *spot* Greek basis is intentional: it is the limited, observed
    # Deribit regression convention, not the generic function's forward basis.
    s = float(underlying_price_usd_per_btc)
    k = float(strike_usd_per_btc)
    t = float(time_to_expiry_years)
    sigma = float(mark_iv_annual_decimal)
    rate = float(interest_rate_annual_decimal)
    multiplier = float(contract_multiplier_btc)
    if t <= 0.0 or sigma <= 0.0:
        # Reuse the deterministic generic limits; the fixture diagnostic is
        # meaningful only for positive time and IV, but its boundary is defined.
        result = black76_call(
            Black76Inputs(
                forward_usd_per_btc=_decimal(s * exp(rate * t)),
                strike_usd_per_btc=strike_usd_per_btc,
                time_to_expiry_years=time_to_expiry_years,
                iv_annual_decimal=mark_iv_annual_decimal,
                risk_free_rate_annual_decimal=interest_rate_annual_decimal,
                index_price_usd_per_btc=underlying_price_usd_per_btc,
                contract_multiplier_btc=contract_multiplier_btc,
            )
        )
    else:
        sqrt_t = sqrt(t)
        vol_sqrt_t = sigma * sqrt_t
        d1 = (log(s / k) + (rate + 0.5 * sigma * sigma) * t) / vol_sqrt_t
        d2 = d1 - vol_sqrt_t
        n1, n2, pdf1 = _normal_cdf(d1), _normal_cdf(d2), _normal_pdf(d1)
        discount = exp(-rate * t)
        price = multiplier * (s * n1 - k * discount * n2)
        result = Black76Result(
            BLACK76_MODEL_VERSION,
            _decimal(price),
            _decimal(price / s),
            underlying_price_usd_per_btc,
            "underlying_price_usd_per_btc",
            LocalGreeks(
                _decimal(multiplier * n1),
                _decimal(multiplier * pdf1 / (s * vol_sqrt_t)),
                _decimal(multiplier * s * pdf1 * sqrt_t / 100.0),
                _decimal(
                    (
                        -multiplier * s * pdf1 * sigma / (2.0 * sqrt_t)
                        - multiplier * rate * k * discount * n2
                    )
                    / 365.0
                ),
                _decimal(multiplier * k * t * discount * n2 / 100.0),
            ),
            False,
        )
    return Black76Result(
        DERIBIT_DIAGNOSTIC_MODEL_VERSION,
        result.price_usd_per_contract,
        result.price_btc_per_contract_at_conversion_reference,
        underlying_price_usd_per_btc,
        "underlying_price_usd_per_btc",
        result.greeks,
        result.expired,
    )
