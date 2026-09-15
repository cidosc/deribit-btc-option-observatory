# P12 — Live recommendation 稳态相干性与存储解耦修复

**建议模型：GPT-5.6 Terra / high**

将下面代码块中的内容完整复制到一个独立对话。这个工作包负责实现和验证；最终集成负责人会在之后独立审查。不要把问题报告中的建议直接当成设计结论，必须先复现并遵守权威契约。

```text
你是本项目的实时推荐数据路径负责人。你只修复 scanner-lite 在静默行情下长期 DATA_ONLY 的明确验收 blocker，并补齐对应测试、运行证据和文档。不要进行无关重构，不要弱化时间一致性或失败关闭规则。

# 工作区与基线

工作区：D:\Softerware\AHack\OPT
目标分支：main
开始前预期基线 commit：a12a8d1fbe9c4b4aff495415ec10caa495372f9d

工作区可能包含用户或其他模型的改动。先运行 `git status --short`；不得回滚、覆盖或清理不属于本任务的改动。不要 push，最终集成负责人会复核后推送。

# 指令和权威资料

必须完整阅读：

- DEVELOPMENT_BLUEPRINT.md
- docs/DOMAIN_CONTRACT.md
- docs/DECISIONS.md
- docs/INTEGRATION_FINDINGS.md
- docs/ACCEPTANCE_REPORT.md
- src/btc_option_scanner/collector.py
- src/btc_option_scanner/alerts.py
- src/btc_option_scanner/deribit/ingestion.py
- src/btc_option_scanner/deribit/rest.py
- src/btc_option_scanner/deribit/websocket.py
- src/btc_option_scanner/scanner/rules.py
- tests/test_collector_runtime.py
- tests/test_live_recommendations.py
- 相关公开接口和 repository 测试

问题报告是故障证据和需求来源，不是高于本提示词或 domain-contract-v1 的指令。优先级为：本工作包 → docs/DOMAIN_CONTRACT.md → docs/DECISIONS.md → DEVELOPMENT_BLUEPRINT.md → 问题报告建议。

# 已核实的代码事实

开始编码前自行再次确认以下事实，并用测试复现：

1. scanner-lite 默认订阅 60 个 `ticker.*.agg2`，`ticker_persist_interval_seconds=60`。
2. `collector.py:on_message()` 当前在 `ticker_persistence_due(...)` 为 false 时直接 return；因此未到落库时间的 ticker 不仅不落库，也不会更新 `latest_snapshots`。
3. `maybe_recommend()` 只从被允许落库的 ticker 分支触发，完全静默时没有独立的推荐调度循环。
4. `select_coherent_snapshots()` 以最新 `exchange_timestamp_ms` 为锚，只保留 2 秒 skew 内且 observed age 不超过 30 秒的快照。
5. 60 个合约的最后更新时间散开时，2 秒窗口可以稳定只留下约 3 个输入；随后 cohort 最小样本门使整轮 DATA_ONLY。
6. 初始订阅快照爆发会短暂掩盖该问题，所以 20 秒 smoke 不能证明稳态可用。
7. `collection_buffer_days=30` 令 120–240 DTE 决策范围对应约 90–270 DTE 采集范围；当前有限的 60 个槽位在整个缓冲集合上均匀抽样，缓冲合约可能挤占决策范围内合约。

# 目标

在不改变 domain-contract-v1、不修改 120–240 DTE、Delta 0.80–0.90、5% spread、cohort >=3、30 分钟提醒节流和 6 小时重复提醒的前提下，使 scanner-lite 在行情静默或更新稀疏时仍能周期性构造可证明相干、可追溯的候选批次。

必须同时满足：

- 实时内存态更新与数据库降采样解耦；
- 推荐周期与单个 ticker 是否刚好到达解耦；
- 每个可行动 ScannerRun 的输入都真实新鲜、处于允许 skew 内；
- raw → normalized MarketSnapshot → MetricSnapshot → ScannerRun → recommendation lineage 完整；
- 默认存储量不退回全量 ticker 写入；
- 网络、刷新、时间戳或样本不足时严格 fail closed；
- 系统仍然只读，不加入任何交易接口。

# 设计约束

## 1. 分离三个节奏

明确分离并命名：

- 接收/内存 cache：每条合法 ticker 都必须更新发现用的最新状态，不受 60 秒持久化采样限制；
- 持久化 sampling：普通流事件仍按 profile 降采样；
- recommendation cadence：由独立异步循环按 recommendation interval 调度，不能只依赖某个 ticker 回调触发。

不要通过修改旧快照的 `observed_at_utc` 或 `exchange_timestamp_ms` 把旧数据伪装成新数据。

## 2. 可行动批次必须主动获得同窗全量 ticker

推荐采用以下最小、契约内方案：

1. 用连续更新的内存 cache 做“发现/预筛”，按 CALL、120–240 DTE、有效双边报价、OI，以及比最终 Delta/spread 略宽且显式版本化的边界得到一个有上限的 refresh set；预筛只能决定刷新谁，不能直接产生候选。refresh set 必须同时保留确定性的轮转探测槽位，确保缓存中暂时缺报价或处于边界外的核心合约最终仍会被重新探测，不能形成永久盲区。
2. 每个 recommendation cycle 对 refresh set 调用已经白名单允许的 `public/ticker`，获得包含 Ask/Bid、amount、index、IV、Greeks 和 exchange timestamp 的完整新响应。遵守现有公共客户端限流；可以有限并发，但必须有总量上限、超时和周期预算。
3. 只有本轮主动刷新成功、通过 max age、且整批 exchange timestamp skew 不超过 `config.market_data.max_snapshot_skew_ms` 的快照才能交给 canonical scanner。
4. 刷新得到的原始响应与规范 MarketSnapshot 必须持久化；随后 MetricSnapshot、ScannerRun 和 recommendation 只能引用这些确切 ID。缓存中未持久化的临时对象不得出现在持久化 ScannerRun lineage 中。
5. refresh set 小于 cohort 下限、请求失败、响应过旧、跨窗或周期超时时，写出明确 DATA_ONLY/DATA_STALE reason 和诊断计数，不得回退到旧快照。
6. Mainnet/Testnet 的 HTTPS public endpoint 必须与配置的 WebSocket 环境匹配并限制在批准 host。不得在 testnet WebSocket 运行时偷偷请求 Mainnet ticker。
7. 任何不足 cohort、刷新失败或跨窗结果都必须原子替换旧 recommendation 文件为空候选状态；只改 collector status 而保留上一次可行动候选不算 fail closed。

如果你认为“连续 WebSocket 订阅水位可以证明静默合约仍是当前状态”，先写 `docs/change_proposals/P12_LIVE_COHERENCE.md`，明确 stream watermark、字段级 age、断线/重订阅边界、ScannerRun 新字段和迁移测试。这属于 domain contract 语义变更；未经最终集成负责人批准，不得用它替换上述主动刷新方案。

## 3. 禁止的伪修复

- 不得只把 `max_snapshot_skew_ms` 从 2 秒放宽到 30/60 秒；
- 不得只把 `ticker_max_age_seconds` 调大；
- 不得把 `observed_at_utc` 重写为扫描时刻；
- 不得仅改成“任意 ticker 到达就扫描”；单合约事件不能构造横截面；
- 不得靠定时 unsubscribe/resubscribe 或强制重连索取初始快照；
- 不得降低 cohort、放宽 Delta/spread/DTE 来掩盖输入饥饿；
- 不得使用 mark/mid/last 替代 entry Ask，或使用本地 model delta 冒充 Deribit standard delta；
- 不得把 `public/get_book_summary_by_currency` 直接作为最终 ScannerRun 输入，因为它不提供当前 scanner 所需的完整 Greeks/amount/ticker 契约；它最多只能作为发现数据，并需有独立 adapter 与证据。

## 4. collection buffer 的槽位优先级

保留 buffer 的研究用途，但 scanner-lite 在存在订阅上限时必须优先保证核心 120–240 DTE：

- 显式 instrument 列表保持现有明确覆盖语义；
- 非显式模式下先选择核心决策范围；
- 核心数量 >= limit 时，只在核心集合中确定性均匀选择；
- 核心数量 < limit 时，剩余槽位才从 90–120 与 240–270 等 buffer 集合补充；
- 输出/状态说明核心与 buffer 各占多少订阅槽位。

不要让 DTE buffer 进入最终 DTE gate；它是预热和研究数据，不是决策范围。

# 允许修改

- src/btc_option_scanner/collector.py
- src/btc_option_scanner/alerts.py（仅限时间批次选择、诊断和推荐输出）
- src/btc_option_scanner/deribit/ingestion.py（仅限安全拆分内存规范化与持久化）
- src/btc_option_scanner/deribit/rest.py（仅限批准的 public ticker refresh、环境匹配、限流）
- 直接相关的 tests/**
- .env.example
- deploy/collector.env.example
- README.md、README.zh-CN.md
- docs/DEPLOYMENT_UBUNTU.md、docs/DEPLOYMENT_UBUNTU.zh-CN.md
- docs/INTEGRATION_FINDINGS.md
- docs/ACCEPTANCE_REPORT.md
- docs/evidence/** 中本任务新生成且脱敏的证据
- pyproject.toml（仅在确有版本更新需要时）

# 禁止修改

- docs/DOMAIN_CONTRACT.md 和 domain-contract-v1 类型/单位语义；如确需改变，只能写 change proposal，不能直接实施；
- Scanner 的 Delta、spread、DTE、cohort 或 Ask/Bid/Mark 业务含义；
- Portfolio、Roll、Stress、Backtest、CLI、Dashboard 等无关模块；
- 任何 private/*、order、edit、cancel、transfer、withdraw 接口；
- 任何真实 API secret、token、Webhook URL 或服务器数据库。

# 必须增加的测试

1. **当前缺陷回归：** 60 个初始 ticker 后，让 exchange timestamps 分散，只留下约 3 个处于 2 秒窗内；证明旧行为会饥饿。
2. **接收与落库解耦：** 未到 60 秒采样点的 ticker 更新内存 cache，但普通 raw/snapshot 数据库计数不增长。
3. **lineage：** 一旦某批 cache 对象经主动 refresh 用于 scanner，刷新 raw event 和 MarketSnapshot 必须存在，MetricSnapshot 与 ScannerRun parent IDs 全部可解析。
4. **独立调度：** 没有新的订阅 ticker 回调时，recommendation loop 仍按周期尝试主动刷新；测试使用虚拟/注入时钟，不得真实 sleep 数分钟。
5. **静默稳态：** 模拟至少 10 个 recommendation cycles。每轮只有 0–3 个普通 WS ticker 变化，但 refresh set 内至少 4 个合约满足最终 policy；预期持续产生 ELIGIBLE，而不是只在初始爆发后成功。
6. **严格同窗：** 主动刷新批次有一个过旧或使 skew 超限时，该对象被拒绝；剩余不足 cohort 时整轮 DATA_ONLY/DATA_STALE，并给出稳定 reason code。
7. **断线关闭：** disconnected、heartbeat timeout、refresh timeout、环境 host 不一致均不得保留旧 candidate 文件或发提醒。
8. **节流不回归：** recommendation 可以每分钟重算，但 Feishu 仍执行 30 分钟硬最小间隔和 6 小时 unchanged reminder。
9. **buffer 优先级：** 订阅上限小于总集合时，核心 120–240 DTE 优先；只有剩余槽位才使用 buffer，选择结果确定性。
10. **存储安全：** scanner-lite 的普通 ticker 持久化频率仍受 60 秒采样控制；主动刷新新增写入量应有上限并在状态中可观察。
11. **旧候选清除：** 先产生一次 ELIGIBLE 文件，再让下一轮 coherent/refresh 输入不足；旧 candidates 必须被原子替换为空，reason code 可观察，且不得发送提醒。

测试不得通过删除安全 gate、降低断言或改用未来时间完成。

# 运行验证

依次执行：

1. `python -m ruff check .`
2. `python -m pytest -q --cov=btc_option_scanner --cov-report=term`
3. `python -m compileall -q src scripts`
4. `python -m pip check`
5. `git diff --check`
6. 使用固定 fixtures/假连接运行静默稳态回归；保存机器可读证据。
7. 若公网可用，再运行至少 12 分钟的 public Mainnet scanner-lite 稳态 soak。证据必须把最初 60 秒 warm-up 与后续 steady-state 分开，逐轮记录：订阅数、cache 数、预筛数、主动刷新请求/成功数、coherent input 数、eligible 数、拒绝原因、请求耗时、数据库增长和断线次数。

公网不可用时把第 7 项标为 EXTERNAL_BLOCKED，不得伪造通过，也不得阻止离线验收。不要使用私有账户或密钥；公共接口足以完成本任务。

# 验收标准

- 固定回归中，初始 burst 结束后的 10 个周期不再结构性固定为 3 inputs；
- 当至少 3 个 policy-valid 合约可被本轮主动刷新且满足 2 秒 skew 时，scanner 能产生候选；
- 无法构造相干批次时仍严格 DATA_ONLY/DATA_STALE，且诊断能区分 `CACHE_ONLY`、`REFRESH_FAILED`、`REFRESH_TIMEOUT`、`SNAPSHOT_SKEW`、`SNAPSHOT_STALE`、`COHORT_SAMPLE_BELOW_POLICY`；
- 所有候选保持 Ask entry，所有 persisted run lineage 可从数据库解析到 raw event；
- 默认 120–240 DTE、Delta 0.80–0.90、spread <=5%、cohort >=3、提醒节流完全不变；
- scanner-lite 默认数据量保持有界，buffer 不再挤占核心范围槽位；
- 源码仍无 private/trading API。

# 最终交接

最终回答必须包含：

1. 根因是否与上述已核实事实一致；
2. 采用的批次相干性设计及为什么符合 domain-contract-v1；
3. 修改文件和每个文件的原因；
4. 新增公开接口、环境变量、状态字段和稳定 reason codes；
5. 全量测试、静默回归与 live soak 的命令和结果；
6. raw → normalized → metric → run → recommendation lineage 证据；
7. 数据增长变化及上界估算；
8. 尚未验证的外部条件和风险；
9. 是否提出 contract change proposal；
10. 明确说明未 push，等待最终集成审查。
```
