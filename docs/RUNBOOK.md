# Runbook

For a persistent Linux service, use `DEPLOYMENT_UBUNTU.md` or
`DEPLOYMENT_UBUNTU.zh-CN.md`. Commands below are development and bounded-evidence tools.

## Local deterministic acceptance

Requires Python 3.12+ (the project metadata sets `requires-python >=3.12`).

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m pytest tests/integration -q
python -m pytest tests/e2e -q
```

The end-to-end test creates temporary SQLite and presentation files only. It does not write a real database or contact Deribit.

## CLI

The CLI accepts a precomputed presentation snapshot; it does not ingest or calculate data:

```powershell
python -m btc_option_scanner.cli --snapshot .\snapshot.json portfolio
python -m btc_option_scanner.cli --snapshot .\snapshot.json scan
python -m btc_option_scanner.cli --snapshot .\snapshot.json roll
python -m btc_option_scanner.cli --snapshot .\snapshot.json stress
python -m btc_option_scanner.cli --snapshot .\snapshot.json inspect BTC-25DEC26-20000-C
```

All commands are serialization-only views. `NO_DATA` means the requested section is absent from the supplied immutable snapshot.

## Public smoke and soak

Run public smoke only as described in `DERIBIT.md`. The bounded collector soak uses only whitelisted public REST methods and writes a SQLite evidence database plus JSON summary:

```powershell
python scripts/public_collector_soak.py --duration-seconds 20 --interval-seconds 2 `
  --database .\docs\evidence\collector_soak.sqlite `
  --report .\docs\evidence\collector_soak.json
```

For the production-like public WebSocket path, including one forced reconnect,
book sequence tracking, fixture capture and close/reopen retention verification:

```powershell
python -m pip install -e ".[collector]"
python scripts/public_websocket_evidence.py `
  --duration-seconds 120 `
  --forced-reconnect-after-messages 30 `
  --database .\docs\evidence\ws_soak.sqlite `
  --report .\docs\evidence\ws_soak.json `
  --fixtures .\tests\fixtures\deribit\ws_soak
```

## Dashboard

```powershell
python -m pip install -e ".[dashboard]"
streamlit run src/btc_option_scanner/dashboard/app.py -- `
  --snapshot .\snapshot.json
```

The dashboard reads the same presentation JSON as the CLI and does not calculate metrics, rankings, NAV, roll cash flow, or stress values.

## Long-running collector health

The installed `btc-option-collector` is the only unbounded runtime. It reads deployment
settings from environment variables, handles SIGINT/SIGTERM, and writes an atomic status
file. The bounded evidence scripts remain acceptance probes and must not be installed as
services.

```bash
btc-option-collector
btc-option-healthcheck --status runtime/collector-status.json
```

Authenticated mode never enables trading. It authenticates before subscribing, validates
the effective read-only scope, and allows `raw` book only when credentials are present.
