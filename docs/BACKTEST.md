# Backtest / Event-Time Replay

## Status and scope

This is a research infrastructure layer, not evidence that Strategy A--E is
profitable.  It operates only on supplied, recorded public-data facts.  The
first day of collection therefore cannot be represented as a complete
long-horizon executable backtest.

Versions are fixed on every result:

| Field | Current value |
| --- | --- |
| replay schema | `event-time-replay@1.0.0` |
| backtest schema | `backtest-schema@1.0.0` |
| metric version | `backtest-metrics@1.0.0` |

The caller must also provide immutable strategy, configuration, and fee-model
versions.  Changing a price side, cost convention, metric calculation, or
configuration requires a new version; historical results are not overwritten.

## Replay clock and visibility

`ReplayEvent` is ordered by `(observed_at_utc, sequence, event_id)`.  Exchange
time is retained in the source DTO but is not used to make a fact visible: a
decision may only use data that had arrived at the client clock.

1. A catalogue revision is visible only after its event has been processed.
2. A market snapshot must have a previously visible catalogue revision; an
   out-of-order snapshot raises an error instead of looking up the current
   catalogue.
3. `ReplayState` exposes only the catalogue and most recent market snapshots
   accumulated so far.  It has no repository or "latest" query path.
4. `rolling_high_usd` scans only snapshots with
   `as_of - window <= observed_at <= as_of`.  Future spot highs, IV values, and
   instruments are excluded.

At a later delisting revision, an earlier `state_at` still observes the earlier
open catalogue state.  A strategy must not use a later listing, current active
set, manually entered high, or future IV to reconstruct an old decision.

## Strategy protocol

`BacktestStrategy.on_event(StrategyContext)` receives the event, causal
`ReplayState`, reserve, and positions, and returns zero or more
`StrategyIntent`s.  The framework supplies no A--E rules or parameter tuning.
`StrategySkeleton` and `strategy_skeletons()` expose named inert skeletons for
Strategy A, B, C, D, and E; each emits `HOLD` until a separately versioned
research rule is supplied.

Strategy intents are `BUY`, `SELL`, or `HOLD`.  They can optionally share a
`roll_id`; a roll is counted only when that ID has both an executed buy and
sell.  A strategy's context is deliberately unable to ask for a current
catalogue or later snapshot.

## Valuation and failure closure

`ValuationMode.EXECUTABLE` applies Buy at recorded Ask, Sell at recorded Bid,
and values open long positions at Bid liquidation value.  It requires a
verified, evidence-referenced `FeeModel`; missing Ask/Bid, unverified fee,
insufficient reserve, absent visible instrument, terminal catalog state, or
expiry boundary, or insufficient position records a failed trade with an
explicit code.  It never
falls back to mark, mid, last, or a current quote.
An executable-mode result with a failed requested trade has
`is_executable=False`; an empty or hold-only executable tape remains a
methodological executable replay, subject to its coverage label.

`ValuationMode.MARK_ONLY` uses recorded Mark for research transactions and NAV.
Its result is always `is_executable=False` and, where market data exists,
credibility `MARK_ONLY_RESEARCH`; an empty tape is instead labelled
`NO_HISTORY`.  It cannot be relabelled as executable later.

## Result interface

`BacktestResult` records the strategy, config/schema/metric versions,
valuation mode, trade audit trail, BTC/USD equity points, and an explicit
executable flag.  It includes:

- turnover in BTC notional;
- half-spread cost versus recorded two-sided midpoint (unavailable when a
  required trade has no two-sided quote);
- fees in BTC (unavailable only when an executed-cost total cannot be known);
- completed roll count;
- BTC and USD CAGR, maximum drawdown, and Sortino interfaces;
- coverage and credibility metadata: clocks, event counts, complete-NAV ratio,
  two-sided-quote ratio, limitations, and credibility label.

CAGR requires two positive values separated in time.  Sortino requires at
least three positive NAV observations and a non-zero downside deviation.  The
framework returns `None` with insufficiency metadata rather than inventing a
return for empty or sparse data.

USD NAV uses the current event's explicitly recorded index price.  If it is
absent, USD NAV is incomplete; no external or current index is queried.

## Data limitations

Deribit public history does not guarantee full historical top-of-book, Greeks,
IV, or instrument-lifecycle coverage.  Credibility is therefore about the
supplied tape, not a universal data-quality claim.  `EXECUTABLE_REPLAY_LIMITED`
means the supplied events and verified fee model passed the local accounting
path; it does not prove that all historical market opportunities were covered.

No backtest output is a profitability conclusion, investment recommendation,
or a basis for an order.
