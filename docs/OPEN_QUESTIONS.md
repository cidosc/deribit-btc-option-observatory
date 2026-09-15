# Domain Contract Open Questions

> **适用契约：** `domain-contract-v1.0.0`  
> **更新：** 2026-09-14 UTC  
> 本文件只列尚未被证据可靠裁决的事项。开放问题不是默认值，也不是实现授权。

## Blocking — P04 入口

**零项。**

P04 只实现 foundation、共享类型、枚举、availability、quality flags 和配置/版本协议。下列不确定性均已通过 raw-only、nullable、versioned adapter 或 feature gate 隔离，不要求 P04 猜测任何单位或公式。因此 `domain-contract-v1.0.0` 明确允许进入 P04。

若 P04 发现无法仅凭已发布文档表达某个共享类型，必须停止该局部实现并提交 change proposal；不得把本表中的研究问题转成隐含默认值。

## Non-blocking for P04 / feature-level blockers

这些问题不阻止 P04，但可能阻止表中列出的后续能力成为 production-ready。

| ID | 未决问题 | 当前强制处理 | 阻止的能力 / 所需证据 |
|---|---|---|---|
| OQ-00 | API standard delta 的 exact derivative basis 是 index spot 还是 forward/underlying？ | 数值与单位可用于 DCE和equal-standard-delta；model comparison携带 provisional basis，不把差异判成API异常。 | 阻止精确的API-vs-local delta attribution。需多 moneyness/DTE、不同carry样本及同输入候选回归。 |
| OQ-01 | Deribit gamma 的 exact basis、scale 和有效显示精度是否稳定为 standard delta per 1 USD underlying move？ | 只保存 `deribit_gamma_raw`；normalized gamma=null，`UnitStatus.UNVERIFIED`。 | 阻止 GCE、portfolio gamma金额、gamma-based ranking。需 ITM/ATM/OTM、多期限、非零 gamma 同快照 analytic + finite difference 回归。 |
| OQ-02 | `underlying_price` 在各 expiry 上何时是 listed future mark、synthetic forward 或 expiry value，其 timestamp/carry relationship如何？ | 保留 `underlying_price` 与 `underlying_index_name`；不自动命名/复制为 model forward。 | 阻止 universal forward adapter 和 production local Black-76 comparison。需多 expiry fixtures与来源行为证据。 |
| OQ-03 | local USD theoretical price 转成 BTC model price 的普适 numeraire 应是 index、underlying/forward还是另一 inverse formula convention？ | 报告换算仅用 observed BTC premium × index；Deribit回归仅用 diagnostic adapter。 | 阻止 production local BTC mark/IV solver。需跨 moneyness/DTE、已知 rate 的 mark/Greek/price回归及官方模型说明。 |
| OQ-04 | 非零 `interest_rate` 的 raw scale、compounding convention和它与 underlying carry 的关系？ | 保存 `interest_rate_raw` 和 `UNVERIFIED` unit status；除 diagnostic raw-input regression 外不用于 production model。 | 阻止含非零 rate/carry 的 production pricing/stress。需非零 rate fixtures和模型回归。 |
| OQ-05 | Deribit option fee 的计算基数、maker/taker语义及 fee cap？ | commission字段仅 raw；fee schedule缺失时 roll cashflow unavailable并设 `FEE_UNVERIFIED`。 | 阻止 P07 production executable roll economics。需官方 fee evidence、计算例和版本化 fee schedule。 |
| OQ-06 | equal-standard-delta 的默认 rounding direction、tie rule和小于 min amount时的策略？ | enum/协议可实现；必须由 `rounding_policy_version` 显式给定，没有默认则 unavailable。 | 阻止 executable quantity recommendation。需策略选择与性质测试；不得由工程实现者代选。 |
| OQ-07 | open option 空 Bid/Ask 的真实 payload 是 null、0还是字段缺失；bid/ask IV如何联动？ | 三种 raw presence都接受并区分；canonical executable side均按正值 gate。 | 不阻止 parser类型；阻止宣称完整 live nullability contract coverage。需 open empty-side fixture。 |
| OQ-08 | lifecycle event 与 book incremental 的真实 envelope/sequence异常形态？ | 依官方 shape预留，未录制形态设 `SOURCE_SHAPE_UNVERIFIED`。 | 阻止 P05 声称 lifecycle/recovery live contract test完备。需 lifecycle event、snapshot+increment+gap fixtures。 |
| OQ-09 | stale、clock skew、old/new snapshot skew、heartbeat和 reconnect backoff阈值是多少？ | 字段和 fail-close语义固定；数值必须配置化、标实验来源并写入 `config_version`。 | 阻止 production行动性默认参数。需 collector soak、网络故障和时间分布证据。 |
| OQ-10 | 最终 peer cohort bucket、minimum sample、percentile方向、IV windows与warm-up阈值？ | ScannerRun必须记录版本、membership、count和insufficient history；不设隐藏默认。 | 阻止稳定排名/IV regime结论。需历史覆盖与回放实验。 |
| OQ-11 | 哪一种 GCE/资本老化指标有研究价值？ | 保留明确量纲的候选接口；不得选赢家或重复加权数值等价候选。 | 阻止 final GCE 和 GCE-based action state。需版本化实证比较。 |
| OQ-12 | Utility 的形式、normalization、window、权重和 BTC certainty-equivalent？ | 不实现 final Utility；无量纲 improvement与BTC cost必须独立 gate。 | 阻止 Utility ranking及单一净收益不等式。需正式目标函数和out-of-sample验证。 |
| OQ-13 | negative extrinsic 的主要成因和是否应成为 hard gate？ | 保留负值，flag `NEGATIVE_EXTRINSIC`；只要 quote本身未触发其他block，不自动clip或宣判策略。 | 阻止把该 flag 固化为 action threshold。需同时间窗 market microstructure统计。 |
| OQ-14 | Stress 的 forward evolution与smile dynamics选择？ | 每个场景显式记录 policy；只能报告条件模型结果，不能称预测或 shocked executable NAV。 | 阻止 surface-aware stress默认模型。需曲面数据与版本化策略研究。 |
| OQ-15 | raw retention、压缩分片与清理周期？ | raw lineage和不可覆盖原则固定；不在领域契约设计数据库细节。 | 阻止 production运维参数，但不阻止 domain/data interfaces。需容量/soak证据后由存储策略决定。 |
| OQ-16 | manual cycle high 的输入、版本化和event-time可见性协议？ | v1 不允许回填历史；任何 manual state必须有 observed time和config/state version。 | 阻止其进入回测或 action rules。需独立状态契约。 |

## 已关闭的原 P02 blocking 项

| 原 ID | v1 落点 |
|---|---|
| QG-01 | standard delta的数值/单位和字段域已冻结；exact derivative basis以 OQ-00 显式保留，不阻止P04。 |
| QG-02 | gamma 的 target canonical unit已定义，但 normalized value明确禁止生成；作为 OQ-01 feature blocker，不再阻止 P04。 |
| QG-03 | vega 由 fixture 回归冻结为 USD/contract/1 vol point。 |
| QG-04 | theta 由官方 horizon规则和 fixture 回归冻结为 USD/contract/effective horizon。 |
| QG-05 | rho 由 fixture 回归冻结为 USD/contract/1 annual rate point。 |
| QG-06 | underlying不自动等同 forward；字段和adapter边界已冻结，深层语义转 OQ-02。 |
| QG-07 | 当前 ICR/OR默认采用 index intrinsic；forward-PV只作显式定价参照。 |
| QG-08 | Deribit/model/BTC-NAV sensitivities分域已冻结。 |
| QG-09 | fee未验证时 typed unavailable + `FEE_UNVERIFIED`，不再迫使 P04猜公式；转 OQ-05。 |
| QG-10 | theoretical/executable quantities和versioned rounding protocol已冻结；策略默认转 OQ-06。 |

## 关闭问题的证据门槛

开放问题只能由以下一种方式关闭：新增不可修改 fixture + 可复现数值报告；明确官方规范且与真实响应无冲突；或正式版本化的研究/策略决策。关闭时必须更新 `DECISIONS.md`，必要时提升 adapter、metric、schema 或 domain-contract版本。单次手工观察、为了让测试通过的常量或实现者直觉均不构成关闭证据。
