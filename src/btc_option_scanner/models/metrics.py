"""Pure metric construction respecting price-side and Deribit-unit contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from math import log

from btc_option_scanner.domain import (
    Availability,
    MetricSnapshot,
    PriceBasis,
    QualityCode,
    QualityFlag,
    QualityScope,
    QualitySeverity,
    SideMetrics,
    UnitStatus,
    ValueState,
)
from btc_option_scanner.domain.market import MarketSnapshot

from .pricing import DERIBIT_DIAGNOSTIC_MODEL_VERSION, LocalGreeks, deribit_observed_diagnostic

QUANT_METRIC_VERSION = "quant-metrics@0.1.0-experimental"
_YEAR_MS = Decimal(365 * 24 * 60 * 60 * 1000)
_NEAR_ZERO_PREMIUM = Decimal("1e-12")


def _unavailable(code: str, message: str) -> ValueState[Decimal]:
    return ValueState.unavailable(code, message)


def _value(value: ValueState[Decimal], code: str, message: str) -> ValueState[Decimal]:
    if value.availability is Availability.AVAILABLE and value.value is not None:
        return value
    return _unavailable(code, message)


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"


@dataclass(frozen=True, slots=True)
class GceCandidates:
    """Parallel experimental GCE candidates; none is a chosen score."""

    gamma_spot_scaled: ValueState[Decimal]
    gamma_dollar_scaled: ValueState[Decimal]
    delta_response: ValueState[Decimal]
    usd_convexity: ValueState[Decimal]
    raw_gamma_per_btc: ValueState[Decimal]


@dataclass(frozen=True, slots=True)
class GreekComparison:
    """Local diagnostic results and explicit differences from untouched Deribit values."""

    diagnostic_model_version: str
    local: LocalGreeks | None
    local_price_btc_per_contract: ValueState[Decimal]
    local_btc_conversion_reference_usd_per_btc: ValueState[Decimal]
    local_btc_conversion_reference_label: str
    mark_minus_local_btc_per_contract: ValueState[Decimal]
    deribit_delta_minus_local: ValueState[Decimal]
    deribit_vega_minus_local_usd_per_vol_point: ValueState[Decimal]
    deribit_theta_minus_local_usd_per_day: ValueState[Decimal]
    deribit_rho_minus_local_usd_per_rate_point: ValueState[Decimal]
    note: str


@dataclass(frozen=True, slots=True)
class QuantitativeMetrics:
    """Supplementary P06 vector; domain MetricSnapshot remains contract-minimal."""

    metric_version: str
    metric_snapshot_id: str
    moneyness_index_over_strike: ValueState[Decimal]
    log_moneyness_index_over_strike: ValueState[Decimal]
    bid_quantity_contracts: ValueState[Decimal]
    ask_quantity_contracts: ValueState[Decimal]
    open_interest_amount_base_btc: ValueState[Decimal]
    volume_24h_amount_base_btc: ValueState[Decimal]
    gce_entry_ask: GceCandidates
    gce_liquidation_bid: GceCandidates
    gce_mark: GceCandidates
    greek_comparison: GreekComparison
    experimental_definitions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetricBundle:
    core: MetricSnapshot
    quantitative: QuantitativeMetrics


def _side_metrics(
    basis: PriceBasis,
    premium: ValueState[Decimal],
    intrinsic: ValueState[Decimal],
    delta: ValueState[Decimal],
    flags: list[QualityFlag],
) -> SideMetrics:
    if premium.availability is not Availability.AVAILABLE or premium.value is None:
        code = (
            "NO_MARK"
            if basis is PriceBasis.THEORETICAL_MARK
            else ("NO_EXECUTABLE_ASK" if basis is PriceBasis.ENTRY_ASK else "NO_EXECUTABLE_BID")
        )
        unavailable = _unavailable(code, f"{basis.value} premium is unavailable")
        return SideMetrics(basis, unavailable, unavailable, unavailable, unavailable)
    if premium.value <= _NEAR_ZERO_PREMIUM:
        unavailable = _unavailable("NEAR_ZERO_PREMIUM", f"{basis.value} premium is too small")
        return SideMetrics(basis, unavailable, unavailable, unavailable, unavailable)
    if intrinsic.availability is not Availability.AVAILABLE or intrinsic.value is None:
        unavailable = _unavailable("INDEX_PRICE_UNAVAILABLE", "index intrinsic is unavailable")
        return SideMetrics(basis, unavailable, unavailable, unavailable, unavailable)
    extrinsic = premium.value - intrinsic.value
    if extrinsic < 0:
        flags.append(
            QualityFlag(
                QualityCode.NEGATIVE_EXTRINSIC,
                QualitySeverity.WARNING,
                QualityScope.VALUE,
                f"{basis.value} premium is below same-snapshot index intrinsic",
            )
        )
    extrinsic_state = ValueState.available(extrinsic)
    icr = ValueState.available(intrinsic.value / premium.value)
    optionality = ValueState.available(extrinsic / premium.value)
    if delta.availability is not Availability.AVAILABLE or delta.value is None:
        dce = _unavailable("DERIBIT_DELTA_UNAVAILABLE", "standard Deribit delta is unavailable")
    else:
        dce = ValueState.available(delta.value / premium.value)
    return SideMetrics(basis, extrinsic_state, icr, optionality, dce)


def _gce_for_side(snapshot: MarketSnapshot, premium: ValueState[Decimal]) -> GceCandidates:
    reason = _unavailable(
        "GREEK_UNIT_UNVERIFIED",
        "GCE is withheld until the normalized Deribit gamma unit is verified",
    )
    gamma = snapshot.greeks.gamma_standard_per_usd
    index = snapshot.index_price_usd_per_btc
    if (
        snapshot.greeks.gamma_unit_status is not UnitStatus.VERIFIED
        or gamma.availability is not Availability.AVAILABLE
        or gamma.value is None
        or index.availability is not Availability.AVAILABLE
        or index.value is None
        or premium.availability is not Availability.AVAILABLE
        or premium.value is None
        or premium.value <= _NEAR_ZERO_PREMIUM
    ):
        return GceCandidates(reason, reason, reason, reason, reason)
    spot_scaled = gamma.value * index.value
    dollar_scaled = gamma.value * index.value * index.value
    delta_response = spot_scaled / premium.value
    return GceCandidates(
        ValueState.available(spot_scaled),
        ValueState.available(dollar_scaled),
        ValueState.available(delta_response),
        ValueState.available(delta_response),  # algebraically identical, interpretation differs
        ValueState.available(gamma.value / premium.value),
    )


def _diagnostic(snapshot: MarketSnapshot, dte_years: Decimal) -> GreekComparison:
    iv = snapshot.iv.mark_annual_decimal
    underlying = snapshot.underlying_price_usd_per_btc
    raw_rate = snapshot.interest_rate_raw
    if (
        iv.availability is not Availability.AVAILABLE
        or iv.value is None
        or underlying.availability is not Availability.AVAILABLE
        or underlying.value is None
        or raw_rate.value is None
        or dte_years <= 0
    ):
        unavailable = _unavailable(
            "DIAGNOSTIC_INPUT_UNAVAILABLE", "diagnostic inputs are incomplete"
        )
        return GreekComparison(
            DERIBIT_DIAGNOSTIC_MODEL_VERSION,
            None,
            unavailable,
            unavailable,
            "unavailable",
            unavailable,
            unavailable,
            unavailable,
            unavailable,
            unavailable,
            "No local diagnostic is produced from missing, expired, or unavailable inputs.",
        )
    local_result = deribit_observed_diagnostic(
        underlying_price_usd_per_btc=underlying.value,
        strike_usd_per_btc=snapshot.strike_usd_per_btc,
        time_to_expiry_years=dte_years,
        mark_iv_annual_decimal=iv.value,
        interest_rate_annual_decimal=raw_rate.value,
        contract_multiplier_btc=snapshot.contract_size_btc,
    )

    def difference(
        exchange: ValueState[Decimal], local: Decimal, unavailable_code: str
    ) -> ValueState[Decimal]:
        if exchange.availability is Availability.AVAILABLE and exchange.value is not None:
            return ValueState.available(exchange.value - local)
        return _unavailable(unavailable_code, "Deribit value is unavailable for comparison")

    mark_diff = difference(
        snapshot.mark_btc_per_contract,
        local_result.price_btc_per_contract_at_conversion_reference,
        "NO_MARK",
    )
    theta = snapshot.greeks.theta_usd_per_effective_horizon
    # A sub-day remaining lifetime has a non-calendar-day exchange horizon and is not comparable.
    theta_diff = (
        difference(theta, local_result.greeks.theta_usd_per_day, "DERIBIT_THETA_UNAVAILABLE")
        if snapshot.greeks.theta_horizon_days.value == Decimal("1")
        else _unavailable("THETA_HORIZON_MISMATCH", "Deribit theta horizon is not one calendar day")
    )
    return GreekComparison(
        DERIBIT_DIAGNOSTIC_MODEL_VERSION,
        local_result.greeks,
        ValueState.available(local_result.price_btc_per_contract_at_conversion_reference),
        ValueState.available(local_result.conversion_reference_usd_per_btc),
        local_result.conversion_reference_label,
        mark_diff,
        difference(
            snapshot.greeks.delta_standard,
            local_result.greeks.delta_per_forward_usd,
            "DERIBIT_DELTA_UNAVAILABLE",
        ),
        difference(
            snapshot.greeks.vega_usd_per_vol_point,
            local_result.greeks.vega_usd_per_vol_point,
            "DERIBIT_VEGA_UNAVAILABLE",
        ),
        theta_diff,
        difference(
            snapshot.greeks.rho_usd_per_rate_point,
            local_result.greeks.rho_usd_per_rate_point,
            "DERIBIT_RHO_UNAVAILABLE",
        ),
        (
            "Diagnostic uses underlying as the fixture-backed observed reference and "
            "must not replace mark or executable prices."
        ),
    )


def build_metric_bundle(
    snapshot: MarketSnapshot,
    *,
    as_of_utc: datetime,
    config_version: str,
) -> MetricBundle:
    """Build deterministic core and experimental P06 metrics from one snapshot."""
    if as_of_utc.tzinfo is None or as_of_utc.utcoffset() != UTC.utcoffset(as_of_utc):
        raise ValueError("as_of_utc must be an aware UTC datetime")
    if not config_version.strip():
        raise ValueError("config_version must not be empty")
    as_of_ms = Decimal(str(int(as_of_utc.timestamp() * 1000)))
    dte_years = max(Decimal("0"), (Decimal(snapshot.expiration_timestamp_ms) - as_of_ms) / _YEAR_MS)
    dte_days = dte_years * Decimal("365")
    flags: list[QualityFlag] = []
    index = snapshot.index_price_usd_per_btc
    if index.availability is Availability.AVAILABLE and index.value is not None:
        intrinsic_usd_value = max(index.value - snapshot.strike_usd_per_btc, Decimal("0"))
        intrinsic_usd = ValueState.available(intrinsic_usd_value * snapshot.contract_size_btc)
        intrinsic_btc = ValueState.available(
            intrinsic_usd_value * snapshot.contract_size_btc / index.value
        )
        moneyness = ValueState.available(index.value / snapshot.strike_usd_per_btc)
        log_moneyness = ValueState.available(Decimal(str(log(float(moneyness.value)))))
    else:
        intrinsic_usd = _unavailable("INDEX_PRICE_UNAVAILABLE", "index price is unavailable")
        intrinsic_btc = _unavailable("INDEX_PRICE_UNAVAILABLE", "index price is unavailable")
        moneyness = _unavailable("INDEX_PRICE_UNAVAILABLE", "index price is unavailable")
        log_moneyness = _unavailable("INDEX_PRICE_UNAVAILABLE", "index price is unavailable")

    ask_metrics = _side_metrics(
        PriceBasis.ENTRY_ASK,
        snapshot.ask.price_btc_per_contract,
        intrinsic_btc,
        snapshot.greeks.delta_standard,
        flags,
    )
    bid_metrics = _side_metrics(
        PriceBasis.LIQUIDATION_BID,
        snapshot.bid.price_btc_per_contract,
        intrinsic_btc,
        snapshot.greeks.delta_standard,
        flags,
    )
    mark_metrics = _side_metrics(
        PriceBasis.THEORETICAL_MARK,
        snapshot.mark_btc_per_contract,
        intrinsic_btc,
        snapshot.greeks.delta_standard,
        flags,
    )
    bid, ask = snapshot.bid.price_btc_per_contract, snapshot.ask.price_btc_per_contract
    if (
        bid.availability is Availability.AVAILABLE
        and bid.value is not None
        and ask.availability is Availability.AVAILABLE
        and ask.value is not None
        and bid.value <= ask.value
    ):
        spread = ValueState.available(ask.value - bid.value)
        mid = (ask.value + bid.value) / Decimal("2")
        relative_spread = ValueState.available((ask.value - bid.value) / mid)
    else:
        spread = _unavailable("TWO_SIDED_QUOTE_REQUIRED", "spread requires valid bid and ask")
        relative_spread = _unavailable(
            "TWO_SIDED_QUOTE_REQUIRED", "relative spread requires valid bid and ask"
        )
    metric_id = _id(
        QUANT_METRIC_VERSION, snapshot.market_snapshot_id, config_version, as_of_utc.isoformat()
    )
    core = MetricSnapshot(
        metric_snapshot_id=metric_id,
        market_snapshot_id=snapshot.market_snapshot_id,
        computed_at_utc=as_of_utc,
        config_version=config_version,
        dte_days=dte_days,
        time_to_expiry_years=dte_years,
        intrinsic_index_btc_per_contract=intrinsic_btc,
        intrinsic_index_usd_per_contract=intrinsic_usd,
        entry_ask=ask_metrics,
        liquidation_bid=bid_metrics,
        mark=mark_metrics,
        absolute_spread_btc_per_contract=spread,
        relative_spread=relative_spread,
        quality_flags=tuple(snapshot.quality_flags) + tuple(flags),
    )
    quantitative = QuantitativeMetrics(
        QUANT_METRIC_VERSION,
        metric_id,
        moneyness,
        log_moneyness,
        snapshot.best_bid_quantity_contracts,
        snapshot.best_ask_quantity_contracts,
        snapshot.open_interest_amount_base_btc,
        snapshot.volume_24h_amount_base_btc,
        _gce_for_side(snapshot, snapshot.ask.price_btc_per_contract),
        _gce_for_side(snapshot, snapshot.bid.price_btc_per_contract),
        _gce_for_side(snapshot, snapshot.mark_btc_per_contract),
        _diagnostic(snapshot, dte_years),
        (
            "GCE candidates are experimental and excluded from ranking.",
            "The diagnostic adapter is regression-only and not an executable price model.",
        ),
    )
    return MetricBundle(core, quantitative)
