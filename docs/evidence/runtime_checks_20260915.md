# Runtime checks — 2026-09-15 Asia/Shanghai

| Check | Result |
|---|---|
| `python -m ruff check .` | PASS — all checks passed |
| `python -m compileall -q src tests scripts` | PASS — exit 0 |
| `python -m pip check` | PASS — no broken requirements |
| `python -m pytest --cov=btc_option_scanner --cov-report=term -q` | PASS — 85 passed, 1 opt-in live test skipped, 80% total coverage after adding live recommendations and the alert transport |
| 20-second public live-recommendation smoke | PASS — 30 ticker subscriptions, 8 runs, up to 10 eligible candidates, Top 3 output, post-stop fail-close, SQLite integrity/WAL; Feishu delivery NOT RUN because no webhook was provided |
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

The ordinary pytest run deliberately skips the opt-in network test. It was run separately and passed. The collector details are in `collector_soak_20260915.json`; its raw/normalized evidence is in `collector_soak_20260915.sqlite`.
