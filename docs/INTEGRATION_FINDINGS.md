# Integration findings

## F-001 — CLI `scan` cannot display application scanner results (P09 presentation)

**Status:** RESOLVED 2026-09-15. The CLI maps `scan` to the canonical `scanner` section and the fixed-fixture E2E test asserts the exact serialized result.

**Expected:** `python -m btc_option_scanner.cli --snapshot <snapshot> scan` returns the `scanner` application section created by `scanner_snapshot(...)`.

**Actual:** it returns `{ "status": "NO_DATA", ... }` even when the snapshot contains `sections["scanner"]` with candidates. The CLI passes the subcommand name `scan` to `_section`, whereas the application adapter serializes the section under `scanner`.

**Reproduction:**

```powershell
python -m pytest tests/e2e/test_fixed_public_snapshot_flow.py -q
```

The final CLI equality assertion fails at `tests/e2e/test_fixed_public_snapshot_flow.py`. Relevant files: `src/btc_option_scanner/cli.py` (`scan` command and `_section` lookup), `src/btc_option_scanner/application/presentation.py` (`scanner_snapshot`), and the acceptance test above.

**Suggested owner:** P09 presentation. Map CLI `scan` to the serialized `scanner` section (or version and rename the application section consistently). Do not make the test accept `NO_DATA`.

## F-002 — Presentation sections have incompatible lineage timestamps (P09 presentation)

**Status:** RESOLVED 2026-09-15. Roll and stress application outputs now carry their actual `as_of_utc`; adapters use that clock and `combine_snapshots` rejects mixed lineage.

**Expected:** scanner, portfolio, roll and stress application sections can be combined under the `PresentationSnapshot` invariant that requires one common `snapshot_time`.

**Actual:** `scanner_snapshot` and `portfolio_snapshot` use ISO timestamps, while `roll_snapshot` and `stress_snapshot` use `portfolio_snapshot_id` as `snapshot_time`. `combine_snapshots(...)` therefore rejects otherwise related results. The E2E test must assemble sections into an envelope directly to test all four CLI views.

**Reproduction:** construct the four application snapshots from the same portfolio evaluation and call `combine_snapshots(...)`; it raises `ValueError: cannot combine presentation snapshots with different lineage`.

**Relevant files:** `src/btc_option_scanner/application/presentation.py` (`portfolio_snapshot`, `roll_snapshot`, `stress_snapshot`, `combine_snapshots`) and `tests/e2e/test_fixed_public_snapshot_flow.py`.

**Suggested owner:** P09 presentation. Define a canonical presentation snapshot clock separately from immutable portfolio IDs, then preserve IDs as fields rather than using them as timestamps.

## F-003 — Sparse lifecycle notifications were parsed as full specs

**Status:** RESOLVED 2026-09-15. Official `instrument.state.option.BTC`
notifications contain only timestamp, state and instrument name. Lifecycle
handling now requires an existing REST-reconciled spec, records the sparse raw
event, versions the catalog state and never fabricates missing specification
fields.

## F-004 — No concrete public WSS/book sequence acceptance path

**Status:** RESOLVED 2026-09-15 for the bounded research scope. A host-restricted
public WSS adapter, ticker/book/lifecycle subscription manager, exact ungrouped
book tracker, client-injected reconnect and close/reopen retention harness are
covered by tests and a 120-second mainnet evidence run. A naturally occurring
lifecycle transition and multi-day endurance remain acceptance limitations.
