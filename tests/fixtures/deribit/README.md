# Deribit fixture capture status

The JSON files captured at 2026-09-14T11:33Z from mainnet are unmodified public
JSON-RPC responses/notifications received over `wss://www.deribit.com/ws/api/v2`.
The transport was WebSocket even for request/response methods; `testnet:false`
is preserved in the exchange response. They contain no credentials.

Coverage now includes a BTC inverse call specification, ticker, REST-style book
response, index response, ticker `agg2`, book `100ms` snapshot, and index
subscription. The ticker is also the intentional empty-24h-stats fixture:
`stats.high`, `stats.low`, and `stats.price_change` are JSON null while volume
is numeric zero.

`20260914T113624Z_mainnet_https_ticker_BTC-25DEC26-20000-C.json` is a separate
direct HTTPS REST capture, sent through the explicitly configured local proxy.

`20260914T113730Z_mainnet_https_ticker_delivered_BTC-14SEP26-68000-C.json` and
its specification companion are direct HTTPS REST captures of a delivered
option. They deliberately preserve the exchange's zero Bid/Ask price/amount,
`last_price: null`, and string `estimated_delivery_price: "expired"`.

`20260915_ws_soak/` adds a mainnet ungrouped-book snapshot and a subsequent
increment containing `prev_change_id`, captured by the 120-second public
WebSocket soak. The sibling `synthetic_book_sequence_gap` file copies that live
increment and changes only `prev_change_id` by +1; its top-level provenance
field prevents it from being mistaken for an unmodified exchange message. The
synthetic lifecycle-shape file is derived from the live catalog and matches the
official three-field notification shape, but no lifecycle state transition was
naturally observed during the soak and it is not labelled as a live event.

Still missing: an **open** option no-bid/no-ask payload, a naturally observed
lifecycle-channel event, and a full unmodified `get_instruments` catalogue
response (the earlier observed catalog was too large for this reusable fixture set).
