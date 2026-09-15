# P09 Presentation / application-output persistence proposal

## Implemented boundary

P09 provides read-only CLI and Streamlit adapters over one immutable
`presentation-snapshot@1.0.0` JSON document.  Both clients show exactly that
same document, including snapshot time, model/config versions, price-basis
wording, data-quality states, reasons, risks, and rejected reasons.  The
adapter never queries SQLite directly and never invokes scanner, portfolio,
roll, stress, or pricing functions.

The seven pages are Market Overview, Option Scanner, IV Surface, Portfolio,
Roll, Stress, and Historical Recommendations.  A section may include a
precomputed Plotly JSON figure (`plotly_figure`); the Dashboard deserializes it
only.  This keeps every chart sourced from the same immutable output snapshot
and prevents a visualization from inventing a metric or a threshold.

Commands are:

- `python -m btc_option_scanner.cli --snapshot FILE scan`
- `python -m btc_option_scanner.cli --snapshot FILE inspect INSTRUMENT`
- `python -m btc_option_scanner.cli --snapshot FILE roll|portfolio|stress`

Start the dashboard with:

`streamlit run src/btc_option_scanner/dashboard/app.py -- --snapshot FILE`

## Required upstream interface

P05's repository persists raw events, instruments, and market snapshots only.
P06 scanner outputs and P07 portfolio/roll/stress application outputs are pure
in-memory DTOs and have no persisted query interface.  Consequently P09 cannot
truthfully provide a live/latest dashboard or reconstruct historical
recommendations without either recalculating business results or directly
hydrating internal stored rows—both are outside its authority.

Approve an application-output store/query contract that can retrieve immutable
`ScannerRun + CandidateAnalysis`, `PortfolioValuation`, `RollDecision`, and
`StressApplicationRun` bundles by snapshot/run ID, plus a history index.  That
contract should emit the P09 presentation snapshot as an export, preserving
all referenced IDs and versions.  Once present, a narrow repository adapter
may replace the JSON import without changing CLI/Dashboard rendering.

## Dependency gap

The locked runtime dependencies do not include Streamlit or Plotly, and P09 is
not authorized to edit `pyproject.toml`.  The modules use lazy imports so unit
tests and CLI remain usable without these optional packages.  Approve their
runtime dependencies (or provide them in the deployment image) to run the
dashboard command.
