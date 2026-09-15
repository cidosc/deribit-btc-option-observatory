"""Conditional price × IV × time revaluation for a hypothetical portfolio.

It is explicitly a model reprice, not a forecast and not an executable
liquidation estimate.  Bid/ask are deliberately absent from its equations.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from btc_option_scanner.domain import (
    Availability,
    PortfolioDefinition,
    PortfolioLeg,
    StressRun,
    StressScenarioPoint,
    ValueState,
)
from btc_option_scanner.domain.market import MarketSnapshot
from btc_option_scanner.domain.primitives import require_utc
from btc_option_scanner.models import (
    BLACK76_MODEL_VERSION,
    Black76Inputs,
    LocalGreeks,
    black76_call,
)

STRESS_APPLICATION_VERSION = "conditional-stress@1.0.0"


class ForwardPolicy(str, Enum):
    PRESERVE_CARRY_RATE = "preserve_carry_rate"
    PRESERVE_BASIS_RATIO = "preserve_basis_ratio"
    EXPLICIT_FORWARD = "explicit_forward"
    AT_EXPIRY_DELIVERY = "at_expiry_delivery"


class SmilePolicy(str, Enum):
    STICKY_STRIKE_PARALLEL = "sticky_strike_parallel"
    FLAT_IV = "flat_iv"


def _id(*parts: str) -> str:
    return "stress-" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def _unavailable(code: str, message: str) -> ValueState[Decimal]:
    return ValueState.unavailable(code, message)


def _value(value: ValueState[Decimal]) -> Decimal | None:
    return value.value if value.availability is Availability.AVAILABLE else None


@dataclass(frozen=True, slots=True)
class StressScenario:
    """All pricing assumptions are explicit and persisted with the result."""

    scenario_id: str
    index_return_decimal: Decimal
    elapsed_days: Decimal
    absolute_iv_vol_point_shock: Decimal
    risk_free_rate_annual_decimal: Decimal
    forward_policy: ForwardPolicy
    smile_policy: SmilePolicy
    rate_policy_version: str
    forward_policy_version: str
    smile_policy_version: str
    explicit_forward_usd_per_btc: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("scenario_id must not be empty")
        if not self.index_return_decimal.is_finite() or self.index_return_decimal <= -1:
            raise ValueError("index_return_decimal must be finite and greater than -1")
        if not self.elapsed_days.is_finite() or self.elapsed_days < 0:
            raise ValueError("elapsed_days must be finite and non-negative")
        if not self.absolute_iv_vol_point_shock.is_finite():
            raise ValueError("absolute_iv_vol_point_shock must be finite")
        if not self.risk_free_rate_annual_decimal.is_finite():
            raise ValueError("risk_free_rate_annual_decimal must be finite")
        for value in (
            self.rate_policy_version,
            self.forward_policy_version,
            self.smile_policy_version,
        ):
            if not value.strip():
                raise ValueError("stress policy versions must not be empty")
        if self.forward_policy is ForwardPolicy.EXPLICIT_FORWARD and (
            self.explicit_forward_usd_per_btc is None
            or not self.explicit_forward_usd_per_btc.is_finite()
            or self.explicit_forward_usd_per_btc <= 0
        ):
            raise ValueError("explicit_forward policy needs a positive explicit forward")

    @property
    def domain_point(self) -> StressScenarioPoint:
        return StressScenarioPoint(
            self.index_return_decimal,
            self.elapsed_days,
            self.absolute_iv_vol_point_shock,
            self.rate_policy_version,
            self.forward_policy_version,
            self.smile_policy_version,
        )


@dataclass(frozen=True, slots=True)
class StressPosition:
    leg: PortfolioLeg
    market: MarketSnapshot

    def __post_init__(self) -> None:
        if self.leg.instrument_name != self.market.instrument_name:
            raise ValueError("stress leg and market instrument_name must match")


@dataclass(frozen=True, slots=True)
class StressLegResult:
    instrument_name: str
    market_snapshot_id: str
    quantity_contracts: Decimal
    stressed_option_value_btc: ValueState[Decimal]
    stressed_option_value_usd: ValueState[Decimal]
    model_greeks: LocalGreeks | None
    expired_or_crossed_expiry: bool
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StressResult:
    scenario: StressScenario
    stressed_index_price_usd_per_btc: Decimal
    leg_results: tuple[StressLegResult, ...]
    stressed_option_value_btc: ValueState[Decimal]
    stressed_option_value_usd: ValueState[Decimal]
    stressed_nav_btc: ValueState[Decimal]
    stressed_nav_usd: ValueState[Decimal]
    model_standard_delta: ValueState[Decimal]
    model_gamma_per_forward_usd: ValueState[Decimal]
    model_theta_usd_per_day: ValueState[Decimal]
    model_vega_usd_per_vol_point: ValueState[Decimal]
    conditional_label: str = "MODEL_CONDITIONAL_NOT_EXECUTABLE_REPRICE"


@dataclass(frozen=True, slots=True)
class StressApplicationRun:
    stress_run: StressRun
    application_version: str
    results: tuple[StressResult, ...]
    notes: tuple[str, ...]
    as_of_utc: datetime


def _forward(
    market: MarketSnapshot,
    scenario: StressScenario,
    stressed_index: Decimal,
    remaining_years: Decimal,
) -> Decimal:
    if scenario.forward_policy is ForwardPolicy.EXPLICIT_FORWARD:
        assert scenario.explicit_forward_usd_per_btc is not None
        return scenario.explicit_forward_usd_per_btc
    if scenario.forward_policy is ForwardPolicy.AT_EXPIRY_DELIVERY or remaining_years == 0:
        return stressed_index
    if scenario.forward_policy is ForwardPolicy.PRESERVE_CARRY_RATE:
        # Decimal.exp keeps the documented policy deterministic without a float conversion.
        return stressed_index * (scenario.risk_free_rate_annual_decimal * remaining_years).exp()
    original_index = _value(market.index_price_usd_per_btc)
    original_forward = _value(market.underlying_price_usd_per_btc)
    if original_index is None or original_forward is None or original_index <= 0:
        raise ValueError("preserve_basis_ratio needs positive original index and underlying")
    return stressed_index * original_forward / original_index


def _sum(results: list[StressLegResult], name: str) -> ValueState[Decimal]:
    total = Decimal("0")
    for result in results:
        value = getattr(result, name)
        if result.quantity_contracts == 0:
            continue
        if value.value is None:
            return _unavailable(
                "STRESS_INPUT_UNAVAILABLE", f"{name} unavailable for a non-zero leg"
            )
        total += value.value
    return ValueState.available(total)


def _sum_greek(results: list[StressLegResult], field: str) -> ValueState[Decimal]:
    total = Decimal("0")
    for result in results:
        if result.quantity_contracts == 0:
            continue
        if result.model_greeks is None:
            return _unavailable(
                "STRESS_INPUT_UNAVAILABLE", "model greeks unavailable for a non-zero leg"
            )
        total += result.quantity_contracts * getattr(result.model_greeks, field)
    return ValueState.available(total)


def _stress_one(
    position: StressPosition,
    scenario: StressScenario,
    *,
    as_of_utc: datetime,
    stressed_index: Decimal,
) -> StressLegResult:
    market, quantity = position.market, position.leg.quantity_contracts
    iv = _value(market.iv.mark_annual_decimal)
    if iv is None:
        unavailable = _unavailable("IV_NOT_AVAILABLE", "stress reprice requires mark IV")
        return StressLegResult(
            market.instrument_name,
            market.market_snapshot_id,
            quantity,
            unavailable,
            unavailable,
            None,
            False,
            ("No IV fallback is permitted.",),
        )
    expiry = datetime.fromtimestamp(market.expiration_timestamp_ms / 1000, tz=as_of_utc.tzinfo)
    remaining_days = max(
        Decimal("0"),
        Decimal(str((expiry - as_of_utc).total_seconds())) / Decimal("86400")
        - scenario.elapsed_days,
    )
    remaining_years = remaining_days / Decimal("365")
    shocked_iv = max(Decimal("0"), iv + scenario.absolute_iv_vol_point_shock / Decimal("100"))
    try:
        forward = _forward(market, scenario, stressed_index, remaining_years)
        result = black76_call(
            Black76Inputs(
                forward_usd_per_btc=forward,
                strike_usd_per_btc=market.strike_usd_per_btc,
                time_to_expiry_years=remaining_years,
                iv_annual_decimal=shocked_iv,
                risk_free_rate_annual_decimal=scenario.risk_free_rate_annual_decimal,
                index_price_usd_per_btc=stressed_index,
                contract_multiplier_btc=market.contract_size_btc,
                settlement_or_reference_usd_per_btc=stressed_index
                if remaining_years == 0
                else None,
            )
        )
    except ValueError as exc:
        unavailable = _unavailable("STRESS_INPUT_UNAVAILABLE", str(exc))
        return StressLegResult(
            market.instrument_name,
            market.market_snapshot_id,
            quantity,
            unavailable,
            unavailable,
            None,
            False,
            ("Conditional reprice failed closed.",),
        )
    return StressLegResult(
        market.instrument_name,
        market.market_snapshot_id,
        quantity,
        ValueState.available(quantity * result.price_btc_per_contract_at_conversion_reference),
        # USD reporting is derived from the BTC result and this scenario's
        # single shocked index, so reserve and option NAV share one numeraire.
        ValueState.available(
            quantity * result.price_btc_per_contract_at_conversion_reference * stressed_index
        ),
        result.greeks,
        result.expired,
        (
            "Black-76 conditional reprice; it is not a forecast or executable option price.",
            f"smile_policy={scenario.smile_policy.value}; IV shock is absolute vol points.",
        ),
    )


def stress_portfolio(
    definition: PortfolioDefinition,
    positions: tuple[StressPosition, ...],
    *,
    portfolio_snapshot_id: str,
    as_of_utc: datetime,
    scenarios: tuple[StressScenario, ...],
    config_version: str,
    max_snapshot_skew_ms: int = 0,
) -> StressApplicationRun:
    """Run explicit conditional scenarios for all portfolio legs."""
    if not scenarios:
        raise ValueError("at least one stress scenario is required")
    require_utc(as_of_utc, "as_of_utc")
    if max_snapshot_skew_ms < 0:
        raise ValueError("max_snapshot_skew_ms cannot be negative")
    if tuple(item.leg for item in positions) != definition.legs:
        raise ValueError("stress positions must exactly bind PortfolioDefinition legs in order")
    exchange_times = [position.market.source.exchange_timestamp_ms for position in positions]
    known_times = [timestamp for timestamp in exchange_times if timestamp is not None]
    if len(exchange_times) > 1 and (
        len(known_times) != len(exchange_times)
        or max(known_times) - min(known_times) > max_snapshot_skew_ms
    ):
        raise ValueError("stress market snapshots exceed the configured exchange-time window")
    if not portfolio_snapshot_id.strip() or not config_version.strip():
        raise ValueError("portfolio_snapshot_id and config_version are required")
    outputs: list[StressResult] = []
    for scenario in scenarios:
        reference = positions[0].market if positions else None
        if reference is None or _value(reference.index_price_usd_per_btc) is None:
            raise ValueError("stress needs a positive index reference for a non-zero portfolio")
        index = _value(reference.index_price_usd_per_btc)
        assert index is not None and index > 0
        stressed_index = index * (Decimal("1") + scenario.index_return_decimal)
        legs = [
            _stress_one(item, scenario, as_of_utc=as_of_utc, stressed_index=stressed_index)
            for item in positions
        ]
        option_btc = _sum(legs, "stressed_option_value_btc")
        option_usd = _sum(legs, "stressed_option_value_usd")
        nav_btc = (
            ValueState.available(definition.reserve_btc + option_btc.value)
            if option_btc.value is not None
            else _unavailable(
                "STRESS_INPUT_UNAVAILABLE", "complete stressed option value unavailable"
            )
        )
        nav_usd = (
            ValueState.available(nav_btc.value * stressed_index)
            if nav_btc.value is not None
            else _unavailable(
                "STRESS_INPUT_UNAVAILABLE", "complete stressed option value unavailable"
            )
        )
        outputs.append(
            StressResult(
                scenario,
                stressed_index,
                tuple(legs),
                option_btc,
                option_usd,
                nav_btc,
                nav_usd,
                _sum_greek(legs, "delta_per_forward_usd"),
                _sum_greek(legs, "gamma_per_forward_usd"),
                _sum_greek(legs, "theta_usd_per_day"),
                _sum_greek(legs, "vega_usd_per_vol_point"),
            )
        )
    run = StressRun(
        _id(portfolio_snapshot_id, as_of_utc.isoformat(), config_version),
        portfolio_snapshot_id,
        tuple(item.domain_point for item in scenarios),
        BLACK76_MODEL_VERSION,
        config_version,
        tuple(_id(output.scenario.scenario_id, portfolio_snapshot_id) for output in outputs),
    )
    return StressApplicationRun(
        run,
        STRESS_APPLICATION_VERSION,
        tuple(outputs),
        (
            "Stress is a conditional revaluation, not a prediction.",
            "All stressed USD values use the shocked index; BTC reserve is unchanged.",
        ),
        as_of_utc,
    )
