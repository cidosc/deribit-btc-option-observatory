# Gate 0：BTC Inverse Call 量化契约草案

> **状态：** `draft / Gate 0`；不得作为策略参数、交易指令或已发布领域契约使用。  
> **版本：** `quant-contract-draft-v0.1`  
> **范围：** Deribit BTC inverse European long Call 的纯函数定价参照、指标、组合估值、滚仓现金流与条件压力重估。  
> **非范围：** 下单、最终候选权重、流动性阈值、投资建议、历史绩效结论。

本文在 Gate 0 中将四种证据严格区分：

| 标签 | 含义 | 在本草案中的用法 |
|---|---|---|
| **[OF] 官方事实** | Deribit 官方支持页/API 文档已明确表述 | 可作为字段、合约和 API 行为的暂定事实；仍须用录制响应覆盖字段存在性。 |
| **[MI] 数学恒等式** | 在列明模型和单位后必然成立 | 可直接实现为无副作用纯函数。 |
| **[MC] 建模选择** | 为避免语义混淆而选定的接口/估值口径 | P03 应确认或版本化，不应悄悄替换。 |
| **[RH] 研究假设** | 尚待样本、回放或实证支持 | 只记录、比较或做 gate；绝不当作事实或最终规则。 |

`NotVerified` 表示不能仅凭官方文字确定精确刻度，必须完成第 12 节的真实响应数值实验后才可参与跨 Greek 的 Utility。

## 1. 权威事实与约定

### 1.1 已确认的官方事实 [OF]

- Deribit inverse options 是欧式、现金结算；BTC inverse option 的报价、保证金和现金结算币种为 BTC。
- BTC option `contract_size`/multiplier 是 **1 BTC**；一张 BTC Call 对应 1 BTC 名义，`min_trade_amount` 应从 instrument 读取，不能假定为整数张。
- 到期 cash settlement 使用 delivery price；Call 的 USD intrinsic 为 delivery price 减 strike 的正部，BTC settlement 为该 USD 金额除以 delivery price。
- Deribit 用对应到期的 forward（对应 future 的 mark；无对应 future 时为 synthetic future）计算展示的 IV；官方 inverse-option 页面将 (R=\ln(F/X)/T)，其中 (X) 是 index、(F) 是 forward。
- `ticker`/ticker subscription 对 option 给出 `underlying_price`、index、bid/ask/mark IV 和 `delta/gamma/theta/vega/rho`。API 文档称 gamma、theta、vega、rho 为 **unadjusted standard Black-Scholes**；delta 为该 expiry 的 Black-Scholes delta。
- 官方特别规定：展示的 `theta` 是 `min(one_day_theta, lifetime_theta)`；不足一天时，不能把 API theta 当完整一日 theta。
- 官方教育资料把 vega 描述为 IV **1 percentage point** 变化的价格敏感度；rho 的“1.00 rate”还是“1 percentage point”刻度未在本次权威 API 字段说明中明确，列为待验证。

来源（本草案写作时检索）：[Inverse Options](https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options)、[public/ticker](https://docs.deribit.com/api-reference/upcoming/market-data/public-ticker)、[ticker subscription](https://docs.deribit.com/subscriptions/market-data/tickerinstrument_nameinterval)、[Greeks introduction](https://insights.deribit.com/education/introduction-to-option-greeks/)。

### 1.2 研究对象和通用符号 [MC]

除非字段名另有后缀，所有 price 是单张合约的价格；所有百分比内部是 decimal（例如 76.67% 为 `0.7667`），显示层才乘 100。

| 符号/规范输入字段 | 含义 | 输入单位 |
|---|---|---|
| `contract_quantity` (n) | signed contracts；long Call (n>0) | contract |
| `contract_size_btc` (m) | instrument multiplier | BTC / contract（目前应为 1） |
| `strike_usd` (K) | exercise price | USD / BTC |
| `index_price_usd` (X) | 当前 Deribit BTC index | USD / BTC |
| `underlying_price_usd` | ticker 原样字段：用于 IV 计算的 underlying price；尚不得自动改名为 forward | USD / BTC |
| `model_forward_price_usd` (F) | Black-76 的明确 forward 输入；来自经验证的 adapter、显式情景输入或可审计 carry construction | USD / BTC |
| `delivery_price_usd` (D) | 到期结算 price；仅 expiry/historical 使用 | USD / BTC |
| `time_to_expiry_years` (T) | 精确 UTC 剩余秒数 / (365\times24\times3600) | year |
| `iv_decimal` (\sigma) | 年化 implied volatility | 1 / √year；数值为 decimal |
| `rate_annual_decimal` (r) | continuously compounded annual carry/rate | 1 / year |
| `premium_{side}_btc` (p_s) | `side ∈ {ask,bid,mark}` 的每张 BTC quote | BTC / contract |
| `premium_{side}_usd` (V_s) | 同一 BTC quote 以当期 index 的 USD 等值 | USD / contract |

这里的 (m) 不是 BTC 现金余额；所有指标应显式乘以 (m)，即使当前事实为 (m=1)。`index_price_usd`、原始 `underlying_price_usd`、`model_forward_price_usd` 和 `delivery_price_usd` 绝不互相回填。P01 尚未以录制 payload 证明 `underlying_price_usd` 在所有 BTC inverse ticker 中就是 (F)，故目前只有显式标注 source 的 `model_forward_price_usd` 可进入 Black-76。

### 1.3 必须保留的价格字段 [MC]

| 字段 | 定义/用途 | 缺失语义 |
|---|---|---|
| `ask_btc` | 买入新 long 的可执行每张成本；entry 指标唯一价格基准 | `NO_EXECUTABLE_ASK`；不可由 mark/mid 填补 |
| `bid_btc` | 卖出既有 long 的可执行每张收入；liquidation/roll 唯一价格基准 | `NO_EXECUTABLE_BID`；不可由 mark/mid 填补 |
| `mark_btc` | 交易所理论/风险监控估值 | 可做 mark NAV；不可冒充成交价 |
| `mid_btc` | 仅当 bid、ask 都存在时的 ((bid+ask)/2) | 单边或无报价时 `null` |
| `premium_{side}_usd` | (p_s\times X\times m) | 需要同一 snapshot 的正 (X)；否则 `null` |

`last_price` 既不是 entry、exit，也不是 NAV 的默认价格；它只能保存为成交历史观测。若 `ask_btc<=0`、`bid_btc<=0`、bid 大于 ask、价格非有限，必须输出质量 flag，不能“修正”数据。

## 2. 到期 payoff 与当前 intrinsic

### 2.1 到期结算 [OF] + [MI]

令 (D>0) 为该合约的 delivery price。每张 long Call 的到期现金结算为：

\[
\operatorname{payoff\_usd}(D,K,m)=m\max(D-K,0)
\quad[\mathrm{USD}]
\]

\[
\operatorname{payoff\_btc}(D,K,m)=m\frac{\max(D-K,0)}{D}
=m\max(1-K/D,0)
\quad[\mathrm{BTC}].
\]

因此，不应把 (\max(D-K,0)) 直接标为 BTC payoff；它是 USD intrinsic。两者满足：

\[
\operatorname{payoff\_usd}=D\times\operatorname{payoff\_btc}.
\]

**边界：** (D\le K\) 时两者为 0；(D\to\infty) 时 BTC payoff 渐近 (m) BTC 而 USD payoff 无上界；(D=0) 未定义且不是有效结算输入。这个 BTC 上限是 inverse payoff 的核心性质。

### 2.2 当前 index intrinsic：报告用的即时结算代理 [MC]

当要回答“若当前 index 就是 delivery price，立即会结算多少”时，使用：

\[
\begin{aligned}
\texttt{intrinsic\_index\_usd}&=m\max(X-K,0) &&[\mathrm{USD}],\\
\texttt{intrinsic\_index\_btc}&=m\max(1-K/X,0) &&[\mathrm{BTC}].
\end{aligned}
\]

它们严格满足 `intrinsic_index_usd = X * intrinsic_index_btc`。这是 ICR/Optionality 的**默认** intrinsic 基准，原因是报价 BTC 的 USD 等值也用同一时点 index (X)。字段名必须含 `index`，不得简称 `intrinsic_value_btc`。

### 2.3 forward intrinsic：定价参照，不是即时可结算金额 [MI] + [MC]

若 `model_forward_price_usd` (F) 是与 option expiry 匹配的 forward、(r=\ln(F/X)/T)，标准定价中的 discounted USD intrinsic 下界为：

\[
\begin{aligned}
\texttt{intrinsic\_forward\_pv\_usd}&=m e^{-rT}\max(F-K,0) &&[\mathrm{USD}],\\
\texttt{intrinsic\_forward\_pv\_btc}&=\texttt{intrinsic\_forward\_pv\_usd}/X
=m\max(1-K/F,0) &&[\mathrm{BTC}].
\end{aligned}
\]

最后一个等式要求 (F=Xe^{rT})。若 feed 提供的 (F,X,r) 不满足给定容差，保留前一式并设 `FORWARD_CARRY_INCONSISTENT`，不可强行代数约化。

该字段衡量“forward 的 discounted intrinsic”；它不是现在行权（欧式也不能行权）更不是实际到期 settlement。直接以 `max(1-K/F,0)` 命名 `current_intrinsic_btc` 会遮蔽上述差异，禁止。

### 2.4 何时使用哪一个 [MC]

| 任务 | 必用价格/字段 | 不能替代它的量 |
|---|---|---|
| 到期兑现与到期测试 | `delivery_price_usd`、`payoff_*` | 当前 index/forward |
| 当前 ICR、time value、资本老化描述 | `intrinsic_index_*` | forward intrinsic |
| Black-76 下界与 IV/Greek 校验 | `intrinsic_forward_pv_*` | 即时 index intrinsic |
| USD quote 换算、NAV_USD | snapshot `index_price_usd` | forward 或 delivery |

同时保存两套 intrinsic 是为了可审计比较；第一版不得把它们相减后解读为“真实时间价值”。`extrinsic_*` 的基准由第 5 节单独固定。

## 3. Black-76 / Black-Scholes 价格参照与 BTC quote 转换

### 3.1 标准 USD Black-76 [MI]

输入明确的 `model_forward_price_usd` (F)、(K>0,T>0,\sigma>0,r\in\mathbb R,m>0)，令：

\[
d_1=\frac{\ln(F/K)+\tfrac12\sigma^2T}{\sigma\sqrt T},\qquad
d_2=d_1-\sigma\sqrt T.
\]

每张 Call 的 USD present value 为：

\[
\texttt{black76\_call\_usd}=m e^{-rT}[F\Phi(d_1)-K\Phi(d_2)]
\quad[\mathrm{USD/contract}].
\]

输出仅是平坦 IV、连续 carry、欧式现金结算下的参照值；不保证可成交，也不取代 Deribit mark。

若 (F=Xe^{rT})，等价的 Black-Scholes spot 写法是：

\[
V_{BS}=m[X\Phi(d_1)-Ke^{-rT}\Phi(d_2)]\quad[\mathrm{USD/contract}],
\]

其中 (d_1=[\ln(X/K)+(r+\sigma^2/2)T]/(\sigma\sqrt T))。Deribit inverse-options 支持页给出该 USD 公式及 (r=\ln(F/X)/T)。

### 3.2 USD price 变为 BTC premium [MI] + [MC]

BTC quote 不是把 (V_{USD}) 除以 forward，而应按当前 index 的 USD 等值定义转换：

\[
\texttt{black76\_call\_btc}=V_{USD}/X
\quad[\mathrm{BTC/contract}].
\]

在 carry identity 成立时它也可写成：

\[
m[\Phi(d_1)-(K/F)\Phi(d_2)].
\]

这在 (T\to0)、(F\to X\) 时收敛到 `intrinsic_index_btc`。错误而常见的转换是 (V_{USD}/F)：它改变了 present-value quote，除非 (F=X) 否则不对应交易所按 index 显示的 USD equivalent。

### 3.3 数值退化规则 [MC]

| 条件 | 定价函数返回 | 说明 |
|---|---|---|
| (T\le0) | `payoff_*` with supplied settlement/reference price | 不再计算 (d_1,d_2) |
| (\sigma=0,T>0) | (m e^{-rT}\max(F-K,0)) USD，再除 (X) | 零方差确定性极限 |
| (F,K,X,m\le0)、非有限值 | `INVALID_PRICING_INPUT` | 不默默 clip |
| (\sigma<0) | `INVALID_PRICING_INPUT` | 负 IV 无数学含义 |
| 极深 ITM/OTM | 使用稳定正态 CDF，价格落在 no-arbitrage 容差内 | 避免 `exp`/尾部数值损失 |

推荐绝对容差：BTC quote (\max(10^{-12},0.1\times\text{tick_size_btc}))；USD price 为该 BTC 容差乘 (X)。模型与交易所 mark 的差不设固定通过阈值（会受 smile、rounding、time skew 影响），而是保存 `difference_btc`、`difference_ticks`、输入时间差和诊断 flags。

## 4. Greeks：原始 API、模型 Greek 与 BTC NAV 不能混同

### 4.1 三个不同的“delta” [MI] + [MC]

令 (V_{USD}=X p_{BTC}) 为每张合约的 USD NAV，(p_{BTC}) 为其 BTC quote。Deribit ticker 的 API delta 应原样保存为 `deribit_delta_standard`; 文档称它为 standard Black-Scholes delta。其自然解释为：

\[
\Delta_{USD}=\frac{\partial V_{USD}}{\partial X}
\quad[\mathrm{BTC\ equivalent/contract}],
\]

即一张合约的 USD NAV 对 index 每变动 1 USD/BTC 的 USD 变化；数值是 BTC-equivalent USD-NAV exposure（multiplier 为 1 时）。它不是 (\partial p_{BTC}/\partial X)。

以下转换只适用于一个可微的 model/mark price，而非“Bid/Ask book 会怎样移动”的主张。在 (V_{USD}=Xp_{BTC}) 且对 (X) 的偏导使用相同 carry convention 时：

\[
\frac{\partial p_{BTC}}{\partial X}=\frac{\Delta_{USD}-p_{BTC}}{X}
\quad[\mathrm{BTC/contract\ per\ (USD/BTC)}],
\]

以及 BTC NAV 的百分比价格敏感度：

\[
\texttt{btc\_nav\_delta\_per\_log\_index}=X\frac{\partial p_{BTC}}{\partial X}
=\Delta_{USD}-p_{BTC}
\quad[\mathrm{BTC/contract}].
\]

**结论：** API delta（约 1 的深 ITM Call）和 model/mark 的 BTC-NAV delta 并不相同；若 (p_{mark}=0.126,\Delta=1)，后者约为 0.874 BTC。这不是 API 错误，而是把 USD option value 转成 BTC quote 的分母效应。建议字段为 `model_btc_nav_delta_per_log_index_mark`；不可据此推断 bid/ask 的 book-curve delta。P03 应保存 API/standard delta 与派生 BTC-NAV sensitivity 为不同字段，绝不以一个模糊的 `delta` 覆盖另一个。

### 4.2 标准模型 Greek 的定义和暂定单位

下表的 `model_*` 来自标准 USD Black-76/BS price (V_{USD})，并须和 `deribit_*` 原值并列保存。`per 1 vol point` 表示 (\Delta\sigma=0.01)；`per 1 rate point` 表示 (\Delta r=0.01)。

| 规范字段 | 公式/定义 | 输出单位 | API 对应刻度状态 |
|---|---|---|---|
| `model_delta_standard` | (m e^{-rT}\Phi(d_1))（Black-76 对 (F) 的 delta）或等价 BS spot delta (m\Phi(d_1))，**须标记 delta basis** | BTC-equivalent / contract | Deribit 文档称 BS delta；用实验确认具体 forward/spot convention |
| `model_gamma_standard_per_usd` | BS (m\phi(d_1)/(X\sigma\sqrt T)) | BTC-equivalent / contract / (USD/BTC) | 官方称 gamma 为 delta 对 underlying price 的变化率；须实验确认 underlying basis与scale |
| `model_theta_usd_per_day` | 对 (T) 的 BS USD PV 负时间导数，乘 1/365；到期不足一天取 lifetime 规则时另标 | USD / day / contract | 官方确认 `min(1d,lifetime)`；BTC 或 USD quote刻度仍须实验 |
| `model_vega_usd_per_vol_point` | (mX\phi(d_1)\sqrt T/100)（BS；Black-76 以 (e^{-rT}F=X) 等价） | USD / 0.01 IV / contract | 官方教育资料支持 per vol point；API ticker 须实验复核 |
| `model_rho_usd_per_rate_point` | BS Call (mKT e^{-rT}\Phi(d_2)/100) | USD / 0.01 annual rate / contract | API 刻度 `NotVerified` |

对于 Black-76 的 forward delta，应使用明确字段 `*_per_forward_usd`，不可与 spot/index delta 混名。若后续 fixture 证实 API `underlying_price_usd` 就是该 expiry forward，则比较 API delta 时优先使用同一 forward basis；若只实现 BS spot Greek，也必须设置 `GREEK_BASIS_MISMATCH`，不能把差异判为 API 异常。

### 4.3 Theta 的特殊边界 [OF] + [MC]

`deribit_theta_raw` 的符号、原值和 snapshot 必须保留。显示/聚合前使用经过实验确认的单位字段，例如 `deribit_theta_usd_per_effective_horizon`；在确认前只能显示 `unit=unverified`。

建议实现如下纯函数语义：

\[
\texttt{theta\_horizon\_days}=\min(1,365T),
\]

\[
\texttt{theta\_effective\_usd}=V_{USD}(T-\texttt{theta\_horizon\_days}/365)-V_{USD}(T)
\quad[\mathrm{USD/contract}].
\]

`T <= 0` 时 theta 为 0/not-applicable（不是以无限值表示）。(0<T<1/365) 时它是**剩余 lifetime** 损耗，不能年化、除以 (T) 后仍称“每日 theta”。`theta_usd_per_calendar_day` 仅在 (T\ge1/365) 定义为上述一日变动；近到期的 API 数字应命名为 `theta_usd_per_effective_horizon`。

### 4.4 Gamma、vega 与 rho 的金额基准 [MC]

不得从 `gamma=0.000035` 这种裸数猜测是 BTC、USD 或“百分比”。本草案的目标字段是：

| 字段 | 暂定经济定义 | 单位/实施限制 |
|---|---|---|
| `deribit_gamma_standard_per_usd` | standard delta 对 index/underlying 1 USD 的导数 | `1/(USD/BTC)`；须实验通过才去掉 `unverified` |
| `deribit_vega_usd_per_vol_point` | USD option price 对 +1 vol point 的一阶变化 | USD / 0.01 / contract；须实验通过 |
| `deribit_theta_usd_per_effective_horizon` | USD option price在官方有效 theta horizon 的变化 | USD / effective horizon / contract；须实验通过币种基准 |
| `deribit_rho_usd_per_rate_point` | USD option price 对 +1 annual rate point 的变化 | USD / 0.01 / contract；须实验通过 |

确认前可使用 raw Greeks 做同字段时间序列监控和同一 Greek 的同量纲排序；禁止直接与 BTC premium、DCE 或其他单位 Greek 加权相加。

### 4.5 Greek 的极端性质 [MI]

在 constant-IV 标准 Call 模型下：深 ITM (\Delta_{USD}\to m)、Γ→0、vega→0；深 OTM 时 delta、gamma、vega→0；ATM 且 (T>0) 往往 gamma/vega 更高；(T\downarrow0) 时 time value→0，ATM gamma 可尖峰而 ITM/OTM gamma→0。零 IV 时 gamma/vega 在除 strike 的点质量极限外趋零，delta 为阶跃，数值实现不能用普通 (d_1) 公式除零。

这些是模型性质，不是“ATM 必然应该买”或“深 ITM 必然要 roll”的策略结论。

## 5. 基础指标：三套报价基准

### 5.1 extrinsic、ICR 与 Optionality Ratio [MC]

对每个 `side ∈ {ask,bid,mark}` 且 (p_s>0)，以**同一 snapshot 的 index intrinsic** 为基准：

\[
\begin{aligned}
\texttt{extrinsic\_\{side\}\_btc}&=p_s-\texttt{intrinsic\_index\_btc} &&[\mathrm{BTC/contract}],\\
\texttt{icr\_\{side\}}&=\texttt{intrinsic\_index\_btc}/p_s &&[1],\\
\texttt{optionality\_ratio\_\{side\}}&=\texttt{extrinsic\_\{side\}\_btc}/p_s=1-\texttt{icr\_\{side\}} &&[1].
\end{aligned}
\]

规范字段必须为：`icr_entry_ask`、`icr_liquidation_bid`、`icr_mark`、`optionality_ratio_entry_ask`、`optionality_ratio_liquidation_bid`、`optionality_ratio_mark`。单一 `ICR`、`OptionalityRatio`、`premium_BTC` 均禁止。

经济含义如下：

| 版本 | 分母经济含义 | 合法用途 |
|---|---|---|
| Ask / `entry` | 现在真实取得该 optionality 的 BTC 资本支出 | 新候选、entry 比较 |
| Bid / `liquidation` | 现在卖出旧仓可得到的 BTC | 持仓退出、roll 比较 |
| Mark | 非执行性的模型/账面分母 | mark NAV、理论研究 |

由于 bid 可能小于当前 index intrinsic（陈旧、宽价差、时间错配或市场异常），`extrinsic<0`、`ICR>1` 和 `optionality_ratio<0` 都必须保留并标记 `NEGATIVE_EXTRINSIC`; 不能 clip 到 0/1。无对应 side 或零分母则结果 `null`，而不是 0。

### 5.2 DCE [MC]

取同一 snapshot 的 `deribit_delta_standard` (\Delta\)（仅在其单位状态已确认是 standard delta 后）和单张 BTC quote：

\[
\begin{aligned}
\texttt{dce\_entry\_ask}&=\Delta/p_{ask},\\
\texttt{dce\_liquidation\_bid}&=\Delta/p_{bid},\\
\texttt{dce\_mark}&=\Delta/p_{mark}.
\end{aligned}
\]

数值上是“每 1 BTC premium 所获得的 standard USD-NAV delta exposure”；因为 (m=1)，可显示为 `BTC-equivalent delta / BTC premium`，数值无量纲，但建议元数据单位写为 `delta_BTC_equiv_per_BTC_capital`，以免误认成融资杠杆或 BTC-NAV delta。

- `dce_entry_ask` 是新买资本效率，Ask 上升而 delta 固定时严格下降。
- `dce_liquidation_bid` 是已经持有资产按可退出价值观察的比率，不是原始买入效率，也不能在无 Bid 时生成。
- `dce_mark` 是理论监控值，绝不替代前两项。

若要描述 BTC NAV 对价格的敏感度，另输出 `model_btc_nav_delta_per_log_index_mark`，而不要把 DCE 分子悄悄替换为它；两种研究问题不同。

### 5.3 并列的 GCE 候选，而非最终 GCE [RH]

令 (\Gamma\) 是已验证的 standard gamma，单位为 `BTC-equivalent delta / contract / (USD/BTC)`，(S=X>0)，(p=p_{side}>0)，(V=pS)。以下候选全部保留显式名称、价格 side 和 Greek basis：

| 候选字段 | 公式 | 数学单位 | 可回答的问题 |
|---|---|---|---|
| `gamma_spot_scaled` | \(\Gamma S\) | BTC-equivalent delta / contract | 约 100% proportional spot move 带来的 delta 一阶变化尺度 |
| `gamma_dollar_scaled` | \(\Gamma S^2\) | USD / contract | 100% move 的二阶 USD-PnL 尺度（实际二阶项是其 1/2） |
| `gce_delta_response_{side}` | \(\Gamma S/p\) | dimensionless | 每 BTC premium 的 delta 变化尺度 |
| `gce_usd_convexity_{side}` | \(\Gamma S^2/V\) | dimensionless | 相对 USD premium 的二阶凸性尺度 |
| `gce_raw_gamma_per_btc_{side}` | \(\Gamma/p\) | 1 / (USD/BTC) | 原始每 BTC capital gamma；很难解释，只作诊断 |

当 (V=pS) 时，`gce_delta_response` 与 `gce_usd_convexity` **数值相同且都无量纲，但解释不同**；两者不能当成两个独立 signal 后重复加权。`gamma*S` 本身是 BTC-equivalent delta 标度而不是 BTC premium；`gamma*S^2` 是 USD 标度。若后者直接除以 BTC premium，结果是 `USD/BTC`，不应伪装为无量纲分数。

本项目不宣布某一 GCE “正确”。是否预测资本老化、是否要用 (S)、(S^2)、premium 或 peer percentile 归一化，都属于 [RH]，只能通过版本化研究比较。

## 6. Portfolio：双币种 NAV 与 Greeks 聚合

### 6.1 两种当前 NAV [MC]

设 `reserve_btc` 为未投入的 BTC cash，不随价格重估；每个 long option (i) 的持仓为 (n_i\ge0)，price side 为 (p_{i,s})。

\[
\texttt{nav\_btc\_mark}=\texttt{reserve\_btc}+\sum_i n_i m_i p_{i,mark}
\quad[\mathrm{BTC}],
\]

\[
\texttt{nav\_btc\_liquidation}=\texttt{reserve\_btc}+\sum_i n_i m_i p_{i,bid}
\quad[\mathrm{BTC}].
\]

各自 USD 显示值为：

\[
\texttt{nav\_usd\_\{basis\}}=\texttt{nav\_btc\_\{basis\}}\times X
\quad[\mathrm{USD}].
\]

`nav_usd` 必须携带 `usd_conversion_index_price_usd` 和 snapshot time；不得以 forward 重估 BTC cash。`premium_paid_btc` 是历史 cash cost/成本基础，永不等于当前 option value，也不纳入一次定义后的 NAV 求和。

若任一 long position 缺 mark，mark NAV 为 `UNAVAILABLE`; 若任一 long position 缺 executable bid，liquidation NAV 为 `UNAVAILABLE`，不能只给已报价 legs 求和并称“组合 liquidation NAV”。这比乐观地以 mark 填价更安全。

### 6.2 Greeks 聚合 [MC]

原始 Deribit Greek 的组合总额，在每项单位已确认后按：

\[
G_{portfolio}=\sum_i n_i m_i G_i.
\]

标准 delta、gamma、theta、vega、rho 分别保留为不同具单位的输出；不因能相加就能与 NAV 或 DCE 相加。若 Greek unit 未验证，输出 `portfolio_deribit_{greek}_raw` 并附 `GREEK_UNIT_UNVERIFIED`，不输出风险金额。

建议另行聚合 `portfolio_model_btc_nav_delta_per_log_index_mark`，它是 BTC NAV 的 model/mark 对数价格敏感度，和 `portfolio_delta_standard` 并列显示。多币种 NAV 并不许可将 BTC theta/vega 与 USD theta/vega混合。

## 7. Roll：数量、现金流、取整和 reserve 守恒

### 7.1 前置 gate [MC]

可执行 roll 输入必须来自同一 `market_snapshot_window`，且均为 open、fresh、同 schema/unit version：旧 long 有正 `old_bid_btc`，新 long 有正 `new_ask_btc`，quantity、multiplier、fee schedule、tick/min trade amount 已知。任一不满足，返回明确状态而不是估算：`DATA_STALE`、`NO_EXECUTABLE_BID`、`NO_EXECUTABLE_ASK`、`SNAPSHOT_TIME_MISMATCH` 或 `UNKNOWN_TRADE_SPEC`。

### 7.2 Equal contracts [MI] + [MC]

旧持仓 (q_o>0) 张，令未取整新数量 (q_n^*=q_o)。实际数量 (q_n\) 由交易所 `min_trade_amount`、允许 fractional contract 和用户指定的 round policy 决定；这两个 quantity 都必须输出。

\[
\begin{aligned}
\texttt{old\_sale\_proceeds\_btc}&=q_o m_o\,bid_o,\\
\texttt{new\_purchase\_cost\_btc}&=q_n m_n\,ask_n,\\
\texttt{fee\_old\_btc}&=\operatorname{fee}(q_o,m_o,bid_o,\text{sell}),\\
\texttt{fee\_new\_btc}&=\operatorname{fee}(q_n,m_n,ask_n,\text{buy}),\\
\texttt{net\_reserve\_release\_btc}&=\texttt{old\_sale\_proceeds\_btc}-\texttt{new\_purchase\_cost\_btc}-\texttt{fee\_old\_btc}-\texttt{fee\_new\_btc}.
\end{aligned}
\]

正数是释放到 reserve，负数是 `reserve_required_btc=-release`。费用的价格基数、rate、cap、maker/taker 假设必须作为 `fee_schedule_version` 和每笔输入记录；不得把 instrument `maker_commission`/`taker_commission` 直接当成已验证 option BTC fee 公式或忽略 fee cap。

滚后：

\[
\texttt{reserve\_after\_btc}=\texttt{reserve\_before\_btc}+\texttt{net\_reserve\_release\_btc}.
\]

现金对账不变量（在不借贷且 reserve 不允许为负时）是：

\[
reserve_{before}+sale\_proceeds=reserve_{after}+new\_cost+fees_{old}+fees_{new}.
\]

“release”是显式 cash output，不默认用来再买 option；若 `reserve_after_btc < 0`，返回 `INSUFFICIENT_RESERVE` 而不是暗含杠杆。

### 7.3 Equal portfolio delta [MI] + [MC]

使用同一已验证标准 delta basis 时，目标是保持旧仓 standard USD-NAV delta：

\[
q_n^*=\frac{q_o m_o\Delta_o}{m_n\Delta_n}.
\]

当 (\Delta_n\le0)、未验证、无效或 (q_n^*) 不有限，返回 `INVALID_EQUAL_DELTA_TARGET`。随后按 instrument 交易格与**事先配置且记录的** `floor`/`ceil`/`nearest` rule 得到 (q_n)，并分别报告：

\[
\texttt{delta\_before}=q_om_o\Delta_o,
\quad
\texttt{delta\_after\_theoretical}=q_n^*m_n\Delta_n,
\quad
\texttt{delta\_after\_executable}=q_nm_n\Delta_n.
\]

取整误差 `delta_after_executable - delta_before` 和其方向必须可见。`equal_delta` 不是 equal BTC-NAV sensitivity；若研究目标是 BTC NAV 风险，必须另建 `equal_btc_nav_delta` 模式，不能重用同名模式。

### 7.4 Roll 后的风险与比率 [MC]

输出 each-basis 的 `dce/icr/optionality`，以及 `delta/gamma/theta/vega` 的 before/after、理论/可执行数量、`net_reserve_release_btc`、`fees_btc` 和 `transaction_spread_cost_btc`。后者若定义为相对 mid 的即时成本，只有两侧完整 bid/ask 才可算，且只是一种摩擦代理，不能替代真实 cash flow。

原始需求的

\[
U_{new}-U_{old}>transaction\ cost+spread+fee
\]

在常见情况下**量纲错误**：左边通常是 normalized/无量纲 Utility，右边是 BTC。第一版须拆为独立 gates：

1. `utility_improvement >= utility_threshold`（同为无量纲/percentile）；
2. `net_reserve_release_btc`、`fees_btc`、`spread_cost_btc` 分别满足 BTC 预算；
3. post-roll delta、reserve、质量、hysteresis/interval 等约束满足。

只有未来将 Utility 定义成可审计的 BTC certainty-equivalent 后，才可和 BTC 成本相减；该效用函数不在本 Gate 0 决定。

## 8. 条件压力重估，不是预测

### 8.1 场景输入和输出 [MC]

每个 stress point 必须包含而不是从环境隐含读取：

| 输入 | 规范字段/单位 | 选择 |
|---|---|---|
| price shock | `index_shock_return_decimal`; (X'=X(1+s)), (s>-1) | 相对 index shock；不得把 -100% 送入 Black formula |
| elapsed time | `elapsed_days >= 0` | (T'=\max(T-elapsed\_days/365,0)) |
| IV shock | `iv_shock_vol_points`; (\sigma'=\max(0,\sigma+shock/100)) | **绝对** vol points，非相对百分比 |
| rate/carry | `stress_rate_annual_decimal` 或 `forward_policy` | 不可默认不变又不记录 |
| smile rule | `smile_dynamics` enum | 见下节 |
| model | `pricing_model_version` | 输出可复现 |

输出必须至少有 `stress_option_value_btc`、`stress_option_value_usd`、`stress_nav_btc`、`stress_nav_usd`、标准 model Greeks、BTC-NAV sensitivities、是否已到期/跨到期 flag。所有 USD 输出以 (X') 转换；reserve BTC 不变但 reserve USD 随 (X') 变。

### 8.2 forward 和 smile 的明示假设 [MC]

第一版可提供、但不得混用的四种 `forward_policy`：

1. `preserve_carry_rate`: (F'=X'e^{rT'})；
2. `preserve_basis_ratio`: (F'=X'(F/X))（在 time decay 下会保留旧期限 basis，须标风险）；
3. `explicit_forward`: 场景输入直接给 (F'>0)；
4. `at_expiry_delivery`: (T'=0)，只能用给定/构造的 delivery (D'=X') 来 payoff。

第一版可提供、但不可假装为市场预测的 `smile_dynamics`：

- `sticky_strike_parallel`: 每个 strike 的 IV 加同一绝对 vol-point shock；
- `sticky_moneyness_parallel`: 用原曲面在新 log-moneyness 取 IV 再平移；
- `sticky_delta_parallel`: 以 delta bucket 取 IV 再平移；
- `flat_iv`: 忽略 smile，用单一输入 IV；
- `unavailable`: 缺 surface 时不报“surface stress”。

第一版推荐仅实现透明的 `flat_iv + preserve_carry_rate` 作为基线；结果须标 `MODEL_CONDITIONAL_NOT_MARK_REPRICE`。它不能替代真实 shocked bid/ask，不能断言 stress liquidation NAV。

### 8.3 到期边界 [MI] + [MC]

若 `elapsed_days` 使 (T'\le0)，忽略 IV、rate、smile，使用 `payoff_btc(D',K,m)`。若只给 (X') 未给真实 delivery，`D'=X'` 是明确的**情景假设**，不是历史结算事实。若 (X'\le0)，BTC quote 换算和 Black price 不定义；返回 `INVALID_STRESS_PRICE`，而非 0。

## 9. 极端边界、缺失与失败关闭

| 情况 | 必须的行为 |
|---|---|
| 深 ITM | `intrinsic_index_btc` 向 (m) 收敛；API standard delta 向 (m)，model/mark BTC-NAV log delta 向 (m-p_{mark})；不要把两者混淆。 |
| ATM | intrinsic 约 0；不因 gamma/vega 通常高就给策略结论。 |
| 深 OTM | intrinsic、payoff、DCE 分子可趋 0；若 quote 极小，DCE/GCE 可数值爆炸，标 `NEAR_ZERO_PREMIUM` 而非无限排名。 |
| 临近到期 | theta 使用官方 effective horizon；stress 穿越到期用 payoff；不使用负 (T) 的 BS。 |
| 零 IV | 使用确定性 forward payoff 极限；不对 (d_1) 除零。 |
| index/forward 不一致 | 保留原输入，计算 carry residual，标 flag；不替换其中一个。 |
| 缺 Ask | entry DCE、ask ICR/OR、entry 候选均 `null`；没有 `NO_EXECUTABLE_ASK` 以外的乐观推断。 |
| 缺 Bid | liquidation DCE、bid ICR/OR、roll sale/NAV 均不可得。 |
| 缺 Mark | mark 指标/NAV 不可得；不取 mid 替代。 |
| 过期/非 open/stale 或快照超窗 | 所有行动性/roll 计算 fail closed；可保留研究性原始值。 |

## 10. 纯函数输入/输出契约与命名

后续模块至少应按下表命名；不必在 Gate 0 实现。

| 纯函数 | 最小输入 | 最小输出 |
|---|---|---|
| `inverse_call_expiry_payoff` | `delivery_price_usd, strike_usd, contract_size_btc` | `payoff_usd, payoff_btc` |
| `current_intrinsic` | `index_price_usd, forward_price_usd?, rate?, T, strike_usd, contract_size_btc` | `intrinsic_index_*`, 可选 `intrinsic_forward_pv_*`, flags |
| `black76_inverse_call_reference` | `index_price_usd, forward_price_usd, strike_usd, iv_decimal, T, rate, contract_size_btc` | `model_price_usd, model_price_btc, model_greeks` |
| `derive_btc_nav_sensitivities` | `standard_delta, standard_gamma, model_or_mark_price_btc, index_price_usd` | `model_btc_nav_delta_per_log_index_mark`, 可选 BTC quote curvature，flags |
| `side_metrics` | `side premium_btc, intrinsic_index_btc, deribit_delta_standard, gamma, index_price_usd` | `extrinsic/icr/OR/dce/GCE candidates` |
| `portfolio_nav` | reserve、positions、side prices、index | mark/liquidation BTC/USD NAV 或 unavailable reason |
| `roll_cashflow` | old bid/new ask、quantities、multipliers、fee schedule、rounding | proceeds/cost/fees/release/reserve reconciliation |
| `stress_reprice` | pre-state + complete scenario policy | conditional values/Greeks/assumption manifest |

返回结构必须附 `metric_version`、`pricing_model_version`、`greek_unit_status`、`price_basis`、`snapshot_id`/timestamps 和 `quality_flags`。任何对 `null` quote 的数学函数必须返回 typed unavailable reason，而非 `NaN`、0 或 fallback price。

## 11. 必须满足的性质测试与建议容差

### 11.1 精确/近精确不变量

1. 对任意 (D,K>0)，`payoff_usd == D * payoff_btc`（浮点相对容差 `1e-12`）。
2. Call payoff/intrinsic 对 (D) 或 (X) 非递减，BTC payoff 在 ([0,m]\) 内。
3. `intrinsic_index_usd == X * intrinsic_index_btc`；当 (F=X) 时 forward-PV intrinsic 与 index intrinsic 相等。
4. 若 carry identity 成立，`black76_call_usd / X == black76_call_btc`；(T\to0) 或 (\sigma\to0) 时收敛到相应确定性/到期值。
5. 固定 Δ、正 Ask 下，Ask 增加则 `dce_entry_ask` 严格不增加；side premium 增加则 ICR 不增加、OR 不减少。
6. 每一可定义 side：`icr + optionality_ratio == 1`（绝对容差 `1e-12`）；这在 negative extrinsic 下也应成立。
7. `gce_delta_response` 和 `gce_usd_convexity` 在 (premium_usd=premium_btc\times X) 时数值相等（相对容差 `1e-12`）；二者都无量纲但 economic interpretation 不同，不得重复加权。
8. 无 Bid 不得产生 liquidation NAV 或 executable roll；无 Ask 不得产生 entry DCE 或 executable candidate。
9. equal-contract 和 equal-delta roll 都满足第 7.2 节 cash conservation；取整后的 delta 不得被误报为理论 target。
10. 同一输入、同一 version、同一 rounding policy 的输出位级或 decimal 级确定；不得读当前时钟/网络。

### 11.2 市场数据/模型比较容差

- BTC price round-trip (`usd = btc*X`, `btc = usd/X`)：相对 `1e-12`，再按 tick display rounding。
- tick alignment：价格与适用 `tick_size` 之差不超过 `1e-12` tick；跨 tick-size step 时以 instrument rule 为准。
- model-vs-Deribit Greek 数值实验：先用相对 `1e-3` 与绝对 floor（delta `5e-5`、gamma `1e-8 BTC-equivalent/(USD/BTC)`、theta/vega/rho `0.01` 对应已验证 unit）作为**调查阈值**，不是通过/失败的最终门槛。time skew、IV rounding、smile、different forward basis 都必须写进误差报告。
- old/new snapshot alignment：阈值尚未决定，必须是配置化的 `max_snapshot_skew`; 超过即 fail closed。

## 12. 真实 Deribit Greek 单位的数值验证实验

官方文字已给出方向和 theta horizon，但 Gate 0 仍不能只凭裸数确定所有 API Greek 的 price numeraire/scale。P01 或后续录制工作必须保留原始 ticker、instrument、收取时间、原始 IV（百分数）及输入 fields；下列实验不写交易代码、不使用 private API。

### 12.1 同 snapshot 的 Black-76 重算

每个样本保存 (X,F,K,T,\sigma_{mark},r=\ln(F/X)/T,m)、Deribit raw Greeks 和 mark BTC。以同一输入重算标准 BS/Black-76：

| 待确认项 | 比较候选 | 通过证据 |
|---|---|---|
| `delta` basis | BS spot (\Phi(d_1))、Black-76 forward (e^{-rT}\Phi(d_1))、BTC-quote delta | 跨 ITM/ATM/OTM 最小误差的候选，并记录 carry basis |
| gamma unit | (\phi(d_1)/(X\sigma\sqrt T)) vs 其乘/除 (X)、100 的候选 | 数值比接近 1，且由第 12.2 有限差分交叉验证 |
| vega scale | USD per 1.00、USD per 0.01、BTC per 1.00、BTC per 0.01 四候选 | 对多 expiry/strike 的比例稳定且符合官方 per-point文字 |
| theta numeraire/scale | USD/BTC × daily/lifetime horizon候选 | (T\ge1d) 和 (T<1d) 分组均匹配；近到期符合 min rule |
| rho scale | USD per 1.00 与 USD per 0.01（及 BTC版本） | 多点比率稳定；否则保持 raw |

至少取深 ITM、近 ATM、深 OTM，以及 (T>7d)、(1d<T<7d)、(T<1d) 的可报价样本；不能以一个深 ITM、gamma 四舍五入为 0 的单点证明单位。

### 12.2 中心有限差分

用相同 Black-76 implementation，在固定其他模型输入下以相对 shock (h_S=10^{-4}X)、(h_\sigma=10^{-4})（1 bp vol decimal）、(h_r=10^{-5})、(h_T=\min(1/365,T/10)) 重估：

\[
\Delta\approx\frac{V(X+h_S)-V(X-h_S)}{2h_S},\quad
\Gamma\approx\frac{V(X+h_S)-2V(X)+V(X-h_S)}{h_S^2},
\]

\[
\nu_{1.00}\approx\frac{V(\sigma+h_\sigma)-V(\sigma-h_\sigma)}{2h_\sigma},
\quad
\Theta_{horizon}\approx V(T-h_T)-V(T).
\]

所有 (V) 先保持 USD；若比较 BTC quote Greek，明确在每个 shocked (X) 重新除以 shocked (X)。不得把 USD finite difference 和 BTC API quote混比。应做 (h/2,h,2h) 敏感性检查，确认不是数值步长伪像。

### 12.3 API 的 live 观察证据（非 fixture）

在 2026-09-14 的只读 `public/ticker` 观察中，`BTC-15SEP26-68000-C` 返回：`index_price=77786.52`、`underlying_price=77780.77`、`mark_price=0.1259 BTC`、`delta=0.99987`、`gamma=0.0`（显示精度下）、`vega=0.01879`、`theta=-0.72047`、`rho=1.57945`、`mark_iv=76.67`。这与深 ITM、临近到期时 Δ 接近 1 且 gamma 显示为 0 的模型方向一致，但**不足以**确定 gamma/vega/theta/rho 的单位；该 observation 不应作为 fixture 或永久参考数据。

## 13. P03 待裁决/冻结的事项

P03 应优先采用以下 [MC] 定义，或发布版本化替代；后续实现者不得自行选择：

1. ICR/OR 默认采用 `intrinsic_index_*`，并并列保留 `intrinsic_forward_pv_*`；不设单一 `intrinsic_value`。
2. 价格字段永远是 `ask_btc` entry、`bid_btc` liquidation、`mark_btc` theoretical；三套 DCE/ICR/OR 不可合并。
3. `premium_usd = premium_btc * index_price_usd`；Black-76 USD PV 除 index 转 BTC，不除 forward。
4. 原始 API Greeks、同 basis 本地模型 Greeks、BTC-NAV sensitivities 是三个字段域；尤其 API/standard delta 不等于 BTC quote derivative。
5. `theta_effective_horizon` 必须显式；`T<1d` 不显示/比较为 one-day theta。
6. GCE 是并列候选系列，全部含 `side` 与 `greek_basis`；不在 domain contract 选最终 GCE。
7. NAV 至少有 `mark` 和 `liquidation` BTC/USD 两套，缺任何价格 leg 即为 unavailable，绝不用 fallback。
8. roll 现金流以 old bid/new ask/fee/rounded quantity 精确对账；release 进入显式 reserve，不自动复投。
9. Utility improvement 与 BTC cost 独立 gate，直至定义 BTC certainty-equivalent Utility。
10. stress 一律记录 `forward_policy`、absolute vol-point IV shock、`smile_dynamics` 和跨到期 policy。

## 14. 已确认与待验证清单

### 已确认到可进入 P03 的项目

- inverse BTC Call 的到期 BTC/USD payoff 及 1 BTC contract multiplier；
- index intrinsic 与 forward discounted intrinsic 的不同经济含义；
- Black-76/BS USD price 与 BTC quote 的 index conversion；
- Ask/Bid/Mark 的不可互换性；
- ICR/OR/DCE 的三 price-side 版本；
- mark/liquidation 双 NAV、equal-contract/equal-standard-delta roll 及 BTC cash reconciliation；
- Utility 与 BTC cost 不可直接相减；
- pressure reprice 必须将 forward、IV、time 和 smile 假设显式化；
- API theta 的 `min(1 day, lifetime)` 官方规则。

### 进入实现前仍需真实响应验证的项目

- API `delta` 到底用 spot/index 还是 forward derivative；
- API gamma 的精确 index/underlying price basis与小数精度；
- API theta、vega、rho 的 USD/BTC numeraire 与 1.00/1 point scale；
- `underlying_price` 与 `underlying_index` 在真实 ticker 的 forward/synthetic-forward 语义和 timestamp；
- bid/ask amount、open interest、stats volume 的数量单位（由 P01 拥有）；
- option fee 的计算基数/上限，以及 tick-size-step、min trade quantity的实际精确执行规则；
- 真实市场在 deep ITM/near-expiry 时 bid 小于 index intrinsic 的频率及应有的 quality policy。
