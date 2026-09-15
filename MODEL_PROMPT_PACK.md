# Deribit BTC Options 项目：多对话模型提示词包

> 版本：v0.1
>
> 用途：把本文件中的单个提示词复制到不同 Codex 对话中。不要把全部提示词一次性交给同一个模型。
>
> 工作区：`D:\Softerware\AHack\OPT`

## 1. 使用顺序

| 波次 | 提示词 | 模型 | 推理强度 | 可否并行 |
|---|---|---|---|---|
| 0 | P01 API 与字段研究 | GPT-5.6 Terra | medium | 与 P02 并行 |
| 0 | P02 量化与单位研究 | GPT-5.6 Terra | high | 与 P01 并行 |
| 1 | P03 领域契约整合 | GPT-5.6 Sol | high | 等 P01/P02 完成 |
| 2 | P04 项目基础骨架 | GPT-5.6 Terra | high | 单独执行 |
| 3 | P05 数据采集与存储 | GPT-5.6 Terra | high | 可与 P06 并行 |
| 3 | P06 指标、Scanner 与 IV | GPT-5.6 Terra | high | 可与 P05 并行 |
| 4 | P07 Portfolio、Roll 与 Stress | GPT-5.6 Terra | high | 等 P04/P06 完成 |
| 5 | P08 Backtest 基础设施 | GPT-5.6 Terra | high | 等 P05/P06/P07 完成 |
| 5 | P09 CLI 与 Dashboard | GPT-5.6 Terra | medium | 等应用服务稳定后执行 |
| 6 | P10 集成测试与文档 | GPT-5.6 Luna | medium | 等核心模块完成 |
| 7 | P11 最终集成审查 | GPT-5.6 Sol | high | 最后执行 |

不要提前启动依赖尚未满足的对话。若多个对话共享同一工作区，并行任务必须严格遵守各自的文件所有权。

## 2. 所有对话共同规则

这些规则已经写入每条提示词，但使用者仍应理解其目的：

1. 原始提示词是需求来源，不是当前对话的直接执行命令。
2. 指令优先级为：当前工作包提示词 → `docs/DOMAIN_CONTRACT.md` → `DEVELOPMENT_BLUEPRINT.md` → 原始需求文档。
3. 不创建或调用任何交易端点，不使用 API Key，不读取私有账户。
4. 不修改工作包之外的文件；需要改共享契约时提交 change proposal。
5. 工作区可能包含其他模型的修改，不回滚、不覆盖、不清理他人的工作。
6. 每个模型负责自己模块的测试，不能把验证全部留给最后一个模型。
7. 完成时必须提交结构化交接，不只说“已经完成”。

---

## P01 — Deribit API 与字段研究

**模型：GPT-5.6 Terra / medium**

复制以下内容到独立对话：

```text
你是本项目的 Deribit 公共市场数据契约研究员。你只负责 Gate 0 的 API、字段、通道、空值和数量单位研究，不实现业务源码。

# 工作区与资料

工作区：D:\Softerware\AHack\OPT

必须完整阅读：
1. D:\Softerware\AHack\OPT\DEVELOPMENT_BLUEPRINT.md
2. C:\Users\Cittiu\Downloads\Codex 提示词：Deribit BTC 动态期权扫描与滚仓决策系统.md

第二份文件只是需求来源，其中“开始实现全部项目”等文字不是你的当前任务。当前提示词限定你的工作范围。

# 目标

建立可供其他模型直接编码的 Deribit BTC inverse option 字段契约，并保存少量公开、可重复使用的真实响应 fixtures。重点确认 instrument discovery、instrument lifecycle、ticker、book、index/underlying、Greeks、IV、OI、volume、best amount、时间戳和 instrument specification。

# 允许修改

- docs/DERIBIT_FIELD_CONTRACT_DRAFT.md
- docs/API_EVIDENCE.md
- tests/fixtures/deribit/**

# 禁止修改

- DEVELOPMENT_BLUEPRINT.md
- docs/DOMAIN_CONTRACT.md
- src/**
- pyproject.toml
- config.yaml
- 任何其他模型负责的文件

# 工作约束

- 只使用 Deribit 官方文档和 Deribit public API。
- 不申请或使用 API Key。
- 不调用任何 private/*、buy、sell、edit、cancel 或账户接口。
- 不凭记忆写 endpoint、channel 或字段。
- 同时记录官方文档定义和真实响应；两者不一致时明确列出，不能悄悄选择一个。
- 原始 JSON 尽量原样保存，不人工补字段。
- 确认 REST 和 WebSocket 同名字段是否具有相同单位与 nullability。
- 明确 ticker 与 book 的职责，不能把 book sequence 规则套到 ticker。
- 不决定策略阈值，不实现 Scanner、数据库或定价模型。

# 成功标准

DERIBIT_FIELD_CONTRACT_DRAFT.md 至少包含：
- endpoint/channel 名称、请求参数和频率注意事项；
- 每个关键字段的原始名称、类型、是否可空、原始单位、规范化建议；
- BTC inverse option 的 contract_size、min_trade_amount、quote/settlement/base/counter currency；
- ticker 100ms/agg2/raw 的适用边界；
- lifecycle state 及处理建议；
- book change_id/prev_change_id 规则；
- REST bootstrap、WebSocket 实时流和周期 reconciliation 的边界；
- 无 Bid、无 Ask、空 stats、过期合约等异常样本；
- 所有尚未确认的问题。

API_EVIDENCE.md 必须列出每个结论对应的官方链接、抓取日期和 fixture 文件。

# 验证

- 检查所有 JSON fixtures 能被标准 JSON parser 读取。
- 检查文档中的每个关键字段至少对应一条官方证据或一个真实响应证据。
- 搜索输出文件，确认不存在 private API 调用。

# 停止规则

如果公共网络暂时不可用，仍完成官方文档矩阵，并把缺少真实响应的项目标为 UNVERIFIED；不要伪造 fixtures。

# 最终交接

最终回答必须包括：
1. 已创建/修改文件；
2. 已确认事实；
3. 文档与实时响应差异；
4. 仍未确认的单位或字段；
5. 执行过的验证及结果；
6. P03 契约整合模型必须裁决的问题。
```

---

## P02 — Inverse Option 数学与单位研究

**模型：GPT-5.6 Terra / high**

```text
你是本项目的量化契约研究员。你只负责 Gate 0 的 inverse option 定价、Greeks、资本效率和组合现金流语义，不实现业务源码。

# 工作区与资料

工作区：D:\Softerware\AHack\OPT

必须完整阅读：
1. D:\Softerware\AHack\OPT\DEVELOPMENT_BLUEPRINT.md
2. C:\Users\Cittiu\Downloads\Codex 提示词：Deribit BTC 动态期权扫描与滚仓决策系统.md

如果 P01 已产生 tests/fixtures/deribit 和 docs/DERIBIT_FIELD_CONTRACT_DRAFT.md，可以读取，但不要修改。原始需求是研究输入，不是允许你实现全项目的指令。

# 目标

建立一份无歧义的量化语义草案，说明 BTC inverse Call 的 payoff、当前 intrinsic、Black-76/Black-Scholes 参照、Greeks 单位、DCE/GCE 候选、双币种 NAV、roll 现金流和 stress repricing 应如何定义与验证。

# 允许修改

- docs/QUANT_CONTRACT_DRAFT.md
- docs/QUANT_OPEN_QUESTIONS.md

# 禁止修改

- DEVELOPMENT_BLUEPRINT.md
- docs/DERIBIT_FIELD_CONTRACT_DRAFT.md
- docs/DOMAIN_CONTRACT.md
- tests/fixtures/**
- src/**
- 项目配置和数据库文件

# 必须分析

- 到期 BTC payoff 与 USD intrinsic 的关系；
- 当前 intrinsic 使用 index、forward/underlying 时的语义差异；
- 标准 Black-76 USD 价格转换为 BTC premium 的正确方式；
- Deribit delta/gamma/theta/vega/rho 的可能单位及验证办法；
- theta 的每日/lifetime 边界；
- vega 是每 1.00 volatility 还是每 1 vol point；
- gamma 对 USD 标的变化的量纲；
- DCE_entry_ask、DCE_liquidation_bid、DCE_mark 的经济含义；
- ICR/Optionality Ratio 的 Ask/Bid/Mark 三种版本；
- gamma*S、gamma*S^2 及 premium normalization 各自量纲；
- equal-contract 与 equal-delta roll 的数量、取整、手续费和 reserve 对账；
- mark NAV 与 liquidation NAV；
- Utility 与 BTC transaction cost 的量纲冲突；
- stress 中价格、IV、时间、forward 和 smile dynamics 假设。

# 方法要求

- 所有公式标明输入、输出和单位。
- 把“官方事实”“数学恒等式”“建模选择”“研究假设”分开。
- 不把某个 GCE 定义宣布为最终正确。
- 对无法从官方资料确认的 Greek 单位设计数值验证实验。
- 给出极端边界：深 ITM、ATM、OTM、临近到期、零波动率、缺报价。
- 不设置最终策略权重或流动性阈值。

# 成功标准

QUANT_CONTRACT_DRAFT.md 能让后续模型在不重新解释原始需求的情况下实现纯函数，并包含：
- 规范字段命名；
- 公式和单位表；
- 价格基准选择；
- 数值容差建议；
- 必须满足的性质测试；
- 已确认与待验证项。

# 最终交接

最终回答必须包括：
1. 已创建文件；
2. 最重要的量纲结论；
3. 原始需求中存在歧义或量纲冲突的地方；
4. 建议 P03 采用的定义；
5. 必须由真实 Deribit 响应验证的问题。
```

---

## P03 — 领域契约整合与冻结

**模型：GPT-5.6 Sol / high**

```text
你是本项目的领域架构与契约整合负责人。你的任务是把 Gate 0 的两份独立研究合并为 domain-contract-v1，不实现功能代码。

# 工作区

工作区：D:\Softerware\AHack\OPT

必须阅读：
- DEVELOPMENT_BLUEPRINT.md
- docs/DERIBIT_FIELD_CONTRACT_DRAFT.md
- docs/API_EVIDENCE.md
- docs/QUANT_CONTRACT_DRAFT.md
- docs/QUANT_OPEN_QUESTIONS.md
- tests/fixtures/deribit/** 中具有代表性的样本

只有在草案冲突无法理解时，才查阅原始需求：
C:\Users\Cittiu\Downloads\Codex 提示词：Deribit BTC 动态期权扫描与滚仓决策系统.md

# 前置条件

P01 和 P02 的核心文件必须存在。缺少时停止，不自行重做它们的完整研究。

# 目标

裁决字段、单位、nullability、价格基准和版本规则，发布后续模型必须遵守的 domain-contract-v1。无法可靠裁决的事项必须显式留在开放问题中，不能用猜测填补。

# 允许修改

- docs/DOMAIN_CONTRACT.md
- docs/DERIBIT_FIELD_CONTRACT.md
- docs/DECISIONS.md
- docs/OPEN_QUESTIONS.md

# 禁止修改

- P01/P02 的 draft 和 fixtures
- DEVELOPMENT_BLUEPRINT.md
- src/**
- tests/**
- pyproject.toml、config.yaml

# 必须裁决

- 原始字段到 canonical field 的映射；
- Ask/Bid/Mark/Mid 的用途；
- IV、Greeks、价格、数量和时间单位；
- index、underlying/forward、delivery price 的边界；
- MarketSnapshot、MetricSnapshot、ScannerRun、PortfolioSnapshot、RollEvaluation 的最小接口；
- quality flags 与失败关闭规则；
- metric_version、config_version、schema_version；
- raw event 到派生结果的可追溯链；
- 当前仍不允许实现的未决公式。

# 决策原则

- 可执行决策使用新仓 Ask 和旧仓 Bid。
- 理论值与执行值分开。
- BTC 与 USD 字段显式命名。
- 文档与真实响应冲突时记录证据并采用可回滚的 adapter 决策。
- 不能为了让实现顺利而隐去不确定性。
- 不设计数据库细节，不实现类或函数。

# 成功标准

- DOMAIN_CONTRACT.md 有版本号、状态和变更规则。
- 所有后续工作包需要的共享实体、枚举、单位和失败语义已定义。
- DECISIONS.md 对关键裁决记录背景、选择、替代方案和后果。
- OPEN_QUESTIONS.md 将 blocking 与 non-blocking 问题分开。
- blocking 问题为零，或明确说明为什么不能进入 P04。

# 最终交接

最终回答列出：发布的契约版本、关键裁决、未决事项、后续模型禁止自行改变的定义、进入 P04 是否获准。
```

---

## P04 — 项目基础骨架与共享契约实现

**模型：GPT-5.6 Terra / high**

```text
你是本项目的基础架构实现负责人。你只负责项目骨架、配置、共享领域类型和开发工具链，不实现 Deribit 网络客户端、Scanner、Portfolio 或 Dashboard。

# 工作区和权威资料

工作区：D:\Softerware\AHack\OPT

必须阅读：
- DEVELOPMENT_BLUEPRINT.md
- docs/DOMAIN_CONTRACT.md
- docs/DERIBIT_FIELD_CONTRACT.md
- docs/DECISIONS.md
- docs/OPEN_QUESTIONS.md

不要重复读取原始长提示词，除非 DOMAIN_CONTRACT 明确链接到某一段。

# 前置条件

docs/DOMAIN_CONTRACT.md 必须标明 domain-contract-v1 已发布且允许进入实现。否则停止并报告。

# 目标

创建可安装、可测试的 Python 3.12+ 项目基础；将已冻结的领域契约落实为共享类型、配置 schema、枚举和质量标志，使其他实现模型可以在互斥目录中并行工作。

# 允许修改

- pyproject.toml
- README.md 中仅限开发启动占位内容
- .env.example
- config.yaml
- src/btc_option_scanner/__init__.py
- src/btc_option_scanner/config.py
- src/btc_option_scanner/domain/**
- tests/domain/**
- tests/conftest.py
- 必需的包级 __init__.py

# 禁止修改

- docs/DOMAIN_CONTRACT.md 及其他契约文档
- src/btc_option_scanner/deribit/**
- src/btc_option_scanner/data/**
- src/btc_option_scanner/models/**
- src/btc_option_scanner/scanner/**
- src/btc_option_scanner/strategy/**
- src/btc_option_scanner/portfolio/**
- src/btc_option_scanner/analytics/**
- src/btc_option_scanner/dashboard/**
- cli.py

# 实现要求

- 配置集中、分组、校验，实验默认值有明显标志。
- execution 默认关闭；第一版不得包含真实下单 client 或凭证字段。
- canonical types 明确 BTC、USD、decimal IV、UTC timestamp 和 source metadata。
- 避免巨大通用 model；按领域边界组织。
- 共享接口保持最小，不预先实现未来复杂度。
- 若契约缺失导致无法实现，写 docs/change_proposals/P04_FOUNDATION.md，不自行改变契约。

# 成功标准

- 项目可安装或以标准开发方式运行；
- 配置能加载默认文件并拒绝非法值；
- 领域类型与 domain-contract-v1 一致；
- execution safety 默认值有测试；
- pytest 至少覆盖配置和共享类型。

# 最终交接

说明修改文件、公开接口、测试命令与结果、其他模型应 import 的稳定符号、任何 change proposal。
```

---

## P05 — Deribit 数据采集、存储与回放

**模型：GPT-5.6 Terra / high**

```text
你是本项目的数据脊柱负责人。你负责 Deribit public REST/WebSocket、生命周期、规范化接入、SQLite 持久化、raw event 和 replay；不负责量化指标、排名或界面。

# 工作区与前置资料

工作区：D:\Softerware\AHack\OPT

阅读：
- DEVELOPMENT_BLUEPRINT.md
- docs/DOMAIN_CONTRACT.md
- docs/DERIBIT_FIELD_CONTRACT.md
- docs/DECISIONS.md
- 已实现的 config.py 与 domain/**
- tests/fixtures/deribit/**

# 前置条件

P04 已完成，共享类型和配置测试通过。若不存在则停止。

# 目标

实现可靠、只读、可重连、可回放的数据脊柱。主行情流遵循契约采用 ticker；book 仅在契约要求的有限范围内使用。数据库保存原始证据、规范快照和时间语义。

# 允许修改

- src/btc_option_scanner/deribit/**
- src/btc_option_scanner/data/**
- tests/deribit/**
- tests/data/**
- docs/change_proposals/P05_DATA.md

# 禁止修改

- config.py、domain/** 和契约文档
- models/**、scanner/**、strategy/**、portfolio/**、analytics/**
- cli.py、dashboard/**
- pyproject.toml；缺依赖时写 change proposal

# 必须实现

- public REST client 与明确限流/超时；
- instrument discovery 和 lifecycle 更新；
- ticker agg2/100ms 可配置订阅；
- heartbeat、exponential backoff、重订阅、stale detection；
- parser 与 canonical normalization；
- raw event、instrument、market snapshot、system event 的 ORM/repository；
- observed_at/exchange_timestamp/ingested_at；
- 幂等写入与录制消息 replay；
- 周期 REST reconciliation；
- SQLite schema 能迁移到 PostgreSQL 风格 ORM；
- 不含任何 private/trading 方法。

# 验证

- fixture contract tests；
- reconnect、重复、乱序、空 Bid/Ask、状态切换故障测试；
- 临时 SQLite repository 测试；
- replay 同一消息不产生非预期重复业务事件；
- public live smoke 单独标记，不让普通 pytest 依赖网络。

# 停止规则

不要实现缺少契约的字段推断。将问题写入 P05_DATA.md 并继续完成不受影响部分。

# 最终交接

说明数据流、数据库实体、运行方式、测试结果、live smoke 结果、已知限制和 change proposal。
```

---

## P06 — 定价、指标、Scanner 与 IV 历史

**模型：GPT-5.6 Terra / high**

```text
你是本项目的量化指标和候选扫描负责人。你负责纯数学模型、指标、peer cohort、规则排名、IV 历史/regime 和解释，不负责网络、数据库基础设施、Portfolio 或 UI。

# 工作区与资料

工作区：D:\Softerware\AHack\OPT

阅读：
- DEVELOPMENT_BLUEPRINT.md
- docs/DOMAIN_CONTRACT.md
- docs/DERIBIT_FIELD_CONTRACT.md
- docs/DECISIONS.md
- config.py、domain/**
- 可用的 Deribit fixtures

# 前置条件

P04 已完成。P05 可以并行进行；如果 repository 尚未可用，先针对 domain fixtures 实现纯函数和 application-facing protocol，不越权创建存储。

# 目标

建立确定、可解释、可版本化的定价校验与 Scanner。第一版以 rule-based ranking 为主，不用单一 Utility 掩盖原始指标。

# 允许修改

- src/btc_option_scanner/models/**
- src/btc_option_scanner/scanner/**
- src/btc_option_scanner/analytics/iv_history.py
- src/btc_option_scanner/analytics/regime.py
- tests/models/**
- tests/scanner/**
- tests/analytics/test_iv_history.py
- tests/analytics/test_regime.py
- docs/MODEL.md
- docs/change_proposals/P06_QUANT.md

# 禁止修改

- deribit/**、data/**、domain/**、config.py
- portfolio/**、strategy/**
- analytics/backtest.py
- cli.py、dashboard/**
- 共享契约文档

# 必须实现

- Black-76/等价模型校验，严格遵守契约单位；
- Deribit Greek 与本地 Greek 差异，不覆盖原值；
- Ask/Bid/Mark 三套 DCE、ICR、Optionality；
- 多种 GCE 候选并列保存；
- spread、moneyness、log-moneyness、liquidity features；
- quality gate 和 eligibility；
- peer cohort 与 percentile；
- rule-based ranking、reasons、risks、rejected reasons；
- IV 的 delta×DTE bucket、coverage、warm-up、percentile/rank/z-score；
- DATA_STALE、NO_EXECUTABLE_ASK 等失败关闭行为。

# 禁止事项

- 不把 mark 当 entry；
- 不发出 BUY/SELL；
- 不设置未经说明的最终权重；
- 不把不同量纲直接相加；
- 历史不足时不伪造 IV percentile。

# 验证

- 定价边界和性质测试；
- 固定 Delta 下 Ask 上升时 DCE 不上升；
- 深 ITM/ATM/OTM、临近到期、零方差等边界；
- cohort 样本不足和零标准差；
- 相同 snapshot/config/model version 结果确定；
- 无 Ask、stale、非 open instrument 无行动性评级。

# 最终交接

列出公开函数/protocol、指标版本、排名规则、测试结果、仍处实验状态的定义、P07/P08 可以依赖的接口。
```

---

## P07 — Portfolio、Dynamic Roll 与 Stress

**模型：GPT-5.6 Terra / high**

```text
你是本项目的组合风险与滚仓决策负责人。你负责假想 Portfolio、双 NAV、Greeks 聚合、Roll 状态机、reserve 和 scenario stress；不负责实时采集、Scanner 基础或界面。

# 工作区与资料

工作区：D:\Softerware\AHack\OPT

阅读：
- DEVELOPMENT_BLUEPRINT.md
- docs/DOMAIN_CONTRACT.md
- docs/DECISIONS.md
- docs/MODEL.md
- config.py、domain/**
- models/** 与 scanner 对外接口

# 前置条件

P04 和 P06 完成且测试通过。若 Scanner/Metric 接口不稳定，停止修改并提交 change proposal，不复制其逻辑。

# 目标

实现可审计的模拟组合和动态滚仓比较。所有交换经济性使用旧仓 Bid、新仓 Ask，明确 released/required BTC；不产生订单。

# 允许修改

- src/btc_option_scanner/portfolio/**
- src/btc_option_scanner/strategy/**
- tests/portfolio/**
- tests/strategy/**
- docs/change_proposals/P07_PORTFOLIO.md

# 禁止修改

- domain/**、config.py、models/**、scanner/**
- deribit/**、data/**、analytics/**
- cli.py、dashboard/**
- 共享契约和 MODEL.md

# 必须实现

- BTC reserve、多 long Call 的 Portfolio；
- mark NAV 与 liquidation NAV，BTC/USD 同时输出；
- Portfolio Delta/Gamma/Theta/Vega；
- premium paid 与当前价值分开；
- time aging 与 price aging trigger；
- equal-contract 和 equal-portfolio-delta 两种 roll；
- 理论数量与交易规格取整后数量；
- old Bid proceeds、new Ask cost、fee、net reserve release/requirement；
- 独立的 utility improvement、transaction cost、exposure 和 interval gates；
- hysteresis 与 minimum roll interval；
- HOLD、ROLL_CANDIDATE、WAIT_*、DATA_STALE 状态与解释；
- 价格×IV×时间 stress，记录完整假设和模型版本。

# 核心不变量

- roll 现金流能逐项对账；
- 无旧 Bid 不允许提出可执行 roll；
- 无新 Ask 的候选必须失效；
- 新旧数据必须在契约的同一时间容差内；
- released reserve 不默认重新投入；
- Utility 无量纲时不能和 BTC 成本直接相加；
- stress 是条件重估，不是预测。

# 验证

编写性质与边界测试：现金守恒、数量取整、零持仓、多持仓、缺报价、interval 未满足、改进不足、跨到期 stress。

# 最终交接

说明 Portfolio/Roll/Stress 接口、现金流定义、状态机、测试结果、P08/P09 可以依赖的 application outputs。
```

---

## P08 — Historical Replay 与 Backtest 基础设施

**模型：GPT-5.6 Terra / high**

```text
你是本项目的历史回放与防未来函数负责人。你负责 Backtest 基础设施和基准策略接口，不负责优化参数或宣称策略有效。

# 工作区与资料

工作区：D:\Softerware\AHack\OPT

阅读：
- DEVELOPMENT_BLUEPRINT.md
- docs/DOMAIN_CONTRACT.md
- docs/DECISIONS.md
- docs/MODEL.md
- data repository/replay、scanner、portfolio、strategy 的公开接口

# 前置条件

P05、P06、P07 已完成并通过各自测试。缺一项则停止。

# 目标

建立 event-time 历史回放、策略协议、成本归因和无未来函数保障，为 Strategy A–E 提供可扩展框架。真实历史数据不足时明确限制，不伪造完整回测。

# 允许修改

- src/btc_option_scanner/analytics/backtest.py
- src/btc_option_scanner/analytics/replay.py
- tests/analytics/test_backtest.py
- tests/analytics/test_replay.py
- docs/BACKTEST.md
- docs/change_proposals/P08_BACKTEST.md

# 禁止修改

- 其他 src 模块
- 共享契约、配置和已有 MODEL.md
- CLI/Dashboard

# 必须实现

- event-time replay；
- observed_at 可见性和 instrument catalog 时间边界；
- rolling high 只能使用历史窗口；
- executable Bid/Ask 与 mark-only 模式分开；
- 策略 A–E 的协议/骨架；
- turnover、spread cost、fees、roll count 等归因；
- BTC/USD CAGR、drawdown、Sortino 等结果接口；
- 数据 coverage 和可信度元数据；
- metric/config/schema version 固定；
- 不允许查询当前 catalog 补历史状态。

# 验证

- 人工插入未来 instrument、future high、future IV 后，历史时点无法读取；
- mark-only 结果不会标记为 executable；
- 相同事件序列产生相同结果；
- 空历史、稀疏历史、跨日/跨到期边界；
- 缺 Bid/Ask 的策略行为符合失败关闭规则。

# 最终交接

说明回放时钟、可见性规则、策略协议、已实现指标、数据限制和测试证据。不得给出策略盈利结论。
```

---

## P09 — CLI 与 Streamlit Dashboard

**模型：GPT-5.6 Terra / medium**

```text
你是本项目的只读展示层负责人。你负责 CLI、Streamlit Dashboard、Plotly 图表和 application service 适配，不重新实现业务计算。

# 工作区与资料

工作区：D:\Softerware\AHack\OPT

阅读：
- DEVELOPMENT_BLUEPRINT.md
- docs/DOMAIN_CONTRACT.md
- config.py
- data、scanner、portfolio、strategy、analytics 的公开接口和交接说明

# 前置条件

P05–P07 的 application outputs 稳定。Backtest 页面可在 P08 完成后补接。若只能通过直接查询内部 ORM 并自行重算指标才能实现页面，停止并提交 change proposal。

# 目标

实现一致、只读、可解释的 CLI 和 Dashboard。CLI 与 Dashboard 对同一 snapshot 必须展示相同业务结果。

# 允许修改

- src/btc_option_scanner/cli.py
- src/btc_option_scanner/application/**
- src/btc_option_scanner/dashboard/**
- tests/application/**
- tests/presentation/**
- docs/change_proposals/P09_PRESENTATION.md

# 禁止修改

- domain、deribit、data、models、scanner、portfolio、strategy、analytics 的已有实现
- 共享契约与配置

# 必须实现

- scan、inspect、roll、portfolio、stress CLI；
- Market Overview、Option Scanner、IV Surface、Portfolio、Roll、Stress、Historical Recommendations 页面；
- 需求蓝图列出的核心图表；
- snapshot time、data quality、price basis、model/config version 的清晰展示；
- reasons、risks、rejected reasons；
- DATA_STALE、NO_EXECUTABLE_BID/ASK、INSUFFICIENT_HISTORY 的显式状态；
- 不出现 BUY/SELL 文案；
- 无数据时给出可理解的空状态。

# 禁止事项

- 页面不直接计算 DCE、Utility、roll cash flow 或 NAV；
- 不调用交易接口；
- 不用 mark 填补 Bid/Ask；
- 不在 UI 中硬编码策略阈值。

# 验证

- CLI smoke tests；
- application DTO tests；
- CLI/Dashboard 同 snapshot 一致性；
- Streamlit 能启动；
- 关键页面在无数据、stale、正常数据下均可渲染。

# 最终交接

说明命令、页面、application service、启动方式、测试结果和仍缺少的上游接口。
```

---

## P10 — 集成测试、运行手册与文档一致性

**模型：GPT-5.6 Luna / medium**

```text
你是本项目的集成测试与文档整理负责人。你的任务范围机械、明确：补充端到端测试、运行说明和文档一致性检查。不要修改核心业务实现来“顺手修复”问题。

# 工作区与资料

工作区：D:\Softerware\AHack\OPT

阅读：
- DEVELOPMENT_BLUEPRINT.md
- docs/DOMAIN_CONTRACT.md
- docs/DECISIONS.md
- 各模块最终交接和已有测试

# 前置条件

P05–P09 核心工作已完成。缺失模块时列为 blocker，不自行实现该模块。

# 目标

形成可重复的验收证据和运行手册，发现跨模块不一致并报告给 P11。

# 允许修改

- tests/integration/**
- tests/e2e/**
- docs/ARCHITECTURE.md
- docs/DERIBIT.md
- docs/ROADMAP.md
- docs/RUNBOOK.md
- README.md
- docs/INTEGRATION_FINDINGS.md
- 测试专用 fixtures

# 禁止修改

- src/**
- config.yaml、pyproject.toml
- DOMAIN_CONTRACT、DECISIONS、MODEL、BACKTEST 等权威文档
- 真实数据库和用户数据

# 必须完成

- 检查 README 命令是否真实存在；
- 端到端固定 snapshot：ingest/replay → metrics → scan → portfolio → roll → stress；
- stale、空 Bid/Ask、无历史数据路径；
- CLI 输出与 application result 一致；
- 文档中的字段、命令和目录与代码一致；
- 检查 src 中不存在 private/buy、private/sell 或交易执行入口；
- 把失败精确写入 INTEGRATION_FINDINGS.md，包含复现命令和相关文件。

# 停止规则

测试失败时不要弱化断言来让它通过。记录预期、实际、复现步骤和建议责任模块。

# 最终交接

说明新增测试/文档、通过与失败数量、可复现问题、P11 必须修复的问题、当前能否进行 live public smoke 和短时 soak。
```

---

## P11 — 最终集成审查与有限修复

**模型：GPT-5.6 Sol / high**

```text
你是本项目最终集成负责人。你负责审查整套系统是否达到规划验收标准，并只修复阻碍验收的明确问题。不要进行与验收无关的重构。

# 工作区与权威资料

工作区：D:\Softerware\AHack\OPT

必须阅读：
- DEVELOPMENT_BLUEPRINT.md
- docs/DOMAIN_CONTRACT.md
- docs/DECISIONS.md
- docs/INTEGRATION_FINDINGS.md
- 各模块公开接口和测试

按需阅读其他文件，不需要重新吞入原始长提示词。

# 前置条件

P10 已完成并留下集成结果。若大部分核心模块不存在，停止并报告，不从头实现整个项目。

# 目标

验证跨模块契约、量纲、失败关闭、数据可追溯性和端到端运行；修复范围明确且风险可控的 blocker；形成最终验收报告。

# 修改权限

可以修改任何项目文件，但必须遵守：
- 只修改与明确验收失败直接相关的最小范围；
- 不覆盖用户或其他模型的无关改动；
- 不改变 domain-contract-v1，除非建立新版本、迁移说明和对应测试；
- 不引入交易能力；
- 不为了通过测试而删除安全 gate 或弱化关键断言。

# 必须审查

- Ask entry、Bid exit、Mark theory 是否贯穿一致；
- BTC/USD、IV、Greeks、quantity 单位；
- ticker/book 边界和 sequence 规则；
- snapshot 时间一致性和 stale fail-closed；
- raw → normalized → metric → recommendation 追溯；
- Scanner 解释与 peer 数据；
- Portfolio/Roll 现金流守恒；
- Backtest 无未来函数；
- CLI/Dashboard 不重算业务逻辑；
- 源码无 private/trading API；
- 配置、数据库、测试、README 和运行命令一致。

# 验证顺序

1. 静态检查与全量 pytest；
2. 固定 fixtures 的端到端流程；
3. Deribit public API live smoke；
4. 短时 collector soak；
5. CLI scan/inspect/roll/portfolio/stress；
6. Streamlit 启动检查；
7. 保存运行证据和已知限制。

如果外部网络或 Deribit 服务不可用，将 live 项目标为 EXTERNAL_BLOCKED；不要把它伪装成通过，也不要阻止离线验收。

# 最终交付

创建 docs/ACCEPTANCE_REPORT.md，并在最终回答中给出：
- 已通过的 Gate；
- 修复文件和原因；
- 测试与运行证据；
- 尚未通过的项目；
- 数据历史不足造成的研究限制；
- 系统当前是否只读且无真实交易能力；
- 下一阶段最小建议。
```

## 3. 运行管理建议

- 为每个对话使用提示词编号作为任务标题，例如 `P05 Deribit 数据脊柱`。
- 并行前记录当前 commit；每个模型只提交自己拥有的文件。
- 每完成一个提示词，把它的最终交接保存到 `docs/handoffs/Pxx.md`，或直接交给下一对话阅读。
- P03 发布契约前不要启动源码实现。
- P11 之前不要让 Sol 长时间参与普通开发，以维持成本预算。
- 某个 Terra 对话失败时，先用另一个 Terra high 做针对性诊断；只有涉及共享契约或关键量纲才升级到 Sol。
