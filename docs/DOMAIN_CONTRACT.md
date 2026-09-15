# BTC Option Scanner Domain Contract

> **契约版本：** `domain-contract-v1.0.0`  
> **schema_version：** `domain-schema-v1`  
> **状态：** `PUBLISHED / P04_ALLOWED`  
> **发布日期：** 2026-09-14 UTC  
> **适用范围：** Deribit mainnet 公开市场数据中的 BTC inverse European options；v1 决策域只评价 long Call，不授权下单。

本文是 P04 及后续工作包的共享领域契约。`DERIBIT_FIELD_CONTRACT.md` 定义 adapter 输入；本文定义规范实体、单位、失败语义和可追溯性。两份 Gate 0 draft 是研究证据，不再是实现时的规范来源。

## 1. 规范强度与变更规则

关键词 `MUST`、`MUST NOT`、`SHOULD` 按其通常规范含义解释。

- 同一 `domain-contract` 主版本内，字段名、单位、null 语义、价格基准和失败关闭规则不得被实现者自行改变。
- 删除/重命名字段、改变单位/公式/null 语义或放宽行动性 gate 是 breaking change，必须发布新的 contract major，并附迁移说明和测试。
- 仅增加 optional 字段或枚举值是 minor change；不改变机器语义的文字澄清是 patch change。
- 不得在相同 `schema_version`、`metric_version` 或 `config_version` 下改变结果语义；历史结果永远按记录的版本解释。
- 发现新交易所形态时先保留 raw event、设置 quality flag、提交 change proposal；不得静默猜测、回填或就地改契约。
- `P04_ALLOWED` 只授权实现项目骨架、共享类型与配置 schema，不解除本文列出的 feature-level 禁令。

## 2. 基础值、单位与枚举

### 2.1 基础值规则

- 所有内部时刻为 UTC。交易所 epoch 毫秒原样记为 `exchange_timestamp_ms`；客户端接收和成功接纳时刻分别为 `observed_at_utc`、`ingested_at_utc`。
- 价格、数量、IV、rate 和 Greeks 必须用有限数值；`NaN`、正负无穷和 magic sentinel 不得进入规范值。
- 所有 BTC option premium 是 `BTC / contract`；strike、index、underlying、forward、delivery 是 `USD / BTC`。
- `contract_size_btc` 是 `BTC / contract`；`quantity_contracts` 可为小数。金额字段若来自交易所 base amount，先保存 `amount_base_btc`，再以 `amount_base_btc / contract_size_btc` 得到 `quantity_contracts`。
- 内部 IV 是年化 decimal，例如 raw `98.3` 规范化为 `0.983`；显示层才乘 100。IV shock 用绝对 vol points，例如 `+5` 表示 decimal IV 加 `0.05`。
- `time_to_expiry_years = max(expiration_timestamp_ms - as_of_ms, 0) / (365 * 24 * 3600 * 1000)`；DTE 同时可按精确日数报告，不用本地日期截断。

### 2.2 共享枚举

| 枚举 | 固定值 |
|---|---|
| `SourceTransport` | `HTTPS_REST`, `WEBSOCKET_RPC`, `WEBSOCKET_SUBSCRIPTION`, `REPLAY` |
| `OptionType` | `CALL`, `PUT`；v1 decision universe 只允许 `CALL` |
| `InstrumentState` | `OPEN`, `SETTLEMENT`, `DELIVERED`, `INACTIVE`, `LOCKED`, `HALTED`, `ARCHIVIZED`, `UNKNOWN` |
| `FieldPresence` | `ABSENT`, `PRESENT_NULL`, `PRESENT_VALUE` |
| `PriceBasis` | `ENTRY_ASK`, `LIQUIDATION_BID`, `THEORETICAL_MARK`, `DESCRIPTIVE_MID` |
| `QuantityMode` | `EQUAL_CONTRACTS`, `EQUAL_STANDARD_DELTA` |
| `UnitStatus` | `VERIFIED`, `PROVISIONAL`, `UNVERIFIED`, `NOT_APPLICABLE` |
| `Availability` | `AVAILABLE`, `UNAVAILABLE`, `NOT_APPLICABLE` |
| `QualitySeverity` | `INFO`, `WARNING`, `BLOCK_VALUE`, `BLOCK_ACTION` |
| `RunStatus` | `COMPLETED`, `COMPLETED_DATA_ONLY`, `FAILED_INPUT`, `FAILED_INTERNAL` |
| `DecisionState` | `DATA_ONLY`, `INELIGIBLE`, `ELIGIBLE`, `ATTRACTIVE`, `HOLD`, `ROLL_CANDIDATE`, `STRONG_ROLL_CANDIDATE`, `WAIT_FOR_LIQUIDITY`, `WAIT_FOR_IV_NORMALIZATION`, `DATA_STALE`, `NO_EXECUTABLE_BID`, `NO_EXECUTABLE_ASK`, `INSUFFICIENT_HISTORY`, `INSUFFICIENT_RESERVE`, `INVALID_INPUT` |

未知 exchange enum 必须映射为 `UNKNOWN` 并保留 raw 字符串；不得把未知值映射到最接近的已知状态。

### 2.3 nullability

`null` 表示值不可用或不适用，永远不表示 0。所有影响决策的 nullable 字段 MUST 同时提供 `Availability` 和 reason/quality flag。raw JSON 的字段缺失、显式 `null` 和具体值必须以 `FieldPresence` 区分；规范层即使都得到 `null`，也不得丢掉这种差异。

以下值不得作为 fallback：mark/mid/last 代替 Ask 或 Bid；index/underlying/forward/delivery 互相代替；0 代替缺失 quote、IV 或 Greek。

## 3. 四种价格基准

| 规范字段 | 用途 | 必要条件 | 明确禁止 |
|---|---|---|---|
| `ask_btc_per_contract` | 新 long 的可执行 entry cost、候选 entry 指标 | state `OPEN`、fresh、有限且 `>0` | mark/mid/last 替代 |
| `bid_btc_per_contract` | 旧 long 的可执行 liquidation value、roll proceeds | state `OPEN`、fresh、有限且 `>0` | mark/mid/last 替代 |
| `mark_btc_per_contract` | 理论监控、model comparison、mark NAV | 有限且 `>=0` | 作为真实成交假设 |
| `mid_btc_per_contract` | spread 和市场状态描述 | 同一 snapshot 中有效双边 quote；`(bid+ask)/2` | 单边报价时生成；作为 entry/exit |

派生指标必须把 side 写入字段名：`dce_entry_ask`、`dce_liquidation_bid`、`dce_mark`，以及相应 `extrinsic_*`、`icr_*`、`optionality_ratio_*`。单一 `premium_btc`、`dce_current`、`intrinsic_value` 或无 basis 的 `nav` 禁止进入共享接口。

### 3.1 报告换算与理论定价严格分开

观察到的 BTC premium 的同快照 USD 报告值固定为：

`premium_usd_at_index = premium_btc_per_contract * index_price_usd_per_btc`。

这条公式只做报告/NAV 换算，不声称复现 Deribit 的 mark engine。Gate 0 fixture 显示，使用 `underlying_price`、`interest_rate`、`mark_iv / 100` 的标准 BS 回归可复现 raw Greeks，而该回归的 USD 理论值除以 `underlying_price` 才接近 `mark_btc`。因此 v1 只允许把此行为封装为带版本的诊断 adapter；禁止将“USD 理论值应除 index 或 forward/underlying”的任一选择冒充已经普适验证的生产公式。

## 4. index、underlying/forward 与 delivery 边界

| 字段 | 唯一语义 | 允许用途 | 禁止用途 |
|---|---|---|---|
| `index_price_usd_per_btc` | 当前 `btc_usd` index | 当前 intrinsic、BTC/USD 报告换算、NAV_USD | 自动替代合约 underlying/forward 或 delivery |
| `underlying_price_usd_per_btc` | ticker 给出的、Deribit 用于该期权 IV/Greeks 的 underlying input | 保存、交易所 model regression | 简称 spot；无 adapter 直接当 model forward |
| `underlying_index_name` | underlying 来源标识，如 future 名或 `SYN.EXPIRY` | 来源审计、adapter selection | 当作价格 |
| `model_forward_price_usd_per_btc` | 由显式且版本化 `forward_adapter_version` 构造的定价输入 | local pricing/reference | 从 `underlying_price` 静默复制 |
| `estimated_delivery_price_usd_per_btc` | open 状态的估计 delivery 数值 | 展示/诊断 | settlement、current intrinsic、NAV 换算 |
| `delivery_price_usd_per_btc` | closed/delivered 合约的实际 delivery | 到期 payoff、历史分析 | live entry/roll/current intrinsic |

raw `estimated_delivery_price` 是 number/string 联合类型；`"expired"` 规范化为数值 `null`，保留 raw，并设 `ESTIMATED_DELIVERY_NOT_NUMERIC`。`delivery_price` 在 live 状态通常为 null；不得用 estimated 值填补。

当前报告 intrinsic 固定使用 index：

- `intrinsic_index_usd_per_contract = contract_size_btc * max(index_price_usd_per_btc - strike_usd_per_btc, 0)`；
- `intrinsic_index_btc_per_contract = contract_size_btc * max(1 - strike_usd_per_btc / index_price_usd_per_btc, 0)`。

到期 payoff 只使用 delivery：同式把 index 换成有效 `delivery_price`。`intrinsic_forward_pv_*` 只能在 `model_forward_price`、rate convention 和 adapter version 均明确时生成，并且不能替换 index intrinsic。

## 5. IV、Greeks、数量和时间

### 5.1 IV

- `bid_iv_raw`、`ask_iv_raw`、`mark_iv_raw` 原样保存。
- 有限且 `>0` 的 raw IV 规范化为 `*_iv_annual_decimal = raw / 100`。
- 缺失、null、非有限或 `<=0` 的 raw IV 不生成规范值；返回 null 并设 `IV_NOT_AVAILABLE` 或 `INVALID_IV`。fixture 中有正 Bid 但 `bid_iv_raw=0.0`，所以 0 不得解释为真实零波动率。
- bid/ask/mark IV 不得互相填补；IV 历史 cohort 必须记录 `metric_side`。

### 5.2 Deribit Greeks

raw 五项永远保留。规范字段与 Gate 0 状态如下：

| 字段 | 规范单位 | v1 状态 |
|---|---|---|
| `deribit_delta_standard` | BTC-equivalent standard delta / contract；交易所模型 reference 为 `underlying_price` | 数值/单位 `VERIFIED`；exact index-vs-forward derivative basis `PROVISIONAL` |
| `deribit_gamma_standard_per_usd` | delta / contract / 1 `(USD/BTC)` underlying move | `UNVERIFIED`，必须为 null，直到非零多样本回归通过 |
| `deribit_theta_usd_per_effective_horizon` | USD / contract / `min(1 calendar day, remaining lifetime)` | `VERIFIED`；另存 `theta_horizon_days` |
| `deribit_vega_usd_per_vol_point` | USD / contract / 1 vol point (`Δiv_decimal=0.01`) | `VERIFIED` |
| `deribit_rho_usd_per_rate_point` | USD / contract / 1 annual rate point (`Δr=0.01`) | `VERIFIED` |

上述验证基于 2026-09-14 同快照 fixture：以 `underlying_price` 为 BS input、`mark_iv_raw/100`、raw `interest_rate`，delta/vega/theta/rho 与 API 显示精度吻合。该单点不能严格区分 delta 是 index-spot derivative 还是等价 forward construction 下的 derivative；因此 `deribit_delta_standard` 可用于同字段 DCE/equal-standard-delta，但 model comparison必须携带 provisional basis metadata。gamma 样本被交易所显示为 `0.0`，不能据此辨别 scale，所以只能保存 `deribit_gamma_raw` 和 `UnitStatus.UNVERIFIED`。

`deribit_delta_standard`、带明确 basis 的 `model_delta_*`、以及 `model_btc_nav_delta_per_log_index_mark` 是三个不同概念，禁止都命名为 `delta`。未验证 gamma 不得进入 GCE、组合 gamma 金额或跨 Greek Utility。

### 5.3 数量

- raw best amount、option open interest、option `stats.volume` 的 source unit 是 base BTC amount。
- 规范层同时提供 `*_amount_base_btc` 与 `*_quantity_contracts = amount_base_btc / contract_size_btc`；当前 BTC options 的 `contract_size_btc=1` 使数值相等，但字段不可合并。
- `min_trade_amount` 同样规范为 `min_trade_amount_base_btc`，并导出 `min_trade_quantity_contracts`。数量 grid/tick 由 instrument metadata 和 versioned rounding policy 决定，不得硬编码为整数。

## 6. 指标与 NAV 的冻结语义

对 `side ∈ {entry_ask, liquidation_bid, mark}`：

- `extrinsic_{side}_btc = premium_{side}_btc - intrinsic_index_btc`；
- `icr_{side} = intrinsic_index_btc / premium_{side}_btc`；
- `optionality_ratio_{side} = extrinsic_{side}_btc / premium_{side}_btc = 1 - icr_{side}`；
- `dce_{side} = deribit_delta_standard / premium_{side}_btc`。

仅在对应 premium 有效且 `>0` 时计算。negative extrinsic 不 clip，保留原值并设 `NEGATIVE_EXTRINSIC`。DCE 分子固定为 Deribit standard delta，不得换成 BTC-NAV sensitivity。

Portfolio 必须同时给出：

- `nav_btc_mark = reserve_btc + Σ(quantity_contracts * mark_btc_per_contract)`；
- `nav_btc_liquidation = reserve_btc + Σ(quantity_contracts * bid_btc_per_contract)`；
- 各自 `nav_usd_* = nav_btc_* * 同一 portfolio snapshot 的 index_price_usd_per_btc`。

这里 price 已是每 contract 的 BTC premium，不再额外乘 `contract_size_btc`。如果未来 adapter 的 price 定义改变，必须升 contract major。任一 leg 缺 mark，则完整 mark NAV unavailable；任一 long leg 缺 Bid，则完整 liquidation NAV unavailable。历史 `premium_paid_btc` 是 cost basis，不是当前 NAV。

## 7. 最小稳定接口

下列是逻辑 DTO 契约，不规定 Python class、数据库表或序列化库。`*_id` 必须在一次历史回放中稳定且唯一。

### 7.1 `MarketSnapshot`

MUST 包含：

- 身份与 lineage：`market_snapshot_id`, `instrument_name`, `instrument_id?`, `raw_event_ids[]`；
- 时钟与来源：`exchange_timestamp_ms`, `observed_at_utc`, `ingested_at_utc`, `source_transport`, `source_channel_or_method`；
- instrument binding：`instrument_spec_version`, `state`, `contract_size_btc`, `strike_usd_per_btc`, `expiration_timestamp_ms`；
- market：四类 premium、best-side base BTC amount 与 contract quantity、last、OI、24h volume；
- reference prices：index、underlying、underlying index、estimated delivery、delivery；
- analytics：三类 raw/decimal IV、interest-rate raw、五个 raw Greeks、允许的规范 Greeks及各自 `UnitStatus`；
- governance：`schema_version`, `quality_flags[]`, `actionable`。

### 7.2 `MetricSnapshot`

MUST 包含：`metric_snapshot_id`, `market_snapshot_id`, `computed_at_utc`, `schema_version`, `metric_version`, `config_version`, 精确 DTE、index intrinsic BTC/USD、可用的 ask/bid/mark extrinsic/ICR/optionality/DCE、spread、所有 nullable output 的 availability reason，以及 `quality_flags[]`。forward-PV、GCE、model Greeks 和 BTC-NAV sensitivities是可选扩展，只有其 adapter/unit/version 已获允许时才能存在。

### 7.3 `ScannerRun`

MUST 包含：`scanner_run_id`, `as_of_utc`, `market_snapshot_ids[]`, `metric_snapshot_ids[]`, `universe_filter_version`, `metric_version`, `config_version`, `schema_version`, `max_snapshot_skew_ms`, `run_status`, `decision_state`, `candidate_evaluations[]`, `quality_flags[]`。每个 candidate evaluation 至少绑定 market/metric snapshot，记录 eligibility、rank、reasons、risks 和 rejected reasons。不同时间窗的旧/新状态不得拼成一次 run。

### 7.4 `PortfolioSnapshot`

MUST 包含：`portfolio_snapshot_id`, `portfolio_definition_id`, `as_of_utc`, `reserve_btc`, 每个 leg 的 instrument、signed `quantity_contracts` 与 `market_snapshot_id`, `nav_btc_mark`, `nav_usd_mark`, `nav_btc_liquidation`, `nav_usd_liquidation`, `usd_conversion_index_price_usd_per_btc`, 各 NAV 的 availability reason，可验证单位的聚合 Greeks，`metric_version`, `config_version`, `schema_version`, `quality_flags[]`。v1 只允许 long option legs 与非负 reserve；不暗含借贷。

### 7.5 `RollEvaluation`

MUST 包含：`roll_evaluation_id`, `portfolio_snapshot_id`, old/new instrument 与各自 `market_snapshot_id`/`metric_snapshot_id`, `quantity_mode`, `old_quantity_contracts`, `new_quantity_theoretical_contracts`, `new_quantity_executable_contracts`, `rounding_policy_version`, `old_bid_btc_per_contract`, `new_ask_btc_per_contract`, `old_sale_proceeds_btc`, `new_purchase_cost_btc`, `fee_old_btc`, `fee_new_btc`, `fee_schedule_version`, `net_reserve_release_btc`, `reserve_before_btc`, `reserve_after_btc`, standard delta before/theoretical after/executable after, `decision_state`, `reasons[]`, `risks[]`, `metric_version`, `config_version`, `schema_version`, `quality_flags[]`。

当 fee 尚未验证时 fee 字段与 cashflow availability 为 unavailable，并设 `FEE_UNVERIFIED`；不得填 0 后声称可执行经济性成立。

### 7.6 其余共享边界

为避免后续包自行创造跨层结构，以下 supporting DTO 也属于 v1；字段只规定逻辑接口，不规定持久化：

- `InstrumentSpec`：`instrument_name`, `instrument_id?`, option/state/currency enums及raw值, strike/expiration/creation, `contract_size_btc`, min amount/quantity, tick rules, lifecycle clocks, `instrument_spec_version`, `raw_event_id`, `schema_version`, `quality_flags[]`。
- `RawMarketEvent`：`raw_event_id`, `source_transport`, endpoint/channel/method, RPC id?, `observed_at_utc`, exchange timestamp（payload有则填）, `payload_sha256`, 无损 payload或其不可变引用。raw event没有 metric/config version。
- `CandidateEvaluation`：candidate instrument, `market_snapshot_id`, `metric_snapshot_id`, `eligibility`, `decision_state`, rank?, peer/cohort id与size, `reasons[]`, `risks[]`, `rejected_reasons[]`, `quality_flags[]`。rank在 unavailable/insufficient cohort时为 null并带reason。
- `IVObservation`：`market_snapshot_id`, instrument, exchange/as-of time, `metric_side`, `iv_annual_decimal`, delta/DTE bucket IDs, `bucket_policy_version`, `config_version`, `schema_version`, `quality_flags[]`；不同 side 不得混入同一历史序列。
- `PortfolioDefinition`：`portfolio_definition_id`, nonnegative `reserve_btc`, long legs的 instrument与非负 `quantity_contracts`, `created_at_utc`, `definition_version`；entry cost另记，不作为当前 value。
- `StressRun`：`stress_run_id`, `portfolio_snapshot_id`, scenario points（index return decimal、elapsed days、absolute IV vol-point shock、rate/forward/smile policy）, `pricing_model_version`, `metric_version`, `config_version`, `schema_version`, conditional results与flags。结果必须标 `MODEL_CONDITIONAL_NOT_EXECUTABLE_REPRICE`。
- `SystemEvent`：`system_event_id`, event time, observed time, component, severity, event code, message/context, related raw/snapshot/run IDs, `schema_version`；用于连接、stale、schema、model和replay异常，不承载策略结论。

## 8. Roll 数量与现金流

- `EQUAL_CONTRACTS`：理论新数量等于旧数量；仍须按 versioned rounding policy 得到 executable quantity。
- `EQUAL_STANDARD_DELTA`：`q_new_theoretical = q_old * delta_old / delta_new`。price 已 per contract，contract size 对 delta 的影响已经包含在每合约 Greek 中；只有在未来 Greek 改成 per-base-BTC 表达时才可另乘 multiplier，并须升 contract major。
- 同时输出理论与可执行数量和 delta rounding error。`delta_new <= 0`、单位未验证或数量不有限时返回 `INVALID_INPUT`。
- `sale_proceeds = q_old * old_bid`；`purchase_cost = q_new_executable * new_ask`；`net_reserve_release = proceeds - cost - fee_old - fee_new`；`reserve_after = reserve_before + release`。
- 正 release 表示 BTC 回到 reserve；负数表示需要 reserve。`reserve_after < 0` 必须失败关闭为 insufficient reserve，不得暗含杠杆。

## 9. Quality flags 与失败关闭

### 9.1 固定 flags

| Severity | Flags |
|---|---|
| `BLOCK_ACTION` | `DISCONNECTED_OR_HEARTBEAT_TIMEOUT`, `STALE_EXCHANGE_TIMESTAMP`, `TIMESTAMP_OUT_OF_ORDER`, `CLOCK_SKEW_EXCEEDED`, `SNAPSHOT_TIME_MISMATCH`, `NON_OPEN_STATE`, `STATE_ACTIVITY_INCONSISTENT`, `BOOK_SEQUENCE_GAP`, `UNKNOWN_SCHEMA_VERSION`, `SCHEMA_FIELD_ABSENT_REQUIRED`, `FIELD_TYPE_MISMATCH_REQUIRED`, `UNVERIFIED_DECISION_UNIT`, `CROSSED_MARKET`, `INVALID_PRICE`, `UNKNOWN_TRADE_SPEC` |
| `BLOCK_VALUE` | `NO_EXECUTABLE_BID`, `NO_EXECUTABLE_ASK`, `NO_MARK`, `IV_NOT_AVAILABLE`, `INVALID_IV`, `GREEK_UNIT_UNVERIFIED`, `FEE_UNVERIFIED`, `INVALID_QUANTITY`, `ESTIMATED_DELIVERY_NOT_NUMERIC` |
| `WARNING` | `EMPTY_BIDS`, `EMPTY_ASKS`, `EMPTY_STATS_OR_NO_TRADE`, `NEGATIVE_EXTRINSIC`, `NEAR_ZERO_PREMIUM`, `FORWARD_CARRY_INCONSISTENT`, `MODEL_BASIS_MISMATCH`, `INSUFFICIENT_HISTORY`, `SOURCE_SHAPE_UNVERIFIED` |
| `INFO` | `ZERO_QUOTE_SENTINEL`, `DELIVERED_FINAL_STATE`, `REPLAY_SOURCE` |

`BLOCK_VALUE` 的作用域是依赖该值的结果：无 Ask 只阻止该 candidate 的 entry；无 Bid 阻止该 leg 的 liquidation/roll；无 mark 阻止 mark NAV。它不一定使同一 run 的其他 instrument 失效。`BLOCK_ACTION` 可在 instrument 或 run scope 生效，scope 必须随 flag 保存。

### 9.2 失败关闭结果

- 连接/heartbeat 失败、stale、非 open、未知 schema/单位、required type error 或 snapshot 超窗时，只能输出 `DATA_ONLY`/明确错误状态，不能输出 `ATTRACTIVE`、`ROLL_CANDIDATE`、`STRONG_ROLL_CANDIDATE`。
- 新仓无有效 Ask：entry 指标 null，candidate 不可执行，状态 `NO_EXECUTABLE_ASK` 或 `WAIT_FOR_LIQUIDITY`。
- 旧仓无有效 Bid：liquidation NAV/roll unavailable，状态 `NO_EXECUTABLE_BID`。
- bid > ask：保留原值，设 `CROSSED_MARKET`，禁止行动性输出；不得交换、clip 或重排两边。
- 异常、缺失或单位不明的数值返回 typed unavailable reason，不返回 0、NaN 或 fallback。
- 内部异常为 `FAILED_INTERNAL`；不得降级成普通 `HOLD`。

## 10. 版本与可追溯链

### 10.1 三类版本

- `schema_version`：规范 DTO 的 shape 与字段语义；v1 固定 `domain-schema-v1`。breaking field/unit/null change 必须升 major。
- `metric_version`：不可变的指标/定价算法标识，格式 `<family>@<semver>`。v1 已冻结基础公式的初始值为 `core-metrics@1.0.0`，覆盖精确 DTE、mid/spread、index intrinsic、side-specific extrinsic/ICR/optionality/DCE和双 NAV；不覆盖本地 pricing、GCE、Utility或未验证 Greek。公式、price side、Greek basis、rounding、缺失处理或数值边界变化均必须产生新版本。
- `config_version`：解析和验证后的有效配置内容标识，推荐 `<label>+sha256:<digest>`。同一字符串必须对应完全相同的阈值、bucket、window、adapter selection 与实验开关；不能只记录文件名或更新时间。

辅助的 `instrument_spec_version`、`universe_filter_version`、`forward_adapter_version`、`pricing_model_version`、`fee_schedule_version` 和 `rounding_policy_version` 遵守同样的不可变原则。

### 10.2 必需链路

```text
RawMarketEvent(raw_event_id, payload_sha256)
  -> MarketSnapshot(market_snapshot_id, raw_event_ids, schema_version)
  -> MetricSnapshot(metric_snapshot_id, market_snapshot_id, metric_version, config_version)
  -> ScannerRun / PortfolioSnapshot
  -> RollEvaluation(old/new market+metric ids, portfolio id, all versions)
```

所有派生结果 MUST 能沿 ID 找回使用的 raw payload、instrument spec、exchange/observed/ingested clocks、schema、metric、config 和 adapter 版本。重放不得读取 event-time 之后才 observed 的 catalog、market 或 manual state。不得让 UI 重新拼接不同 snapshot 或自行重算核心指标。

## 11. 当前明确禁止实现的公式/策略

下列项目没有可靠的 v1 生产公式；接口可以预留或返回 unavailable，但不得猜测实现：

1. 最终 GCE 定义、GCE 权重，以及任何使用未验证 Deribit gamma 的指标；
2. 跨 Greek Utility、Utility 训练/归一化，以及无量纲 Utility 与 BTC cost 直接相减；
3. 把 `underlying_price` 无条件提升为 universal forward，或在未记录 adapter 时构造 forward；
4. local Black-76/BS USD 理论值转 BTC 的单一 production convention；当前只允许 versioned diagnostic regression；
5. option fee 的 rate base、cap 与 maker/taker production 假设；
6. 未配置的 equal-delta rounding 默认值、自动借贷或 release 自动复投；
7. 最终 peer buckets、staleness/skew 阈值、IV regime window、ranking 权重与行动阈值；
8. smile/skew dynamics、真实 shocked Bid/Ask 或把 stress 当预测；
9. 由当前数据回填历史 manual cycle high 或其他未来可见状态。

这些禁令不阻止 P04 实现稳定类型、枚举、availability、版本和配置入口。

## 12. 发布结论

`domain-contract-v1.0.0` 已发布，P04 获准进入。P04 必须实现本文的共享类型和失败表达，但不得借“补全实现”改变任何冻结定义。当前开放事项均已通过 raw-only、nullable、versioned adapter 或 feature gate 给出明确落点，因此不构成 P04 foundation blocker。
