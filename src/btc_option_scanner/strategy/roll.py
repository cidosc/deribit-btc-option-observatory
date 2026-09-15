"""Auditable roll comparison; it evaluates exchanges and never creates orders."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from enum import Enum

from btc_option_scanner.domain import (
    Availability,
    DecisionState,
    InstrumentSpec,
    MetricSnapshot,
    QualityCode,
    QualityFlag,
    QualityScope,
    QualitySeverity,
    QuantityMode,
    RollEvaluation,
    ValueState,
)
from btc_option_scanner.domain.market import MarketSnapshot
from btc_option_scanner.domain.primitives import require_utc

ROLL_APPLICATION_VERSION = "roll-state-machine@1.0.0"


def _id(*parts: str) -> str:
    return "roll-" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def _unavailable(code: str, message: str) -> ValueState[Decimal]:
    return ValueState.unavailable(code, message)


def _value(value: ValueState[Decimal]) -> Decimal | None:
    if value.availability is Availability.AVAILABLE and value.value is not None:
        return value.value
    return None


class RoundingMode(str, Enum):
    FLOOR = "FLOOR"
    CEIL = "CEIL"
    NEAREST_HALF_UP = "NEAREST_HALF_UP"


@dataclass(frozen=True, slots=True)
class RoundingPolicy:
    """A caller-supplied quantity grid; contracts expose no v1 quantity tick."""

    version: str
    mode: RoundingMode
    quantity_increment_contracts: Decimal

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("rounding policy version must not be empty")
        if (
            not self.quantity_increment_contracts.is_finite()
            or self.quantity_increment_contracts <= 0
        ):
            raise ValueError("quantity_increment_contracts must be finite and positive")


def round_contract_quantity(
    quantity_theoretical_contracts: Decimal,
    *,
    min_trade_quantity_contracts: Decimal,
    policy: RoundingPolicy,
) -> Decimal:
    """Round to a declared trade grid, returning zero when below its minimum.

    Zero is not an executable new order.  It is returned so the caller can
    show the theoretical quantity and fail closed with a useful explanation.
    """
    if not quantity_theoretical_contracts.is_finite() or quantity_theoretical_contracts < 0:
        raise ValueError("quantity_theoretical_contracts must be finite and non-negative")
    if not min_trade_quantity_contracts.is_finite() or min_trade_quantity_contracts <= 0:
        raise ValueError("min_trade_quantity_contracts must be finite and positive")
    steps = quantity_theoretical_contracts / policy.quantity_increment_contracts
    if policy.mode is RoundingMode.FLOOR:
        rounded_steps = steps.to_integral_value(rounding=ROUND_FLOOR)
    elif policy.mode is RoundingMode.CEIL:
        rounded_steps = (-steps).to_integral_value(rounding=ROUND_FLOOR) * Decimal("-1")
    else:
        rounded_steps = steps.to_integral_value(rounding=ROUND_HALF_UP)
    result = rounded_steps * policy.quantity_increment_contracts
    return result if result >= min_trade_quantity_contracts else Decimal("0")


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """An explicit, externally evidenced premium-notional fee assumption.

    ``fee_rate_on_premium_decimal`` is applied independently to each sell/buy
    leg.  ``per_leg_fee_cap_btc`` is an optional cap on that leg's total fee.
    No exchange commission field is interpreted here.  A caller must set
    ``evidence_reference`` and ``verified`` after documenting the schedule.
    """

    version: str
    fee_rate_on_premium_decimal: Decimal
    evidence_reference: str
    verified: bool
    per_leg_fee_cap_btc: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.version.strip() or not self.evidence_reference.strip():
            raise ValueError("fee schedule version and evidence_reference are required")
        if not self.fee_rate_on_premium_decimal.is_finite() or self.fee_rate_on_premium_decimal < 0:
            raise ValueError("fee_rate_on_premium_decimal must be finite and non-negative")
        if self.per_leg_fee_cap_btc is not None and (
            not self.per_leg_fee_cap_btc.is_finite() or self.per_leg_fee_cap_btc < 0
        ):
            raise ValueError("per_leg_fee_cap_btc must be finite and non-negative")

    def calculate_btc(
        self, quantity_contracts: Decimal, price_btc_per_contract: Decimal
    ) -> Decimal:
        fee = quantity_contracts * price_btc_per_contract * self.fee_rate_on_premium_decimal
        return min(fee, self.per_leg_fee_cap_btc) if self.per_leg_fee_cap_btc is not None else fee


@dataclass(frozen=True, slots=True)
class AgingPolicy:
    version: str
    time_aging_dte_days_at_or_below: Decimal
    price_aging_standard_delta_at_or_above: Decimal

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("aging policy version must not be empty")
        if self.time_aging_dte_days_at_or_below < 0:
            raise ValueError("time aging DTE must be non-negative")
        if not Decimal("0") <= self.price_aging_standard_delta_at_or_above <= Decimal("1"):
            raise ValueError("price aging delta must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class AgingAssessment:
    time_aging: bool
    price_aging: bool
    reasons: tuple[str, ...]

    @property
    def triggered(self) -> bool:
        return self.time_aging or self.price_aging


@dataclass(frozen=True, slots=True)
class RollGatePolicy:
    """Independent gates.  Utility is never added to BTC cash costs."""

    version: str
    max_snapshot_skew_ms: int
    minimum_roll_interval: timedelta
    utility_improvement_enter: Decimal
    utility_improvement_continue: Decimal
    max_total_fee_btc: Decimal
    max_reserve_required_btc: Decimal
    min_post_standard_delta: Decimal
    max_post_standard_delta: Decimal
    require_iv_normalization: bool = False

    def __post_init__(self) -> None:
        if not self.version.strip() or self.max_snapshot_skew_ms <= 0:
            raise ValueError("gate policy requires version and positive snapshot skew")
        if self.minimum_roll_interval < timedelta(0):
            raise ValueError("minimum_roll_interval cannot be negative")
        for name in (
            "utility_improvement_enter",
            "utility_improvement_continue",
            "max_total_fee_btc",
            "max_reserve_required_btc",
            "min_post_standard_delta",
            "max_post_standard_delta",
        ):
            value = getattr(self, name)
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if self.utility_improvement_continue > self.utility_improvement_enter:
            raise ValueError("hysteresis continue threshold cannot exceed enter threshold")
        if self.max_total_fee_btc < 0 or self.max_reserve_required_btc < 0:
            raise ValueError("BTC cost budgets must be non-negative")
        if self.min_post_standard_delta > self.max_post_standard_delta:
            raise ValueError("post-roll delta bounds are inverted")


@dataclass(frozen=True, slots=True)
class RollInput:
    portfolio_snapshot_id: str
    reserve_before_btc: Decimal
    old_quantity_contracts: Decimal
    old_market: MarketSnapshot
    new_market: MarketSnapshot
    old_metrics: MetricSnapshot
    new_metrics: MetricSnapshot
    old_spec: InstrumentSpec
    new_spec: InstrumentSpec
    quantity_mode: QuantityMode
    fee_schedule: FeeSchedule | None
    rounding_policy: RoundingPolicy
    aging_policy: AgingPolicy
    gate_policy: RollGatePolicy
    as_of_utc: datetime
    config_version: str
    utility_improvement: ValueState[Decimal]
    last_roll_at_utc: datetime | None = None
    prior_roll_candidate: bool = False
    iv_normalized: bool | None = None

    def __post_init__(self) -> None:
        if not self.portfolio_snapshot_id.strip() or not self.config_version.strip():
            raise ValueError("portfolio_snapshot_id and config_version are required")
        for name in ("reserve_before_btc", "old_quantity_contracts"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        require_utc(self.as_of_utc, "as_of_utc")
        if self.last_roll_at_utc is not None:
            require_utc(self.last_roll_at_utc, "last_roll_at_utc")


@dataclass(frozen=True, slots=True)
class RollCashflow:
    old_sale_proceeds_btc: ValueState[Decimal]
    new_purchase_cost_btc: ValueState[Decimal]
    fee_old_btc: ValueState[Decimal]
    fee_new_btc: ValueState[Decimal]
    net_reserve_release_btc: ValueState[Decimal]
    reserve_after_btc: ValueState[Decimal]
    reserve_required_btc: ValueState[Decimal]
    accounting_identity_holds: ValueState[bool]


@dataclass(frozen=True, slots=True)
class RollGates:
    data: bool
    aging: bool
    interval: bool
    utility: bool
    transaction_cost: bool
    exposure: bool
    iv: bool
    explanations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RollDecision:
    """Application-facing extension of the frozen RollEvaluation DTO."""

    evaluation: RollEvaluation
    application_version: str
    aging: AgingAssessment
    cashflow: RollCashflow
    gates: RollGates
    standard_delta_rounding_error: ValueState[Decimal]
    transaction_spread_cost_btc: ValueState[Decimal]
    as_of_utc: datetime


def _has_block_action(market: MarketSnapshot) -> bool:
    return (not market.actionable) or any(
        flag.severity is QualitySeverity.BLOCK_ACTION for flag in market.quality_flags
    )


def _time_skew_ms(old: MarketSnapshot, new: MarketSnapshot) -> int | None:
    old_time, new_time = old.source.exchange_timestamp_ms, new.source.exchange_timestamp_ms
    if old_time is None or new_time is None:
        return None
    return abs(old_time - new_time)


def _aging(input: RollInput) -> AgingAssessment:
    time_aging = input.old_metrics.dte_days <= input.aging_policy.time_aging_dte_days_at_or_below
    old_delta = _value(input.old_market.greeks.delta_standard)
    price_aging = (
        old_delta is not None
        and old_delta >= input.aging_policy.price_aging_standard_delta_at_or_above
    )
    reasons: list[str] = []
    if time_aging:
        reasons.append("TIME_AGING_DTE_TRIGGERED")
    if price_aging:
        reasons.append("PRICE_AGING_DELTA_TRIGGERED")
    if not reasons:
        reasons.append("NO_AGING_TRIGGER")
    return AgingAssessment(time_aging, price_aging, tuple(reasons))


def _state_values(
    input: RollInput,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    return (
        _value(input.old_market.bid.price_btc_per_contract),
        _value(input.new_market.ask.price_btc_per_contract),
        _value(input.old_market.greeks.delta_standard),
        _value(input.new_market.greeks.delta_standard),
    )


def _unavailable_cashflow(reason: str) -> RollCashflow:
    value = _unavailable(reason, "roll cashflow is unavailable before executable inputs pass")
    return RollCashflow(
        value,
        value,
        value,
        value,
        value,
        value,
        value,
        ValueState.unavailable(reason, value.reason.message),
    )


def _flags(input: RollInput, state: DecisionState) -> tuple[QualityFlag, ...]:
    result = list(input.old_market.quality_flags) + list(input.new_market.quality_flags)
    if state is DecisionState.NO_EXECUTABLE_BID:
        result.append(
            QualityFlag(
                QualityCode.NO_EXECUTABLE_BID,
                QualitySeverity.BLOCK_VALUE,
                QualityScope.VALUE,
                "old long has no executable Bid",
            )
        )
    if state is DecisionState.NO_EXECUTABLE_ASK:
        result.append(
            QualityFlag(
                QualityCode.NO_EXECUTABLE_ASK,
                QualitySeverity.BLOCK_VALUE,
                QualityScope.VALUE,
                "new long has no executable Ask",
            )
        )
    if state is DecisionState.DATA_STALE:
        result.append(
            QualityFlag(
                QualityCode.SNAPSHOT_TIME_MISMATCH,
                QualitySeverity.BLOCK_ACTION,
                QualityScope.RUN,
                "old/new snapshots are stale, blocked, or outside the configured skew",
            )
        )
    if input.fee_schedule is None or not input.fee_schedule.verified:
        result.append(
            QualityFlag(
                QualityCode.FEE_UNVERIFIED,
                QualitySeverity.BLOCK_VALUE,
                QualityScope.VALUE,
                "fee schedule is absent or lacks evidence verification",
            )
        )
    return tuple(result)


def _frozen_evaluation(
    input: RollInput,
    *,
    state: DecisionState,
    theoretical: Decimal,
    executable: ValueState[Decimal],
    cashflow: RollCashflow,
    delta_before: ValueState[Decimal],
    delta_theoretical: ValueState[Decimal],
    delta_executable: ValueState[Decimal],
    reasons: list[str],
    risks: list[str],
) -> RollEvaluation:
    old_bid, new_ask, _, _ = _state_values(input)
    return RollEvaluation(
        _id(
            input.portfolio_snapshot_id,
            input.old_market.market_snapshot_id,
            input.new_market.market_snapshot_id,
            input.quantity_mode.value,
            input.as_of_utc.isoformat(),
        ),
        input.portfolio_snapshot_id,
        input.old_market.instrument_name,
        input.new_market.instrument_name,
        input.old_market.market_snapshot_id,
        input.new_market.market_snapshot_id,
        input.old_metrics.metric_snapshot_id,
        input.new_metrics.metric_snapshot_id,
        input.quantity_mode,
        input.old_quantity_contracts,
        theoretical,
        executable,
        input.rounding_policy.version,
        ValueState.available(old_bid)
        if old_bid is not None
        else _unavailable("NO_EXECUTABLE_BID", "old Bid unavailable"),
        ValueState.available(new_ask)
        if new_ask is not None
        else _unavailable("NO_EXECUTABLE_ASK", "new Ask unavailable"),
        cashflow.old_sale_proceeds_btc,
        cashflow.new_purchase_cost_btc,
        cashflow.fee_old_btc,
        cashflow.fee_new_btc,
        input.fee_schedule.version if input.fee_schedule is not None else "fee-unverified",
        cashflow.net_reserve_release_btc,
        input.reserve_before_btc,
        cashflow.reserve_after_btc,
        delta_before,
        delta_theoretical,
        delta_executable,
        state,
        tuple(reasons),
        tuple(risks),
        input.config_version,
        _flags(input, state),
    )


def evaluate_roll(input: RollInput) -> RollDecision:
    """Evaluate one old/new exchange through fail-closed, independent gates."""
    reasons: list[str] = []
    risks: list[str] = ["Research-only comparison; this output is not an order."]
    aging = _aging(input)
    old_bid, new_ask, old_delta, new_delta = _state_values(input)
    zero = Decimal("0")
    delta_before = (
        ValueState.available(input.old_quantity_contracts * old_delta)
        if old_delta is not None
        else _unavailable("DERIBIT_DELTA_UNAVAILABLE", "old standard delta unavailable")
    )
    invalid_metric_binding = (
        input.old_metrics.market_snapshot_id != input.old_market.market_snapshot_id
        or input.new_metrics.market_snapshot_id != input.new_market.market_snapshot_id
    )
    same_contract_versions = (
        input.old_market.schema_version == input.new_market.schema_version
        and input.old_metrics.schema_version == input.new_metrics.schema_version
        and input.old_metrics.metric_version == input.new_metrics.metric_version
        and input.old_metrics.config_version == input.new_metrics.config_version
        and input.old_metrics.config_version == input.config_version
    )
    skew = _time_skew_ms(input.old_market, input.new_market)
    data_ok = not (
        _has_block_action(input.old_market)
        or _has_block_action(input.new_market)
        or invalid_metric_binding
        or not same_contract_versions
        or skew is None
        or skew > input.gate_policy.max_snapshot_skew_ms
    )
    if not data_ok:
        reasons.append("DATA_STALE_OR_SNAPSHOT_WINDOW_INVALID")
        cashflow = _unavailable_cashflow("DATA_STALE")
        evaluation = _frozen_evaluation(
            input,
            state=DecisionState.DATA_STALE,
            theoretical=zero,
            executable=_unavailable("DATA_STALE", "data gate failed"),
            cashflow=cashflow,
            delta_before=delta_before,
            delta_theoretical=_unavailable("DATA_STALE", "data gate failed"),
            delta_executable=_unavailable("DATA_STALE", "data gate failed"),
            reasons=reasons,
            risks=risks,
        )
        return RollDecision(
            evaluation,
            ROLL_APPLICATION_VERSION,
            aging,
            cashflow,
            RollGates(False, False, False, False, False, False, False, tuple(reasons)),
            _unavailable("DATA_STALE", "data gate failed"),
            _unavailable("DATA_STALE", "data gate failed"),
            input.as_of_utc,
        )
    if old_bid is None:
        reasons.append("OLD_BID_REQUIRED_FOR_EXECUTABLE_ROLL")
        cashflow = _unavailable_cashflow("NO_EXECUTABLE_BID")
        evaluation = _frozen_evaluation(
            input,
            state=DecisionState.NO_EXECUTABLE_BID,
            theoretical=zero,
            executable=_unavailable("NO_EXECUTABLE_BID", "old Bid missing"),
            cashflow=cashflow,
            delta_before=delta_before,
            delta_theoretical=_unavailable("NO_EXECUTABLE_BID", "old Bid missing"),
            delta_executable=_unavailable("NO_EXECUTABLE_BID", "old Bid missing"),
            reasons=reasons,
            risks=risks,
        )
        return RollDecision(
            evaluation,
            ROLL_APPLICATION_VERSION,
            aging,
            cashflow,
            RollGates(True, aging.triggered, False, False, False, False, False, tuple(reasons)),
            _unavailable("NO_EXECUTABLE_BID", "old Bid missing"),
            _unavailable("NO_EXECUTABLE_BID", "old Bid missing"),
            input.as_of_utc,
        )
    if new_ask is None:
        reasons.append("NEW_ASK_REQUIRED_FOR_EXECUTABLE_ROLL")
        cashflow = _unavailable_cashflow("NO_EXECUTABLE_ASK")
        evaluation = _frozen_evaluation(
            input,
            state=DecisionState.NO_EXECUTABLE_ASK,
            theoretical=zero,
            executable=_unavailable("NO_EXECUTABLE_ASK", "new Ask missing"),
            cashflow=cashflow,
            delta_before=delta_before,
            delta_theoretical=_unavailable("NO_EXECUTABLE_ASK", "new Ask missing"),
            delta_executable=_unavailable("NO_EXECUTABLE_ASK", "new Ask missing"),
            reasons=reasons,
            risks=risks,
        )
        return RollDecision(
            evaluation,
            ROLL_APPLICATION_VERSION,
            aging,
            cashflow,
            RollGates(True, aging.triggered, False, False, False, False, False, tuple(reasons)),
            _unavailable("NO_EXECUTABLE_ASK", "new Ask missing"),
            _unavailable("NO_EXECUTABLE_ASK", "new Ask missing"),
            input.as_of_utc,
        )
    if input.old_quantity_contracts <= 0:
        reasons.append("NO_OLD_POSITION_TO_ROLL")
        cashflow = _unavailable_cashflow("INVALID_QUANTITY")
        evaluation = _frozen_evaluation(
            input,
            state=DecisionState.INVALID_INPUT,
            theoretical=zero,
            executable=_unavailable("INVALID_QUANTITY", "old quantity must be positive"),
            cashflow=cashflow,
            delta_before=delta_before,
            delta_theoretical=_unavailable("INVALID_QUANTITY", "old quantity must be positive"),
            delta_executable=_unavailable("INVALID_QUANTITY", "old quantity must be positive"),
            reasons=reasons,
            risks=risks,
        )
        return RollDecision(
            evaluation,
            ROLL_APPLICATION_VERSION,
            aging,
            cashflow,
            RollGates(True, aging.triggered, False, False, False, False, False, tuple(reasons)),
            _unavailable("INVALID_QUANTITY", "old quantity must be positive"),
            _unavailable("INVALID_QUANTITY", "old quantity must be positive"),
            input.as_of_utc,
        )

    if input.quantity_mode is QuantityMode.EQUAL_CONTRACTS:
        theoretical = input.old_quantity_contracts
    elif old_delta is None or new_delta is None or new_delta <= 0:
        reasons.append("INVALID_EQUAL_DELTA_TARGET")
        cashflow = _unavailable_cashflow("INVALID_EQUAL_DELTA_TARGET")
        evaluation = _frozen_evaluation(
            input,
            state=DecisionState.INVALID_INPUT,
            theoretical=zero,
            executable=_unavailable(
                "INVALID_EQUAL_DELTA_TARGET", "positive verified deltas required"
            ),
            cashflow=cashflow,
            delta_before=delta_before,
            delta_theoretical=_unavailable(
                "INVALID_EQUAL_DELTA_TARGET", "positive verified deltas required"
            ),
            delta_executable=_unavailable(
                "INVALID_EQUAL_DELTA_TARGET", "positive verified deltas required"
            ),
            reasons=reasons,
            risks=risks,
        )
        return RollDecision(
            evaluation,
            ROLL_APPLICATION_VERSION,
            aging,
            cashflow,
            RollGates(True, aging.triggered, False, False, False, False, False, tuple(reasons)),
            _unavailable("INVALID_EQUAL_DELTA_TARGET", "delta target invalid"),
            _unavailable("INVALID_EQUAL_DELTA_TARGET", "delta target invalid"),
            input.as_of_utc,
        )
    else:
        theoretical = input.old_quantity_contracts * old_delta / new_delta
    executable_quantity = round_contract_quantity(
        theoretical,
        min_trade_quantity_contracts=input.new_spec.min_trade_quantity_contracts,
        policy=input.rounding_policy,
    )
    if executable_quantity <= 0:
        reasons.append("ROUNDED_QUANTITY_BELOW_MINIMUM_TRADE")
        cashflow = _unavailable_cashflow("INVALID_QUANTITY")
        evaluation = _frozen_evaluation(
            input,
            state=DecisionState.INVALID_INPUT,
            theoretical=theoretical,
            executable=_unavailable(
                "INVALID_QUANTITY", "rounded new quantity is below minimum trade"
            ),
            cashflow=cashflow,
            delta_before=delta_before,
            delta_theoretical=_unavailable("INVALID_QUANTITY", "new quantity unavailable"),
            delta_executable=_unavailable("INVALID_QUANTITY", "new quantity unavailable"),
            reasons=reasons,
            risks=risks,
        )
        return RollDecision(
            evaluation,
            ROLL_APPLICATION_VERSION,
            aging,
            cashflow,
            RollGates(True, aging.triggered, False, False, False, False, False, tuple(reasons)),
            _unavailable("INVALID_QUANTITY", "new quantity unavailable"),
            _unavailable("INVALID_QUANTITY", "new quantity unavailable"),
            input.as_of_utc,
        )

    delta_theoretical = (
        ValueState.available(theoretical * new_delta)
        if new_delta is not None
        else _unavailable("DERIBIT_DELTA_UNAVAILABLE", "new delta unavailable")
    )
    delta_executable = (
        ValueState.available(executable_quantity * new_delta)
        if new_delta is not None
        else _unavailable("DERIBIT_DELTA_UNAVAILABLE", "new delta unavailable")
    )
    rounding_error = (
        ValueState.available(delta_executable.value - delta_before.value)
        if delta_executable.value is not None and delta_before.value is not None
        else _unavailable("DERIBIT_DELTA_UNAVAILABLE", "cannot calculate delta rounding error")
    )
    if input.fee_schedule is None or not input.fee_schedule.verified:
        reasons.append("FEE_SCHEDULE_UNVERIFIED")
        cashflow = _unavailable_cashflow("FEE_UNVERIFIED")
        evaluation = _frozen_evaluation(
            input,
            state=DecisionState.INVALID_INPUT,
            theoretical=theoretical,
            executable=ValueState.available(executable_quantity),
            cashflow=cashflow,
            delta_before=delta_before,
            delta_theoretical=delta_theoretical,
            delta_executable=delta_executable,
            reasons=reasons,
            risks=risks,
        )
        return RollDecision(
            evaluation,
            ROLL_APPLICATION_VERSION,
            aging,
            cashflow,
            RollGates(True, aging.triggered, False, False, False, False, False, tuple(reasons)),
            rounding_error,
            _unavailable("FEE_UNVERIFIED", "cashflow requires verified fee schedule"),
            input.as_of_utc,
        )

    fee_old = input.fee_schedule.calculate_btc(input.old_quantity_contracts, old_bid)
    fee_new = input.fee_schedule.calculate_btc(executable_quantity, new_ask)
    proceeds = input.old_quantity_contracts * old_bid
    purchase = executable_quantity * new_ask
    release = proceeds - purchase - fee_old - fee_new
    reserve_after = input.reserve_before_btc + release
    cashflow = RollCashflow(
        ValueState.available(proceeds),
        ValueState.available(purchase),
        ValueState.available(fee_old),
        ValueState.available(fee_new),
        ValueState.available(release),
        ValueState.available(reserve_after),
        ValueState.available(max(-release, zero)),
        ValueState.available(
            input.reserve_before_btc + proceeds == reserve_after + purchase + fee_old + fee_new
        ),
    )
    # A two-sided quoted friction proxy is separate from executable cash flow.
    old_mid_cost = (
        ((input.old_market.ask.price_btc_per_contract.value - old_bid) / Decimal("2"))
        * input.old_quantity_contracts
        if input.old_market.ask.price_btc_per_contract.value is not None
        else None
    )
    new_bid = input.new_market.bid.price_btc_per_contract.value
    spread_cost = (
        ValueState.available(
            old_mid_cost + (new_ask - new_bid) * executable_quantity / Decimal("2")
        )
        if old_mid_cost is not None and new_bid is not None
        else _unavailable(
            "TWO_SIDED_QUOTE_REQUIRED", "spread cost requires both sides on old and new instruments"
        )
    )

    interval_ok = (
        input.last_roll_at_utc is None
        or input.as_of_utc - input.last_roll_at_utc >= input.gate_policy.minimum_roll_interval
    )
    threshold = (
        input.gate_policy.utility_improvement_continue
        if input.prior_roll_candidate
        else input.gate_policy.utility_improvement_enter
    )
    utility = _value(input.utility_improvement)
    utility_ok = utility is not None and utility >= threshold
    transaction_ok = (
        fee_old + fee_new <= input.gate_policy.max_total_fee_btc
        and max(-release, zero) <= input.gate_policy.max_reserve_required_btc
    )
    exposure_ok = (
        delta_executable.value is not None
        and input.gate_policy.min_post_standard_delta
        <= delta_executable.value
        <= input.gate_policy.max_post_standard_delta
    )
    iv_ok = not input.gate_policy.require_iv_normalization or input.iv_normalized is True
    if not aging.triggered:
        reasons.append("HOLD_NO_AGING_TRIGGER")
    if not interval_ok:
        reasons.append("MINIMUM_ROLL_INTERVAL_NOT_MET")
    if not utility_ok:
        reasons.append("UTILITY_IMPROVEMENT_GATE_NOT_MET")
    if not transaction_ok:
        reasons.append("BTC_TRANSACTION_COST_OR_RESERVE_GATE_NOT_MET")
    if not exposure_ok:
        reasons.append("POST_ROLL_EXPOSURE_GATE_NOT_MET")
    if not iv_ok:
        reasons.append("WAIT_FOR_IV_NORMALIZATION")
    if reserve_after < 0:
        state = DecisionState.INSUFFICIENT_RESERVE
        reasons.append("RESERVE_AFTER_WOULD_BE_NEGATIVE")
    elif not iv_ok:
        state = DecisionState.WAIT_FOR_IV_NORMALIZATION
    elif aging.triggered and interval_ok and utility_ok and transaction_ok and exposure_ok:
        state = DecisionState.ROLL_CANDIDATE
        reasons.append("ALL_INDEPENDENT_ROLL_GATES_PASSED")
    else:
        state = DecisionState.HOLD
    evaluation = _frozen_evaluation(
        input,
        state=state,
        theoretical=theoretical,
        executable=ValueState.available(executable_quantity),
        cashflow=cashflow,
        delta_before=delta_before,
        delta_theoretical=delta_theoretical,
        delta_executable=delta_executable,
        reasons=reasons,
        risks=risks,
    )
    return RollDecision(
        evaluation,
        ROLL_APPLICATION_VERSION,
        aging,
        cashflow,
        RollGates(
            True,
            aging.triggered,
            interval_ok,
            utility_ok,
            transaction_ok,
            exposure_ok,
            iv_ok,
            tuple(reasons),
        ),
        rounding_error,
        spread_cost,
        input.as_of_utc,
    )
