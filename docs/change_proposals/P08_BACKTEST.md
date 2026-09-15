# P08 Backtest / Replay Change Proposal

## Status

Accepted as an additive analytics-layer implementation.  It adds no storage,
domain, scanner, portfolio, strategy, configuration, CLI, or dashboard change.

## Public surfaces

- `analytics.replay.ReplayEvent`, `EventTimeReplay`, and `ReplayState` provide
  deterministic observed-time replay and historical rolling-high access.
- `analytics.backtest.BacktestStrategy`, `StrategyIntent`, `StrategySkeleton`,
  `BacktestConfig`, and `run_backtest` provide the strategy A--E protocol,
  accounting, performance, attribution, and coverage result surface.

## Contract decisions

- Visibility is defined by recorded `observed_at_utc`, then recorded sequence
  and event ID; it is never inferred from exchange time or a current database
  query.
- A market event without an already observed catalogue record fails immediately.
  A future catalog entry cannot make a prior snapshot valid.
- A rolling high reads only the state tape accumulated at the requested clock.
- Executable entry is Ask, exit and executable valuation are Bid.  Mark-only is
  an explicit separate mode and cannot be marked executable.
- Executable accounting requires an explicit verified fee assumption.  Missing
  quotes, fee evidence, reserve, position, visible catalog, or open state fail
  closed with a trade record; they do not use mark/mid/current data.
- Results preserve `config_version`, `metric_version`, and `schema_version`.
  Cost attribution separately exposes turnover, midpoint spread cost, fees,
  and completed roll count.

## Validation evidence

`tests/analytics/test_replay.py` covers future-catalog exclusion, future-high
and future-IV exclusion, rolling-window causality, and missing catalog failure.
`tests/analytics/test_backtest.py` covers mark-only labelling, missing Ask and
unverified-fee closure, deterministic replay, cost attribution, and empty
history.  Existing P05--P07 focused tests were also run before this package:
21 passed, 1 explicitly opt-in live smoke test skipped.

## Non-decisions

This change does not tune Strategy A--E, select a winning strategy, fill gaps
in public historical data, claim fee correctness beyond the caller's cited
assumption, or make a profitability statement.
