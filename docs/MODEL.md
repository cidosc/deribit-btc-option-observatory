# P06 Quant Model

## Scope and versions

P06 adds deterministic, pure calculation and scanning functions.  It does not
read a network, database, portfolio, UI, or emit a transaction command.

| Item | Version / status |
| --- | --- |
| Core metric vector | `core-metrics@1.0.0` (published contract) |
| Quant extensions | `quant-metrics@0.1.0-experimental` |
| Generic pricing | `black76-reporting@1.0.0` |
| Fixture regression adapter | `deribit-bs-observed@1.0.0-diagnostic` |
| Scanner rules | `scanner-lexicographic@0.1.0-experimental` |
| IV history / regime | `iv-history@0.1.0-experimental` / `iv-regime@0.1.0-experimental` |

## Pricing and Greeks

`black76_call(Black76Inputs)` evaluates a European Call in USD per contract:

`exp(-rT) * m * (F*N(d1) - K*N(d2))`.

Its BTC result is a reporting conversion `USD price / supplied index`, never an
entry, exit, or mark replacement. The result records the exact conversion
reference and label; the diagnostic adapter labels its distinct underlying
reference rather than calling it an index conversion. Inputs carry their units
in their names.
At expiry a caller must explicitly supply settlement/reference; zero IV uses
the deterministic discounted-forward limit. Invalid, negative, infinite, or
unit-ambiguous inputs fail rather than being clipped.

`deribit_observed_diagnostic(...)` is deliberately separate. It is the
fixture-backed Black-Scholes-equivalent comparison using Deribit
`underlying_price`, raw rate, and mark IV. Its local values and differences are
saved in `GreekComparison`, including the explicitly labelled local BTC
conversion reference; it never overwrites any `deribit_*` value. The
adapter is diagnostic only, because one fixture does not establish a universal
forward or BTC conversion convention.

## Metrics

`build_metric_bundle(snapshot, as_of_utc, config_version)` returns a contract
`MetricSnapshot` plus `QuantitativeMetrics`. It calculates exact DTE,
index-based intrinsic, moneyness `index/strike`, log-moneyness, bid/ask size,
open interest, 24h volume and two-sided spread.

For each permitted price side independently:

- `extrinsic = premium - intrinsic_index_btc`
- `ICR = intrinsic_index_btc / premium`
- `optionality = extrinsic / premium`
- `DCE = deribit_delta_standard / premium`

Ask is exclusively used for entry, Bid exclusively used for liquidation, and
Mark is theoretical only. Missing side prices return typed unavailable values;
negative extrinsic is retained and flagged rather than clipped. At fixed delta,
an increasing Ask cannot increase entry DCE.

`GceCandidates` retains five named candidates per side: spot-scaled gamma,
dollar-scaled gamma, delta response, USD convexity, and raw gamma per BTC.
They are never summed or ranked. Current canonical Deribit gamma is
`UNVERIFIED`, so all are unavailable by design.

## Scanner

`scan_candidates(tuple[ScanInput, ...], ScannerPolicy)` is an application
facing in-memory protocol. The caller supplies all experimental bounds,
bucket definitions, cohort minimum, `rule_version`, and
`universe_filter_version`; there are no secret weights or final defaults.

The quality gate closes before eligibility: stale or action-blocked snapshots
become `DATA_STALE`/`DATA_ONLY`; a missing Ask becomes
`NO_EXECUTABLE_ASK`; non-open instruments have no actionability rating.
Eligible calls require DTE, confirmed standard delta, two-sided relative
spread, open interest, and executable Ask.

Peers share a versioned `absolute-delta × DTE` cohort. A cohort below its
explicit sample threshold becomes `INSUFFICIENT_HISTORY` with no rank.
Otherwise the fully disclosed sort key is:

1. entry-Ask DCE descending;
2. DCE peer percentile descending;
3. relative spread ascending;
4. instrument name ascending.

Each evaluation includes reasons, risks, rejected reasons, cohort membership,
size, and rank. `ELIGIBLE` means only that all research gates passed; it is not
a BUY/SELL signal and this package intentionally does not generate
`ATTRACTIVE`. `scan_candidate_analyses` provides the corresponding structured
percentile and peer rank as an additive DTO, since the frozen
`CandidateEvaluation` does not contain percentile fields.

## IV history and regime

`build_iv_observation` maintains strict `metric_side`, delta bucket and DTE
bucket separation. `evaluate_iv_history(target, history, policy)` filters to
strictly earlier observations in the exact same side/bucket/policy. It reports
count, coverage, warm-up, empirical percentile, competition rank, population
mean/std, and z-score. Before both count and coverage warm-up requirements are
met, percentile/rank/z-score/mean/std are unavailable. Zero variance permits
percentile and rank but makes z-score explicitly unavailable.

`classify_iv_regime` labels an available percentile LOW/NORMAL/HIGH according
to caller-provided, versioned percentile thresholds. It otherwise returns
`INSUFFICIENT_HISTORY`; a regime label is explanation only, not an action.

## Interfaces available to P07/P08

- `models.black76_call`, `models.deribit_observed_diagnostic`, and
  `models.build_metric_bundle` for reproducible model/metric work;
- `scanner.Bucket`, `CohortPolicy`, `ScannerPolicy`, `ScanInput`,
  `ScanInputSource`, and `scan_candidates` for application services using
  persisted snapshots; `CandidateAnalysis` and `scan_candidate_analyses` for
  structured peer statistics;
- `analytics.IVObservationSource`, `IVBucketPolicy`, `IVHistoryPolicy`,
  `build_iv_observation`, `evaluate_iv_history`, `RegimePolicy`, and
  `classify_iv_regime` for a storage adapter and explanations.

P07 must still provide its own fee evidence, quantity rounding policy, and
portfolio/roll interfaces. P08 may display the saved DTOs, but must not
recompute metrics or silently join snapshots from different times.
