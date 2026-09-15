# Deribit API Evidence Index — Gate 0

Research date: 2026-09-14 UTC. Only Deribit official documentation and public
endpoints were used. Initial REST CLI capture failed because that process did
not inherit the enabled user proxy. Public WebSocket mainnet capture succeeded.
`UNVERIFIED_LIVE` means a particular runtime shape has not yet been observed,
not an inference that an API field is absent.

| Conclusion / contract section | Official source | Captured real fixture |
|---|---|---|
| `public/get_instruments` catalogue discovery; `currency`, `kind`, `expired` parameters and instrument metadata | https://docs.deribit.com/api-reference/market-data/public-get_instruments | Catalog request succeeded over WS: 892 BTC active options; full raw catalogue deliberately not saved (779,171 chars). |
| Detailed per-symbol fields incl. `contract_size`, `min_trade_amount`, currency fields, state and option fields | https://docs.deribit.com/api-reference/market-data/public-get_instrument | `20260914T113335Z_mainnet_ws_get_instrument_BTC-25DEC26-20000-C.json`; `20260914T113731Z_mainnet_https_get_instrument_delivered_BTC-14SEP26-68000-C.json` |
| Contract multiplier 1 BTC, premium/book price BTC, cash settlement BTC, 0.1 BTC-option minimum, forward construction | https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options | `20260914T113335Z_mainnet_ws_get_instrument_BTC-25DEC26-20000-C.json` |
| `public/ticker` field definitions: top quote null semantics, OI/options base amount, stats volume, IV, underlying, Greeks and theta note | https://docs.deribit.com/api-reference/upcoming/market-data/public-ticker | `20260914T113624Z_mainnet_https_ticker_BTC-25DEC26-20000-C.json`; `20260914T113335Z_mainnet_ws_ticker_BTC-25DEC26-20000-C.json`; `20260914T113336Z_mainnet_ws_ticker_agg2_BTC-25DEC26-20000-C.json`; `20260914T113730Z_mainnet_https_ticker_delivered_BTC-14SEP26-68000-C.json` |
| `public/get_order_book`: depth, sorting, null top quote semantics, book snapshot fields | https://docs.deribit.com/api-reference/market-data/public-get_order_book | `20260914T113335Z_mainnet_ws_get_order_book_BTC-25DEC26-20000-C.json` |
| Ticker channel form, `raw`/`100ms`/`agg2`, ticker fields and states | https://docs.deribit.com/subscriptions/market-data/tickerinstrument_nameinterval | `20260914T113336Z_mainnet_ws_ticker_agg2_BTC-25DEC26-20000-C.json` |
| Ungrouped book snapshot/increment semantics, actions, option amount unit, `change_id` / `prev_change_id` | https://docs.deribit.com/subscriptions/orderbook/bookinstrument_nameinterval | Initial snapshot plus `20260915_ws_soak/*mainnet_ws_book_100ms_{snapshot,increment}*`; injected sibling gap is explicitly synthetic. |
| Grouped book price/amount and aggregation boundary | https://docs.deribit.com/subscriptions/orderbook/bookinstrument_namegroupdepthinterval | None — UNVERIFIED_LIVE |
| Lifecycle channel name, parameters and event fields | https://docs.deribit.com/subscriptions/upcoming/market-data/instrumentstatekindcurrency | Subscription accepted; no lifecycle event arrived in capture window — UNVERIFIED_LIVE |
| Common WS notification envelope, channel categories, sequence guidance | https://docs.deribit.com/articles/notifications | Live ticker/book notification fixtures and the 120-second reconnect/retention soak. |
| Index REST price and index WS channel | https://docs.deribit.com/api-reference/market-data/public-get_index_price ; https://docs.deribit.com/subscriptions/market-data/deribit_price_indexindex_name | `20260914T113336Z_mainnet_ws_get_index_price_btc_usd.json`; `20260914T113336Z_mainnet_ws_index_subscription_btc_usd.json` |
| Subscribe request limit (500 channels) and subscription-over-polling guidance | https://docs.deribit.com/articles/api-usage-policy ; https://docs.deribit.com/articles/market-data-collection-best-practices | None — UNVERIFIED_LIVE |

## Live-capture ledger

| Attempt UTC | Public URI/method | Result | Fixture |
|---|---|---|---|
| 2026-09-14 | REST CLI `public/get_instruments` | Direct CLI DNS resolution failed because it bypassed the enabled user proxy. | None |
| 2026-09-14T11:33Z | WebSocket `public/get_instruments`, `public/get_instrument`, `public/ticker`, `public/get_order_book`, `public/get_index_price`, `public/subscribe` | Mainnet succeeded; only raw responses small enough for reusable fixtures retained. | See named JSON fixtures above. |
| 2026-09-14T11:36Z | HTTPS REST `public/ticker` | Mainnet succeeded when `curl` was explicitly pointed to the enabled local proxy. | `20260914T113624Z_mainnet_https_ticker_BTC-25DEC26-20000-C.json` |
| 2026-09-14T11:37Z | HTTPS REST `public/ticker`, `public/get_instrument` for delivered call | Mainnet succeeded; records the zero-price/amount and expired-string runtime shape. | `20260914T113730Z_mainnet_https_ticker_delivered_BTC-14SEP26-68000-C.json`; `20260914T113731Z_mainnet_https_get_instrument_delivered_BTC-14SEP26-68000-C.json` |
| 2026-09-14T17:04Z | Public WebSocket ticker/book/lifecycle subscription, client-injected reconnect, retention reopen | PASS: 2 connections, 111 ticker, 2 book snapshots, 63 real increments, 177/177 replayable raw events. Lifecycle subscribed but no natural event. | `20260915_ws_soak/`; `docs/evidence/ws_soak_120s_20260915.json` |

## Evidence-to-field coverage

| Fields | Documentation evidence | Live evidence |
|---|---|---|
| Instrument identity/lifecycle/specification: `instrument_name`, `instrument_id`, `kind`, `option_type`, `strike`, timestamps, currencies, `contract_size`, `min_trade_amount`, tick fields, state | get_instruments + get_instrument + Inverse Options | get_instrument fixture; lifecycle channel accepted/re-subscribed, natural event still UNVERIFIED_LIVE |
| Ticker and book: top price/amount, bids/asks, mark/last, index, underlying, delivery/settlement, OI/stats | ticker + get_order_book + ticker subscription | Direct HTTPS open/delivered ticker, WS ticker, book and ticker-WS fixtures; open-option null-quote shape UNVERIFIED_LIVE |
| Options analytics: bid/ask/mark IV, interest rate, delta/gamma/theta/vega/rho | ticker | ticker REST-over-WS and ticker subscription fixtures |
| Channel and reconciliation: ticker interval, lifecycle, book sequence, index | subscription pages + notifications + collection best practices | ticker/book/index fixtures; real book increment verified; synthetic gap recovery verified; natural lifecycle event UNVERIFIED_LIVE |

Fixtures labelled `mainnet` are actual exchange traffic, not documentation examples.
Files labelled `synthetic` carry an explicit provenance field. The missing open
no-quote and naturally observed lifecycle-event cases remain explicit gaps.
