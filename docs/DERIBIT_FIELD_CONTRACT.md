# Deribit BTC Inverse Option Field Contract

> **版本：** `deribit-field-contract-v1.0.0`  
> **状态：** `PUBLISHED`，从属于 `domain-contract-v1.0.0`  
> **证据日期：** 2026-09-14 UTC  
> **范围：** mainnet public API；BTC inverse options；无认证、无下单。

本文冻结 raw Deribit payload 到 canonical domain 的 adapter 边界。raw JSON 必须逐字节或等价无损留存，并记录 SHA-256。canonical null 不得反向覆盖 raw `null`、缺失字段、zero sentinel 或非数值联合类型。

## 1. Surface 与来源

| Surface | v1 用途 | 约束 |
|---|---|---|
| `public/get_instruments` | bootstrap 与低频 catalog reconciliation | 参数 `currency=BTC`, `kind=option`, `expired=false`；不作高频行情轮询 |
| `public/get_instrument` | 单合约完整规格/状态刷新 | 新上市、状态变化、reconciliation 使用 |
| `public/ticker` | 单合约 bootstrap/point reconciliation | 可和 ticker subscription 共用规范字段，但 raw envelope/transport 独立保存 |
| `ticker.{instrument_name}.agg2` | 默认实时 top-of-book/mark/IV/Greeks | 无 book sequence；不得应用 `prev_change_id` |
| `ticker.{instrument_name}.100ms` | 持仓或精选高频子集 | 不是全链默认 |
| `public/get_order_book` | book snapshot/resync | REST snapshot 的 `change_id` 不构成连续性证明 |
| `book.{instrument_name}.100ms|agg2` | 可选 depth/local book | 只对 ungrouped book 执行 sequence contract |
| `deribit_price_index.btc_usd` / `public/get_index_price` | 独立 index 时钟 | index `price` 不得命名为 option underlying/forward |
| `instrument.state.option.BTC` | lifecycle | 120 秒 soak 中订阅和重订阅成功但无自然事件；保留周期 reconciliation；合成 shape fixture 不冒充 live event |

每个 raw event 最少记录：`raw_event_id`, `source_transport`, URI/channel/method, RPC id（如有）, `observed_at_utc`, raw payload, `payload_sha256`。exchange payload 内没有 timestamp 的 instrument response仍须依赖 `observed_at_utc`，不得合成 `exchange_timestamp_ms`。

## 2. Instrument mapping

`get_instruments` 与 `get_instrument` 共享下表。`Required` 表示 v1 canonical instrument 所需；raw 字段缺失时应 flag，而不是推导。

| Raw path | Raw type / observed nullability | Canonical field | Canonical unit / rule |
|---|---|---|---|
| `instrument_name` | string / non-null | `instrument_name` | verbatim natural key |
| `instrument_id` | integer / present in fixtures | `instrument_id?` | nullable exchange id；不从 symbol 推导 |
| `kind` | string | `kind` | 必须为 `option`，否则 out of scope |
| `instrument_type` | string | `instrument_type_raw` | v1 期望 `reversed`；未知值 fail closed |
| `option_type` | string | `option_type` | `call`/`put` 映射 enum；decision domain 仅 call |
| `state` | string | `state` + `state_raw` | 映射 lifecycle enum；未知为 `UNKNOWN` |
| `is_active` | boolean | `is_active` | 与 state 独立保存，不互推 |
| `strike` | number | `strike_usd_per_btc` | USD/BTC，必须 `>0` |
| `expiration_timestamp` | integer | `expiration_timestamp_ms` | UTC epoch ms |
| `creation_timestamp` | integer | `creation_timestamp_ms` | UTC epoch ms |
| `price_index` | string | `price_index_name` | BTC inverse 期望 `btc_usd` |
| `base_currency` | string | `base_currency` | v1 必须 `BTC` |
| `quote_currency` | string | `quote_currency` | live fixture 为 `BTC`；以 live raw 为 authority |
| `counter_currency` | string | `counter_currency` | live fixture 为 `USD` |
| `settlement_currency` | string | `settlement_currency` | v1 必须 `BTC` |
| `contract_size` | number | `contract_size_btc` | BTC/contract；v1 仅接受 `1.0`，其他值 out of scope |
| `min_trade_amount` | number | `min_trade_amount_base_btc`; derived `min_trade_quantity_contracts` | base BTC；quantity = amount / contract size |
| `tick_size` | number | `base_tick_size_btc_per_contract` | BTC/contract |
| `tick_size_steps[]` | array | `tick_size_steps` | 保留 `above_price` 与 `tick_size`；按价格区间选择，不硬编码 |
| `maker_commission` | number | `maker_commission_raw` | 仅 raw rate input；不等于已验证 fee cashflow |
| `taker_commission` | number | `taker_commission_raw` | 同上 |

`lot_size`、block-trade 字段、UUID 等未知于第一版核心逻辑的字段仍留在 raw payload；不得因为未进入 canonical DTO 而从 raw archive 删除。

## 3. Ticker / order-book summary mapping

### 3.1 时钟、状态和身份

| Raw path | Raw type | Canonical field | Null/quality rule |
|---|---|---|---|
| `timestamp` | integer | `exchange_timestamp_ms` | required；缺失/类型错误为 required schema failure |
| `instrument_name` | string | `instrument_name` | required；必须绑定已知或同时刷新中的 instrument spec |
| `state` | string | `state`, `state_raw` | 只有 `open` 可进入 actionable gate |

REST result、WS RPC result 与 WS subscription `params.data` 的 payload 字段可进入同一个 field adapter，但 raw envelope、transport 和 channel/method MUST 分开保存。已有同 timestamp fixture 证明 WS RPC ticker 和 `agg2` notification 的字段/数值一致；HTTPS fixture 证明字段 vocabulary 一致，但不声称所有时刻的 presence/nullability 必然相同。

### 3.2 Quotes 与 depth

| Raw path | Raw type / observed | Canonical field | Canonical rule |
|---|---|---|---|
| `best_bid_price` | number or null；delivered 为 `0.0` | `best_bid_price_raw`; `bid_btc_per_contract?` | raw 有限 `>0` 且 state open 才生成 executable Bid；否则 null + reason |
| `best_ask_price` | 同上 | `best_ask_price_raw`; `ask_btc_per_contract?` | 同上 |
| `best_bid_amount` | number or null；delivered 为 `0.0` | `best_bid_amount_base_btc?`; derived `best_bid_quantity_contracts?` | 需 price executable 且 amount `>0`；0/null 为无 depth |
| `best_ask_amount` | 同上 | `best_ask_amount_base_btc?`; derived `best_ask_quantity_contracts?` | 同上 |
| `mark_price` | number | `mark_btc_per_contract?` | 有限且 `>=0`；非执行价 |
| `last_price` | number or null | `last_btc_per_contract?` | 只作 last-trade observation，不作 fallback |
| `bids[][0]`, `asks[][0]` | number | book `price_btc_per_contract` | bid 降序、ask 升序；不静默重排异常输入 |
| `bids[][1]`, `asks[][1]` | number | `amount_base_btc`; derived `quantity_contracts` | base BTC；必须非负 |
| book WS `[action, price, amount]` | tuple | action + same price/amount fields | action 仅 `new/change/delete`；delete 通常 amount 0 |
| `change_id` | integer | `book_change_id` | book only |
| `prev_change_id` | integer / incremental only | `book_prev_change_id?` | book incremental only；ticker 不得要求 |

规范 `mid_btc_per_contract` 只在同一 MarketSnapshot 具有有效正 Bid/Ask 且 `bid<=ask` 时计算。raw zero quote 必须保留并设 `ZERO_QUOTE_SENTINEL`; delivered 的 0 永远不可执行。open 状态下若将来观察到 0，也按不可执行处理，不能猜测为可成交零价。

### 3.3 Index、underlying、delivery 与 analytics

| Raw path | Raw type / observed | Canonical field | Unit / rule |
|---|---|---|---|
| `index_price` | number | `index_price_usd_per_btc` | USD/BTC，必须 `>0` |
| index channel `price` | number | `index_price_usd_per_btc` | 绑定 `index_name` 与其独立 timestamp |
| `underlying_price` | number | `underlying_price_usd_per_btc` | USD/BTC；Deribit IV/Greek input，不自动改名 forward |
| `underlying_index` | string | `underlying_index_name` | verbatim，如 `BTC-25DEC26`, `SYN.EXPIRY` |
| `estimated_delivery_price` | number 或 string `"expired"` | `estimated_delivery_price_usd_per_btc?` + raw tagged value | 只有有限正 number 进入 canonical；string -> null + flag |
| `delivery_price` | number / closed-state optional | `delivery_price_usd_per_btc?` | historical expiry only |
| `settlement_price` | number / observed open | `settlement_price_raw?` | v1 语义未足够验证；不得用于 NAV/intrinsic/delivery |
| `open_interest` | number | `open_interest_amount_base_btc`; derived `open_interest_quantity_contracts` | option base BTC amount |
| `stats.volume` | number | `volume_24h_amount_base_btc`; derived `volume_24h_quantity_contracts` | option base BTC amount |
| `stats.high`, `stats.low` | number or null | `high_24h_btc_per_contract?`, `low_24h_btc_per_contract?` | null 合法，表示无对应 trade statistic |
| `stats.price_change` | number or null | `price_change_24h_percent_raw?` | raw percent；null 是 no-trade，不是 0 |
| `stats.volume_usd` | number / observed though docs偏 futures | `volume_usd_raw?` | 保留 raw，v1 option liquidity 不依赖它 |
| `bid_iv`, `ask_iv`, `mark_iv` | number | `*_iv_raw`; `*_iv_annual_decimal?` | 有限正 raw /100；0/null/absent 不可用 |
| `interest_rate` | number | `interest_rate_raw` | scale/compounding 未由非零 fixture 验证；不得未经 adapter 用于 production pricing |
| `greeks.delta` | number | `deribit_delta_raw`; verified `deribit_delta_standard` | standard BS delta per contract；模型 reference为 underlying，exact index-vs-forward derivative basis provisional |
| `greeks.gamma` | number | `deribit_gamma_raw`; normalized null | target unit 已定义但 live scale未验证 |
| `greeks.theta` | number | `deribit_theta_raw`; `deribit_theta_usd_per_effective_horizon` | USD/contract，horizon=min(1 day,lifetime) |
| `greeks.vega` | number | `deribit_vega_raw`; `deribit_vega_usd_per_vol_point` | USD/contract/1 vol point |
| `greeks.rho` | number | `deribit_rho_raw`; `deribit_rho_usd_per_rate_point` | USD/contract/1 annual rate point |

若整个 `stats`、`greeks` 或 required field 缺失，必须区分 `SCHEMA_FIELD_ABSENT_REQUIRED` 和合法的 child null。open ticker 的 stats child null 不阻止 quote 行动性，但产生 `EMPTY_STATS_OR_NO_TRADE`。

## 4. Lifecycle mapping

| Raw state | Canonical | Actionability |
|---|---|---|
| `open` | `OPEN` | 仍需 freshness/schema/quote gates |
| `settlement` | `SETTLEMENT` | freeze actionable output；保存最终状态 |
| `delivered` | `DELIVERED` | terminal；保存 delivery，退订 live feed |
| `inactive` | `INACTIVE` | non-actionable；保留历史 |
| `locked` | `LOCKED` | non-actionable |
| `halted` | `HALTED` | non-actionable，alert/reconcile |
| `archivized` | `ARCHIVIZED` | terminal archived |
| any other | `UNKNOWN` | fail closed，保留 raw state |

`is_active=true` 不能覆盖非 open state；`state=open` 也不能覆盖 `is_active=false`。二者冲突时保留并设 `STATE_ACTIVITY_INCONSISTENT`。

## 5. Book sequence contract

只适用于 ungrouped WS `book.*`：

1. 首个 `type=snapshot` 建立 local book 和 last `change_id`；
2. incremental 的 `prev_change_id` 必须等于 last applied `change_id`；
3. mismatch 时丢弃 derived local book（raw event 不删），标记 gap，REST resync 和/或重订阅，并等待新的完整 snapshot；
4. grouped/truncated book 不能作为 exact incremental reconstruction；
5. ticker 没有此 sequence contract，不能因缺 `prev_change_id` 被拒绝。

现有 fixture 已覆盖真实 mainnet book snapshot 与连续 increment。gap fixture 由该 live increment 仅修改 `prev_change_id` 生成并明确标注 synthetic；tracker 测试证明 gap 时清空 derived book、发出 `BOOK_SEQUENCE_GAP` 并等待新 snapshot。自然丢包仍未在主网观察。

## 6. 可回滚 Deribit model adapter

Adapter id：`deribit-bs-observed@1.0.0-diagnostic`。

从 `BTC-25DEC26-20000-C` 同一 exchange timestamp 的 instrument/ticker fixture 得到：

- `iv_decimal = mark_iv_raw / 100 = 0.983`；
- 以 `S = underlying_price = 78869.27`、`K=20000`、精确 UTC T、`r = interest_rate = 0` 的标准 BS 计算，得到 delta `0.9981476`、vega `2.46346 USD/vol point`、theta `-1.18910 USD/day`、rho `55.31494 USD/rate point`；
- API 为 `0.99815`, `2.46398`, `-1.18939`, `55.31467`，在交易所显示/输入舍入范围内一致；预测 gamma约 `1.44e-7`，API 显示 `0.0`，无法确认 gamma scale；
- 同一回归 USD price 除 `underlying_price` 接近 raw mark BTC，而除 index 不同。这证明报告换算与交易所定价回归不可混成一个公式，但单一深 ITM fixture 不足以宣布 universal pricing convention。

因此该 adapter只可用于 parser contract test、单位回归和诊断差异报告；不得作为 production mark、IV solver、交易建议或 PnL 真值。若新多样本证据否定它，发布新 adapter version，旧结果仍按旧版本回放。

## 7. Evidence ledger 与已知差异

| 证据 | 已冻结结论 | 仍未证明 |
|---|---|---|
| open instrument fixture | BTC quote/settlement、USD counter、contract size 1、min amount 0.1、tick steps | 未来非 1 contract size 行为（v1 out of scope） |
| WS RPC ticker + same-timestamp `agg2` | key/value parity for captured payload | 所有状态/空值的永久 parity |
| HTTPS open ticker | REST vocabulary 与 WS 一致；two-sided/open | same-window transport value equality |
| order-book RPC + WS snapshot/increment | top/depth amount和price shape；snapshot/change id；真实 `prev_change_id` 连续性 | 自然网络丢包 case；当前 gap 为故障注入 |
| delivered ticker + instrument | zero quote sentinel、null last、string `expired`、numeric delivery、`SYN.EXPIRY` | open instrument empty-side shape |
| index RPC + subscription | 独立 index 字段和时钟 | 与 ticker 同 timestamp 的同步保证 |

文档与响应冲突时 v1 规则是：raw response 优先决定 observed shape；官方文档决定声明的经济语义；冲突通过 `SOURCE_SHAPE_UNVERIFIED`、adapter version 和本表记录，不在 parser 中静默“修复”。

## 8. Adapter acceptance / rejection

- required raw field absence/type mismatch：保留 event，拒绝形成 actionable MarketSnapshot。
- optional/状态相关字段缺失：形成 nullable canonical field，并提供 presence 与 reason。
- quote `<=0`、amount `<=0`、非 open、crossed market：形成 data-only snapshot，相关 executable value 为 null。
- unknown schema/enum/unit：不得生成 actionable evaluation。
- raw archive 和 normalized event 必须关联；重新 normalization 必须生成新 `schema_version` 或 adapter version 的 snapshot，不得覆盖旧 snapshot。
