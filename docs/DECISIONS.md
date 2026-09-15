# Domain Contract Decision Log

> **适用契约：** `domain-contract-v1.0.0`  
> 本日志记录 P03 的关键裁决。后续工作包如需改变裁决，必须提交 change proposal 并发布相应新版本。

## D-001 — 执行价与理论价分域

**背景：** 单一 premium 会让 mark/mid 被误用为成交价。  
**选择：** 新 long 只用有效 Ask；旧 long liquidation/roll 只用有效 Bid；mark 只用于理论监控/mark NAV；mid 只描述 spread。所有指标名带 side。  
**替代方案：** 无 quote 时回退 mark、mid 或 last；使用单一 `premium_btc`。  
**后果：** 数据稀疏时更多结果 unavailable，但不会伪造可执行性。此定义在 contract v1 内不可改变。

## D-002 — index intrinsic 为当前资本指标基准

**背景：** index intrinsic 与 forward discounted intrinsic 回答不同问题。  
**选择：** ICR、optionality、extrinsic 的默认基准固定为同快照 `intrinsic_index_*`；forward-PV intrinsic 仅作明确命名的定价参照；delivery 只用于到期。  
**替代方案：** 只保留一个 `intrinsic_value`；把 underlying/forward intrinsic 用于当前可结算价值。  
**后果：** 当前资本指标和模型下界可并列审计，不能互相替换。

## D-003 — 报告 USD 换算与 Deribit 定价回归分开

**背景：** 量化 draft 提议 local USD PV 除 index 转 BTC；fixture 的 Deribit mark/Greek 回归则以 `underlying_price` 为 BS input，USD model price 除 underlying 才接近 raw mark。  
**选择：** observed premium 的报告值始终为 `premium_btc * index`；另设可回滚 `deribit-bs-observed@1.0.0-diagnostic` 做交易所数值回归。v1 不发布 universal local pricing BTC conversion。  
**替代方案：** 把 index 或 forward/underlying 任一除数直接宣布为普适真理；忽略 fixture 冲突。  
**后果：** P04 可实现类型，P06 可实现报告换算和诊断 adapter；production local pricing 必须等多样本证据和新 metric version。该冲突不得被实现者“顺手修正”。

## D-004 — IV scaling

**背景：** fixture `mark_iv=98.3` 与 BS 回归只有解释成 98.3% 才吻合；`bid_iv=0.0` 同时出现在正 Bid 上。  
**选择：** raw IV 原样保留；正有限值除以 100 得 annual decimal；0/null/absent 不生成 canonical IV。bid/ask/mark IV 不互填。  
**替代方案：** 直接把 raw 当 decimal；把 0 当真实 zero-vol 或用 mark IV 填 bid IV。  
**后果：** 历史序列和 shock 使用统一 decimal；0 sentinel 以 availability/flag 表达。

## D-005 — Greek 三域与单位状态

**背景：** Deribit raw Greeks、本地同 basis model Greeks、BTC quote/NAV sensitivities不是同一概念。fixture 可精确辨别 delta、theta、vega、rho，但 gamma显示为 0。  
**选择：** 三域分字段。Deribit delta 的 standard 数值/单位可用，但 exact index-vs-forward derivative basis标为 provisional；theta为 USD/effective horizon；vega为 USD/vol point；rho为 USD/rate point。gamma target unit 定义为 delta per 1 USD underlying move，但 canonical normalized gamma 在多样本验证前为 null/UNVERIFIED。  
**替代方案：** 所有值都叫 `delta/gamma/...`；从裸 `0.0` 猜 gamma；先做跨 Greek score。  
**后果：** 可以安全实现 raw ingestion 和已验证单位；GCE、portfolio gamma金额和跨 Greek Utility 暂停。

## D-006 — 数量采用 base amount + contracts 双表示

**背景：** Deribit option best amount、OI、volume 以 base currency amount 表达；当前 contract size 为 1 BTC，数值上易被误当整数 contracts。  
**选择：** 保留 `amount_base_btc`，并通过 instrument `contract_size_btc` 导出 fractional `quantity_contracts`。v1 instrument scope 验证 contract size 必须为 1。  
**替代方案：** 只存模糊 `amount`；直接命名 USD notional；把数量强制为整数。  
**后果：** BTC 与 contract 单位显式，未来规格变化会 fail closed 而非静默错算。

## D-007 — price per contract 不重复乘 multiplier

**背景：** 两份草案在公式中同时出现 per-contract premium 和 contract multiplier，存在未来双乘风险。  
**选择：** canonical `*_btc_per_contract` 已是每 contract 价格；position value 和 roll cashflow为 `quantity_contracts * price_btc_per_contract`。`contract_size_btc` 用于 amount/contract转换、payoff和规格校验，不在同一 price 公式中再次相乘。  
**替代方案：** `q * contract_size * premium_per_contract`；保留无单位 `premium`。  
**后果：** v1 对当前 1 BTC contract 数值不变，但防止接口语义自相矛盾。改变此约定是 breaking change。

## D-008 — null、absence 与 zero sentinel 分离

**背景：** delivered ticker 用 `0.0` 表示不可执行两侧，`last_price=null`，`estimated_delivery_price="expired"`；文档则常描述 no quote 为 null。  
**选择：** raw 缺失/null/value 分开保存；canonical executable quote 必须 state open、price和amount均正且 fresh。raw 0/string sentinel只导致 canonical null + typed reason，不被重写。  
**替代方案：** 全部归一为 0；把 0 当可执行；parser 强制数字化 `"expired"`。  
**后果：** adapter 能兼容文档/响应差异并保持可回放。

## D-009 — failure closure 采用 scoped flags

**背景：** 全局“一错全停”会丢失可用数据，而局部 fallback 会伪造行动性。  
**选择：** quality flag 带 severity 和 scope。`BLOCK_ACTION` 阻止对应 instrument/run 的行动状态；`BLOCK_VALUE` 只阻止依赖该值的输出。内部错误不能伪装成 HOLD。  
**替代方案：** Boolean `is_valid`；任何缺值仍给默认建议；所有问题都让整批失败。  
**后果：** scanner 可以保留其他有效候选，同时缺 Ask/Bid 的对象严格 fail closed。

## D-010 — 最小 DTO 按 snapshot 绑定

**背景：** UI 或模块若各自查询“最新”数据，旧仓和新候选会发生时间拼接。  
**选择：** MarketSnapshot、MetricSnapshot、ScannerRun、PortfolioSnapshot、RollEvaluation 均使用不可变 ID，并显式携带输入 snapshot IDs 与版本。Roll 的 old/new 数据必须在记录的 skew window。  
**替代方案：** DTO只传 symbol/value；展示层再查数据库最新值。  
**后果：** 输出可复现；presentation 不得自行重算业务逻辑。

## D-011 — 三类核心版本不可变

**背景：** schema、指标公式和参数变化会让同名结果不可比较。  
**选择：** `schema_version` 管字段/单位/null语义，`metric_version` 管公式/basis/数值行为，`config_version` 指向验证后的完整有效配置内容。任何语义变化都换版本，历史不覆盖。  
**替代方案：** 只记录应用版本或 Git commit；运行时读取当前 config。  
**后果：** replay 和 scanner order 可复现；P04 必须提供不可变版本字段和 config fingerprint。

## D-012 — Roll 数量与 fee 的落点

**背景：** equal-contract/equal-delta 均需 fractional rounding；instrument commission字段不足以证明 option fee cap/formula。  
**选择：** 数量 mode 与 theoretical/executable quantity同时输出；rounding policy version化。fee schedule未验证时完整 executable cashflow unavailable，设 `FEE_UNVERIFIED`，不假定 fee=0。  
**替代方案：** 默认 nearest、隐去取整误差；直接用 commission raw；fee缺失按0。  
**后果：** P04 可定义协议和配置入口；P07 的 production roll economics 要等 fee evidence。

## D-013 — Utility、GCE 与 stress 保持研究状态

**背景：** Utility 无量纲而 cost 以 BTC 计；GCE候选量纲/解释不同，部分数值重复；smile dynamics没有唯一事实。  
**选择：** 不发布最终 Utility/GCE/权重。若以后启用，utility improvement与BTC成本独立 gate；stress必须记录 price/time/absolute vol-point shock、forward policy、smile policy和model version。  
**替代方案：** 为推进实现临时选权重/公式；把 stress 当预测。  
**后果：** P06/P07 只能实现已明确的基础指标和条件重估接口，实验项必须新 metric/config version。

## D-014 — P04 进入决定

**背景：** P04 需要稳定共享类型，但不需要解决所有研究公式。  
**选择：** 发布 `domain-contract-v1.0.0`，状态 `P04_ALLOWED`。所有尚未验证事项已有 raw-only、nullable、versioned adapter 或 feature gate，不会迫使 P04 猜测。  
**替代方案：** 因 gamma/fee/forward研究未完而阻止整个基础骨架；或假设答案清零问题。  
**后果：** P04 可以开始；被列为 feature blocker 的功能仍不得实现为 production semantics。

## D-015 — 可选只读认证与常驻采集

**背景：** 公共匿名连接足以验证字段，但长期采集需要更透明的限流，并可能需要授权用户
可用的 `raw` book。原始蓝图排除了所有 API Key，无法表达这种不增加交易能力的运维需求。

**选择：** 允许从进程环境读取成对的 Client ID/Secret，仅调用 `public/auth` 并继续使用
公开行情订阅。请求最小 scope，认证后再次验证实际 scope；任何 `:read_write` 或未知权限
均拒绝。密钥不得进入 YAML、数据库、状态文件、日志、fixture 或 Git。系统仍不实现任何
交易、资金或私有写方法。

**替代方案：** 匿名连接长期运行；把密钥写进 `config.yaml`；相信密钥页面上的权限而不
验证服务端 token；为方便以后扩展而加入通用 `private/*` 调用器。

**后果：** `domain-contract-v1` 的价格、单位、失败关闭和追溯语义不变。安全验收由“源码
没有认证”调整为“认证可选、scope 失败关闭、没有写端点或执行能力”。
