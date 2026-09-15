"""Side-separated IV observations and history statistics.

All inputs are in memory protocol-friendly values.  Persistence, queries and
sampling schedules remain outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import sqrt
from typing import Iterable, Protocol

from btc_option_scanner.domain import (
    Availability,
    IVObservation,
    MetricSnapshot,
    PriceBasis,
    ValueState,
)
from btc_option_scanner.domain.market import MarketSnapshot
from btc_option_scanner.scanner.cohort import Bucket

IV_HISTORY_VERSION = "iv-history@0.1.0-experimental"


class IVObservationSource(Protocol):
    """Application-facing protocol; no repository implementation is imposed."""

    def observations_for(self, observation: IVObservation) -> Iterable[IVObservation]: ...


@dataclass(frozen=True, slots=True)
class IVBucketPolicy:
    bucket_policy_version: str
    delta_buckets: tuple[Bucket, ...]
    dte_buckets: tuple[Bucket, ...]

    def __post_init__(self) -> None:
        if not self.bucket_policy_version.strip() or not self.delta_buckets or not self.dte_buckets:
            raise ValueError("a version and both IV bucket dimensions are required")


@dataclass(frozen=True, slots=True)
class IVHistoryPolicy:
    policy_version: str
    min_observations: int
    expected_observations: int
    min_coverage_ratio: Decimal

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be empty")
        if self.min_observations < 1 or self.expected_observations < self.min_observations:
            raise ValueError("expected observations must be at least the positive warm-up minimum")
        if not Decimal("0") <= self.min_coverage_ratio <= Decimal("1"):
            raise ValueError("min_coverage_ratio must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class IVObservationBuildResult:
    observation: IVObservation | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class IVHistoryResult:
    history_version: str
    observation_count: int
    coverage_ratio: Decimal
    warm_up_complete: bool
    percentile: ValueState[Decimal]
    rank: ValueState[Decimal]
    z_score: ValueState[Decimal]
    mean_iv: ValueState[Decimal]
    population_standard_deviation: ValueState[Decimal]
    metric_side: PriceBasis
    delta_bucket_id: str
    dte_bucket_id: str


def _bucket(value: Decimal, buckets: tuple[Bucket, ...]) -> str | None:
    found = [bucket.bucket_id for bucket in buckets if bucket.contains(value)]
    if len(found) > 1:
        raise ValueError("IV bucket policy overlaps")
    return found[0] if found else None


def build_iv_observation(
    market: MarketSnapshot,
    metrics: MetricSnapshot,
    *,
    metric_side: PriceBasis,
    bucket_policy: IVBucketPolicy,
    config_version: str,
) -> IVObservationBuildResult:
    """Build one side-specific observation; unavailable IV is never backfilled."""
    if market.market_snapshot_id != metrics.market_snapshot_id:
        raise ValueError("market and metric snapshot IDs must match")
    if metric_side is PriceBasis.ENTRY_ASK:
        iv = market.iv.ask_annual_decimal
    elif metric_side is PriceBasis.LIQUIDATION_BID:
        iv = market.iv.bid_annual_decimal
    elif metric_side is PriceBasis.THEORETICAL_MARK:
        iv = market.iv.mark_annual_decimal
    else:
        raise ValueError("IV observations cannot use descriptive mid")
    delta = market.greeks.delta_standard
    if iv.availability is not Availability.AVAILABLE or iv.value is None:
        return IVObservationBuildResult(None, "IV_NOT_AVAILABLE_FOR_REQUESTED_SIDE")
    if delta.availability is not Availability.AVAILABLE or delta.value is None:
        return IVObservationBuildResult(None, "DERIBIT_DELTA_UNAVAILABLE")
    delta_bucket = _bucket(abs(delta.value), bucket_policy.delta_buckets)
    dte_bucket = _bucket(metrics.dte_days, bucket_policy.dte_buckets)
    if delta_bucket is None or dte_bucket is None:
        return IVObservationBuildResult(None, "OUTSIDE_IV_BUCKET_POLICY")
    return IVObservationBuildResult(
        IVObservation(
            market_snapshot_id=market.market_snapshot_id,
            instrument_name=market.instrument_name,
            as_of_utc=metrics.computed_at_utc,
            metric_side=metric_side,
            iv_annual_decimal=iv.value,
            delta_bucket_id=delta_bucket,
            dte_bucket_id=dte_bucket,
            bucket_policy_version=bucket_policy.bucket_policy_version,
            config_version=config_version,
        ),
        None,
    )


def _unavailable(code: str, message: str) -> ValueState[Decimal]:
    return ValueState.unavailable(code, message)


def evaluate_iv_history(
    target: IVObservation,
    history: Iterable[IVObservation],
    policy: IVHistoryPolicy,
) -> IVHistoryResult:
    """Evaluate only observations strictly earlier than target in its exact cohort.

    Empirical percentile is ``count(history_iv <= target_iv) / count`` and rank
    is ``1 + count(history_iv > target_iv)``.  Mean/std are population moments
    of prior observations.  No statistic is manufactured during warm-up.
    """
    matching = sorted(
        (
            item
            for item in history
            if item.as_of_utc < target.as_of_utc
            and item.metric_side is target.metric_side
            and item.delta_bucket_id == target.delta_bucket_id
            and item.dte_bucket_id == target.dte_bucket_id
            and item.bucket_policy_version == target.bucket_policy_version
        ),
        key=lambda item: (item.as_of_utc, item.market_snapshot_id),
    )
    values = [item.iv_annual_decimal for item in matching]
    count = len(values)
    coverage = min(Decimal("1"), Decimal(count) / Decimal(policy.expected_observations))
    warm = count >= policy.min_observations and coverage >= policy.min_coverage_ratio
    unavailable = _unavailable(
        "INSUFFICIENT_HISTORY", "IV warm-up or coverage requirement is not met"
    )
    if not warm:
        return IVHistoryResult(
            IV_HISTORY_VERSION,
            count,
            coverage,
            False,
            unavailable,
            unavailable,
            unavailable,
            unavailable,
            unavailable,
            target.metric_side,
            target.delta_bucket_id,
            target.dte_bucket_id,
        )
    mean = sum(values, Decimal("0")) / Decimal(count)
    variance = sum((value - mean) ** 2 for value in values) / Decimal(count)
    std = Decimal(str(sqrt(float(variance))))
    percentile = (
        Decimal(sum(value <= target.iv_annual_decimal for value in values))
        * Decimal("100")
        / Decimal(count)
    )
    rank = Decimal(1 + sum(value > target.iv_annual_decimal for value in values))
    z = (
        ValueState.available((target.iv_annual_decimal - mean) / std)
        if std > 0
        else _unavailable(
            "ZERO_STANDARD_DEVIATION", "z-score is undefined for a zero-variance cohort"
        )
    )
    return IVHistoryResult(
        IV_HISTORY_VERSION,
        count,
        coverage,
        True,
        ValueState.available(percentile),
        ValueState.available(rank),
        z,
        ValueState.available(mean),
        ValueState.available(std),
        target.metric_side,
        target.delta_bucket_id,
        target.dte_bucket_id,
    )
