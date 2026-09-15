# P07 Portfolio / Roll / Stress application outputs

## Status

Accepted as an additive application-layer implementation.  No shared domain,
metric, scanner, configuration, or execution interface is changed.

## Public application services

- `portfolio.value_portfolio(definition, positions, as_of_utc,
  usd_conversion_index_price_usd_per_btc, config_version)` returns a
  `PortfolioValuation` wrapping the frozen `PortfolioSnapshot`.  A
  `PortfolioPosition` binds a long `PortfolioLeg`, immutable valuation market
  snapshot, and `premium_paid_btc` cost basis.
- `strategy.evaluate_roll(RollInput)` returns `RollDecision`, wrapping the
  frozen `RollEvaluation`, cash-flow reconciliation, aging assessment,
  independent gate outcomes, spread proxy, and Delta rounding error.
- `strategy.stress_portfolio(...)` returns `StressApplicationRun`, wrapping
  the frozen `StressRun` plus complete conditional scenario results.

All services are pure, in-memory, and cannot submit an order or fetch a new
quote.  Callers must persist their returned application DTOs and the input
snapshot/metric IDs together.

## Cash and quantity semantics

For a roll, old long calls are sold only at their recorded Bid and new calls
are bought only at their recorded Ask:

`release_btc = old_quantity * old_bid - executable_new_quantity * new_ask - old_fee - new_fee`

`reserve_after_btc = reserve_before_btc + release_btc`

The service records and checks the identity:

`reserve_before + proceeds = reserve_after + purchase_cost + old_fee + new_fee`.

Positive release remains in BTC reserve; it is never implicitly reinvested.
Negative reserve is rejected as `INSUFFICIENT_RESERVE` rather than borrowing.
`EQUAL_CONTRACTS` and `EQUAL_STANDARD_DELTA` respectively produce the same
theoretical quantity as the old position, or `q_old * delta_old / delta_new`.
Both preserve their theoretical and transaction-grid-rounded values.

## Explicit unresolved inputs

The frozen `InstrumentSpec` has a minimum trade amount but no quantity
increment.  `RoundingPolicy` therefore requires an explicit versioned quantity
grid and direction; it supplies no hidden default.  A result below minimum is
zero and fails closed as non-executable.

OQ-05 remains open.  `FeeSchedule` is a caller-provided premium-notional
assumption with a version, evidence reference, verification flag, and optional
per-leg cap.  A missing or unverified schedule produces unavailable fees and
cashflows with `FEE_UNVERIFIED`; zero is never assumed.  This is a research
comparison input, not a claim of Deribit fee semantics.  Wiring an evidenced
schedule into runtime configuration needs a separate config/decision change.

## State machine and gates

The ordered failure closure is: stale/action-blocked or snapshot-window mismatch
→ `DATA_STALE`; missing old Bid → `NO_EXECUTABLE_BID`; missing new Ask →
`NO_EXECUTABLE_ASK`; invalid quantity/Delta/fee evidence → `INVALID_INPUT`.
Only then can the machine offer `ROLL_CANDIDATE`.

Time aging (DTE) and price aging (standard Delta) are separately recorded.
After executable economics, the following remain independent booleans:

- aging trigger;
- minimum roll interval;
- dimensionless utility-improvement threshold, with entry/continue hysteresis;
- BTC fee and reserve-requirement budgets;
- post-roll standard-Delta exposure range;
- optional IV-normalization gate (`WAIT_FOR_IV_NORMALIZATION`).

Failures of normal economic/strategy gates return `HOLD`; lack of verified
quotes returns its specific non-executable state.  Utility is never added to or
subtracted from BTC cashflow.

## Stress semantics

Stress inputs always retain index return, elapsed days, absolute IV-vol-point
shock, rate, forward policy/version, smile policy/version, and model version.
The application supports `preserve_carry_rate`, `preserve_basis_ratio`,
`explicit_forward`, and `at_expiry_delivery`; IV is a flat or sticky-strike
parallel conditional shock.  It uses `black76-reporting@1.0.0` only as a
conditional reprice and labels every result
`MODEL_CONDITIONAL_NOT_EXECUTABLE_REPRICE`.  USD values are derived using the
single shocked index; reserve BTC is unchanged.  Crossing expiry uses the
explicit shocked-index delivery payoff.

Normalized portfolio Gamma remains unavailable because OQ-01 is open.  Raw
Gamma is exposed separately; standard Delta, Theta, and Vega aggregate only in
their distinct documented units.

## Consumers

P08 can persist/replay these application DTOs without re-querying a latest
market state.  P09/UI can render mark vs liquidation NAV, historical premium
paid, aggregated Greeks, roll gates/cash reconciliation, and conditional stress
labels without recomputing any business formula.
