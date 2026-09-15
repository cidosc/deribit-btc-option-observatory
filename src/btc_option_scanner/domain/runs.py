"""Immutable scanner and operational-run boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import DecisionState, RunStatus
from .primitives import (
    CORE_METRIC_VERSION,
    DOMAIN_SCHEMA_VERSION,
    QualityFlag,
    require_nonempty,
    require_utc,
)


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    instrument_name: str
    market_snapshot_id: str
    metric_snapshot_id: str
    eligible: bool
    decision_state: DecisionState
    reasons: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    rejected_reasons: tuple[str, ...] = ()
    quality_flags: tuple[QualityFlag, ...] = ()
    rank: int | None = None
    cohort_id: str | None = None
    cohort_size: int | None = None

    def __post_init__(self) -> None:
        for name in ("instrument_name", "market_snapshot_id", "metric_snapshot_id"):
            require_nonempty(getattr(self, name), name)
        if self.rank is not None and self.rank < 1:
            raise ValueError("rank must be positive")
        if self.cohort_size is not None and self.cohort_size < 0:
            raise ValueError("cohort_size cannot be negative")
        for name in ("reasons", "risks", "rejected_reasons", "quality_flags"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class ScannerRun:
    scanner_run_id: str
    as_of_utc: datetime
    market_snapshot_ids: tuple[str, ...]
    metric_snapshot_ids: tuple[str, ...]
    universe_filter_version: str
    config_version: str
    max_snapshot_skew_ms: int
    run_status: RunStatus
    decision_state: DecisionState
    candidate_evaluations: tuple[CandidateEvaluation, ...]
    quality_flags: tuple[QualityFlag, ...] = ()
    schema_version: str = DOMAIN_SCHEMA_VERSION
    metric_version: str = CORE_METRIC_VERSION

    def __post_init__(self) -> None:
        for name in ("scanner_run_id", "universe_filter_version", "config_version"):
            require_nonempty(getattr(self, name), name)
        object.__setattr__(self, "as_of_utc", require_utc(self.as_of_utc, "as_of_utc"))
        if self.max_snapshot_skew_ms < 0:
            raise ValueError("max_snapshot_skew_ms cannot be negative")
        if len(self.market_snapshot_ids) != len(set(self.market_snapshot_ids)):
            raise ValueError("market_snapshot_ids must be unique")
        if len(self.metric_snapshot_ids) != len(set(self.metric_snapshot_ids)):
            raise ValueError("metric_snapshot_ids must be unique")
        object.__setattr__(self, "market_snapshot_ids", tuple(self.market_snapshot_ids))
        object.__setattr__(self, "metric_snapshot_ids", tuple(self.metric_snapshot_ids))
        object.__setattr__(self, "candidate_evaluations", tuple(self.candidate_evaluations))
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))


@dataclass(frozen=True, slots=True)
class SystemEvent:
    system_event_id: str
    event_at_utc: datetime
    observed_at_utc: datetime
    component: str
    severity: str
    event_code: str
    message: str
    related_ids: tuple[str, ...] = ()
    schema_version: str = DOMAIN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("system_event_id", "component", "severity", "event_code", "message"):
            require_nonempty(getattr(self, name), name)
        object.__setattr__(self, "event_at_utc", require_utc(self.event_at_utc, "event_at_utc"))
        object.__setattr__(
            self, "observed_at_utc", require_utc(self.observed_at_utc, "observed_at_utc")
        )
        object.__setattr__(self, "related_ids", tuple(self.related_ids))
