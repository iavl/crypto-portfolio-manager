# 工作原理

本文档说明 `crypto-portfolio-manager` 当前的架构、Python 与模型的边界、
数据流、历史记录和可复现性。它描述当前代码，不把未来扩展写成已实现功能。
策略概念和决策原则见[投资策略](../references/investment-strategy.md)。

## 1. 系统总览

`SKILL.md` 负责工作流编排；`AcquisitionManager` 和 `ProviderRouter` 负责
缓存优先的数据获取；规范化、记账、评分、regime、allocation、risk、rebalance
和 execution 由 Python 完成。

```text
截图或结构化 snapshot
    -> Python snapshot 校验与 Position P&L
    -> 历史 context 与 resolved policy
    -> Python metric collection plan
    -> 新鲜 observation / provider cache / structured provider
    -> MetricObservation 与 CollectionEvent
    -> Python Facts 与确定性指标
    -> 受限语义判断
    -> Python score / regime / allocation / risk / rebalance
    -> DecisionReviewPacket
    -> ReportPacket
    -> 中文报告与 append-only 历史
```

系统只提供建议和执行区间，不连接交易下单、提现、杠杆、保证金或自动交易。

## 2. Python 与模型的边界

如果结果可以从结构化数据确定性推导，Python 拥有该结果。模型只处理截图
字段提取、未解决来源检索、有界语义判断、重大事件解释和报告文字。

| 责任 | 所有者 | 说明 |
|---|---|---|
| 截图字段提取 | `LUNA_MAX` | 读取可见 Binance 行，不计算金融结果。 |
| metric 计划 | Python | 从 registry 选择适用 metric key。 |
| provider 获取与规范化 | Python | 负责 route、cache、单位、时间、freshness、provenance。 |
| Position P&L | Python | 计算成本基础、未实现盈亏、收益率和覆盖率。 |
| 指标数学 | Python | MA、ATR、收益、波动率、回撤、Volume Profile。 |
| 语义判断 | `LUNA_MAX` | 解释 fundamentals、event、冲突和不确定性。 |
| 评分、regime、allocation、risk、rebalance | Python | 模型不能覆盖确定性输出。 |
| 重大影响复核 | `SOL`，按 Python predicate | 只做高影响批评，不改金融数学。 |
| 最终报告 | 配置的 report stage | 只能解释 finalized `ReportPacket`。 |

数据包不传递原始网页、完整 OHLCV、完整历史或私有 reasoning。Evidence、
来源、时间和 hash 会保留在规范化记录和 finalized packet 中。

## 3. 策略与资产分类

`config/policy.json` 是 canonical policy，包含 universe、benchmark、stablecoin
floor、drawdown budget、scoring weights、regime limits、rebalance thresholds
和 execution constants。

```text
canonical policy
    -> resolved policy
    -> 每个 symbol 的 core/satellite/stablecoin/cash/other 分类
```

snapshot 可以提供明确的局部 `config` override。解析后会保存 resolved policy
和 policy hash，使历史 decision 不依赖未来 checkout 的配置变化。资产 hint
必须与 resolved classification 一致；重叠分组和冲突 hint 会失败。

## 4. 组合输入

标准 Binance 钱包截图由以下链路处理：

```text
可见截图字段
    -> symbol/quantity/value/price/cost/P&L
    -> PortfolioSnapshot
    -> Position P&L engine
    -> normalized snapshot
```

行布局中价格/成本列的上行是当前价，下行是平均成本；数量列的上行是数量，
下行是当前价值。`--` 成本和盈亏保持未知。正数量配合 `$0.00` 当前价时，
Python 可用 `value / quantity` 作为当前价，并记录四舍五入警告。

Python 校验：

```text
quantity * current price  ≈ current value
quantity * average cost   ≈ cost basis
current value - cost basis ≈ exchange floating P&L
```

小于较大者 `$0.05` 或预期值 `0.5%` 的差异保留为 rounding warning；material
mismatch 不应直接持久化。可见行与 reported total 不一致时，结果保留
visible-value coverage，并说明截图可能不完整。

## 5. 历史与记账

运行时状态默认位于 `~/.local/share/crypto-portfolio-manager/`，可以用
`CRYPTO_PORTFOLIO_DATA_DIR` 修改。

```text
portfolio/snapshots.jsonl
decisions/decisions.jsonl
decisions/status-events.jsonl
metrics/observations.jsonl
metrics/collection-events.jsonl
market-data/sha256/<ohlcv_hash>.json
volume-profiles/sha256/<profile_hash>.json
provider-cache/
```

snapshot 和 decision 是 append-only。状态变更写入独立 status event，不覆盖
旧 rationale。MetricObservation 保留成功的规范化观测；CollectionEvent 保留
所有采集结果，包括失败和 skipped。

仓库只支持当前内部运行时契约。破坏性变更后的不兼容生成状态必须手动重新生成，
不会自动迁移或静默删除用户状态。

现金流调整后的 NAV 使用 unitized NAV。一个 snapshot 附着的外部现金流被视为
发生在该 snapshot valuation 之前。如果 material balance change 没有明确
DEPOSIT/WITHDRAWAL/NONE 分类，NAV 和 benchmark 表现必须标记为 `PROVISIONAL`。

## 6. 指标注册表与采集计划

`crypto_portfolio/metrics_registry.py` 为每个 metric 定义：

- factor、expected type、unit 和 direction；
- freshness window；
- asset scope 与 criticality；
- `SCORING_FACTOR`、`EVENT_RISK`、`POSITIONING_OVERLAY`、`CYCLE_CONTEXT`、
  `EXECUTION_CONTEXT` 或 `STRUCTURAL_RISK` role。

`engine.metric_plan.build_metric_collection_plan()` 在 Python 中选择 metric。
它不会让模型发明 key，也不会给 stablecoin/cash 行请求不适用的资产指标。

采集顺序：

```text
fresh MetricObservation
    -> provider cache
    -> free structured provider
    -> optional API-key provider
    -> 明确的 WebFallbackRequest
```

每个请求必须产生一个结果。缺失、重复、额外结果都会失败。状态含义如下：

- `SUCCESS`：规范化观测可用于该 metric；
- `STALE`：旧 observation 存在，但当前刷新不可用或超出 freshness；
- `FAILED`：应用数据预期存在，但没有可用值；
- `CONFLICT`：来源冲突；
- `NOT_APPLICABLE`：语义上不适用；
- `SKIPPED`：可选或 premium provider 没有配置。

`market.flow_state`、BTC-relative returns、ETH/BTC opportunity ratios、ETH
staking/flow normalization 和 OI/market-cap 等明确依赖图由
Python 派生。派生输入缺失时不会制造中性值。

## 7. Provider 与事件边界

具体 source、字段、metric key 和限制见
[`../references/data-providers.md`](../references/data-providers.md)；来源质量和
方法论见 [`../references/data-sources.md`](../references/data-sources.md)。

Provider 只返回规范化结构化数据。Chain liveness 是特殊的 chain-specific
structured provider，不会回退到普通 Web 搜索。EventScanner 只使用固定
allowlist source catalog；页面内容不能添加 URL、改变范围、执行命令或泄露
secret。

广义市场估值与协议基本面分开处理：

```text
valuation.market_cap / valuation.fdv
    -> CoinGecko
    -> Coin Metrics CapMrktEstUSD fallback for market cap only
    -> Python-derived FDV / market-cap ratio

fundamentals.tvl / fees / revenue / fee-revenue multiple
    -> DeFiLlama
```

### Provider 可靠性

Provider 获取过程会保留清晰可见的失败边界：

```text
request
 -> cache
 -> provider router
 -> HttpClient
 -> normalization
 -> diagnostics
 -> fallback
 -> unresolved evidence
```

`--status` 是离线 readiness 检查。显式执行 `--doctor`、`--probe`、
`--contract` 和 `--smoke` 才会诊断或运行实时获取；这些命令不会改变组合计算。
传输、HTTP、plan、schema、规范化、缓存和断路器失败都会保留为结构化 Provider
诊断。fallback 可以提供可用观测值，但不会从 telemetry 中抹掉原始尝试。

报告生成器绝不会用 Web 或模型推断替换成功的结构化观测。历史估值请求只使用
复盘截止时间及之前的证据。

事件扫描采用两阶段流程：

事件请求的正常运行顺序是：pass 1 生成 EventSourceScanRequest，外部阶段
为每个请求返回一个 EventSourceScanResponse（不可达时也必须返回
reachable=false 和有界错误），pass 2 重新运行 acquisition，随后调用
require_scoring_ready()，最后才进入 scoring。BNB 使用固定的官方
security/governance source catalog，监管仍复用共享 MARKET source。

BTC-relative return 请求会把资产和 BTC 的 market.return_30d/90d/180d
作为一个依赖 cohort 处理。缓存日期不一致时两侧一起刷新/重建；Python
只在同一 venue、quote、completed daily candle 和共同 calendar anchor 上
相减，不接受错位标量。

```text
Python source catalog
    -> EventSourceScanRequest
    -> runtime 访问 allowlisted source
    -> EventSourceScanResponse
    -> Python coverage/materiality/status
```

`MATERIAL_EVENT_FOUND` 表示扫描到重要 proposal/announcement，不等于 exploit、
approval 或 execution。完整覆盖无事件使用
`NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES`；部分来源不可达使用
`INSUFFICIENT_SOURCE_COVERAGE`，不代表绝对安全。

## 8. 确定性决策流程

### Facts 与 factors

Python 从 MetricObservation 构建 compact Facts 和 metric history。趋势、flow
解释、BTC-relative strength 等已有确定性实现；其他需要上下文的 fundamentals、
valuation、event risk 可由模型在 bounded packet 中判断。

当前 policy 的 canonical factor namespace 有八个 key；其中正权重因子由资产
profile 决定：默认非 BTC profile 有六个正权重因子，BTC profile 有四个。

```text
trend
valuation
fundamentals
onchain
capital_flows
relative_strength_btc
btc_valuation
macro_liquidity
```

默认非 BTC profile 将 `btc_valuation` 和 `macro_liquidity` 设为零权重；BTC
profile 使用 `trend`、`btc_valuation`、`capital_flows` 和 `macro_liquidity`
的正权重。完整的策略解释见[投资策略](../references/investment-strategy.md)，
权重和评分语义见[评分模型](../references/scoring-model.md)。

event/security risk 使用独立的 typed gate；positioning 和 BTC cycle 是不计分的
overlay。缺失 factor 保留原权重并通过 reliability 向中性 50 收缩，不能靠
消失的数据抬高分数。
关键 current price、trend、portfolio value 或材料安全事件缺失时，高置信新增
仓位被阻止。

当前 Policy 对 ETH 额外分组展示 monetary economics、staking security、L2/DA
settlement、DeFi/stablecoin 和 developer/ecosystem 证据；这些只是语义分组，
数值仍由 Python 的 MetricObservation 和确定性派生函数拥有。ETH 核心门控与
70/30 BTC/ETH 核心袖套锚点独立于 base score，不能把 core 分类当作目标保证。

### Regime

Python 结合 BTC trend、volatility、portfolio drawdown、flows、breadth 和
systemic event risk，输出 `NORMAL`、`DEFENSIVE` 或 `CAPITAL_PRESERVATION`。
单一 noisy indicator 不应切换 regime；drawdown floor 和严重事件可以形成
硬性下限。

### Allocation、risk 与 rebalance

组合配置顺序为：

```text
regime -> score -> confidence -> risk tier -> volatility/correlation
       -> stable floor -> concentration cap -> target
```

Risk gate 检查 target sum、stablecoin floor、core minimum、satellite envelope、
single-asset cap、chain liveness 和 overlays。Rebalance 使用 post-new-cash
经济金额，按 2pp/4pp/8pp 阈值决定 HOLD、WATCH 或交易优先级。交易金额必须
大于零；HOLD/WAIT/NO_TRADE 的金额必须为零。

## 9. 技术执行

只有 rebalance 先批准 `INCREASE`，才进入技术层。技术层使用带 timestamp 的
`SpotPrice` 和 completed `1D` OHLCV，优先至少 200 根日线、最好 240 天，
并检查 freshness、cadence、calendar coverage 和 provenance。

技术 snapshot 计算 MA20/50/100/200、execution-specific calendar
30D/90D/180D return、ATR14、
realized volatility、relative volume、drawdown 和 confirmed swings。

Volume Profile 优先使用同一流动 spot venue 的 completed `1H`/`4H` bars，按
`(high + low + close) / 3` 分配 volume，输出 POC、VAL、VAH、HVN 和 LVN。它
是历史成交量集中度 proxy，不是 holder cost basis；LVN 只能作背景。

当前只生成 `PULLBACK`。`BREAKOUT` 返回 `WAIT`，`MIXED` 被拒绝。技术层可以
stage less 或返回 WAIT，但不能增加 approved USD 或提交订单。

## 10. 数据包与报告

主要 handoff packet：

- `AssetFactorPacket`：单资产 Facts、coverage、previous assessment、Evidence ID；
- `DecisionReviewPacket`：资产摘要、current/target weights、actions、risk flags、
  missing data 和 overlays；
- `ReportPacket`：finalized regime、scores、weights、actions、amounts、zones、
  historical changes、risk flags、data quality、overlay 结果、最终数据抓取失败
  和脚本执行失败日志。

Packet 是 frozen/validated model。报告阶段可以解释 finalized values，但不能
重新计算或修改 score、weight、amount、zone、Action 或 risk flag。报告中的
决策依据应按以下顺序表达：

```text
Evidence -> fact meaning -> portfolio constraint -> risk gate
         -> rebalance threshold -> Action
```

## 11. 失败与安全模式

系统向减少行动性方向失败：

- `FAILED`、`STALE`、`CONFLICT` 降低 coverage/confidence；
- chain liveness transport failure 不等于 HALTED；
- `NOT_APPLICABLE` 不进入适用 coverage；
- optional/premium `SKIPPED` 保持可见且不进入适用分母；
- critical missing data、material conflict 或低 confidence 可阻止新增仓位；
- risk gate `ERROR` 阻止不安全 target；
- technical freshness、coverage、setup quality 或 CAPITAL_PRESERVATION 可以
  返回 `WAIT` 或保留未部署资金；
- `NO_TRADE` 是合法结果，不是系统错误。
- Provider traceback 和脚本 `stderr` 只以脱敏、限长的 Debug 报告上下文保留；
  没有直接日志时保持结构化失败原因，不猜测日志内容。

核心原则：

```text
不确定性降低行动性，而不是被猜测抹掉。
```

## 12. 实现索引

| 区域 | 实现 |
|---|---|
| Policy | `config/policy.json`, `crypto_portfolio/models/policy.py` |
| Metric registry | `crypto_portfolio/metrics_registry.py` |
| Collection | `crypto_portfolio/engine/metric_plan.py`, `crypto_portfolio/acquisition.py` |
| Normalization/history | `crypto_portfolio/engine/metric_normalization.py`, `crypto_portfolio/state/metrics.py` |
| Events | `crypto_portfolio/events/sources.py`, `crypto_portfolio/events/scanner.py` |
| Factors/scoring | `crypto_portfolio/engine/factors/`, `crypto_portfolio/engine/scoring.py` |
| Regime/risk/rebalance | `crypto_portfolio/engine/regime.py`, `risk.py`, `rebalance.py` |
| Accounting | `crypto_portfolio/engine/ledger.py`, `benchmark.py`, `position_pnl.py` |
| Technical/execution | `crypto_portfolio/engine/technical.py`, `volume_profile.py`, `entry.py` |
| Packets | `crypto_portfolio/models/*packet.py`, `crypto_portfolio/engine/report_packet.py` |
| Providers | `crypto_portfolio/providers/` |
| Runtime state | `crypto_portfolio/state/` |

## 信心层级

确定性路径是 `Data Confidence -> Regime Confidence -> Decision Confidence`。
每一层都会保留有界分数、等级、原因、上限和证据 ID。缺失数据保持缺失；即使
regime 标签为正常，也不会因此获得增加风险的权限。

运行检查：

```bash
python3 -m unittest discover -s tests -v
ruff check .
python3 -m compileall crypto_portfolio scripts
```

## 本轮数据获取修复

Metric history 由 `crypto_portfolio/metric_history_requirements.py` 显式拥有：
30D/90D/180D 请求分别使用约 45D/105D/195D，365D 使用约 380D，
`btc_valuation.mvrv_zscore` 使用 `FULL_AVAILABLE`。240D 只属于执行层
OHLCV，不会截断货币、tokenomics 或 valuation 历史。

Coin Metrics 在一个 bundle 中按 metric 保留成功值和独立 diagnostics；
Community 是 no-key 首选，Pro 只是可选 fallback。数值指标使用
`STRUCTURED_ONLY`，不会被转换成随机 Web fallback。ETH 30D burn 使用
Ultrasound 的 `d30.rate.eth_per_minute`，Etherscan 只作为可选当前 supply/
cumulative burn 来源；缺少同源、日期对齐的累计快照时不制造窗口值。

EventScanner 可注入 `StructuredEventTransport`，使用 bounded GitHub、RSS/Atom、
Discourse JSON 和 allowlisted BNB Governor RPC。Transport 只发现并去重候选，
`LUNA_MAX` 只判断候选 materiality；同 authority 的 URL 以 source group 完成
覆盖，独立安全域仍分别保留。
