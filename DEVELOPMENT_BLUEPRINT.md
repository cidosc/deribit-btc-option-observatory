# Deribit BTC 动态期权扫描与滚仓决策系统：开发蓝图

> 状态：规划基线 v0.1
>
> 本文件是对原始需求文档的工程化重构。原始文档是需求来源，不是直接执行脚本；本蓝图不实现代码，也不授权真实交易。

## 1. 项目定义

### 1.1 一句话目标

建立一套只使用公开市场数据、以 BTC 和 USD 双重计量、基于可执行 Bid/Ask 报价的研究型决策支持系统，用于长期积累 Deribit BTC 币本位 Call 数据，比较中长期 ITM Call 的资本效率、可选性与滚仓替代方案。

### 1.2 系统要回答什么

系统不预测 BTC 方向，主要形成回答以下研究问题所需的证据：

1. 哪些 Delta/DTE 区域能以合理 BTC 权利金获得稳定 Delta 敞口？
2. 一个 Call 何时因 Delta 接近 1、Gamma/Vega 衰减和内在价值占比升高而出现“资本老化”？
3. Roll Up 是否能在维持足够 Delta 的同时释放 BTC reserve？
4. 新合约的 IV、Vega 与交易摩擦是否抵消了滚仓改善？
5. 深度回撤时，现货、Buy deeper ITM Call 与 Roll Down 的风险收益如何比较？

第一版只建立可重复计算和验证这些问题的数据与分析基础，不声称已经回答这些问题。

### 1.3 明确边界

第一阶段包含：

- Deribit BTC 币本位 Call 的公开数据采集；
- 规范化、质量检查、降采样和历史留存；
- 规则筛选、相对排名和可解释候选输出；
- 假想组合、滚仓比较和压力测试；
- SQLite、CLI、Streamlit 与回测接口；
- 模型版本、指标版本和推荐快照留痕。

第一阶段排除：

- 任何 Mainnet 或 Testnet 下单；
- 私有 API、API Key、账户读取；
- 裸卖期权、借贷、自动杠杆和 Portfolio Margin；
- 机器学习方向预测；
- 用历史最优参数宣称策略有效；
- LLM 参与运行时交易决策。

### 1.4 只读部署扩展（2026-09-15）

长期采集部署允许通过环境变量提供可选 Deribit OAuth 凭据，仅用于认证公开行情连接、
获得较透明的限流和按需使用 `raw` book。该扩展不改变 `domain-contract-v1`，不引入订单、
转账、提现或任何私有写接口。程序必须请求最小权限、检查服务端返回的实际 scope，并对
`:read_write` 或未知权限失败关闭。初始“排除 API Key”约束在此窄范围内被本节取代；真实
交易禁令不变。

## 2. 需求分层

原始文档中的内容应按以下层级管理，不能全部当作同等强度的“必须实现”。

### 2.1 不可违反的系统约束

- 只读公开数据，禁止交易端点；
- 新买候选使用真实 Ask，旧仓退出使用真实 Bid；
- 无 Ask 不生成可执行的新买评价，无 Bid 不生成可执行的滚出评价；
- 数据过期时停止生成 `ATTRACTIVE` 或 `ROLL_CANDIDATE` 等行动性状态；
- BTC、USD、合约数量、波动率和时间单位不可混用；
- 所有内部时间使用 UTC；
- `exchange_timestamp`、`observed_at`、`ingested_at` 分开保存；
- 保存原始响应，派生指标可按新模型重算；
- 推荐必须可解释，原始指标不可被总分隐藏；
- 所有规则和模型必须有版本号；
- 回测只能使用当时已经可见的数据。

### 2.2 第一版产品能力

- instrument catalog 与生命周期同步；
- ticker 市场数据与数据质量标志；
- SQLite 持久化；
- DCE、内在/外在价值、价差、流动性与多种 GCE 候选指标；
- Rule-based Scanner；
- IV 历史、分桶与 regime 接口；
- 假想组合、两种滚仓数量模式；
- 场景压力测试；
- CLI 与只读 Dashboard；
- Backtest 接口和无未来函数测试。

### 2.3 研究假设

以下内容必须作为可检验假设，不得固化为事实：

- DCE 能代表有用的 BTC 资本效率；
- Delta 接近 1、ICR 上升、Gamma/Vega 下降意味着资本老化；
- Roll Up 释放 reserve 能改善周期路径；
- 高 IV regime 应提高购买 Vega 的惩罚；
- 0.80–0.90 Delta、120–240 DTE 是优选区域；
- 某一种 Gamma normalization 比其他定义更有效；
- Utility score 能稳定优于规则筛选。

### 2.4 实验默认值

Delta/DTE、价差、OI、成交量、最小盘口量、滚仓间隔、IV 窗口、staleness 等阈值均为实验默认值。它们必须：

1. 集中配置；
2. 带来源和理由；
3. 在输出中标明配置版本；
4. 能通过回放或回测替换；
5. 不被测试写成永恒正确的业务常量。

## 3. 先冻结的全局语义

多模型开发能否成功，取决于所有模块是否共享同一套单位与估值语义。以下契约应在业务代码前冻结。

### 3.1 四类价格

| 名称 | 用途 | 禁止替代 |
|---|---|---|
| `ask_btc` | 新仓可执行成本、候选 DCE | 不得由 mark 或 mid 代替 |
| `bid_btc` | 旧仓可变现价值、滚仓收入 | 不得由 mark 或 mid 代替 |
| `mark_btc` | 理论监控、模型比较、mark NAV | 不得作为真实成交假设 |
| `mid_btc` | 价差和市场状态描述 | 无双边报价时必须为空 |

对应派生字段必须显式命名，例如：

- `dce_entry_ask`；
- `dce_liquidation_bid`；
- `dce_mark`；
- `extrinsic_entry_btc`；
- `extrinsic_liquidation_btc`；
- `extrinsic_mark_btc`。

原始需求中的单一 `premium_BTC`、`DCE_current` 和 `extrinsic_value_BTC` 语义不足，不能直接进入共享数据模型。

### 3.2 三种 NAV

- `nav_btc_mark`：现金加按 mark 估值的期权；
- `nav_btc_liquidation`：现金加按可执行 Bid 估值的多头期权；
- `nav_usd_*`：相应 BTC NAV 乘以指定的、带时间戳的 index price。

任何页面或报告展示 NAV 时必须附估值基准。组合比较默认同时输出 mark 与 liquidation，不能把两者混成一个“当前价值”。

### 3.3 标的价格语义

- `index_price_usd`：指数参考价；
- `underlying_price_usd`：Deribit 用于该期权 IV/Greeks 的 underlying/forward；
- `underlying_index_name`：对应期货或 `index_price`；
- `delivery_price_usd`：只用于到期结算或历史到期分析；
- `manual_cycle_high_usd`：用户输入的研究状态，不得回填历史。

模型校验优先使用 Deribit 为合约返回的 `underlying_price`，当前内在价值则必须在字段名中注明使用 index 还是 forward。

### 3.4 波动率与 Greeks

- API 原始 IV 原样保存；
- 规范层统一成 decimal，显示层再转为百分比；
- Greeks 原值原样保存，并在 Gate 0 通过真实响应和公式校验确定各项单位；
- `theta` 必须记录时间尺度；
- `vega` 必须记录是每 1.00 波动率变化还是每 1 vol point；
- `gamma` 必须记录标的价格变化的单位；
- Deribit Greek 与本地模型 Greek 分字段保存，不覆盖。

在单位验证完成前，不允许把 Theta、Vega、Gamma 直接合成 Utility。

### 3.5 时间和数据质量

每条规范化行情至少带：

- `exchange_timestamp`：交易所消息时间；
- `observed_at`：客户端收到消息时间；
- `ingested_at`：成功写入存储时间；
- `source_transport`：REST 或 WebSocket；
- `source_channel`；
- `schema_version`；
- `quality_flags`。

建议状态在计算时绑定一个不可变 `market_snapshot_id`，防止前端把不同时间的旧仓和新候选拼在一起。

## 4. 数据采集架构

### 4.1 三个 universe

不要把“交易所全部合约”“需要保存的合约”和“当前候选”混成一个集合。

1. `catalog_universe`：所有活动 BTC inverse option instrument 元数据；
2. `collection_universe`：策略目标区间外带缓冲的 BTC Call，加上当前假想持仓和研究指定合约；
3. `decision_universe`：经过 DTE、Delta、流动性、数据质量和报价可执行性筛选的候选。

`collection_universe` 应宽于 `decision_universe`，否则无法观察合约跨越阈值和形成稳定 peer 分布。

### 4.2 初始化与生命周期

数据面建议顺序：

1. REST `public/get_instruments` 建立全量 catalog；
2. WebSocket `instrument.state.option.BTC` 接收上市和状态变化；
3. 新合约先更新 catalog，再依据 collection policy 决定是否订阅；
4. `delivered`、`inactive`、`archivized` 等状态触发退订或终态保存；
5. 低频 REST reconciliation 修复长时间断线期间可能遗漏的生命周期事件。

### 4.3 主行情通道

第一版主数据通道使用 `ticker.{instrument_name}.agg2`，必要时才为少量活跃或持仓合约提升到 `100ms`。理由：ticker 已覆盖第一阶段需要的 top-of-book、报价量、IV、Greeks、OI、24h 统计、mark、index 和 underlying。

`book.{instrument_name}.agg2` 作为后续可选能力，用于多档深度、盘口冲击和 order-book sequence validation。只有 book 消息才应用 `change_id/prev_change_id` 连续性规则。

### 4.4 启动、实时与落库频率分离

- 启动 bootstrap：批量摘要可用于快速发现报价覆盖，但不能替代包含完整 IV/Greeks 的 ticker；
- 内存状态：按 WebSocket 实时更新；
- 常规快照：按 1/5/15 分钟配置降采样；
- 事件快照：阈值穿越、报价从无到有、staleness、IV 突变、新合约和状态变化；
- 原始归档：按消息或时间分片压缩，不能只依赖 SQLite 的大文本列无限增长。

### 4.5 失败关闭原则

任何一个条件成立时，决策层只输出数据状态，不输出行动性评价：

- 连接已断开或 heartbeat 超时；
- 行情超过 staleness 阈值；
- instrument 非 `open`；
- 关键时间戳逆序或超出容许漂移；
- 新候选无 Ask；
- 当前旧仓无 Bid；
- 旧仓和新仓数据不属于同一时间容差窗口；
- 单位或 schema 版本未知。

## 5. 逻辑分层

```text
External Deribit Public API
        |
        v
Transport -> Raw Events -> Normalization -> Quality Gate -> Snapshot Store
                                      |                         |
                                      v                         v
                              Canonical Market State       Historical Series
                                      |                         |
                                      v                         v
                              Metric Engine <------------ IV Regime
                                      |
                           +----------+-----------+
                           |                      |
                           v                      v
                    Scanner / Peers        Portfolio Valuation
                           |                      |
                           +----------+-----------+
                                      v
                                Roll Engine
                                      |
                         +------------+-------------+
                         v                          v
                    Stress Engine             Explanation Engine
                         |                          |
                         +------------+-------------+
                                      v
                                CLI / Dashboard
```

### 5.1 分层职责

1. **Transport**：连接、订阅、重连、心跳、限流；不做策略计算。
2. **Raw Event**：原样留存与可回放；不修正交易所值。
3. **Normalization**：字段映射、单位转换、缺失值语义；不做候选判断。
4. **Quality Gate**：新鲜度、时序、报价和 schema 检查；输出 flags。
5. **Metric Engine**：纯函数派生指标和模型校验；不访问网络。
6. **Scanner**：eligibility、peer comparison、ranking；不读取 UI 状态。
7. **Portfolio**：持仓与 reserve 的双币种估值；不生成交易指令。
8. **Roll Engine**：同张数/同 Delta 两种交换情景、摩擦和滞后；不下单。
9. **Stress/Backtest**：情景重估与历史回放；明确假设和可见数据边界。
10. **Presentation**：展示 application service 的稳定 DTO；不得直接拼接数据库字段做业务逻辑。

## 6. 指标与决策框架

### 6.1 基础派生指标

第一批只实现有清晰单位的指标：

- DTE；
- moneyness 与 log-moneyness；
- bid/ask/mid/mark；
- absolute spread 与 `spread / mid`；
- index-based intrinsic BTC；
- ask/bid/mark 三套 extrinsic BTC；
- ICR 与 Optionality Ratio 的 ask/bid/mark 版本；
- DCE ask/bid/mark 版本；
- 多个并列 GCE 候选，不提前选择赢家；
- Theta、Vega 相对 premium 的候选归一化指标；
- Deribit 与本地模型差异和异常 flags。

### 6.2 Peer cohort

Peer 是一个有版本的动态集合，而不是模糊的“类似期权”。每次 scanner run 保存：

- universe filter 版本；
- DTE/Delta bucket 边界；
- cohort size；
- 每个候选的 cohort membership；
- percentile 的方向定义；
- 样本不足标志。

第一版可使用宽区间 cohort；积累数据后再比较固定 bucket、滚动邻域和 k-nearest cohort。

### 6.3 Ranking 的两阶段路线

第一阶段采用可解释的规则路径：

1. 数据质量 gate；
2. 可执行报价 gate；
3. DTE/Delta eligibility；
4. 流动性约束；
5. peer-relative 指标；
6. 按一个明确主键排序，并用次级指标破同分；
7. 输出 reasons、risks 与被淘汰原因。

Utility 只能在指标单位、归一化和稳定性被验证后启用。即使启用 Utility，也必须保留规则视图和原始向量。

### 6.4 Utility 的单位问题

原始需求中的 `U_new - U_old > transaction cost + spread + fee` 存在量纲冲突：标准化 Utility 通常无量纲，交易成本以 BTC 计。

第一版应拆成独立 gate：

- `utility_improvement >= configured_standardized_threshold`；
- `roll_cost_btc <= configured_budget`；
- `spread_cost_btc <= configured_limit`；
- `delta_after`、`reserve_after` 和风险指标满足约束；
- 已超过 minimum roll interval。

只有未来定义了 Utility 的 BTC 等价价值后，才能把这些项放进同一个不等式。

### 6.5 IV regime

IV 历史必须按 `delta_bucket × dte_bucket × metric_side` 比较；ask IV、mark IV 和 bid IV 不可混用。每个窗口输出：

- observation count；
- coverage ratio；
- warm-up 状态；
- percentile；
- rank；
- z-score；
- 使用的均值/标准差定义。

不足 30/90/180/365 天时返回 `INSUFFICIENT_HISTORY`，不得伪造百分位。高 IV 也不自动意味着“不买”，它只进入解释和策略函数。

## 7. Roll Engine 作为状态机

### 7.1 输入

- 当前假想持仓及 entry price；
- 同一市场时间窗口内的旧仓 Bid 与候选 Ask；
- portfolio reserve；
- fee schedule 与 tick/min amount 元数据；
- 策略、指标和配置版本；
- 上次滚仓时间；
- 当前 IV regime 和数据质量状态。

### 7.2 处理顺序

1. 验证旧仓可退出和候选可进入；
2. 识别 time-aging、price-aging 或两者都没有；
3. 生成符合 target band 的候选；
4. 分别计算 equal-contracts 和 equal-portfolio-delta；
5. 对数量执行 min trade amount 与 tick/rounding 处理；
6. 计算 proceeds、required premium、fees、net BTC release/requirement；
7. 比较 Delta/Gamma/Theta/Vega、DCE、ICR 和 reserve；
8. 应用经济 gate、改进 gate 和 hysteresis；
9. 生成状态、reasons、risks 和 rejected reasons；
10. 保存完整 roll frontier，而不是只保存 Top 5。

### 7.3 两种数量模式

- **Equal contracts**：新旧数量相同，突出单合约资本释放与 Greeks 变化；
- **Equal portfolio Delta**：根据 Delta 比例求新数量，再按交易规格取整，突出维持方向敞口的真实资本需求。

必须同时输出取整前理论结果和取整后可执行结果。

### 7.4 输出状态

- `HOLD`；
- `ROLL_CANDIDATE`；
- `STRONG_ROLL_CANDIDATE`；
- `WAIT_FOR_LIQUIDITY`；
- `WAIT_FOR_IV_NORMALIZATION`；
- `DATA_STALE`；
- `NO_EXECUTABLE_BID`；
- `NO_EXECUTABLE_ASK`；
- `INSUFFICIENT_HISTORY`。

状态必须由明确规则产生，并带规则版本。

## 8. Portfolio、Stress 与 Backtest

### 8.1 Portfolio 不变量

- reserve BTC 与 long option value 分开；
- contract quantity、contract multiplier 和 Delta 的乘法只在一个共享函数中实现；
- premium paid 是历史成本，不等于当前 NAV；
- roll 后 BTC 守恒应能由 proceeds、new premium、fees 和 released reserve 对账；
- NAV_BTC 与 NAV_USD 同时输出；
- mark NAV 与 liquidation NAV 分开。

### 8.2 Stress 的定义

价格、IV 和时间三维场景是“条件重估”，不是预测。每个结果必须记录：

- spot/index shock；
- forward 构造方式；
- IV shock 是绝对 vol points 还是相对比例；
- 利率假设；
- elapsed time；
- 使用的 pricing model version；
- 是否对 smile/skew 做平移、扭曲或固定；
- 到期和穿越到期的处理方式。

第一版优先采用透明、简单的 shock 规则，并把 surface dynamics 留作可替换策略。

### 8.3 Backtest 的现实边界

公开历史接口无法保证重建所有合约的完整历史 Bid/Ask、Greeks 与 IV surface。因此：

- 自建采集数据是主要可信来源；
- 第一天不能声称完成长期动态策略回测；
- mark-only 历史研究必须和 executable backtest 分开；
- 历史推荐必须使用当时 instrument catalog；
- rolling high 必须由当时历史窗口计算；
- 任何合约上市信息只能从其 `observed_at` 起可见；
- 所有策略运行按 event-time 回放，不查询“当前表”的未来状态。

## 9. 数据模型边界

逻辑实体建议保持以下边界：

| 实体 | 职责 |
|---|---|
| `Instrument` | 交易所静态/慢变规格与生命周期 |
| `RawMarketEvent` | 原始可回放消息 |
| `MarketSnapshot` | 规范化市场状态和质量标志 |
| `MetricSnapshot` | 与 market snapshot、metric version 绑定的派生指标 |
| `IVObservation` | 分桶所需的 IV 历史观测 |
| `ScannerRun` | 一次扫描的输入版本、时间和状态 |
| `CandidateEvaluation` | 每个候选的 eligibility、percentile、解释与排名 |
| `PortfolioDefinition` | 假想组合定义 |
| `PortfolioSnapshot` | 指定行情下的 mark/liquidation 估值与 Greeks |
| `RollEvaluation` | 旧仓、新仓、数量模式与真实交换经济性 |
| `StressRun` | 场景假设和结果矩阵 |
| `SystemEvent` | 连接、stale、schema、模型与异常事件 |

所有派生表必须能追溯到原始行情、计算版本和配置版本。

## 10. 开发依赖图

```text
G0 规格与单位验证
 |
 v
G1 领域契约、配置契约、固定 fixtures
 |
 +-------------------+
 |                   |
 v                   v
A 数据采集/重连      B 存储/迁移/回放
 |                   |
 +---------+---------+
           v
C 规范化与质量 Gate
           |
     +-----+------+
     |            |
     v            v
D 指标/Black-76   E 运维/日志/soak harness
     |
     v
F Rule Scanner
     |
 +---+-------------+
 |                 |
 v                 v
G IV History       H Portfolio
 |                 |
 +--------+--------+
          v
I Roll Engine
          |
          v
J Stress Engine
          |
     +----+----+
     |         |
     v         v
K CLI      L Dashboard
     \         /
      +---+---+
          v
M Backtest scaffolding + end-to-end acceptance
```

G0、G1 和 C 是并行开发前的硬门槛。D 与 E 可并行，G 与 H 可并行，K 与 L 可并行。Roll Engine 不能在 Portfolio 与执行价格契约未稳定前开始。

## 11. 多模型分工

模型应按任务风险和歧义分配，而不是让每个模型“自由实现一个模块”。本项目采用成本优先方案：`GPT-5.6 Terra` 是默认主力，`GPT-5.6 Luna` 处理契约冻结后的机械任务，`GPT-5.6 Sol` 只在少数高风险检查点使用。不使用 GPT-6。

各角色可直接复制使用的独立对话工作包见 `MODEL_PROMPT_PACK.md`。

| 角色 | 建议模型/推理强度 | 主要职责 | 不应负责 |
|---|---|---|---|
| 总架构与集成负责人 | GPT-5.6 Sol，high，按检查点调用 | 冻结契约、解决跨层冲突、审核 Gate 0/Gate 5、最终验收 | 持续参与普通编码和重复样板 |
| 日常开发主力 | GPT-5.6 Terra，medium | REST、WebSocket、ORM、指标流水线、Scanner、CLI、Dashboard | 未经评审修改共享契约 |
| 复杂模块实现 | GPT-5.6 Terra，high | 断线恢复、事件回放、Portfolio、Stress、Backtest | 独自决定 Greek 单位和核心交易数学 |
| 关键量化设计/复核 | GPT-5.6 Sol，high，短任务 | inverse 定价换算、Greek 单位、Roll 现金流、无未来函数审查 | 编写整套模块或批量测试 |
| 批量与机械任务 | GPT-5.6 Luna，low/medium | fixtures、字段映射、CRUD、文档表格、重复测试与格式清理 | 关键数学、跨模块重构、交易安全边界 |
| 日常独立审查 | GPT-5.6 Terra，high，独立上下文 | 边界条件、失败路径、接口一致性和性质测试 | 与实现者共享未经说明的假设 |

角色比模型名称更重要；模型不可用时，保持角色、输入和验收标准不变，再映射到当前可用模型。

### 11.1 成本控制规则

1. 默认工作包使用 Terra medium；只有工作包列出明确复杂点时才升到 Terra high。
2. Sol 只用于四类任务：Gate 0 数学/单位冻结、共享契约争议、Roll/Portfolio 现金流复核、最终集成验收。
3. Sol 不读取整个仓库做常规巡检；输入应压缩为契约、相关文件、失败证据和待裁决问题。
4. Luna 只能接收输入输出明确、可由测试机械验收的工作包。
5. 同一模块不让两个高成本模型重复实现；采用“一次实现 + 一次针对性审查”。
6. reasoning 默认 medium；high 只给复杂状态机、量化边界和失败诊断；xhigh/max 仅在 high 已暴露无法解决的矛盾时临时启用。
7. 每个模型只加载当前工作包所需文件，避免重复携带完整需求和长历史。
8. 每个 Gate 先由 Terra 汇总证据，只有仍存在高影响歧义时才升级给 Sol。

### 11.2 文件所有权原则

并行模型必须拥有互斥文件集合：

- 数据模型负责人：`deribit/`、ingestion tests；
- 存储负责人：`data/`、migration/replay tests；
- 量化负责人：`models/`、metric/model tests；
- 扫描与策略负责人：`scanner/`、`strategy/`、对应 tests；
- 组合负责人：`portfolio/`、stress tests；
- 展示负责人：`cli.py`、`dashboard/`、presentation tests；
- 总架构负责人：共享 domain contracts、配置 schema、集成 tests 和 docs。

共享文件只由总架构负责人修改。其他模型通过变更提案请求契约调整，避免并发覆盖。

### 11.3 每个工作包必须包含

1. 单一目标；
2. 前置版本和输入 fixtures；
3. 允许修改的文件；
4. 禁止修改的文件；
5. 明确接口；
6. 必须处理的失败路径；
7. 单元/性质/集成验收；
8. 运行命令；
9. 输出与证据格式；
10. 未解决问题清单。

### 11.4 标准交接格式

每个模型完成工作时只交接以下内容：

- 完成了什么；
- 修改了哪些文件；
- 接口是否与约定一致；
- 运行了哪些验证及结果；
- 仍存在的假设/风险；
- 后续模块可依赖什么；
- 是否建议变更共享契约以及理由。

没有测试证据和接口说明的工作包不得进入集成。

## 12. 分阶段工作包与验收门

### Gate 0：事实与单位验证

目标：用官方文档和真实公开响应冻结 API 字段、合约规格与 Greek 单位。

交付：

- `DERIBIT_FIELD_CONTRACT.md`；
- 一组脱敏的真实 JSON fixtures；
- 字段 presence/nullability/unit 表；
- ticker 与 book 的选型记录；
- Black-76/Deribit Greek 差异预期；
- API 文档和真实响应不一致清单。

退出条件：所有第一版关键字段都有来源、空值语义和规范单位。

### Gate 1：领域与配置契约

目标：建立稳定的 domain DTO、枚举、quality flags、metric/config versions。

退出条件：数据、量化、存储三个模型能只靠 fixtures 独立开发，并对同一输入产生一致规范值。

### Gate 2：可靠数据脊柱

目标：REST catalog、lifecycle、ticker、重连、stale detection、SQLite、raw replay。

验证：

- unit tests 使用固定 fixtures；
- integration tests 使用录制消息；
- live smoke test 只调用 public API；
- 断网、乱序、空报价和 schema drift 故障注入；
- 短时 soak test 无静默停止和明显重复落库。

### Gate 3：指标与规则扫描

目标：无歧义指标、peer cohort、rule ranking、解释和拒绝原因。

验证：

- Ask 上升时 `dce_entry_ask` 不上升；
- 深 ITM 极限下 intrinsic/optionality 行为合理；
- 无 Bid/Ask、负或交叉价差均被 flag；
- candidate order 可由保存的输入完全复现；
- score 关闭时系统仍可工作。

### Gate 4：历史 IV

目标：分桶历史、coverage、warm-up、percentile/rank/z-score。

验证：样本不足、零标准差、bucket 边界、窗口截断与缺失观测。

### Gate 5：Portfolio 与 Roll

目标：双 NAV、Greeks 聚合、两种滚仓数量模式、reserve 与 hysteresis。

验证：

- BTC cash-flow 对账；
- equal-delta 取整前后差异；
- 无旧 Bid 时禁止 roll；
- 无新 Ask 时候选失效；
- 改进不足、间隔不足时保持 HOLD；
- 所有推荐均可追溯到同一市场时间窗口。

### Gate 6：Stress 与展示

目标：透明情景假设、CLI 全功能、Dashboard 只读展示。

验证：

- 场景轴和模型版本完整；
- 临近/穿越到期处理；
- CLI 与 Dashboard 对同一 snapshot 显示一致；
- 页面不自行重算核心业务逻辑。

### Gate 7：回测接口与运行验收

目标：event-time replay、无未来函数、基准策略接口、端到端证据包。

验证：

- 人为插入未来合约和未来 high，策略无法提前读取；
- transaction costs 只使用当时可见 Bid/Ask；
- mark-only 与 executable 结果分开；
- pytest、live public smoke、collector soak、scanner、portfolio、roll、stress、CLI 与 Dashboard 均有运行记录。

## 13. 测试策略

### 13.1 测试金字塔

- 纯数学单元测试：快、确定、不访问网络；
- 性质测试：单调性、边界、守恒和单位；
- parser contract tests：录制的 Deribit JSON；
- repository tests：临时 SQLite 与迁移；
- replay integration tests：断线、重复、乱序、状态切换；
- public API smoke tests：默认不在普通 pytest 中强制执行；
- soak tests：独立运行并生成统计报告；
- end-to-end acceptance：固定市场 snapshot，保证结果可复现。

### 13.2 关键性质

- `ask_btc > 0` 时，固定 Delta 下 Ask 越高，DCE 越低；
- bid 不得高于 ask，出现时应 flag 而非静默修正；
- Call 的 BTC intrinsic 在 index 上升时不下降，并趋近 1；
- 时间不得倒流，DTE 不得在同一合约连续快照中无原因增加；
- 合约 roll 前后现金流可对账；
- 同一原始消息重放不得造成非预期重复业务事件；
- 相同 snapshot/config/model version 必须产生相同 scanner 结果。

## 14. 需要在 Gate 0 解决的开放问题

1. Deribit 各 Greek 的精确单位与符号，特别是 Theta、Vega、Gamma；
2. 本地 Black-76 输出应先以 USD 计价再换算 BTC，还是直接使用等价币本位表达；
3. 当前 intrinsic 采用 index 还是 underlying/forward，是否同时保存两套；
4. API 的 IV 是百分数还是 decimal，所有通道是否一致；
5. `stats.volume`、OI、best amount 对 BTC option 的精确数量单位；
6. ticker 全量订阅的连接/频道上限与分片策略；
7. SQLite raw retention、压缩归档与清理策略；
8. 可接受 staleness、snapshot 同步时间窗和 reconnect backoff；
9. fee 对 option premium 的封顶规则及其模拟口径；
10. equal-delta 模式的数量取整与最小交易量；
11. peer cohort 的最小样本量和 bucket 边界；
12. IV Rank 的正式定义，避免与 percentile 重复或混淆；
13. Utility normalization 的训练窗口和跨时稳定性；
14. 压力测试中的 smile dynamics；
15. `manual cycle high` 如何版本化且不污染回测。

## 15. 风险登记

| 风险 | 后果 | 控制措施 |
|---|---|---|
| 把 mark 当成交价 | 高估策略可执行性 | Ask/Bid 硬契约与性质测试 |
| Greek 单位误读 | 指标和组合风险全面错误 | Gate 0 单位表与双模型校验 |
| 只收 decision universe | 无法研究阈值跨越和 peer | collection universe 留缓冲 |
| 全链高频订阅 | 带宽、写入和重连压力 | agg2、分片、内存实时/落库降采样分离 |
| SQLite raw_json 膨胀 | 长期运行退化 | 分片压缩归档与 retention policy |
| Utility 量纲混合 | 神秘 score 和伪精确 | 先规则、后 Utility，成本单独 gate |
| 历史数据不足 | IV regime 不可信 | coverage/warm-up 明示，不回填伪数据 |
| 前端直接算业务逻辑 | CLI/Dashboard 结果不一致 | application service DTO |
| 多模型同时改共享契约 | 接口漂移、合并冲突 | 文件所有权与契约变更提案 |
| 测试只覆盖 happy path | 长期采集静默失败 | replay、故障注入、soak test |
| 回测读取当前 catalog | look-ahead bias | event-time catalog 和 observed_at |
| 扩展 execution 时误触 Mainnet | 不可接受外部副作用 | 第一版不创建 execution client；后续独立安全评审 |

## 16. 第一轮执行建议

下一轮只启动 Gate 0，不开始完整项目实现。建议按以下顺序：

1. Terra medium 建立字段清单、官方 API 对照表和真实公开 fixtures；
2. Terra high 独立推导 inverse payoff、Black-76 换算和 Greek 单位问题；
3. 仅把两者无法一致解决的高影响问题交给 Sol high 裁决；
4. Sol high 在短上下文中审核并发布 `domain-contract-v1`；
5. Terra medium/high 并行启动数据采集、存储和量化纯函数工作包；
6. Luna low/medium 根据已冻结契约补 fixtures、CRUD、文档和重复测试。

在 `domain-contract-v1` 发布前，任何模型都不应自由创建数据库字段、重新命名单位或实现 Utility score。

## 17. 官方事实基线

截至本蓝图编制时，官方资料支持以下初步判断，最终仍需在 Gate 0 以真实响应固化：

- Deribit inverse options 为欧式、现金结算、以基础币种计价；BTC option multiplier 为 1，允许 fractional contracts；
- `public/get_instruments` 适合初始化，生命周期订阅适合持续维护 catalog；
- `ticker.{instrument}.{interval}` 提供 top-of-book、IV、Greeks、OI 和统计，支持 `100ms` 与 `agg2`；
- order book sequence 的 `change_id/prev_change_id` 属于 book subscription；
- `public/get_mark_price_history` 只覆盖参与波动率指数计算的部分 option，不能作为全历史链替代品。

参考：

- https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options
- https://docs.deribit.com/api-reference/market-data/public-get_instruments
- https://docs.deribit.com/subscriptions/market-data/tickerinstrument_nameinterval
- https://docs.deribit.com/articles/notifications
- https://docs.deribit.com/api-reference/market-data/public-get_order_book
- https://docs.deribit.com/api-reference/market-data/public-get_mark_price_history
