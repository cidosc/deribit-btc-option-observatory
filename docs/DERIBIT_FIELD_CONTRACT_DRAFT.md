# Deribit BTC Inverse Option Field Contract — Gate 0 Draft

Status: **DRAFT / coding input, not a strategy specification**  
Research date: 2026-09-14 (UTC)  
Scope: Deribit mainnet public market data for BTC inverse options only.

## 1. Source and operating boundary

Use JSON-RPC 2.0 over `https://www.deribit.com/api/v2/` and
`wss://www.deribit.com/ws/api/v2`. This contract permits only the public
methods/channels named below. No credential is required.

The initial REST CLI capture on 2026-09-14 did not inherit the enabled local
proxy and could not resolve `www.deribit.com`. Public mainnet WebSocket capture
then succeeded. Fixtures now contain an unmodified two-sided BTC-call ticker,
book snapshot, instrument specification and index updates. The REST-like
request/response methods were sent through the public WebSocket transport;
direct HTTPS REST transport and same-field runtime equality remain
`UNVERIFIED_LIVE`. Official documentation examples are evidence, not fixtures.

Canonical storage rules:

- Preserve raw JSON and JSON `null` unchanged. Never replace a missing quote
  with mark, zero, or a synthetic price.
- Store exchange timestamps as UTC epoch milliseconds; record local
  `observed_at` and `ingested_at` separately.
- Preserve API IV and Greeks in `*_raw` fields until their exact scaling is
  resolved. A normalized IV decimal may be populated only after a live sample
  establishes whether a published number such as 50 means 50% or 50.0.
- A field absent from an option response is distinct from a field present with
  `null`; parsers must retain that distinction or emit a schema-quality flag.

## 2. Instrument discovery and specifications

### Required bootstrap

`public/get_instruments`

| Request parameter | Required value | Contract / frequency |
|---|---|---|
| `currency` | `BTC` | Required; discovery universe. |
| `kind` | `option` | Required; exclude futures/spot/combos. |
| `expired` | `false` | Request active catalog at bootstrap; periodically reconcile, never high-frequency poll. |

For a newly seen instrument call `public/get_instrument` with
`instrument_name` when a complete current specification is needed. Treat
`get_instruments` as catalog bootstrap and lifecycle reconciliation, not a
market-data polling loop.

### Instrument fields (from `get_instruments` / `get_instrument`)

| Raw field | Type / nullable | Raw unit / meaning | Canonical field and handling |
|---|---|---|---|
| `instrument_name` | string / no | Exchange symbol | Primary natural key; retain verbatim. |
| `instrument_id` | integer / docs do not require it on every schema | Exchange numeric identifier | Store nullable integer; do not derive from symbol. |
| `kind` | string / no | `option` | Validate exactly `option`. |
| `option_type` | enum / option only | `call` or `put` | Canonical enum. |
| `strike` | number / option only | USD per BTC strike | `strike_usd_per_btc`; do not treat as BTC. |
| `expiration_timestamp` | integer / no | UTC epoch ms | `expiration_timestamp_ms`. |
| `creation_timestamp` | integer / no | UTC epoch ms | `creation_timestamp_ms`. |
| `state` | enum / no | lifecycle state | Store raw enum and apply §4. |
| `is_active` | boolean / no | tradability indicator | Keep independently from `state`; do not infer one from the other. |
| `base_currency` | enum / no | underlying/base coin | Expected BTC for scope; validate live. |
| `quote_currency` | enum / docs/sample conflict | quoted currency | Preserve response verbatim; see discrepancy §8. |
| `counter_currency` | enum / documented | counter currency | Expected USD for BTC inverse sample; validate live. |
| `settlement_currency` | enum / optional in generic schema | settlement coin | Expected BTC; nullable only for generic spot schema, not assumed for options. |
| `price_index` | string / no | index name | Expected `btc_usd` for BTC inverse sample; retain source value. |
| `contract_size` | integer / no | option base-coin multiplier | Store as `contract_size_base_coin`; BTC inverse specification says 1 BTC. |
| `min_trade_amount` | number / no | options: underlying base coin | `min_trade_amount_base_coin`; docs/support say BTC option minimum is 0.1. |
| `tick_size` | number / no | price increment in quoted option premium unit | Preserve; apply `tick_size_steps`, never hard-code. |
| `tick_size_steps` | array / no | conditional increments | Preserve raw and implement price-dependent tick selection. |
| `maker_commission`, `taker_commission` | number / docs field | rate | Retain raw; fee interpretation is outside Gate 0. |

**BTC inverse option specification confirmed by official support documentation:**
base/underlying asset BTC; contract multiplier/size 1 BTC; premium is shown for
1 BTC; cash settlement in BTC; minimum order size 0.1 option contract; the
corresponding expiry forward is the relevant future mark or a synthetic future.
The source describes option book prices as BTC and strike as USD. This is the
binding price-unit interpretation pending a live mainnet response.

## 3. Market data surfaces

### 3.1 REST bootstrap / point reconciliation

| Method | Required parameters | Intended use | Do not use as |
|---|---|---|---|
| `public/ticker` | `instrument_name` | One instrument’s top-of-book, mark, IV/Greeks, OI, stats. | Chain-wide high-frequency polling. |
| `public/get_order_book` | `instrument_name`, `depth` in documented set 1…10000 | REST book snapshot/resync and depth. | A substitute for WS incremental state. |
| `public/get_index_price` | `index_name` (for example `btc_usd`) | Explicit index bootstrap/reconciliation. | Option forward/underlying substitute. |

`get_order_book` returns sorted bids descending and asks ascending. It carries
`change_id`, but REST bootstrap has no `prev_change_id` continuity contract;
sequence validation begins only after a WebSocket book snapshot.

### 3.2 WebSocket channels

| Channel | Purpose / payload contract | Production boundary |
|---|---|---|
| `instrument.state.option.BTC` | Instrument-name, state, timestamp lifecycle events. | Subscribe continuously; retrieve/refresh metadata after a newly listed symbol; reconcile periodically because disconnected periods may be missed. |
| `ticker.{instrument_name}.agg2` | Primary scan feed: top quote, mark, stats, OI, state, index/underlying, IV and Greeks. | Default; events aggregated over 2 seconds. No sequence rule. |
| `ticker.{instrument_name}.100ms` | Same ticker shape at 100-ms aggregation. | Restrict to held/active subset when 2-second aggregation is insufficient. No sequence rule. |
| `ticker.{instrument_name}.raw` | Finest ticker interval; docs say authorized users only. | Do not make it a public/no-key dependency. |
| `book.{instrument_name}.agg2` or `.100ms` | Full first snapshot followed by incremental price levels `[action, price, amount]`. | Optional depth/impact feed. Apply sequence handling in §5. |
| `deribit_price_index.btc_usd` | `index_name`, `price`, `timestamp`. | Maintain a separate index clock; never call its `price` “underlying/forward”. |

The official API policy states a maximum 500 channels in one subscribe request;
shard subscription messages/connections and prefer subscriptions over repeated
market-data REST calls. The frequency policy is: catalog at startup + low-rate
reconciliation; live ticker via `agg2`; `100ms` only selectively; REST only
for bootstrap, reconnect, or reconciliation.

### 3.3 Ticker / option-market fields

The captured WS request/result and `ticker.*.agg2` notification for the same
BTC call/timestamp have the same observed option-market keys and numeric
values. A separate direct-HTTPS REST ticker capture confirms the same field
vocabulary for that two-sided BTC call, but has a later timestamp and therefore
is not a value-for-value comparison. Same-window REST/WS nullability remains
**UNVERIFIED_LIVE**.

| Raw field | Type / nullable per docs | Raw unit / definition | Canonical handling |
|---|---|---|---|
| `timestamp` | integer / required | UTC epoch ms | `exchange_timestamp_ms`. |
| `state` | enum / required | lifecycle state | Quality gate as §4. |
| `best_bid_price`, `best_ask_price` | number or null / docs; `0.0` observed on delivered option | BTC option premium per 1-BTC contract | `bid_btc_per_contract`, `ask_btc_per_contract`; `null` or non-open zero = no executable side. |
| `best_bid_amount`, `best_ask_amount` | number or null / docs; `0.0` observed on delivered option | aggregate best-level requested size; options are base-coin units | `*_amount_btc_contracts`; zero is not executable depth. |
| `bids`, `asks` | arrays / required for order book | `[price, amount]` REST; book WS adds action | price BTC premium; option amount BTC-contract units. Empty side is valid. |
| `mark_price` | number / required | option mark premium | `mark_btc_per_contract`; never an executable quote. |
| `last_price` | number or null / required | last trade premium | Preserve nullable; not a bid/ask fallback. |
| `index_price` | number / required | current index | `index_price_usd_per_btc`. |
| `underlying_price` | number / option field | price used in IV calculations | `underlying_price_usd_per_btc`; do not rename spot. |
| `underlying_index` | string / option field | relevant future or `index_price` | `underlying_index_name`. |
| `estimated_delivery_price` | number / documented; string `"expired"` observed delivered | estimated delivery price | Retain raw tagged union; do not parse as number without type check. |
| `delivery_price` | number / closed-state only | settlement/delivery price | nullable historical field; do not use for live intrinsic. |
| `settlement_price` | number / optional generic field | settlement price, documented open state | preserve raw; semantics need live validation for options. |
| `open_interest` | number / required | options: base-currency amount | `open_interest_btc_contracts`. |
| `stats.volume` | number / required in stats | 24-h options volume in base currency | `volume_24h_btc_contracts`. |
| `stats.volume_usd` | number / futures only | USD volume | do not require/store as option liquidity input. |
| `stats.high`, `stats.low` | number / required in stats | 24-h option premium | preserve as BTC premium. |
| `stats.price_change` | number or null | 24-h percent, null if no trades | Preserve raw percent; null is `NO_24H_TRADE`, not zero. |
| `bid_iv`, `ask_iv`, `mark_iv` | number / option-only; nullable not specified | respective implied volatilities | `*_iv_raw`; scale UNVERIFIED until live. Missing side must not be imputed. |
| `interest_rate` | number / option-only | rate used in IV calculations | `interest_rate_raw`; scale/compounding convention UNVERIFIED. |
| `greeks.delta` | number / required for option docs | individual-expiry Black-Scholes Delta | `delta_raw`; confirmed model family, scaling must be regression-tested live. |
| `greeks.gamma` | number / required | BS rate of delta change vs underlying price | `gamma_raw`; exact currency/price-unit scaling UNVERIFIED. |
| `greeks.theta` | number / required | Deribit minimum of 1-day theta and lifetime theta | `theta_raw`; record special near-expiry rule; exact premium unit needs live calibration. |
| `greeks.vega` | number / required | BS sensitivity to IV | `vega_raw`; per 1.00 versus per vol point UNVERIFIED. |
| `greeks.rho` | number / required | BS rate sensitivity | `rho_raw`; scale UNVERIFIED. |

**Amount rule:** `contract_size=1 BTC` does not license a conversion of
`best_*_amount`, OI, or `stats.volume` into USD. Official market-data docs say
options use underlying base-currency coin units, so retain the BTC-contract
quantity verbatim. This numerically equals a 1-BTC-multiplier contract count
only while the instrument’s own `contract_size` is verified as 1.

## 4. Lifecycle and quality gates

Official states: `open`, `settlement`, `delivered`, `inactive`, `locked`,
`halted`, `archivized`.

| State | Meaning / coding action |
|---|---|
| `open` | Eligible for live market collection; executable analysis still needs quote and freshness checks. |
| `settlement` | Freeze actionable scan/roll evaluations; capture final state. Docs say new orders/edits/cancels are not accepted. |
| `delivered` | Final delivery state; archive final data and unsubscribe ticker/book. |
| `inactive` | Not open; stop actionable evaluation and retain metadata/history. |
| `locked` | No new orders/edits (cancels only); treat as non-actionable. |
| `halted` | Error state; stale/non-actionable, alert and reconcile. |
| `archivized` | Terminal archived state; do not expect an active book. |

Required option quality flags: `NON_OPEN_STATE`, `NO_EXECUTABLE_BID`,
`NO_EXECUTABLE_ASK`, `EMPTY_BIDS`, `EMPTY_ASKS`, `EMPTY_STATS_OR_NO_TRADE`,
`STALE_EXCHANGE_TIMESTAMP`, `SCHEMA_FIELD_ABSENT`, `UNVERIFIED_UNIT`.

**Observed delivered ticker:** `best_bid_price`, `best_ask_price`,
`best_bid_amount`, and `best_ask_amount` are all numeric `0.0`; `last_price`
is JSON `null`; `estimated_delivery_price` is string `"expired"`; `delivery_price`
is numeric. Therefore `state != open` is an unconditional non-executable gate
and numeric zero must not be accidentally valued as an executable price.

## 5. Book-only sequence contract

Do **not** apply this section to `ticker.*`.

For ungrouped `book.{instrument_name}.{interval}` the first WS notification is
a full snapshot with `change_id`; subsequent messages are incremental and
contain `prev_change_id`. Accept an incremental update only when its
`prev_change_id` equals the last applied `change_id`. On mismatch, discard the
derived local book, obtain a fresh REST `public/get_order_book` snapshot and/or
resubscribe, then wait for a fresh WS snapshot. Price-level actions are `new`,
`change`, `delete`; deletions normally carry zero amount. Grouped book channels
are aggregated/truncated and are not a replacement for the ungrouped local book
when exact incremental reconstruction is required.

Ticker is an independently aggregated market summary. It has timestamp/state
but no documented `prev_change_id`; keep its latest complete payload, apply
timestamp/freshness checks, and never reject ticker based on book sequence
numbers.

## 6. Exceptional-response fixture requirements (not yet captured)

When public network access returns, retain original JSON (one payload/file) for
these cases, without manual completion of fields:

1. active BTC call, two-sided book, all IV/Greeks present (captured; the
   captured `stats.high`, `stats.low`, and `stats.price_change` are null and
   volume is zero, which is an intentional no-24h-trade sample);
2. `best_bid_price: null` plus empty `bids` (or live equivalent) — missing;
3. `best_ask_price: null` plus empty `asks`;
4. `stats.price_change: null` / no 24-hour trades;
5. a non-`open` option ticker or lifecycle event, ideally `delivered` and
   `archivized`;
6. direct HTTPS REST `ticker` and WS `ticker.*.agg2` for the same symbol/time
   window — missing; WS request/result and ticker notification are captured;
7. REST order-book snapshot followed by WS snapshot/increment pair with
   `change_id` and `prev_change_id`; and
8. `get_instruments` plus `get_instrument` for the same BTC option.

Suggested names: `YYYYMMDDTHHMMSSZ_mainnet_<surface>_<instrument>.json`.
Save a matching manifest entry with endpoint/channel, request, UTC capture
time, SHA-256, and whether the payload is REST/WS. Fixtures do not contain
credentials and must be standard JSON.

## 7. Explicit implementation boundary

1. REST bootstrap builds catalog and recovers point state.
2. WS lifecycle maintains catalog; WS ticker maintains market summary;
   optional WS book maintains depth only.
3. Periodic REST reconciliation repairs missed listings/state and stale/gapped
   connections. It does not overwrite raw WS evidence or invent prices.
4. New-entry logic needs non-null fresh Ask; liquidation/roll-out logic needs
   non-null fresh Bid; mid requires both sides. Mark and last are never
   substitutes.
5. `index_price`, index-feed `price`, `underlying_price`, forward indicated by
   `underlying_index`, and delivery values must remain distinct fields.

## 8. Documentation discrepancies and open Gate-0 questions

| Item | Evidence / impact | Required P03 ruling or live test |
|---|---|---|
| `quote_currency` | `get_instrument` sample shows `BTC`, while the field-description block shown on the same docs family gives USD as generic allowed/example wording; support says BTC option quoted currency is BTC. | Treat live response as authority; retain raw field, record discrepancy test. |
| No-bid/no-ask representation | Reference says `best_*_price`/amount are `null` when absent. A live delivered BTC option returns numeric `0.0` on both quote sides; an older official example also shows zero on an empty side. | Treat `null` or a quote-side numeric zero as non-executable; retain raw representation and only classify zero for the relevant state. Capture an open-option empty-side case. |
| REST vs WS equality | One WS request/result and WS ticker notification match for the same symbol/timestamp, but direct HTTPS REST is not captured. | Capture direct HTTPS matched window; compare keys, types, units and nullability before a shared parser is declared identical. |
| IV scale | Docs call it implied volatility but do not state decimal vs percentage in the inspected field definitions. | Calibrate using live premium/underlying/strike/time or official confirmation; keep raw until then. |
| Greek units | Docs specify BS origin and theta’s time rule, but do not specify gamma price denomination, vega vol increment, rho increment, or full premium unit. | P03 must not permit utility/GCE aggregation until a Black-Scholes/live regression fixture resolves these. |
| `stats` object absence | Docs mark stats required, but no current option live sample was captured. | Parser must accept required-by-doc shape but preserve an absent object as schema drift and a present-but-null fields as data state. |
| `underlying_price` / forward | Docs define it as IV-calculation underlying and `underlying_index` as a future or `index_price`; support says forward may be future mark or synthetic. | Name canonical field `underlying_price_usd_per_btc`, not `forward_price`, until live examples distinguish all cases. |
