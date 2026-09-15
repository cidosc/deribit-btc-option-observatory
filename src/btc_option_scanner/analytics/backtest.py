"""Research-only, event-time backtest framework.

It provides causal strategy seams and accounting, not a claim that any Strategy
A--E is profitable.  All state comes from :mod:`analytics.replay`; no current
catalogue, latest ticker, or silently inferred fee is consulted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from math import sqrt
from statistics import mean
from typing import Protocol

from btc_option_scanner.domain import Availability, InstrumentState
from btc_option_scanner.domain.market import MarketSnapshot

from .replay import EventTimeReplay, ReplayEvent, ReplayEventKind, ReplayState

BACKTEST_SCHEMA_VERSION = "backtest-schema@1.0.0"
BACKTEST_METRIC_VERSION = "backtest-metrics@1.0.0"


def _available(value) -> Decimal | None:
    return (
        value.value
        if value.availability is Availability.AVAILABLE and value.value is not None
        else None
    )


class ValuationMode(str, Enum):
    EXECUTABLE = "EXECUTABLE"
    MARK_ONLY = "MARK_ONLY"


class StrategyId(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class IntentAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True)
class StrategyIntent:
    action: IntentAction
    instrument_name: str | None = None
    quantity_contracts: Decimal = Decimal("0")
    reason: str = ""
    roll_id: str | None = None

    def __post_init__(self) -> None:
        if not self.quantity_contracts.is_finite() or self.quantity_contracts < 0:
            raise ValueError("quantity_contracts must be finite and non-negative")
        if self.action is IntentAction.HOLD:
            if self.quantity_contracts != 0:
                raise ValueError("HOLD quantity must be zero")
            return
        if (
            not self.instrument_name
            or not self.instrument_name.strip()
            or self.quantity_contracts <= 0
        ):
            raise ValueError("BUY/SELL requires an instrument and positive quantity")


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Causal strategy input; ``state`` cannot inspect a future replay clock."""

    event: ReplayEvent
    state: ReplayState
    positions: tuple[tuple[str, Decimal], ...]
    reserve_btc: Decimal

    def rolling_high_usd(self, instrument_name: str, *, window_days: int) -> Decimal | None:
        from datetime import timedelta

        return self.state.rolling_high_usd(instrument_name, window=timedelta(days=window_days))


class BacktestStrategy(Protocol):
    strategy_id: StrategyId
    strategy_version: str

    def on_event(self, context: StrategyContext) -> tuple[StrategyIntent, ...]: ...


@dataclass(frozen=True, slots=True)
class StrategySkeleton:
    """A--E protocol skeleton.  It deliberately emits no investment signal."""

    strategy_id: StrategyId
    strategy_version: str

    def __post_init__(self) -> None:
        if not self.strategy_version.strip():
            raise ValueError("strategy_version must not be empty")

    def on_event(self, context: StrategyContext) -> tuple[StrategyIntent, ...]:
        return (StrategyIntent(IntentAction.HOLD, reason="strategy skeleton has no research rule"),)


def strategy_skeletons(version: str = "strategy-skeleton@1.0.0") -> tuple[StrategySkeleton, ...]:
    return tuple(StrategySkeleton(item, version) for item in StrategyId)


@dataclass(frozen=True, slots=True)
class FeeModel:
    version: str = "fee-unverified@1.0.0"
    rate_on_notional_decimal: Decimal = Decimal("0")
    verified: bool = False
    evidence_reference: str = "unverified"

    def __post_init__(self) -> None:
        if not self.version.strip() or not self.evidence_reference.strip():
            raise ValueError("fee model version and evidence reference are required")
        if not self.rate_on_notional_decimal.is_finite() or self.rate_on_notional_decimal < 0:
            raise ValueError("fee rate must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    config_version: str
    initial_reserve_btc: Decimal
    valuation_mode: ValuationMode
    fee_model: FeeModel = FeeModel()
    metric_version: str = BACKTEST_METRIC_VERSION
    schema_version: str = BACKTEST_SCHEMA_VERSION
    periods_per_year: Decimal = Decimal("365")

    def __post_init__(self) -> None:
        for name in ("config_version", "metric_version", "schema_version"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if not self.initial_reserve_btc.is_finite() or self.initial_reserve_btc < 0:
            raise ValueError("initial_reserve_btc must be finite and non-negative")
        if not self.periods_per_year.is_finite() or self.periods_per_year <= 0:
            raise ValueError("periods_per_year must be positive")


@dataclass(frozen=True, slots=True)
class TradeRecord:
    as_of_utc: datetime
    action: IntentAction
    instrument_name: str
    quantity_contracts: Decimal
    price_btc_per_contract: Decimal | None
    fee_btc: Decimal | None
    executed: bool
    failure_code: str | None
    roll_id: str | None


@dataclass(frozen=True, slots=True)
class EquityPoint:
    as_of_utc: datetime
    nav_btc: Decimal | None
    nav_usd: Decimal | None
    complete: bool


@dataclass(frozen=True, slots=True)
class CostAttribution:
    turnover_btc: Decimal
    spread_cost_btc: Decimal | None
    fees_btc: Decimal | None
    roll_count: int


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    btc_cagr: Decimal | None
    usd_cagr: Decimal | None
    max_drawdown_btc: Decimal | None
    max_drawdown_usd: Decimal | None
    btc_sortino: Decimal | None
    usd_sortino: Decimal | None
    observation_count: int
    unavailable_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoverageMetadata:
    first_observed_at_utc: datetime | None
    last_observed_at_utc: datetime | None
    catalog_event_count: int
    market_event_count: int
    nav_observation_count: int
    complete_nav_ratio: Decimal
    executable_quote_ratio: Decimal
    credibility: str
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BacktestResult:
    strategy_id: StrategyId
    strategy_version: str
    config_version: str
    metric_version: str
    schema_version: str
    valuation_mode: ValuationMode
    is_executable: bool
    trades: tuple[TradeRecord, ...]
    equity_curve: tuple[EquityPoint, ...]
    attribution: CostAttribution
    metrics: PerformanceMetrics
    coverage: CoverageMetadata
    notes: tuple[str, ...]


def _transaction_price(
    snapshot: MarketSnapshot, action: IntentAction, mode: ValuationMode
) -> tuple[Decimal | None, str | None]:
    if mode is ValuationMode.MARK_ONLY:
        price = _available(snapshot.mark_btc_per_contract)
        return (price, None) if price is not None and price > 0 else (None, "NO_MARK")
    side = (
        snapshot.ask.price_btc_per_contract
        if action is IntentAction.BUY
        else snapshot.bid.price_btc_per_contract
    )
    price = _available(side)
    if price is None or price <= 0:
        return None, "NO_EXECUTABLE_ASK" if action is IntentAction.BUY else "NO_EXECUTABLE_BID"
    return price, None


def _nav(
    reserve: Decimal,
    positions: dict[str, Decimal],
    state: ReplayState,
    mode: ValuationMode,
    index: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    nav = reserve
    for name, quantity in positions.items():
        if quantity == 0:
            continue
        snapshot = state.market(name)
        if snapshot is None:
            return None, None
        value = _available(
            snapshot.mark_btc_per_contract
            if mode is ValuationMode.MARK_ONLY
            else snapshot.bid.price_btc_per_contract
        )
        if value is None or value <= 0:
            return None, None
        nav += quantity * value
    return nav, (nav * index if index is not None and index > 0 else None)


def _drawdown(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    peak, worst = values[0], Decimal("0")
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - Decimal("1"))
    return worst


def _cagr(values: list[tuple[datetime, Decimal]]) -> Decimal | None:
    if len(values) < 2 or values[0][1] <= 0:
        return None
    years = Decimal(str((values[-1][0] - values[0][0]).total_seconds())) / Decimal("31557600")
    if years <= 0:
        return None
    return Decimal(str((float(values[-1][1] / values[0][1]) ** (1 / float(years))) - 1))


def _sortino(values: list[Decimal], periods_per_year: Decimal) -> Decimal | None:
    if len(values) < 3 or any(value <= 0 for value in values):
        return None
    returns = [
        float(values[index] / values[index - 1] - Decimal("1")) for index in range(1, len(values))
    ]
    downside = [min(value, 0.0) ** 2 for value in returns]
    deviation = sqrt(sum(downside) / len(downside))
    if deviation == 0:
        return None
    return Decimal(str(mean(returns) / deviation * sqrt(float(periods_per_year))))


def _metrics(curve: tuple[EquityPoint, ...], periods_per_year: Decimal) -> PerformanceMetrics:
    btc = [(point.as_of_utc, point.nav_btc) for point in curve if point.nav_btc is not None]
    usd = [(point.as_of_utc, point.nav_usd) for point in curve if point.nav_usd is not None]
    reasons: list[str] = []
    if len(btc) < 2:
        reasons.append("BTC_HISTORY_INSUFFICIENT")
    if len(usd) < 2:
        reasons.append("USD_HISTORY_INSUFFICIENT")
    btc_values = [value for _, value in btc]
    usd_values = [value for _, value in usd]
    return PerformanceMetrics(
        _cagr(btc),
        _cagr(usd),
        _drawdown(btc_values),
        _drawdown(usd_values),
        _sortino(btc_values, periods_per_year),
        _sortino(usd_values, periods_per_year),
        len(curve),
        tuple(reasons),
    )


def run_backtest(
    replay: EventTimeReplay, strategy: BacktestStrategy, config: BacktestConfig
) -> BacktestResult:
    """Replay a strategy once, applying only quotes visible at each event.

    Executable mode requires an evidenced fee model and the correct Bid/Ask.
    Mark-only mode permits mark-valued research but its result is permanently
    labelled ``is_executable=False``.
    """
    reserve = config.initial_reserve_btc
    positions: dict[str, Decimal] = {}
    trades: list[TradeRecord] = []
    curve: list[EquityPoint] = []
    turnover = Decimal("0")
    spread_cost: Decimal | None = Decimal("0")
    fees: Decimal | None = Decimal("0")
    completed_rolls: set[str] = set()
    roll_actions: dict[str, set[IntentAction]] = {}
    market_events = 0
    catalog_events = 0
    executable_quotes = 0

    for event, state in replay.states():
        catalog_events += event.kind is ReplayEventKind.CATALOG
        market_events += event.kind is ReplayEventKind.MARKET
        if event.kind is ReplayEventKind.MARKET:
            snapshot = event.payload
            assert isinstance(snapshot, MarketSnapshot)
            if (
                _available(snapshot.bid.price_btc_per_contract) is not None
                and _available(snapshot.ask.price_btc_per_contract) is not None
            ):
                executable_quotes += 1
        context = StrategyContext(event, state, tuple(sorted(positions.items())), reserve)
        for intent in strategy.on_event(context):
            if intent.action is IntentAction.HOLD:
                continue
            assert intent.instrument_name is not None
            snapshot = state.market(intent.instrument_name)
            failure: str | None = None
            price: Decimal | None = None
            fee: Decimal | None = None
            if snapshot is None or state.instrument(intent.instrument_name) is None:
                failure = "INSTRUMENT_NOT_VISIBLE_AT_EVENT_TIME"
            elif state.instrument(intent.instrument_name).state is not InstrumentState.OPEN:
                failure = "INSTRUMENT_NOT_OPEN_AT_EVENT_TIME"
            elif (
                event.observed_at_utc.timestamp() * 1000
                >= state.instrument(intent.instrument_name).expiration_timestamp_ms
            ):
                failure = "EXPIRED_INSTRUMENT_AT_EVENT_TIME"
            elif (
                config.valuation_mode is ValuationMode.EXECUTABLE and not config.fee_model.verified
            ):
                failure = "FEE_UNVERIFIED"
            else:
                price, failure = _transaction_price(snapshot, intent.action, config.valuation_mode)
                if price is not None:
                    fee = (
                        price
                        * intent.quantity_contracts
                        * config.fee_model.rate_on_notional_decimal
                    )
                    notional = price * intent.quantity_contracts
                    if intent.action is IntentAction.BUY and reserve < notional + fee:
                        failure = "INSUFFICIENT_RESERVE"
                    elif (
                        intent.action is IntentAction.SELL
                        and positions.get(intent.instrument_name, Decimal("0"))
                        < intent.quantity_contracts
                    ):
                        failure = "INSUFFICIENT_POSITION"
                    else:
                        reserve += (
                            -(notional + fee)
                            if intent.action is IntentAction.BUY
                            else notional - fee
                        )
                        positions[intent.instrument_name] = positions.get(
                            intent.instrument_name, Decimal("0")
                        ) + (
                            intent.quantity_contracts
                            if intent.action is IntentAction.BUY
                            else -intent.quantity_contracts
                        )
                        turnover += notional
                        fees = None if fees is None else fees + fee
                        bid, ask = (
                            _available(snapshot.bid.price_btc_per_contract),
                            _available(snapshot.ask.price_btc_per_contract),
                        )
                        if bid is None or ask is None:
                            spread_cost = None
                        elif spread_cost is not None:
                            midpoint = (bid + ask) / Decimal("2")
                            spread_cost += (
                                price - midpoint
                                if intent.action is IntentAction.BUY
                                else midpoint - price
                            ) * intent.quantity_contracts
                        if intent.roll_id:
                            actions = roll_actions.setdefault(intent.roll_id, set())
                            actions.add(intent.action)
                            if actions == {IntentAction.BUY, IntentAction.SELL}:
                                completed_rolls.add(intent.roll_id)
            trades.append(
                TradeRecord(
                    event.observed_at_utc,
                    intent.action,
                    intent.instrument_name,
                    intent.quantity_contracts,
                    price,
                    fee,
                    failure is None,
                    failure,
                    intent.roll_id,
                )
            )
        event_market = event.payload if event.kind is ReplayEventKind.MARKET else None
        index = (
            _available(event_market.index_price_usd_per_btc)
            if isinstance(event_market, MarketSnapshot)
            else None
        )
        nav_btc, nav_usd = _nav(reserve, positions, state, config.valuation_mode, index)
        curve.append(
            EquityPoint(
                event.observed_at_utc, nav_btc, nav_usd, nav_btc is not None and nav_usd is not None
            )
        )

    complete = sum(point.complete for point in curve)
    quote_ratio = (
        Decimal(executable_quotes) / Decimal(market_events) if market_events else Decimal("0")
    )
    if market_events == 0:
        credibility, limitations = "NO_HISTORY", ("No market events were supplied.",)
    elif config.valuation_mode is ValuationMode.MARK_ONLY:
        credibility, limitations = "MARK_ONLY_RESEARCH", ("Mark-only results are not executable.",)
    elif any(not trade.executed for trade in trades):
        credibility, limitations = (
            "EXECUTABLE_LIMITED",
            ("At least one requested trade failed closed.",),
        )
    else:
        credibility, limitations = (
            "EXECUTABLE_REPLAY_LIMITED",
            ("Coverage reflects only supplied recorded data.",),
        )
    coverage = CoverageMetadata(
        curve[0].as_of_utc if curve else None,
        curve[-1].as_of_utc if curve else None,
        catalog_events,
        market_events,
        len(curve),
        Decimal(complete) / Decimal(len(curve)) if curve else Decimal("0"),
        quote_ratio,
        credibility,
        limitations,
    )
    return BacktestResult(
        strategy.strategy_id,
        strategy.strategy_version,
        config.config_version,
        config.metric_version,
        config.schema_version,
        config.valuation_mode,
        config.valuation_mode is ValuationMode.EXECUTABLE
        and all(trade.executed for trade in trades),
        tuple(trades),
        tuple(curve),
        CostAttribution(turnover, spread_cost, fees, len(completed_rolls)),
        _metrics(tuple(curve), config.periods_per_year),
        coverage,
        ("No profitability conclusion is implied by this research replay.",),
    )
