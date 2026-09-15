# Gate 0：量化开放问题

> **状态：** `draft / input to P03`  
> **关联：** `quant-contract-draft-v0.1`  
> 此文件记录未决问题；不是默认实现授权，也不是策略参数表。

## Blocking：P03 发布领域契约前须明确落点

| ID | 问题 | 当前证据/风险 | 所需裁决或验证 |
|---|---|---|---|
| QG-01 | API `delta` 的 exact basis 是 index-spot (\partial V/\partial X) 还是 forward (\partial V/\partial F)？ | 文档称 Black-Scholes delta，ticker 有 `underlying_price`; basis 决定模型 sanity check。 | 录制 ITM/ATM/OTM 多期限 ticker，按同 snapshot BS/Black-76 两个候选比较；P03 冻结字段名/basis。 |
| QG-02 | `gamma` 的精确 unit/scale 是否为 standard delta 对 1 USD/BTC underlying move 的变化？ | 官方只说明定义；深 ITM ticker 可能显示 `0.0`，单样本无鉴别力。 | 按 QUANT 第 12 节做多样本 analytic + finite-difference 比例实验；确认前不用于 GCE/portfolio 风险金额。 |
| QG-03 | ticker `vega` 的 numeraire/scale 是否 USD per 1 vol point？ | Deribit 教育资料称 vega 是每 1% IV，但 API schema 未明写 price currency/scale。 | 用 +1 bp 与 +1 vol-point Black-76 shock 比例验证；保存 raw value 和 conversion version。 |
| QG-04 | ticker `theta` 是否 USD，且在不足一天准确采用 `min(one day, lifetime)` 的 value scale？ | horizon 由官方确认，numeraire及符号/annualization仍不能以文字推定。 | 选择 `T>1d` 与 `T<1d` 样本，比较 effective-horizon finite difference；确认后字段带 `effective_horizon`。 |
| QG-05 | `rho` 是每 1.00 annual rate 还是每 1 rate point，且是 USD 还是 BTC？ | API 仅给 sensitivity定义。 | 多 IV/expiry 的 BS rho 1.00/0.01 比例实验；未确认前只保存 `deribit_rho_raw`。 |
| QG-06 | `underlying_price` 与 `underlying_index` 对 BTC inverse options 的 exact forward/synthetic-forward、timestamp 和 carry relationship？ | 官方支持页给出概念；需要真实 field contract 确认 feed 行为。 | P01 以录制 payload、官方 API reference 定义 nullability/映射；P03 决定 `forward_price_usd` adapter。 |
| QG-07 | P03 是否接受 `intrinsic_index_*` 为 ICR/OR 默认，并把 `intrinsic_forward_pv_*` 只做 pricing reference？ | 两者都有意义但回答的问题不同；混用会改变“资本老化”。 | 在 `DOMAIN_CONTRACT` 明确字段/默认 side；禁止单一 `intrinsic_value`。 |
| QG-08 | Domain contract 是否把 `deribit_delta_standard`、`model_delta_*` 与 `model_btc_nav_delta_per_log_index_mark` 分开？ | BTC quote分母使后两者不等；复用 `delta` 会产生错误 NAV/GCE 解释。 | 采纳或版本化替代语义，并规定 API Greek 未验证前的 quality flag。 |
| QG-09 | option fee 的公式、费率基数、fee cap、maker/taker假设如何进入模拟？ | instrument commission 字段不等于已验证的 option cash-flow fee formula。 | P01 获取官方 fee evidence；P03 要么冻结 `fee schedule input` protocol，要么让 roll 输出 `FEE_UNVERIFIED`。 |
| QG-10 | equal-delta 的 rounding 规则和不足 min trade amount 行为？ | BTC options 允许 fractional contracts，但具体 instrument rules和策略的 rounding方向影响 reserve/delta。 | Domain/config 定义 versioned `rounding_policy`；必须同时输出 theoretical/executable quantities。 |

## Non-blocking：可在冻结接口后由后续研究/实现版本化

| ID | 问题 | 当前处理 |
|---|---|---|
| QN-01 | 哪一种 GCE 预测 capital aging 或未来风险调整收益？ | 并列保存 `gamma_spot_scaled`、`gamma_dollar_scaled`、两个 premium-normalized GCE；不选赢家。 |
| QN-02 | DCE 是否真是有用的资本效率，是否该用 standard USD-NAV delta 或 BTC-NAV sensitivity？ | 两者并列；DCE 定义保持原始研究目标，BTC-NAV sensitivity 独立呈现。 |
| QN-03 | ICR/OR 应否使用 forward PV 版本做补充排名？ | 先默认 index versions；可增列显式 `*_forward_pv`，不覆盖默认。 |
| QN-04 | Utility 的确定形式、训练窗口、归一化和阈值？ | 不实现最终 Utility；BTC cost、spread、fee 与 standardized improvement 独立 gate。 |
| QN-05 | liquidity/OI/volume/spread/min amount 最终阈值？ | 不在 Gate 0 设置；所有未来阈值配置化并标 `EXPERIMENTAL`。 |
| QN-06 | stress 的 smile dynamics 应 sticky strike、moneyness、delta还是模型曲面？ | baseline 只用 `flat_iv + preserve_carry_rate`; 其他规则为可替换情景，非预测。 |
| QN-07 | stress forward 随时间应如何构造？ | 强制记录 `forward_policy`; 不把旧 F 无记录地沿用。 |
| QN-08 | negative extrinsic（bid 小于 index intrinsic）是否主要是 timestamp/market microstructure？ | 保留值并 flag，基于录制数据统计后再决定 quality gate。 |
| QN-09 | model-vs-exchange Greek 容差和 stale/snapshot skew 阈值？ | 先用调查阈值，后由 fixtures/live sampling 经验和配置版本决定。 |
| QN-10 | BTC 和 USD NAV 各自应作为何种风险/回测报告主指标？ | 强制双报，不在 Gate 0 宣布优先级或策略目标。 |

## 必须随 P01 真实响应补充的证据包

1. 原始 `public/get_instrument(s)` 与 option ticker payload（至少深 ITM、ATM、OTM；至少三类 DTE）；
2. 每个 payload 的 exchange timestamp、接收时间、instrument metadata、`index_price`、`underlying_price`、`interest_rate`、bid/ask/mark、IV 与五个 Greeks；
3. 明确原始 IV 是百分数还是 decimal，及零/缺失 bid/ask IV 的语义；
4. `contract_size`、`min_trade_amount`、tick-size steps、commission/fee 相关字段的官方含义；
5. 含 `T<1d` 的 ticker，用于 theta effective-horizon 实验；
6. 以相同快照输入运行的 Black-76 analytic 与 finite-difference 比对表，含使用的 basis/scale/容差。

## P03 不得绕过的量纲警报

- `standardized_utility`（无量纲）不能减 `fees_btc`、`spread_cost_btc` 或 `net_reserve_release_btc`（BTC）。
- `gamma * S`、`gamma * S^2`、`gamma/premium_btc` 和 `gamma*S^2/premium_usd` 单位不同；后两者数值可能相等但不是两个独立 signal。
- `deribit_delta_standard` 不是 BTC quote 对 index 的导数；BTC NAV sensitivity需要显式转换并依赖 basis。
- `premium_btc * index_price_usd` 才是同 snapshot 的 USD premium；不能无说明地以 forward 或 delivery 换算。
- `theta` 的不足一天有效期限不是“daily theta”；`vega`、`rho` 不得在未确认是 1.00 还是 1-point 前与 shock 相乘。
- mark NAV 与 liquidation NAV 不可相减、混合或在缺 Bid 时将 mark 当 liquidation value。
