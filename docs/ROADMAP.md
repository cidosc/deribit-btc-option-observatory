# Roadmap / acceptance state

- P05–P09 unit coverage is present for ingestion, replay, metrics, scanner, portfolio, roll, stress and presentation.
- Fixed-fixture end-to-end acceptance is in `tests/e2e/test_fixed_public_snapshot_flow.py`.
- Failure closure integration coverage is in `tests/integration/test_failure_closure.py`.
- Live public smoke remains opt-in. Both bounded REST and 120-second public WebSocket reconnect/retention evidence are available; naturally observed lifecycle transitions and multi-day capacity evidence remain future work.
- The CLI scanner-section and presentation timestamp blockers documented in `docs/INTEGRATION_FINDINGS.md` are resolved.
