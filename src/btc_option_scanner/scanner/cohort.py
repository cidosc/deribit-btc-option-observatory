"""Versioned peer cohorts and tie-stable percentile calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Bucket:
    """Half-open interval [lower, upper), except a final open-ended bucket."""

    bucket_id: str
    lower_inclusive: Decimal
    upper_exclusive: Decimal | None

    def __post_init__(self) -> None:
        if not self.bucket_id.strip():
            raise ValueError("bucket_id must not be empty")
        if not self.lower_inclusive.is_finite():
            raise ValueError("bucket lower bound must be finite")
        if self.upper_exclusive is not None:
            if not self.upper_exclusive.is_finite() or self.upper_exclusive <= self.lower_inclusive:
                raise ValueError("bucket upper bound must be finite and greater than lower")

    def contains(self, value: Decimal) -> bool:
        return value >= self.lower_inclusive and (
            self.upper_exclusive is None or value < self.upper_exclusive
        )


@dataclass(frozen=True, slots=True)
class CohortPolicy:
    """No bucket/threshold default is implicit; callers version their policy."""

    policy_version: str
    delta_buckets: tuple[Bucket, ...]
    dte_buckets: tuple[Bucket, ...]
    min_cohort_size: int

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be empty")
        if not self.delta_buckets or not self.dte_buckets:
            raise ValueError("both delta and DTE buckets are required")
        if self.min_cohort_size < 1:
            raise ValueError("min_cohort_size must be at least one")
        for collection in (self.delta_buckets, self.dte_buckets):
            ids = [bucket.bucket_id for bucket in collection]
            if len(ids) != len(set(ids)):
                raise ValueError("bucket ids must be unique within each dimension")
            ordered = sorted(collection, key=lambda bucket: bucket.lower_inclusive)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if (
                    previous.upper_exclusive is None
                    or previous.upper_exclusive > current.lower_inclusive
                ):
                    raise ValueError("bucket intervals must be non-overlapping")


def _bucket_id(value: Decimal, buckets: tuple[Bucket, ...]) -> str | None:
    matches = [bucket.bucket_id for bucket in buckets if bucket.contains(value)]
    if len(matches) > 1:
        raise ValueError("bucket policy matched a value more than once")
    return matches[0] if matches else None


def cohort_id_for(
    policy: CohortPolicy, *, absolute_delta: Decimal, dte_days: Decimal
) -> str | None:
    """Return the versioned delta×DTE membership, or None outside policy bounds."""
    if not absolute_delta.is_finite() or not dte_days.is_finite():
        raise ValueError("cohort inputs must be finite")
    delta_id = _bucket_id(abs(absolute_delta), policy.delta_buckets)
    dte_id = _bucket_id(dte_days, policy.dte_buckets)
    if delta_id is None or dte_id is None:
        return None
    return f"{policy.policy_version}:{delta_id}x{dte_id}"


@dataclass(frozen=True, slots=True)
class Percentile:
    percentile: Decimal
    rank: int
    sample_size: int


def percentile_rank(
    values: Iterable[Decimal], target: Decimal, *, higher_is_better: bool
) -> Percentile:
    """Tie-stable empirical percentile and competition rank.

    Percentile is the proportion no worse than target, so 100 means best in
    cohort for either direction. Rank is 1 plus the number strictly better;
    equal values retain the same rank.  Inputs are sorted for deterministic
    behaviour even if the source iteration order changes.
    """
    materialized = sorted(values)
    if (
        not materialized
        or not target.is_finite()
        or any(not value.is_finite() for value in materialized)
    ):
        raise ValueError("target and cohort values must be finite and non-empty")
    if higher_is_better:
        no_worse = sum(value <= target for value in materialized)
        better = sum(value > target for value in materialized)
    else:
        no_worse = sum(value >= target for value in materialized)
        better = sum(value < target for value in materialized)
    return Percentile(
        Decimal(no_worse) * Decimal("100") / Decimal(len(materialized)),
        better + 1,
        len(materialized),
    )
