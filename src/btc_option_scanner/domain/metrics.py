"""Versioned metric and IV-history boundary types, without metric formulas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .enums import PriceBasis
from .primitives import (
    CORE_METRIC_VERSION,
    DOMAIN_SCHEMA_VERSION,
    QualityFlag,
    ValueState,
    require_nonempty,
    require_utc,
)


@dataclass(frozen=True, slots=True)
class SideMetrics:
    """A price-basis-specific vector; never mix entry, liquidation, and mark."""

    basis: PriceBasis
    extrinsic_btc_per_contract: ValueState[Decimal]
    icr: ValueState[Decimal]
    optionality_ratio: ValueState[Decimal]
    dce: ValueState[Decimal]

    def __post_init__(self) -> None:
        if self.basis is PriceBasis.DESCRIPTIVE_MID:
            raise ValueError("mid has no side-specific extrinsic/ICR/optionality/DCE contract")


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    metric_snapshot_id: str
    market_snapshot_id: str
    computed_at_utc: datetime
    config_version: str
    dte_days: Decimal
    time_to_expiry_years: Decimal
    intrinsic_index_btc_per_contract: ValueState[Decimal]
    intrinsic_index_usd_per_contract: ValueState[Decimal]
    entry_ask: SideMetrics
    liquidation_bid: SideMetrics
    mark: SideMetrics
    absolute_spread_btc_per_contract: ValueState[Decimal]
    relative_spread: ValueState[Decimal]
    quality_flags: tuple[QualityFlag, ...] = ()
    schema_version: str = DOMAIN_SCHEMA_VERSION
    metric_version: str = CORE_METRIC_VERSION

    def __post_init__(self) -> None:
        require_nonempty(self.metric_snapshot_id, "metric_snapshot_id")
        require_nonempty(self.market_snapshot_id, "market_snapshot_id")
        require_nonempty(self.config_version, "config_version")
        object.__setattr__(
            self, "computed_at_utc", require_utc(self.computed_at_utc, "computed_at_utc")
        )
        if self.dte_days < 0 or self.time_to_expiry_years < 0:
            raise ValueError("DTE and time to expiry cannot be negative")
        if self.entry_ask.basis is not PriceBasis.ENTRY_ASK:
            raise ValueError("entry_ask metrics must use ENTRY_ASK")
        if self.liquidation_bid.basis is not PriceBasis.LIQUIDATION_BID:
            raise ValueError("liquidation_bid metrics must use LIQUIDATION_BID")
        if self.mark.basis is not PriceBasis.THEORETICAL_MARK:
            raise ValueError("mark metrics must use THEORETICAL_MARK")
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))


@dataclass(frozen=True, slots=True)
class IVObservation:
    market_snapshot_id: str
    instrument_name: str
    as_of_utc: datetime
    metric_side: PriceBasis
    iv_annual_decimal: Decimal
    delta_bucket_id: str
    dte_bucket_id: str
    bucket_policy_version: str
    config_version: str
    quality_flags: tuple[QualityFlag, ...] = ()
    schema_version: str = DOMAIN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_nonempty(self.market_snapshot_id, "market_snapshot_id")
        require_nonempty(self.instrument_name, "instrument_name")
        require_nonempty(self.bucket_policy_version, "bucket_policy_version")
        require_nonempty(self.config_version, "config_version")
        object.__setattr__(self, "as_of_utc", require_utc(self.as_of_utc, "as_of_utc"))
        if self.metric_side not in {
            PriceBasis.ENTRY_ASK,
            PriceBasis.LIQUIDATION_BID,
            PriceBasis.THEORETICAL_MARK,
        }:
            raise ValueError("IV observations require ask, bid, or mark metric side")
        if not self.iv_annual_decimal.is_finite() or self.iv_annual_decimal <= 0:
            raise ValueError("iv_annual_decimal must be finite and positive")
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))
