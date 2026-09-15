"""Portfolio, roll, and stress DTOs only; no valuation or execution logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .enums import DecisionState, QuantityMode
from .primitives import (
    CORE_METRIC_VERSION,
    DOMAIN_SCHEMA_VERSION,
    QualityFlag,
    ValueState,
    require_finite,
    require_nonempty,
    require_utc,
)


@dataclass(frozen=True, slots=True)
class PortfolioLeg:
    instrument_name: str
    quantity_contracts: Decimal
    market_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        require_nonempty(self.instrument_name, "instrument_name")
        if require_finite(self.quantity_contracts, "quantity_contracts") < 0:
            raise ValueError("v1 permits long option legs only")


@dataclass(frozen=True, slots=True)
class PortfolioDefinition:
    portfolio_definition_id: str
    reserve_btc: Decimal
    legs: tuple[PortfolioLeg, ...]
    created_at_utc: datetime
    definition_version: str

    def __post_init__(self) -> None:
        require_nonempty(self.portfolio_definition_id, "portfolio_definition_id")
        require_nonempty(self.definition_version, "definition_version")
        if require_finite(self.reserve_btc, "reserve_btc") < 0:
            raise ValueError("reserve_btc must be non-negative")
        object.__setattr__(
            self, "created_at_utc", require_utc(self.created_at_utc, "created_at_utc")
        )
        object.__setattr__(self, "legs", tuple(self.legs))


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    portfolio_snapshot_id: str
    portfolio_definition_id: str
    as_of_utc: datetime
    reserve_btc: Decimal
    legs: tuple[PortfolioLeg, ...]
    nav_btc_mark: ValueState[Decimal]
    nav_usd_mark: ValueState[Decimal]
    nav_btc_liquidation: ValueState[Decimal]
    nav_usd_liquidation: ValueState[Decimal]
    usd_conversion_index_price_usd_per_btc: ValueState[Decimal]
    config_version: str
    quality_flags: tuple[QualityFlag, ...] = ()
    schema_version: str = DOMAIN_SCHEMA_VERSION
    metric_version: str = CORE_METRIC_VERSION

    def __post_init__(self) -> None:
        for name in ("portfolio_snapshot_id", "portfolio_definition_id", "config_version"):
            require_nonempty(getattr(self, name), name)
        if require_finite(self.reserve_btc, "reserve_btc") < 0:
            raise ValueError("reserve_btc must be non-negative")
        object.__setattr__(self, "as_of_utc", require_utc(self.as_of_utc, "as_of_utc"))
        object.__setattr__(self, "legs", tuple(self.legs))
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))


@dataclass(frozen=True, slots=True)
class RollEvaluation:
    roll_evaluation_id: str
    portfolio_snapshot_id: str
    old_instrument_name: str
    new_instrument_name: str
    old_market_snapshot_id: str
    new_market_snapshot_id: str
    old_metric_snapshot_id: str
    new_metric_snapshot_id: str
    quantity_mode: QuantityMode
    old_quantity_contracts: Decimal
    new_quantity_theoretical_contracts: Decimal
    new_quantity_executable_contracts: ValueState[Decimal]
    rounding_policy_version: str
    old_bid_btc_per_contract: ValueState[Decimal]
    new_ask_btc_per_contract: ValueState[Decimal]
    old_sale_proceeds_btc: ValueState[Decimal]
    new_purchase_cost_btc: ValueState[Decimal]
    fee_old_btc: ValueState[Decimal]
    fee_new_btc: ValueState[Decimal]
    fee_schedule_version: str
    net_reserve_release_btc: ValueState[Decimal]
    reserve_before_btc: Decimal
    reserve_after_btc: ValueState[Decimal]
    standard_delta_before: ValueState[Decimal]
    standard_delta_after_theoretical: ValueState[Decimal]
    standard_delta_after_executable: ValueState[Decimal]
    decision_state: DecisionState
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    config_version: str
    quality_flags: tuple[QualityFlag, ...] = ()
    schema_version: str = DOMAIN_SCHEMA_VERSION
    metric_version: str = CORE_METRIC_VERSION

    def __post_init__(self) -> None:
        for name in (
            "roll_evaluation_id",
            "portfolio_snapshot_id",
            "old_instrument_name",
            "new_instrument_name",
            "rounding_policy_version",
            "fee_schedule_version",
            "config_version",
        ):
            require_nonempty(getattr(self, name), name)
        for name in (
            "old_quantity_contracts",
            "new_quantity_theoretical_contracts",
            "reserve_before_btc",
        ):
            if require_finite(getattr(self, name), name) < 0:
                raise ValueError(f"{name} must be non-negative")
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "risks", tuple(self.risks))
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))


@dataclass(frozen=True, slots=True)
class StressScenarioPoint:
    index_return_decimal: Decimal
    elapsed_days: Decimal
    absolute_iv_vol_point_shock: Decimal
    rate_policy_version: str
    forward_policy_version: str
    smile_policy_version: str

    def __post_init__(self) -> None:
        for name in ("index_return_decimal", "elapsed_days", "absolute_iv_vol_point_shock"):
            require_finite(getattr(self, name), name)
        if self.elapsed_days < 0:
            raise ValueError("elapsed_days cannot be negative")


@dataclass(frozen=True, slots=True)
class StressRun:
    stress_run_id: str
    portfolio_snapshot_id: str
    scenario_points: tuple[StressScenarioPoint, ...]
    pricing_model_version: str
    config_version: str
    conditional_result_ids: tuple[str, ...]
    quality_flags: tuple[QualityFlag, ...] = ()
    schema_version: str = DOMAIN_SCHEMA_VERSION
    metric_version: str = CORE_METRIC_VERSION

    def __post_init__(self) -> None:
        for name in (
            "stress_run_id",
            "portfolio_snapshot_id",
            "pricing_model_version",
            "config_version",
        ):
            require_nonempty(getattr(self, name), name)
        if not self.scenario_points:
            raise ValueError("StressRun requires scenario points")
        object.__setattr__(self, "scenario_points", tuple(self.scenario_points))
        object.__setattr__(self, "conditional_result_ids", tuple(self.conditional_result_ids))
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))
