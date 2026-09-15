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

## F-005 — scanner-lite recommendations starve to ~3 inputs after the initial burst

**Status:** RESOLVED 2026-09-15 (P12 live-coherence work package).

**Expected:** scanner-lite should keep producing periodic, coherent, traceable
recommendation batches while the market is silent or updates are sparse, without
changing any `domain-contract-v1` gate.

**Actual:** every received ticker only updated memory when its per-instrument
persistence sample was due (`collector.on_message` returned early otherwise),
`maybe_recommend` only fired from that persisted branch, and there was no
independent recommendation schedule. `select_coherent_snapshots` anchors on the
newest `exchange_timestamp_ms` and keeps only snapshots within the configured
2-second skew window and 30-second age. With 60 subscribed instruments whose
last-update times scatter, the window could stably retain about 3 inputs, and
the cohort minimum then made every round `DATA_ONLY`. The initial subscription
burst masked this, so a 20-second smoke could not detect it. Separately, the
60 subscription slots sampled the whole 30-day collection buffer uniformly, so
buffered DTEs could crowd out core 120–240 DTE instruments.

**Resolution:** keep the three cadences separate and name them: every legal WS
ticker now updates an in-memory cache (new `PublicDataSpine.observe_ticker`
splits normalization from persistence), ordinary persistence stays sampled per
profile, and a dedicated async loop schedules recommendation cycles on its own
cadence. Each cycle derives a bounded, versioned refresh set (`live-refresh-`
prefilter@1.0.0`, wider than the final Delta/spread gates, with deterministic
rotation probes for blind-spot avoidance), actively refreshes it through the
whitelisted `public/ticker` REST endpoint of the same environment as the
WebSocket, and only hands same-cycle responses that pass max-age and the
2-second skew window to the canonical scanner. Refresh raw events, market
snapshots, metric snapshots and the scanner run persist first; the candidate
file references those exact IDs. Insufficient cohort, refresh failure, timeout,
stale, or skew outcomes atomically replace the previous candidate file with a
fail-closed state and stable reason codes (`CACHE_ONLY`, `REFRESH_FAILED`,
`REFRESH_TIMEOUT`, `SNAPSHOT_SKEW`, `SNAPSHOT_STALE`,
`COHORT_SAMPLE_BELOW_POLICY`, `ENVIRONMENT_MISMATCH`). Subscription selection
now prioritizes core 120–240 DTE instruments and only fills leftover slots from
the collection buffer; the core/buffer split is reported in status. Alert
throttling (30-minute hard minimum, 6-hour unchanged reminder) and every
scanner gate are unchanged.

**Reproduction / evidence:**

```powershell
python -m pytest tests/test_live_refresh.py -q
python scripts/silent_steady_regression.py --cycles 10 \
  --report docs/evidence/silent_steady_regression_20260915.json
python scripts/public_scanner_lite_soak.py --duration-seconds 780 \
  --report docs/evidence/live_scanner_lite_soak_20260915.json
```
