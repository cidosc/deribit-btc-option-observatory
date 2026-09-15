# Final Acceptance Report

> Review date: 2026-09-15 (Asia/Shanghai)  
> Contract: `domain-contract-v1` / `domain-schema-v1`  
> Verdict: **Accepted for the current read-only research scope and as a supervised Linux deployment candidate, with explicit data-history, lifecycle, and target-host limitations. It is not accepted as a production trading system.**

## 1. Gate result

| Gate | Result | Acceptance evidence |
|---|---|---|
| Gate 0 — facts and units | PASS WITH LIMITATIONS | Public fixtures and the Deribit field contract define premium, reference-price, IV, quote and Greek semantics. Real book increment shape is now captured. Unverified gamma, open empty-side shape and a naturally observed lifecycle event remain unresolved. |
| Gate 1 — domain and configuration | PASS | `domain-schema-v1`, versioned metrics/config, typed availability and fixed quality severities are enforced by DTO/config tests. No domain-contract version was changed. |
| Gate 2 — reliable data spine | PASS WITH LIMITATIONS | Public REST and concrete WebSocket adapters, optional fail-closed read-only OAuth, raw-first persistence, exact book sequence tracking, reconnect/resubscribe, SQLite WAL, lineage-safe retention, disk floor and health status pass. Mainnet authenticated `raw` book captured a real snapshot and increment. A natural lifecycle transition and multi-day capacity/fault soak remain absent. |
| Gate 3 — metrics and scanner | PASS | Ask-entry, Bid-liquidation and Mark-theory metrics remain separate. Scanner produces deterministic lexicographic ranks, cohort sizes, peer percentile/rank, explanations and rejection reasons. Mixed exchange-time windows now fail closed. |
| Gate 4 — historical IV | PASS FUNCTIONALLY / RESEARCH LIMITED | Windowing, bucket, insufficient-sample, zero-variance and regime tests pass. Available history is insufficient for credible empirical thresholds or performance conclusions. |
| Gate 5 — Portfolio and Roll | PASS | Mark and executable Bid NAV remain separate. Roll uses old Bid/new Ask, verified fee input, explicit rounding, reserve conservation, exposure/interval/utility gates, and same-window validation. |
| Gate 6 — Stress and presentation | PASS | Stress outputs remain explicitly model-conditional and non-executable. CLI and Dashboard consume the same immutable presentation snapshot and contain no core financial recomputation. Streamlit startup returned HTTP 200. |
| Gate 7 — replay and runtime acceptance | PASS WITH LIMITATIONS | Event-time replay/future-visibility tests, full pytest, fixed-fixture E2E, live public smoke, REST/WebSocket soak, public daemon smoke, authenticated raw-book daemon smoke, all CLI views and Dashboard startup passed. Ubuntu/systemd runtime and multi-day endurance remain for the target server. |

No Gate has a failing blocker inside the declared read-only research scope. The limitations above must not be re-labelled as production validation.

### Why Gates 0, 2, 4 and 7 remain qualified

- **Gate 0 is evidence-limited, not code-failure-limited.** One deep-ITM sample cannot establish a universal Deribit gamma scale or local BTC pricing convention; an open empty-side ticker and a naturally emitted lifecycle notification are also still absent. The safe response is to keep those fields/features unavailable rather than infer them.
- **Gate 2 is operationally limited.** The live WebSocket path, read-only auth, raw book, reconnect, resubscription, WAL and retention now pass, but short Windows runs and a client-injected disconnect do not prove multi-day Linux stability, capacity, exchange-side outages or a lifecycle transition across expiry.
- **Gate 4 is history-limited.** The algorithms and edge-case tests pass, but the stored observations are far too sparse to calibrate percentile windows, regimes, cohort thresholds or stable liquidity distributions.
- **Gate 7 inherits the unresolved evidence limits.** End-to-end runtime checks pass, but final operational acceptance requires the user's Ubuntu/systemd deployment, longer history and natural lifecycle/fault evidence. This qualification does not indicate a failing offline E2E or presentation command.

## 2. Mandatory contract review

- **Price side:** new-long/entry metrics use executable Ask; long liquidation and old-leg roll proceeds use executable Bid; Mark is descriptive/NAV/model input only. Missing sides stay unavailable and are never filled from mark/mid/last.
- **Units:** option premium is BTC/contract; index, strike, underlying and delivery are USD/BTC; IV is normalized from percent input to annual decimal; quantity is contracts after explicit base-BTC/contract conversion. Raw Greeks remain retained, and unverified normalized gamma is blocked.
- **Ticker/book boundary:** ticker processing does not require `prev_change_id`. The ungrouped-book tracker accepts an initial snapshot, applies only continuous increments, preserves raw events, clears derived levels on a mismatch, emits `BOOK_SEQUENCE_GAP`, and waits for a new snapshot.
- **Time and failure closure:** ingestion flags stale and out-of-order ticker data. Roll, scanner, Portfolio and Stress reject mixed exchange-time windows; presentation composition rejects differing as-of/config/model lineage.
- **Traceability:** SQLite schema v2 stores raw events, instrument specs, market snapshots and canonical derived evidence rows. Metric, ScannerRun, PortfolioSnapshot, RollEvaluation and StressRun rows retain immutable parent IDs, schema/metric/config versions and clocks where present. The fixed E2E test walks this chain.
- **Scanner explanation:** candidate rows retain market/metric IDs, decision state, eligibility, rank, cohort ID/size, structured peer percentile/rank, reasons, risks, rejection reasons and quality flags.
- **Portfolio/Roll conservation:** current NAV excludes historical premium-paid cost basis; roll reserve follows `reserve_after = reserve_before + old Bid proceeds - new Ask cost - both fees`. The accounting identity is asserted in tests.
- **Backtest causality:** replay order is observed event time plus recorded tie-break sequence. Strategies receive only state visible at that clock; executable mode uses the then-visible Ask for buys and Bid for sells, while mark-only output is labelled non-executable.
- **Presentation boundary:** CLI/Dashboard only rename/serialize precomputed application outputs. They do not calculate metrics, ranks, NAV, roll cash flow or stress.
- **Trading safety:** the Deribit method surface contains public market-data methods plus `public/auth`; it contains no `private/*`, order, transfer or withdrawal endpoint. Credentials are optional environment-only inputs, secrets are excluded from repr/status/log/database, and returned scopes reject write or unknown authority. Backtest BUY/SELL intents remain simulation records. All execution-related gates remain disabled in `config.yaml`.

## 3. Blocker fixes made

| Area | Files | Reason |
|---|---|---|
| CLI scanner view | `src/btc_option_scanner/cli.py`, E2E test | `scan` looked for `scan` while the canonical section is `scanner`, returning false `NO_DATA`. |
| Presentation lineage | `application/presentation.py`, `strategy/roll.py`, `strategy/stress.py`, tests | Roll/Stress used a portfolio ID as snapshot time, making same-run sections impossible to combine safely. Outputs now carry actual UTC as-of time. |
| Snapshot-window closure | `scanner/rules.py`, `portfolio/valuation.py`, `strategy/roll.py`, `strategy/stress.py`, tests | Scanner/Portfolio/Stress could accept mixed market clocks. They now enforce configured exchange-time windows; roll also validates UTC inputs. |
| Derived evidence persistence | `data/models.py`, `data/repository.py`, E2E test | SQLite stopped at normalized market rows. Schema v2 adds idempotent canonical derived rows and parent-ID lineage without changing domain DTOs. |
| Collector acceptance | `scripts/public_collector_soak.py`, runbook | There was no approved bounded soak command or machine-readable evidence. Added a public-only, rate-limited REST harness. |
| Dashboard packaging and docs | `pyproject.toml`, `README.md`, Runbook/Architecture/Roadmap/Findings | Streamlit was used but not declared; commands and known-issue documents were stale after integration fixes. |
| Public WebSocket evidence | `deribit/websocket.py`, `deribit/book.py`, `deribit/lifecycle.py`, ingestion, tests and `scripts/public_websocket_evidence.py` | Added the missing concrete WSS adapter, correct sparse lifecycle handling, book sequence fail-close, fixture provenance, forced-reconnect and retention evidence. |
| Long-running deployment | `collector.py`, repository, `deploy/`, bilingual README/deployment docs, tests | Added an unbounded supervised daemon, periodic catalog reconciliation, bounded subscriptions, signal shutdown, health status, WAL/busy timeout, lineage-safe retention, low-disk fail-stop, systemd/journal/logrotate configuration and pinned collector constraints. |
| Optional read-only auth | `deribit/auth.py`, WebSocket adapter, probes and tests | Added environment-only OAuth, explicit effective-scope validation, authenticated raw-book support and fatal handling for invalid/write scopes without adding any private or trading method. |
| Storage-safe defaults | `collector.py`, environment templates, bilingual README and tests | Added a default `scanner-lite` profile with 60 aggregated tickers, 60-second per-instrument persistence sampling and no books. Full ticker/book evidence capture is now explicit opt-in and status reports estimated daily growth. |
| Live recommendations and alerts | `alerts.py`, collector integration, environment templates and tests | Reuses the existing Ask-based, fail-closed scanner on coherent WebSocket snapshots; persists metric/run lineage, atomically publishes a manual-review candidate file, and optionally sends signed, deduplicated plain-text alerts through an approved Feishu custom-bot HTTPS webhook. |
| Live candidate quality/frequency | `scanner/rules.py`, `alerts.py`, collector integration and tests | Cheap low-Delta OTM calls could dominate DCE and any changed Top set could alert every scan. Live defaults now require a configurable rolling 120–240 DTE interval, absolute Delta 0.80–0.90, spread at most 5%, a three-contract peer cohort, a hard 30-minute delivery interval and a six-hour unchanged reminder. |

These changes are limited to observed acceptance failures and their tests/documentation; no safety gate was removed or weakened.

## 4. Test and runtime evidence

- Static/packaging: Ruff PASS; compileall PASS; `pip check` reports no broken requirements.
- Full suite: **87 passed, 1 skipped**, total line coverage **80%**. The lower aggregate percentage reflects the service orchestration and new alert transport; authentication, configuration, universe selection, persistence sampling, recommendation quality/frequency gates, Feishu payload/signature validation, health, WAL/retention and fatal-scope paths have direct tests plus live daemon evidence. The skipped test is network-opt-in and was run separately.
- Live public smoke: **1 passed in 1.54s**, no credentials and no private endpoint.
- Fixed fixture E2E: **1 passed** with retained SQLite and presentation artifacts under `docs/evidence/e2e_runtime/`.
- Collector soak: **PASS**, 12 requested seconds, 7/7 samples normalized, 7 actionable, 0 errors, 8 new raw events and 7 new market snapshots. See `docs/evidence/collector_soak_20260915.json` and its SQLite companion.
- Public WebSocket reconnect/retention soak: **PASS**, 120 requested seconds, 2 connections after one injected disconnect, 111 ticker notifications, 2 book snapshots, 63 real book increments, 1 injected sequence gap, and 177/177 raw events replayable after database reopen. See `docs/evidence/ws_soak_120s_20260915.json` and its SQLite companion.
- Formal public daemon smoke: **PASS**, bounded subscription selection, live ticker/book ingestion, atomic health status, WAL and SQLite integrity passed; final state contained no fatal error.
- Authenticated raw-book daemon smoke: **PASS**, Mainnet OAuth returned only connection/read authority; the 20-second process exited 0 after programmatic shutdown and retained 52 tickers plus one raw snapshot and one raw change. Testnet correctly rejected the Mainnet credential. Sanitized evidence is in `docs/evidence/deployment_smoke_20260915.json`; no secret or token is stored there.
- Public live-recommendation smoke: **PASS**, 60 ticker subscriptions, no books/auth/bot delivery, 9 scanner runs and 3 quality-gated candidates. At a 76,843.15 USD/BTC index the Top 3 strikes were 60k/58k/55k with Delta 0.846/0.867/0.894 and spreads below 3.74%; low-Delta 102k–112k calls were excluded. Graceful stop replaced the candidate file with `DATA_STALE/COLLECTOR_STOPPED`, and SQLite remained `integrity=ok` in WAL mode. See `docs/evidence/live_recommendation_smoke_20260915.json`.
- Linux/systemd runtime: **NOT RUN BY USER DIRECTION**. README and unit configuration were reviewed for theoretical portability; actual host permissions, SIGTERM and endurance are delegated to the target server deployment.
- CLI: `scan`, `inspect`, `portfolio`, `roll`, `stress` all returned populated results from the same retained snapshot.
- Dashboard: Streamlit started on port 8765 and returned **HTTP 200**, then was stopped normally.
- Consolidated command log: `docs/evidence/runtime_checks_20260915.md`.

SQLite smoke databases are retained in the local acceptance workspace but intentionally
ignored by Git; committed JSON summaries and deterministic fixtures carry the portable
evidence without growing the repository with mutable database files.

## 5. Items not fully validated

1. The lifecycle channel was accepted on both initial connection and resubscription, but no natural state transition occurred during the 120-second window. Its deterministic fixture is explicitly synthetic from a live catalog item, not represented as received exchange traffic.
2. Reconnect was a controlled client-side fault injection, not a naturally occurring network outage. The 120-second run validates behavior and retention, not multi-hour/day capacity or stability.
3. Dashboard acceptance proves process startup/HTTP availability and snapshot-only architecture; it is not a pixel-by-pixel browser QA of every page.
4. Deribit gamma scale and a universal local BTC pricing conversion remain intentionally unverified. Corresponding production feature gates remain off.
5. Real fee rules are not approved. Executable roll cash flow therefore remains disabled in normal configuration; tests use an explicit test-only verified fixture schedule.
6. Ubuntu/systemd behavior has not been executed in this workspace. The supplied service unit, paths and commands are theoretically portable, but target-host validation remains required.
7. Feishu payload, signing and response handling are contract-tested, but delivery was not sent to a user-provided webhook. The local recommendation file and scanner lineage do not depend on Feishu being available.

## 6. Data-history research limits

The repository contains a small fixed fixture set, a 7-sample REST soak and a 120-second one-instrument WebSocket run. That is not enough to estimate stable IV percentiles, regime transitions, cohort robustness, liquidity distributions, transaction-cost tails, stale/skew production thresholds, or any Strategy A–E risk/return statistic. Backtest code correctly labels absent/sparse history, but no claim of profitability, statistical significance, executable capacity or production-grade parameter calibration is supported.

## 7. Read-only and execution status

**The system is currently read-only and has no real trading capability.** It can retrieve whitelisted public market data, optionally authenticate a connection for read-only/raw market data, normalize/store evidence, compute research outputs, simulate historical intents, and render saved results. It cannot call any `private/*` method, create/edit/cancel orders, transfer funds, withdraw, or route execution. Configuration keeps all execution-related gates disabled.

## 8. Minimal next phase

The smallest useful next phase is the user's Ubuntu/systemd deployment using the bilingual README, followed by a 24-hour check and preferably an option-expiry window. Preserve the resulting status, journal, disk-growth and lifecycle evidence, then calibrate data-quality thresholds before considering model or strategy expansion; keep every execution gate off.
