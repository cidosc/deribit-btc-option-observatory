"""Read-only presentation DTO adapters.

This package deliberately consumes immutable application outputs; it does not
query storage, fetch a quote, or calculate a business metric.
"""

from .presentation import (
    PRESENTATION_SCHEMA_VERSION,
    PresentationSnapshot,
    combine_snapshots,
    historical_recommendations_snapshot,
    load_presentation_snapshot,
    market_overview_snapshot,
    portfolio_snapshot,
    roll_snapshot,
    save_presentation_snapshot,
    scanner_snapshot,
    stress_snapshot,
)

__all__ = [
    "PRESENTATION_SCHEMA_VERSION",
    "PresentationSnapshot",
    "combine_snapshots",
    "historical_recommendations_snapshot",
    "load_presentation_snapshot",
    "market_overview_snapshot",
    "portfolio_snapshot",
    "roll_snapshot",
    "save_presentation_snapshot",
    "scanner_snapshot",
    "stress_snapshot",
]
