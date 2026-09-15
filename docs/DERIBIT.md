# Deribit public-data operation

Only public Deribit payloads are in scope. The parser accepts instrument metadata and ticker payloads, preserves raw fields, then normalizes executable Bid/Ask only when the instrument is open and both price and amount are positive. Zero, missing or null quotes are unavailable; they are never substituted by Mark, Mid or Last.

The supplied test fixtures are recorded public mainnet responses under `tests/fixtures/deribit/`. They are suitable for deterministic parser and end-to-end testing; they are not current market data.

Optional live public smoke test (network access, no credentials, no order endpoint):

```powershell
$env:RUN_DERIBIT_LIVE_SMOKE = "1"
python -m pytest tests/deribit/test_live_smoke.py -q
```

Unset the variable to return to the default offline suite.

Install the public WebSocket collector dependency and run a bounded evidence soak:

```powershell
python -m pip install -e ".[collector]"
python scripts/public_websocket_evidence.py `
  --duration-seconds 120 `
  --forced-reconnect-after-messages 30 `
  --database .\docs\evidence\ws_soak.sqlite `
  --report .\docs\evidence\ws_soak.json `
  --fixtures .\tests\fixtures\deribit\ws_soak
```

The harness uses only unauthenticated public subscriptions. The forced reconnect
is a client-side fault injection. A synthetic gap fixture is derived from one
live increment and labelled as synthetic. Rare lifecycle events are reported as
`NOT_OBSERVED` rather than fabricated as live evidence.
