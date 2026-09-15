# P06 Quantitative Model Change Proposal

**Status:** implemented as an additive, experimental application layer.

## Contract impact

No shared domain, Deribit adapter, storage, portfolio, strategy, CLI, or UI
contract was changed. P06 consumes `MarketSnapshot` and produces existing
`MetricSnapshot` / `CandidateEvaluation` values plus additive models-scanner
DTOs. The core contract metric version stays `core-metrics@1.0.0`.

## Additions

- Unit-explicit Black-76 Call pricing and analytic Greeks with numeric boundary
  handling;
- a separately named `deribit-bs-observed@1.0.0-diagnostic` local comparison;
- side-specific Ask/Bid/Mark extrinsic, ICR, optionality and DCE;
- moneyness, log-moneyness, spread and liquidity features;
- versioned peer cohort percentile and lexicographic scanner rules;
- strict side-separated IV observations, warm-up/coverage statistics and regime
  labelling.

## Failure closure

Stale/action-blocked snapshots are data-only; missing Ask has no entry DCE or
eligibility; non-open instruments have no actionability; small cohorts and
incomplete IV coverage have no rank/percentile; zero-standard-deviation IV
series have no z-score. Mark remains theoretical and never supplies an entry.

## Explicit non-decisions

This proposal does not select a final GCE, gamma basis, Utility, peer bucket
boundaries, ranking weights, IV windows, fee formula, rounding direction,
forward convention, or production BTC conversion of a local model. All GCE
candidates remain unavailable until canonical gamma is verified and are
excluded from ranking.

## Validation

The added tests cover Black-76 boundaries (including zero IV and expiry), deep
ITM/OTM behavior, Ask-DCE monotonicity, deterministic scanner output, cohort
undercoverage, stale/no-Ask/non-open closure, IV side/future filtering,
warm-up, zero standard deviation, and regime gating.
