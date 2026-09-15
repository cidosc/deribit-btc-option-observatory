"""Pure portfolio valuation with explicit price sides and availability.

This module deliberately does not fetch a "latest" quote.  Every position
brings the immutable market snapshot used to value it, allowing a caller to
persist and replay the exact calculation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from btc_option_scanner.domain import (
    Availability,
    PortfolioDefinition,
    PortfolioLeg,
    PortfolioSnapshot,
    QualityFlag,
    ValueState,
)
from btc_option_scanner.domain.market import MarketSnapshot

PORTFOLIO_APPLICATION_VERSION = "portfolio-valuation@1.0.0"


def _unavailable(code: str, message: str) -> ValueState[Decimal]:
    return ValueState.unavailable(code, message)


def _id(*parts: str) -> str:
    return "portfolio-" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def _available(value: ValueState[Decimal]) -> Decimal | None:
    if value.availability is Availability.AVAILABLE and value.value is not None:
        return value.value
    return None


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    """A long leg bound to its valuation snapshot and immutable entry cost.

    ``premium_paid_btc`` is the historic total cash cost of this lot.  It is
    intentionally absent from all current-NAV formulas.
    """

    leg: PortfolioLeg
    market: MarketSnapshot
    premium_paid_btc: Decimal
    entry_market_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        if self.leg.instrument_name != self.market.instrument_name:
            raise ValueError("position leg and market instrument_name must match")
        if not self.premium_paid_btc.is_finite() or self.premium_paid_btc < 0:
            raise ValueError("premium_paid_btc must be a finite non-negative Decimal")
        if (
            self.leg.market_snapshot_id is not None
            and self.leg.market_snapshot_id != self.market.market_snapshot_id
        ):
            raise ValueError("leg market_snapshot_id must match its bound market snapshot")


@dataclass(frozen=True, slots=True)
class PositionValue:
    instrument_name: str
    market_snapshot_id: str
    quantity_contracts: Decimal
    premium_paid_btc: Decimal
    mark_value_btc: ValueState[Decimal]
    liquidation_value_btc: ValueState[Decimal]


@dataclass(frozen=True, slots=True)
class PortfolioGreeks:
    """Exchange Greek sums; each field keeps its own documented unit."""

    standard_delta: ValueState[Decimal]
    gamma_standard_per_usd: ValueState[Decimal]
    theta_usd_per_effective_horizon: ValueState[Decimal]
    vega_usd_per_vol_point: ValueState[Decimal]
    deribit_gamma_raw: ValueState[Decimal]


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    """Application output wrapping the frozen PortfolioSnapshot DTO."""

    snapshot: PortfolioSnapshot
    application_version: str
    positions: tuple[PositionValue, ...]
    premium_paid_btc_total: Decimal
    option_mark_value_btc: ValueState[Decimal]
    option_liquidation_value_btc: ValueState[Decimal]
    greeks: PortfolioGreeks
    notes: tuple[str, ...]


def _sum_values(
    values: list[tuple[Decimal, ValueState[Decimal]]], *, code: str, label: str
) -> ValueState[Decimal]:
    total = Decimal("0")
    for quantity, value in values:
        if quantity == 0:
            continue
        raw = _available(value)
        if raw is None:
            return _unavailable(code, f"{label} is unavailable for a non-zero portfolio leg")
        total += quantity * raw
    return ValueState.available(total)


def _sum_greek(
    positions: tuple[PortfolioPosition, ...], attribute: str, *, code: str, label: str
) -> ValueState[Decimal]:
    values = [
        (position.leg.quantity_contracts, getattr(position.market.greeks, attribute))
        for position in positions
    ]
    return _sum_values(values, code=code, label=label)


def _sum_raw_gamma(positions: tuple[PortfolioPosition, ...]) -> ValueState[Decimal]:
    total = Decimal("0")
    for position in positions:
        if position.leg.quantity_contracts == 0:
            continue
        raw = position.market.greeks.gamma_raw.value
        if raw is None:
            return _unavailable("DERIBIT_GAMMA_RAW_UNAVAILABLE", "raw gamma is unavailable")
        total += position.leg.quantity_contracts * raw
    return ValueState.available(total)


def value_portfolio(
    definition: PortfolioDefinition,
    positions: tuple[PortfolioPosition, ...],
    *,
    as_of_utc: datetime,
    usd_conversion_index_price_usd_per_btc: ValueState[Decimal],
    config_version: str,
    max_snapshot_skew_ms: int = 0,
) -> PortfolioValuation:
    """Value a portfolio without mixing mark and executable liquidation sides.

    The supplied USD conversion reference is intentionally one explicit value
    for the whole portfolio; a leg's forward/underlying must never revalue the
    BTC reserve.  A non-zero leg with no mark (or no bid) makes the respective
    complete NAV unavailable rather than valuing a partial portfolio.
    """
    if not config_version.strip():
        raise ValueError("config_version must not be empty")
    if max_snapshot_skew_ms < 0:
        raise ValueError("max_snapshot_skew_ms cannot be negative")
    if tuple(item.leg for item in positions) != definition.legs:
        raise ValueError("positions must exactly bind PortfolioDefinition legs in order")
    exchange_times = [position.market.source.exchange_timestamp_ms for position in positions]
    known_times = [timestamp for timestamp in exchange_times if timestamp is not None]
    if len(exchange_times) > 1 and (
        len(known_times) != len(exchange_times)
        or max(known_times) - min(known_times) > max_snapshot_skew_ms
    ):
        raise ValueError("portfolio market snapshots exceed the configured exchange-time window")

    position_values: list[PositionValue] = []
    mark_inputs: list[tuple[Decimal, ValueState[Decimal]]] = []
    liquidation_inputs: list[tuple[Decimal, ValueState[Decimal]]] = []
    flags: list[QualityFlag] = []
    for position in positions:
        quantity = position.leg.quantity_contracts
        market = position.market
        mark_inputs.append((quantity, market.mark_btc_per_contract))
        liquidation_inputs.append((quantity, market.bid.price_btc_per_contract))
        position_values.append(
            PositionValue(
                market.instrument_name,
                market.market_snapshot_id,
                quantity,
                position.premium_paid_btc,
                _sum_values(
                    [(quantity, market.mark_btc_per_contract)], code="NO_MARK", label="mark"
                ),
                _sum_values(
                    [(quantity, market.bid.price_btc_per_contract)],
                    code="NO_EXECUTABLE_BID",
                    label="liquidation value",
                ),
            )
        )
        flags.extend(market.quality_flags)

    mark_options = _sum_values(mark_inputs, code="NO_MARK", label="mark NAV")
    liquidation_options = _sum_values(
        liquidation_inputs, code="NO_EXECUTABLE_BID", label="liquidation NAV"
    )
    mark_nav = (
        ValueState.available(definition.reserve_btc + mark_options.value)
        if mark_options.value is not None
        else _unavailable("NO_MARK", "complete portfolio mark NAV is unavailable")
    )
    liquidation_nav = (
        ValueState.available(definition.reserve_btc + liquidation_options.value)
        if liquidation_options.value is not None
        else _unavailable("NO_EXECUTABLE_BID", "complete portfolio liquidation NAV is unavailable")
    )
    index = _available(usd_conversion_index_price_usd_per_btc)
    if index is None or index <= 0:
        usd_mark = _unavailable(
            "INDEX_PRICE_UNAVAILABLE", "USD NAV needs a positive portfolio index"
        )
        usd_liquidation = _unavailable(
            "INDEX_PRICE_UNAVAILABLE", "USD NAV needs a positive portfolio index"
        )
    else:
        usd_mark = (
            ValueState.available(mark_nav.value * index)
            if mark_nav.value is not None
            else _unavailable("NO_MARK", "mark NAV is unavailable")
        )
        usd_liquidation = (
            ValueState.available(liquidation_nav.value * index)
            if liquidation_nav.value is not None
            else _unavailable("NO_EXECUTABLE_BID", "liquidation NAV is unavailable")
        )

    frozen = PortfolioSnapshot(
        _id(definition.portfolio_definition_id, as_of_utc.isoformat(), config_version),
        definition.portfolio_definition_id,
        as_of_utc,
        definition.reserve_btc,
        definition.legs,
        mark_nav,
        usd_mark,
        liquidation_nav,
        usd_liquidation,
        usd_conversion_index_price_usd_per_btc,
        config_version,
        tuple(flags),
    )
    return PortfolioValuation(
        frozen,
        PORTFOLIO_APPLICATION_VERSION,
        tuple(position_values),
        sum((item.premium_paid_btc for item in positions), Decimal("0")),
        mark_options,
        liquidation_options,
        PortfolioGreeks(
            _sum_greek(
                positions, "delta_standard", code="DERIBIT_DELTA_UNAVAILABLE", label="delta"
            ),
            _sum_greek(
                positions,
                "gamma_standard_per_usd",
                code="GREEK_UNIT_UNVERIFIED",
                label="gamma",
            ),
            _sum_greek(
                positions,
                "theta_usd_per_effective_horizon",
                code="DERIBIT_THETA_UNAVAILABLE",
                label="theta",
            ),
            _sum_greek(
                positions,
                "vega_usd_per_vol_point",
                code="DERIBIT_VEGA_UNAVAILABLE",
                label="vega",
            ),
            _sum_raw_gamma(positions),
        ),
        (
            "premium_paid_btc_total is a historic cost basis and is excluded from NAV.",
            "Gamma is withheld when the frozen contract marks its normalized unit unverified.",
        ),
    )
