"""Interpret IV percentile as a labelled research regime, never an action."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from btc_option_scanner.domain import Availability

from .iv_history import IVHistoryResult

REGIME_VERSION = "iv-regime@0.1.0-experimental"


class IVRegime(str, Enum):
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class RegimePolicy:
    policy_version: str
    low_percentile_inclusive: Decimal
    high_percentile_inclusive: Decimal

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be empty")
        if (
            not Decimal("0")
            <= self.low_percentile_inclusive
            < self.high_percentile_inclusive
            <= Decimal("100")
        ):
            raise ValueError("regime percentiles must satisfy 0 <= low < high <= 100")


@dataclass(frozen=True, slots=True)
class RegimeAssessment:
    regime_version: str
    policy_version: str
    regime: IVRegime
    reason: str
    percentile: Decimal | None


def classify_iv_regime(history: IVHistoryResult, policy: RegimePolicy) -> RegimeAssessment:
    """Classify only complete history; a label is not a trade recommendation."""
    if (
        not history.warm_up_complete
        or history.percentile.availability is not Availability.AVAILABLE
    ):
        return RegimeAssessment(
            REGIME_VERSION,
            policy.policy_version,
            IVRegime.INSUFFICIENT_HISTORY,
            "IV percentile is unavailable during warm-up.",
            None,
        )
    percentile = history.percentile.value
    assert percentile is not None
    if percentile <= policy.low_percentile_inclusive:
        regime = IVRegime.LOW
    elif percentile >= policy.high_percentile_inclusive:
        regime = IVRegime.HIGH
    else:
        regime = IVRegime.NORMAL
    return RegimeAssessment(
        REGIME_VERSION,
        policy.policy_version,
        regime,
        "Percentile-labelled IV research regime; not an action signal.",
        percentile,
    )
