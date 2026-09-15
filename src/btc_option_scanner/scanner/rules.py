"""A lexicographic, explanation-first candidate scanner with no trade commands."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Protocol

from btc_option_scanner.domain import (
    Availability,
    CandidateEvaluation,
    DecisionState,
    InstrumentState,
    MetricSnapshot,
    OptionType,
    QualityCode,
    QualityFlag,
    QualityScope,
    QualitySeverity,
    ValueState,
)
from btc_option_scanner.domain.market import MarketSnapshot

from .cohort import CohortPolicy, cohort_id_for, percentile_rank

RULE_VERSION = "scanner-lexicographic@0.1.0-experimental"


@dataclass(frozen=True, slots=True)
class ScannerPolicy:
    """Explicit experimental rules, not a hidden final weighting scheme."""

    rule_version: str
    universe_filter_version: str
    min_dte_days: Decimal
    max_dte_days: Decimal
    min_open_interest_base_btc: Decimal
    max_relative_spread: Decimal
    cohort_policy: CohortPolicy
    max_snapshot_skew_ms: int = 0
    min_absolute_delta: Decimal = Decimal("0")
    max_absolute_delta: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not self.rule_version.strip() or not self.universe_filter_version.strip():
            raise ValueError("rule and universe versions must not be empty")
        if self.min_dte_days < 0 or self.max_dte_days < self.min_dte_days:
            raise ValueError("DTE bounds are invalid")
        if self.min_open_interest_base_btc < 0:
            raise ValueError("minimum open interest must be non-negative")
        if not Decimal("0") < self.max_relative_spread <= Decimal("1"):
            raise ValueError("max_relative_spread must be in (0, 1]")
        if self.max_snapshot_skew_ms < 0:
            raise ValueError("max_snapshot_skew_ms cannot be negative")
        if (
            self.min_absolute_delta < 0
            or self.max_absolute_delta > 1
            or self.min_absolute_delta > self.max_absolute_delta
        ):
            raise ValueError("absolute Delta bounds must satisfy 0 <= min <= max <= 1")


@dataclass(frozen=True, slots=True)
class ScanInput:
    option_type: OptionType
    market: MarketSnapshot
    metrics: MetricSnapshot

    def __post_init__(self) -> None:
        if self.market.market_snapshot_id != self.metrics.market_snapshot_id:
            raise ValueError("market and metric snapshot IDs must match")


class ScanInputSource(Protocol):
    """Application-facing source seam; P06 imposes no storage implementation."""

    def scan_inputs(self, policy: ScannerPolicy) -> Iterable[ScanInput]: ...


@dataclass(frozen=True, slots=True)
class CandidateAnalysis:
    """Structured peer outputs alongside the contract-level evaluation DTO."""

    evaluation: CandidateEvaluation
    ranking_rule_version: str
    entry_dce_peer_percentile: ValueState[Decimal]
    entry_dce_peer_rank: ValueState[Decimal]


def _blocked_action(market: MarketSnapshot) -> bool:
    return (not market.actionable) or any(
        flag.severity is QualitySeverity.BLOCK_ACTION for flag in market.quality_flags
    )


def _evaluation(
    item: ScanInput,
    *,
    eligible: bool,
    state: DecisionState,
    reasons: list[str],
    risks: list[str],
    rejected: list[str],
    cohort_id: str | None = None,
    cohort_size: int | None = None,
    rank: int | None = None,
    extra_flags: tuple[QualityFlag, ...] = (),
) -> CandidateEvaluation:
    return CandidateEvaluation(
        item.market.instrument_name,
        item.market.market_snapshot_id,
        item.metrics.metric_snapshot_id,
        eligible,
        state,
        tuple(reasons),
        tuple(risks),
        tuple(rejected),
        tuple(item.market.quality_flags) + tuple(item.metrics.quality_flags) + extra_flags,
        rank,
        cohort_id,
        cohort_size,
    )


def _prequalify(item: ScanInput, policy: ScannerPolicy) -> CandidateEvaluation | None:
    market, metrics = item.market, item.metrics
    if _blocked_action(market):
        stale = any(
            flag.code is QualityCode.STALE_EXCHANGE_TIMESTAMP for flag in market.quality_flags
        )
        state = DecisionState.DATA_STALE if stale else DecisionState.DATA_ONLY
        return _evaluation(
            item,
            eligible=False,
            state=state,
            reasons=[],
            risks=["Actionability gate failed."],
            rejected=[state.value],
        )
    if market.state is not InstrumentState.OPEN:
        return _evaluation(
            item,
            eligible=False,
            state=DecisionState.DATA_ONLY,
            reasons=[],
            risks=[],
            rejected=["NON_OPEN_INSTRUMENT"],
        )
    if item.option_type is not OptionType.CALL:
        return _evaluation(
            item,
            eligible=False,
            state=DecisionState.INELIGIBLE,
            reasons=[],
            risks=[],
            rejected=["OPTION_TYPE_NOT_CALL"],
        )
    if (
        market.ask.price_btc_per_contract.availability is not Availability.AVAILABLE
        or market.ask.amount_base_btc.availability is not Availability.AVAILABLE
    ):
        return _evaluation(
            item,
            eligible=False,
            state=DecisionState.NO_EXECUTABLE_ASK,
            reasons=[],
            risks=[],
            rejected=["NO_EXECUTABLE_ASK"],
        )
    rejected: list[str] = []
    if not policy.min_dte_days <= metrics.dte_days <= policy.max_dte_days:
        rejected.append("DTE_OUTSIDE_EXPERIMENTAL_UNIVERSE")
    delta = market.greeks.delta_standard
    if delta.availability is not Availability.AVAILABLE or delta.value is None:
        rejected.append("DERIBIT_DELTA_UNAVAILABLE")
    elif not policy.min_absolute_delta <= abs(delta.value) <= policy.max_absolute_delta:
        rejected.append("ABSOLUTE_DELTA_OUTSIDE_POLICY")
    spread = metrics.relative_spread
    if spread.availability is not Availability.AVAILABLE or spread.value is None:
        rejected.append("RELATIVE_SPREAD_UNAVAILABLE")
    elif spread.value > policy.max_relative_spread:
        rejected.append("RELATIVE_SPREAD_EXCEEDS_POLICY")
    oi = market.open_interest_amount_base_btc
    if oi.availability is not Availability.AVAILABLE or oi.value is None:
        rejected.append("OPEN_INTEREST_UNAVAILABLE")
    elif oi.value < policy.min_open_interest_base_btc:
        rejected.append("OPEN_INTEREST_BELOW_POLICY")
    dce = metrics.entry_ask.dce
    if dce.availability is not Availability.AVAILABLE or dce.value is None:
        rejected.append("ENTRY_DCE_UNAVAILABLE")
    if rejected:
        return _evaluation(
            item,
            eligible=False,
            state=DecisionState.INELIGIBLE,
            reasons=[],
            risks=[],
            rejected=rejected,
        )
    return None


def scan_candidates(
    inputs: tuple[ScanInput, ...], policy: ScannerPolicy
) -> tuple[CandidateEvaluation, ...]:
    """Scan a same-as-of collection deterministically using an explicit key.

    Order: entry-ask DCE descending, DCE peer percentile descending, relative
    spread ascending, then instrument name.  No heterogeneous metrics are added
    and no BUY/SELL instruction is emitted.
    """
    names = [item.market.instrument_name for item in inputs]
    if len(names) != len(set(names)):
        raise ValueError("scanner inputs must contain one snapshot per instrument")
    exchange_times = [item.market.source.exchange_timestamp_ms for item in inputs]
    known_times = [timestamp for timestamp in exchange_times if timestamp is not None]
    if len(inputs) > 1 and (
        len(known_times) != len(exchange_times)
        or max(known_times) - min(known_times) > policy.max_snapshot_skew_ms
    ):
        flag = QualityFlag(
            QualityCode.SNAPSHOT_TIME_MISMATCH,
            QualitySeverity.BLOCK_ACTION,
            QualityScope.RUN,
            "scanner inputs do not share the configured exchange-time window",
        )
        return tuple(
            _evaluation(
                item,
                eligible=False,
                state=DecisionState.DATA_ONLY,
                reasons=[],
                risks=["Snapshot time gate failed."],
                rejected=["SNAPSHOT_TIME_MISMATCH"],
                extra_flags=(flag,),
            )
            for item in inputs
        )
    prequalified: list[ScanInput] = []
    output: dict[str, CandidateEvaluation] = {}
    for item in inputs:
        rejected = _prequalify(item, policy)
        if rejected is None:
            prequalified.append(item)
        else:
            output[item.market.instrument_name] = rejected

    cohorts: dict[str, list[ScanInput]] = {}
    for item in prequalified:
        delta = item.market.greeks.delta_standard.value
        assert delta is not None
        cohort_id = cohort_id_for(
            policy.cohort_policy, absolute_delta=delta, dte_days=item.metrics.dte_days
        )
        if cohort_id is None:
            output[item.market.instrument_name] = _evaluation(
                item,
                eligible=False,
                state=DecisionState.INELIGIBLE,
                reasons=[],
                risks=[],
                rejected=["OUTSIDE_PEER_BUCKET_POLICY"],
            )
        else:
            cohorts.setdefault(cohort_id, []).append(item)

    ranked: list[tuple[ScanInput, str, Decimal, Decimal, Decimal]] = []
    for cohort_id, members in cohorts.items():
        if len(members) < policy.cohort_policy.min_cohort_size:
            for item in members:
                output[item.market.instrument_name] = _evaluation(
                    item,
                    eligible=False,
                    state=DecisionState.INSUFFICIENT_HISTORY,
                    reasons=[],
                    risks=["Peer-relative ranking is unavailable."],
                    rejected=["COHORT_SAMPLE_BELOW_POLICY"],
                    cohort_id=cohort_id,
                    cohort_size=len(members),
                )
            continue
        dces = [member.metrics.entry_ask.dce.value for member in members]
        assert all(value is not None for value in dces)
        for item in members:
            dce = item.metrics.entry_ask.dce.value
            spread = item.metrics.relative_spread.value
            assert dce is not None and spread is not None
            peer = percentile_rank(dces, dce, higher_is_better=True)
            ranked.append((item, cohort_id, dce, peer.percentile, spread))

    ranked.sort(key=lambda row: (-row[2], -row[3], row[4], row[0].market.instrument_name))
    for position, (item, cohort_id, dce, percentile, spread) in enumerate(ranked, start=1):
        output[item.market.instrument_name] = _evaluation(
            item,
            eligible=True,
            state=DecisionState.ELIGIBLE,
            reasons=[
                f"ENTRY_DCE={dce} (primary lexicographic key)",
                f"DCE_PEER_PERCENTILE={percentile}",
                f"RELATIVE_SPREAD={spread} (tertiary key)",
            ],
            risks=["Experimental rule ranking; no utility score or trade instruction."],
            rejected=[],
            cohort_id=cohort_id,
            cohort_size=len(cohorts[cohort_id]),
            rank=position,
        )
    return tuple(output[item.market.instrument_name] for item in inputs)


def scan_candidate_analyses(
    inputs: tuple[ScanInput, ...], policy: ScannerPolicy
) -> tuple[CandidateAnalysis, ...]:
    """Return structured peer percentile/rank without changing the shared DTO.

    This is intentionally a separate additive view because the frozen
    ``CandidateEvaluation`` contract does not carry percentile fields.
    """
    evaluations = scan_candidates(inputs, policy)
    by_name = {item.market.instrument_name: item for item in inputs}
    eligible_by_cohort: dict[str, list[ScanInput]] = {}
    for evaluation in evaluations:
        if evaluation.eligible and evaluation.cohort_id is not None:
            eligible_by_cohort.setdefault(evaluation.cohort_id, []).append(
                by_name[evaluation.instrument_name]
            )
    result: list[CandidateAnalysis] = []
    for evaluation in evaluations:
        if evaluation.eligible and evaluation.cohort_id is not None:
            cohort = eligible_by_cohort[evaluation.cohort_id]
            dces = [member.metrics.entry_ask.dce.value for member in cohort]
            target = by_name[evaluation.instrument_name].metrics.entry_ask.dce.value
            assert target is not None and all(value is not None for value in dces)
            peer = percentile_rank(dces, target, higher_is_better=True)
            percentile = ValueState.available(peer.percentile)
            rank = ValueState.available(Decimal(peer.rank))
        else:
            percentile = ValueState.unavailable(
                evaluation.decision_state.value,
                "Peer percentile is unavailable because the candidate was not ranked.",
            )
            rank = ValueState.unavailable(
                evaluation.decision_state.value,
                "Peer rank is unavailable because the candidate was not ranked.",
            )
        result.append(CandidateAnalysis(evaluation, policy.rule_version, percentile, rank))
    return tuple(result)
