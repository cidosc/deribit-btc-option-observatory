# P05 Data Spine Change Proposal / Evidence Gaps

## Accepted implementation boundary

P05 implements only public, read-only Deribit surfaces. The default real-time
feed is `ticker.{instrument}.agg2`; `ticker.{instrument}.100ms` requires an
explicit `TickerSubscription(interval="100ms")`. No private endpoint, account
surface, credential, or order method exists in the package.

Raw messages are deterministically SHA-256 addressed and written before
normalization. Replaying the same raw payload is idempotent. The SQLite schema
keeps raw events, slow-moving instrument versions, canonical market snapshots,
and system events, with all three clocks (`exchange_timestamp`, `observed_at`,
`ingested_at`) retained.

## Dependency proposal: production WebSocket and ORM adapter

The locked project dependency list contains only PyYAML. P05 therefore uses a
standard-library HTTPS client and an injected async JSON connection protocol;
this makes reconnect/backoff/heartbeat/resubscription testable without adding
an unapproved runtime dependency. The SQLite repository is explicit,
repository-shaped portable SQL rather than a third-party ORM.

For a deployable WebSocket endpoint and a PostgreSQL implementation, approve a
separate dependency change adding a maintained async WebSocket client and an
ORM/migration package (for example SQLAlchemy plus Alembic), then implement a
PostgreSQL repository conforming to `SqliteRepository`'s operations. Do not add
these packages by editing `pyproject.toml` inside P05 without approval.

## Evidence still needed

The supplied fixtures intentionally lack:

- an open ticker with one/both empty Bid/Ask sides;
- an `instrument.state.option.BTC` lifecycle notification;
- an ungrouped incremental book update with `prev_change_id`;
- a complete raw `public/get_instruments` catalog response.

P05 does not infer missing shapes. Delivered zero-quote behavior is covered;
open empty-side semantics are fail-closed through the generic non-positive or
absent quote rule. Book reconstruction is deliberately not subscribed by the
main feed and is not claimed as sequence-recovery validated until an
incremental fixture is captured.

## Operations

Run normal tests with `pytest -q`; they have no network dependency. The public
REST smoke is opt-in and separately marked: in PowerShell run
`$env:RUN_DERIBIT_LIVE_SMOKE='1'; pytest -m live -q`. It does not authenticate
or trade.
