# Runtime checks — 2026-09-15 Asia/Shanghai

| Check | Result |
|---|---|
| `python -m ruff check .` | PASS — all checks passed |
| `python -m compileall -q src tests scripts` | PASS — exit 0 |
| `python -m pip check` | PASS — no broken requirements |
| `python -m pytest --cov=btc_option_scanner --cov-report=term -q` | PASS — 87 passed, 1 opt-in live test skipped, 80% total coverage after adding live recommendation quality and frequency gates |
| 20-second public live-recommendation smoke | PASS — 60 ticker subscriptions, 9 runs, 3 quality-gated candidates around 55k–60k strikes at a 76.8k index, post-stop fail-close, SQLite integrity/WAL; Feishu delivery NOT RUN because no webhook was provided |
| Fixed-fixture E2E with retained runtime artifacts | PASS — 1 passed |
| `RUN_DERIBIT_LIVE_SMOKE=1` public REST smoke | PASS — 1 passed in 1.54s |
| 12-second bounded public collector soak | PASS — 7/7 normalized and actionable, 0 errors |
| 120-second public WebSocket reconnect/retention soak | PASS — 2 connections, 111 ticker, 2 book snapshots, 63 real increments, injected gap blocked, 177/177 raw events replayed after reopen |
| CLI `scan`, `inspect`, `portfolio`, `roll`, `stress` | PASS — all returned populated results |
| Streamlit startup and HTTP probe | PASS — server started and `127.0.0.1:8765` returned HTTP 200; then stopped normally |
| Public long-running daemon smoke | PASS — live ticker/book ingestion, health status, WAL/integrity, and graceful final state |
| Authenticated raw-book daemon smoke | PASS — Mainnet read-only OAuth, one raw snapshot plus one real increment, exit 0, no token/secret persisted |
| Private/trading API source scan | PASS — `public/auth` is the only auth method; no `private/*`, order, transfer, withdrawal, or embedded credential in source/config |
| Ubuntu/systemd runtime | NOT RUN BY USER DIRECTION — deployment is documented for target-server validation |

## P12 addendum — silent-market live recommendation coherence

| Check | Result |
|---|---|
| `python -m pytest tests/test_live_refresh.py -q` | PASS — 18 tests: original 2-second-window starvation regression, cache/persistence decoupling, refresh lineage, independent cadence, silent steady state, strict skew/stale fail-closed, refresh failure/timeout, environment mismatch, heartbeat timeout, disconnect closure, Feishu throttle, storage bounds, buffer priority, prefilter versioning, observation split |
| `python scripts/silent_steady_regression.py --cycles 10 --report docs/evidence/silent_steady_regression_20260915.json` | PASS — offline, no network; initial scattered burst collapses to 3 coherent inputs (old behaviour), 10 cycles completed including 7 classified steady-state cycles with 12 coherent inputs each and all ELIGIBLE |
| `python scripts/public_scanner_lite_soak.py --duration-seconds 780 --report docs/evidence/live_scanner_lite_soak_20260915.json` | PASS — public Mainnet, 13 minutes, warm-up separated from steady state, per-cycle telemetry, no credentials |
| `python -m pytest -q --cov=btc_option_scanner --cov-report=term` | PASS — 105 passed, 1 opt-in live test skipped, ~85% total coverage |
| `python -m ruff check .` / `compileall` / `pip check` / `git diff --check` | PASS |

The ordinary pytest run deliberately skips the opt-in network test. It was run separately and passed. The collector details are in `collector_soak_20260915.json`; its raw/normalized evidence is in `collector_soak_20260915.sqlite`.
