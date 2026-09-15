"""Stable, JSON-safe read models for CLI and Dashboard.

The functions here only rename/format fields already present in P05--P07
outputs.  In particular they never calculate DCE, utility, roll cash flow or
NAV.  A serialized :class:`PresentationSnapshot` is the common boundary used
by both presentation clients.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from btc_option_scanner.portfolio import PortfolioValuation
from btc_option_scanner.scanner import CandidateAnalysis
from btc_option_scanner.strategy import RollDecision, StressApplicationRun

PRESENTATION_SCHEMA_VERSION = "presentation-snapshot@1.0.0"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _state(value: Any) -> dict[str, Any]:
    """Expose an availability state without inventing a fallback value."""
    return _jsonable(value)


@dataclass(frozen=True, slots=True)
class PresentationSnapshot:
    """A portable, immutable application-output envelope.

    ``sections`` intentionally contains only display values copied from known
    application DTOs.  It is also an approved hand-off seam until upstream
    persistence can retrieve P06/P07 outputs by immutable snapshot IDs.
    """

    snapshot_time: str
    model_version: str
    config_version: str
    sections: Mapping[str, Any]
    schema_version: str = PRESENTATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.snapshot_time or not self.model_version or not self.config_version:
            raise ValueError("presentation metadata cannot be empty")
        if self.schema_version != PRESENTATION_SCHEMA_VERSION:
            raise ValueError("unsupported presentation snapshot schema")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def save_presentation_snapshot(snapshot: PresentationSnapshot, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_presentation_snapshot(path: str | Path) -> PresentationSnapshot:
    """Load a precomputed presentation snapshot; do not hydrate ORM/domain DTOs."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"snapshot_time", "model_version", "config_version", "sections", "schema_version"}
    if not isinstance(raw, dict) or set(raw) != required or not isinstance(raw["sections"], dict):
        raise ValueError("input is not a complete presentation snapshot")
    return PresentationSnapshot(**raw)


def combine_snapshots(*snapshots: PresentationSnapshot) -> PresentationSnapshot:
    """Combine only same-lineage views; never join different market times."""
    if not snapshots:
        raise ValueError("at least one presentation snapshot is required")
    first = snapshots[0]
    lineage = (first.snapshot_time, first.model_version, first.config_version)
    sections: dict[str, Any] = {}
    for snapshot in snapshots:
        if (snapshot.snapshot_time, snapshot.model_version, snapshot.config_version) != lineage:
            raise ValueError("cannot combine presentation snapshots with different lineage")
        overlap = set(sections).intersection(snapshot.sections)
        if overlap:
            raise ValueError(f"duplicate presentation sections: {', '.join(sorted(overlap))}")
        sections.update(snapshot.sections)
    return PresentationSnapshot(*lineage, sections)


def market_overview_snapshot(
    markets: tuple[Any, ...], *, model_version: str, config_version: str
) -> PresentationSnapshot:
    """Display raw P05 market fields; derived entry/liquidation values stay upstream."""
    if not markets:
        raise ValueError("a market overview requires at least one MarketSnapshot")
    first = markets[0]
    rows = []
    for market in markets:
        rows.append(
            {
                "instrument_name": market.instrument_name,
                "market_snapshot_id": market.market_snapshot_id,
                "state": market.state.value,
                "actionable": market.actionable,
                "bid_btc_per_contract": _state(market.bid.price_btc_per_contract),
                "ask_btc_per_contract": _state(market.ask.price_btc_per_contract),
                "mark_btc_per_contract": _state(market.mark_btc_per_contract),
                "index_price_usd_per_btc": _state(market.index_price_usd_per_btc),
                "quality_flags": _jsonable(market.quality_flags),
            }
        )
    return PresentationSnapshot(
        first.source.observed_at_utc.isoformat(),
        model_version,
        config_version,
        {"market_overview": {"rows": rows}},
    )


def historical_recommendations_snapshot(runs: tuple[Any, ...]) -> PresentationSnapshot:
    """Adapt persisted ScannerRun DTOs; this function does not replay history."""
    if not runs:
        raise ValueError("historical presentation requires at least one ScannerRun")
    first = runs[0]
    return PresentationSnapshot(
        first.as_of_utc.isoformat(),
        first.metric_version,
        first.config_version,
        {"historical_recommendations": {"runs": _jsonable(runs)}},
    )


def scanner_snapshot(run: Any, analyses: tuple[CandidateAnalysis, ...]) -> PresentationSnapshot:
    """Adapt a ScannerRun plus its P06 analyses without re-ranking candidates."""
    rows = []
    for analysis in analyses:
        evaluation = analysis.evaluation
        rows.append(
            {
                "instrument_name": evaluation.instrument_name,
                "market_snapshot_id": evaluation.market_snapshot_id,
                "metric_snapshot_id": evaluation.metric_snapshot_id,
                "state": evaluation.decision_state.value,
                "eligible": evaluation.eligible,
                "rank": evaluation.rank,
                "cohort_id": evaluation.cohort_id,
                "cohort_size": evaluation.cohort_size,
                "entry_dce_peer_percentile": _state(analysis.entry_dce_peer_percentile),
                "entry_dce_peer_rank": _state(analysis.entry_dce_peer_rank),
                "reasons": list(evaluation.reasons),
                "risks": list(evaluation.risks),
                "rejected_reasons": list(evaluation.rejected_reasons),
                "quality_flags": _jsonable(evaluation.quality_flags),
            }
        )
    return PresentationSnapshot(
        run.as_of_utc.isoformat(),
        run.metric_version,
        run.config_version,
        {"scanner": {"run": _jsonable(run), "candidates": rows}},
    )


def portfolio_snapshot(valuation: PortfolioValuation) -> PresentationSnapshot:
    """Adapt P07 portfolio output, preserving mark/liquidation separation."""
    data = _jsonable(valuation)
    snapshot = valuation.snapshot
    return PresentationSnapshot(
        snapshot.as_of_utc.isoformat(),
        snapshot.metric_version,
        snapshot.config_version,
        {"portfolio": data},
    )


def roll_snapshot(decisions: tuple[RollDecision, ...]) -> PresentationSnapshot:
    if not decisions:
        raise ValueError("a roll presentation snapshot requires at least one decision")
    first = decisions[0].evaluation
    snapshot_time = decisions[0].as_of_utc
    if any(decision.as_of_utc != snapshot_time for decision in decisions[1:]):
        raise ValueError("roll decisions do not share one as-of time")
    return PresentationSnapshot(
        snapshot_time.isoformat(),
        first.metric_version,
        first.config_version,
        {"roll": {"evaluations": _jsonable(decisions)}},
    )


def stress_snapshot(run: StressApplicationRun) -> PresentationSnapshot:
    stress = run.stress_run
    return PresentationSnapshot(
        run.as_of_utc.isoformat(),
        stress.metric_version,
        stress.config_version,
        {"stress": _jsonable(run)},
    )
