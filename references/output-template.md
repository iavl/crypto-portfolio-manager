# User-facing Output Template

The report is rendered from a finalized immutable `ReportPacket`. Numeric
scores, weights, actions, approved amounts, execution zones, historical
changes, and risk flags are authoritative Python outputs. The report model may
explain them in Chinese but must not recompute, alter, or invent them.

Default language: Chinese. Keep asset tickers and metric names in English.

## 1. 结论

Start with the actual decision in 2–5 concise bullets, for example:

- 当前市场状态：`DEFENSIVE (MEDIUM confidence)`
- 本轮建议：`NO TRADE` / deploy only part of available cash / rebalance specific assets
- 最优先动作
- Stablecoin target after actions

### 结论依据

After the bullets, give a compact portfolio-level bridge:

```text
证据 → 事实含义 → 组合约束 → 风险门 → 调仓阈值 → Action
```

Name the matching Evidence IDs, sources, observed times, collection status,
and the effect on confidence or trade eligibility. State the current-versus-
target deviation, active threshold, stablecoin floor, concentration and
turnover constraints. If evidence is missing, say `无法确认` and state the
decision effect; never replace missing evidence with a neutral assumption.
For daily OHLCV-derived metrics, also show the completed-candle close boundary
as `freshness_reference_at` and verify it equals `metadata.completed_through`.

### Debug 报告

Render the finalized failure categories separately when present:

```text
Decision-blocking failures: ReportPacket.decision_blocking_failures
Required scoring data unavailable: ReportPacket.required_scoring_failures
Optional/context data unavailable: ReportPacket.optional_data_unavailable
Provider diagnostics: ReportPacket.provider_operational_failures
```

`SKIPPED` optional/premium metrics belong in the optional/context section, not
the final failure table. A provider error code remains visible in provider
diagnostics even when the affected metric is non-blocking.

### 本轮数据抓取失败明细

Render every item from `ReportPacket.failed_data_fetches` using this table. The
list is metric-centric: one row per final `(asset/scope, metric)` result, with
matching provider attempts shown as one compact ordered path under the failure
reason when needed.

| 资产/范围 | Metric | 最终状态 | 阶段 / Provider | 错误码 | 失败原因 | 程序失败日志 | 最近可用数据 | 决策影响 |
|---|---|---|---|---|---|---|---|---|

If the list is empty, write `本轮没有最终抓取失败的数据。` For a `STALE`
refresh failure, write `当前刷新失败；最近可用数据为 <timestamp>，因此本轮按 STALE 处理。`
Use the structured reason, error code, stage/provider, timestamp, and decision
effect exactly as supplied by the packet. `SKIPPED` and `NOT_APPLICABLE` are not failed fetches
and must not appear in this subsection; keep them in section 9.

When multiple attempts belong to one final metric, keep them under the same
row, for example: `尝试路径：CoinGecko HTTP_429 → Coin Metrics Community PROVIDER_UNSUPPORTED`.
For each attempt, show `exception_class`, `detail`, and the bounded `log` when
present. If no direct program log was captured, write `未捕获直接日志` and keep
the structured reason unchanged.

### 本轮脚本执行异常

Render every item from `ReportPacket.script_failures`:

| 脚本 | 状态 | Exit code | 日志来源 | 直接失败日志 |
|---|---|---:|---|---|

If the list is empty, write `本轮没有脚本执行异常。` The log must be the
sanitized, bounded `stderr` excerpt from `scripts/run_with_debug.py`, or the
captured `stdout`/launcher error when stderr is empty. Do not treat a successful
script with ordinary warnings as an execution failure.

### Excluded holdings

If a snapshot contains an asset from `policy.universe.excluded`, show it as
`EXCLUDED / UNMANAGED` with its supplied quantity/value when available. Keep it
out of scored asset, target-allocation, and rebalance-recommendation tables;
exclusion is not an automatic sell signal. Pass-1 diagnostics may separately
show `PENDING_EXTERNAL_RESOLUTION`, but a final portfolio report requires
`pending_external_resolution == 0`.

## 2. 组合诊断

Include:

- total portfolio value;
- stablecoin share;
- configured core share;
- satellite share;
- current drawdown when known;
- major concentration issue.

When position cost data is available, also include:

### 当前持仓收益

| 资产 | 数量 | 当前价 | 平均成本 | 当前价值 | 持仓成本 | 未实现盈亏 | 持仓收益率 | 仓位占比 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|

Rows with unknown cost show `--` for cost, unrealized P&L, and Position
return. Stablecoin/cash rows still contribute to portfolio value and the
stable sleeve. Below the table, report:

- 已知成本仓位的未实现盈亏;
- 已知成本仓位的加权未实现收益率;
- 成本数据覆盖率 (`pnl_value_coverage_ratio`);
- reported total and visible-value coverage when the screenshot is partial.

Position unrealized return is not Portfolio NAV Return and must not be called
`总收益`.

## 3. 市场状态

Explain the regime using only the most decision-relevant evidence:

- BTC trend;
- volatility/drawdown;
- dominance/breadth;
- flows;
- major current events.

Separate fact from judgment.

Use explicit labels such as `事实` and `判断`. For a phrase such as
“整体是混合状态”, explain that market breadth and core-asset trend disagree,
identify the measured breadth/trend evidence, and state that this lowers
confidence for broad satellite additions rather than automatically creating a
sell signal.

### 术语解释与决策影响

Add this subsection only for terms used in the report that could be
misunderstood. Keep entries short and use this format:

| 术语 | 含义 | 本轮决策影响 |
|---|---|---|
| `MATERIAL_EVENT_FOUND` | Scanned sources contain a material proposal or announcement. It is not proof of an exploit, approval, or execution. | State whether it lowers confidence, blocks an increase, supports HOLD, or creates a concrete REDUCE/EXIT trigger. |
| `协议提案/升级活动` | An Ethereum protocol roadmap, upgrade, or developer-economics proposal. | State the affected risk assumption and what confirmation would change the Action. |
| `治理提案/风险参数活动` | An Aave governance proposal or risk-parameter change under discussion. | State why it does or does not justify new exposure, reduction, or continued HOLD. |
| `STALE` / `FAILED` / `SKIPPED` | Old evidence / attempted collection without usable evidence / intentionally omitted decision-active optional evidence. | State the affected factor, confidence, and trade eligibility. |
| `Position P&L` | Remaining-position unrealized P&L based on usable cost basis. | Do not call it Portfolio NAV Return or use it as a buy signal. |
| `NAV Return` | Cash-flow-adjusted portfolio performance. | No disclosure is `ASSUMED_NONE` and `FINAL`; mark it `PROVISIONAL` only for explicit unresolved cash flows. |
| `成本数据覆盖率` | Current-value share of positions with usable cost data. | Explain how much of the Position P&L is known. |

Use the actual source term when it is more precise. Do not call a proposal a
“治理案件” or a security incident unless the Evidence explicitly supports
that classification.

## 4. 单币评估

先给出概览表：

| Asset | Score | Confidence | Trend | Fundamentals | vs BTC | Thesis |
|---|---:|---|---|---|---|---|

Then use this decision chain for every risk-bearing asset with an explicit
`HOLD`, `WAIT`, `INCREASE`, `REDUCE`, or `EXIT`, and for each major asset even
when its action is unchanged:

```text
Evidence → Factor Score → Weighted Score/Confidence → Market Regime
→ Target Allocation → Risk Gate → Current vs Target → Rebalance Threshold
→ Action
```

For each such asset, include:

### ETH — HOLD

```text
Asset Score: 73.4
Data coverage: 90%
Confidence: HIGH
```

| Factor | Policy weight / effective weight | Score | Key evidence | Effect |
|---|---:|---:|---|---|
| trend | 30% / 30% | 82 | evidence ID, source, observed time, concise fact | ++ |
| valuation | 15% / 15% | 61 | ... | + |
| fundamentals | 20% / 20% | 78 | ... | ++ |
| onchain | 10% / 10% | 70 | ... | + |
| capital_flows | 10% / 10% | 66 | ... | + |
| relative_strength_btc | 15% / 15% | 54 | ... | - |

Use the canonical factor names from `config/policy.json`. Show the fixed
profile weight, effective factor score after reliability shrinkage, availability,
and reliability; v2 weights are not renormalized when factors are missing. Every
key evidence statement must come from a matching persisted `Evidence` record
and identify its evidence ID, source, and observed time; never fill a failed,
stale, or conflicting metric with an invented value.

Show event risk separately as a typed gate state and evidence-backed reasons;
never add it as a seventh base-score row.

Below the factor table, include this compact decision bridge:

- **支持证据**：the strongest positive factors and their evidence.
- **反对证据 / 风险**：the strongest negative, missing, stale, or conflicting
  factors and their effect on confidence.
- **组合层面约束**：current weight, target weight, deviation, active
  rebalance threshold, regime envelope, stablecoin floor, concentration, and
  funding/turnover constraints that matter.
- **为什么是这个 Action**：explain why the chain ends in `HOLD`/`WAIT`,
  `INCREASE`, `REDUCE`, or `EXIT`, rather than mapping the score directly to a
  trade. If a high score still produces `HOLD`, state the deviation and
  threshold explicitly and explain any retained stablecoin optionality or
  turnover concern.
- **什么会改变建议**：link to the concrete invalidation/catalyst in section
  8.

Make each bridge distinguish four layers: the observed fact, its bounded
meaning, the portfolio-level constraint, and the resulting Action. A high score
does not override missing critical evidence, a regime envelope, a stablecoin
floor, concentration, or the active rebalance threshold.

For material assets, also show the most important historical metric change:
current value, previous value, absolute/percentage change, recent trend, and
the observation IDs used. Explain any recommendation change with the changed
metrics, confidence change, regime change, and portfolio constraint.

## Positioning & Cycle Context

Show the finalized compact overlay values without raw social posts or dense
series:

| Asset | Positioning | Bias | Risk | Social | Decision Effect |
|---|---|---|---|---|---|

For BTC, show `Halving Context`, `Market Cycle State`, `Cycle Risk`,
`Confidence`, and key non-clock drivers. State explicitly that cycle context is
not a deterministic top/bottom forecast. State the effective deployment cap and
whether the target is unchanged, immediate deployment is reduced, or the
remaining approved amount is `WAIT`/unallocated.

Example:

```text
Target unchanged. Positioning is LONG_CROWDED/HIGH; immediate deployment is
capped to 50% and the remaining amount stays WAIT. The halving clock is context
only and does not create this action.
```

For a satellite, also state the BTC opportunity cost: relative performance,
relative risk/volatility, current satellite exposure, applicable regime cap,
and why the incremental risk is or is not better than allocating the same
capital to BTC. For `NO_TRADE`, provide the same evidence-to-decision bridge
at portfolio level, including regime, stablecoin, risk-gate, threshold, and
funding reasons. Stablecoin/cash rows use the stable-sleeve evidence and
constraints instead of fabricated asset-specific factors.

For `NO_TRADE` or `WAIT`, render the deterministic attribution from the packet:

```text
Primary reason: <primary_reason>
Secondary reasons: <secondary_reasons>
Gates: score=<...>, confidence=<...>, regime=<...>, event=<...>, liveness=<...>, BTC-relative=<...>, allocation-delta=<...>, rebalance=<...>, execution=<...>
```

Do not replace these reason codes with model judgment.

## 5. 当前仓位 vs 目标仓位

| Asset | Current | Target | Deviation | Action | Priority |
|---|---:|---:|---:|---|---|

Targets should sum to approximately 100%.

## 6. 操作计划

For each approved recommendation:

| Asset | Action | Amount | Target weight after trade | Execution |
|---|---|---:|---:|---|

For staged orders, give zones and tranche percentages, not false exact precision.

For staged buys, use the validated execution plan:

| Asset | Tranche | Price Zone | USD | Est. Qty | Structural Basis |
|---|---:|---|---:|---:|---|
| ETH | 1 | 结构区间 | 600U | ~0.157 ETH | MA50 + confirmed swing support |

When available, add:

```text
Volume Profile：4H / 120D / MEDIUM
POC：$...
VAL / VAH：$... / $...
重要 HVN：$...
依据：MA50 + confirmed swing + 60D POC + 120D HVN
说明：历史成交密集区代理，不是所有持币者真实成本；LVN 仅作过渡区背景。
```

Label quantity as approximate and use the zone midpoint as the reference price.
Also report:

- Approved total capacity;
- Staged amount (`planned_amount_usd`), which is a recommendation rather than a fill;
- Still unallocated / reserved amount;
- Technical confidence;
- Data confidence and setup quality;
- Spot `observed_at`, OHLCV observation freshness, and `ohlcv_hash` when available;
- Structured invalidation review trigger.

If new capital is supplied, explicitly state:

- available capital;
- amount staged now;
- amount retained as cash / still unallocated;
- reason not to deploy the remainder.

## 7. 风险检查

State:

- stablecoin floor after recommendation;
- whether portfolio risk is rising or falling;
- key concentration/beta risk;
- whether the portfolio appears consistent with the configured drawdown risk budget (15% by default);
- note that the risk budget cannot guarantee a loss ceiling.

## 8. 什么情况会改变建议

List 2–5 concrete invalidation/catalyst conditions.

## 9. 数据质量

State:

- analysis timestamp;
- the compact `Data Collection Summary`: requested metrics, counts for each
  status, critical failures, weighted evidence coverage, and decision
  confidence;
- missing or stale factors;
- score confidence impact;
- any conflicting data;
- screenshot cross-check status and material mismatches;
- whether visible rows reconcile with the reported total.

The detailed `Data Collection Log` is shown during evidence acquisition. The
final report must still list every `FAILED`, `STALE`, or `CONFLICT` metric and
its scoring/decision effect; do not silently omit collection failures. Explain
which missing evidence lowered confidence, blocked an increase, forced
`HOLD_ONLY`, or left the decision `NO_TRADE`. Optional and premium `SKIPPED`
items must state that they were excluded from applicable coverage.
Detailed final fetch failures are shown in `本轮数据抓取失败明细`; keep this
section as the aggregate data-quality report rather than duplicating that table.

Render `ReportPacket.optional_data` separately as `本次未采集的可选数据` with
asset, metric, reason/provider/history issue, and the explicit effect
`non-blocking`. Optional `SKIPPED` values are excluded from applicable coverage;
they must not be promoted to final required failures or silently counted as
success.

For `FULL_REVIEW`, compare the previous and current Position P&L by asset and
show the change in percentage points when both cost bases are usable. For
`SNAPSHOT_REVIEW`, always show the current table when cost observations are
present.

## 10. Final action line

End with one unambiguous sentence such as:

> 本轮执行建议：ETH 分三档增持 1,500U；BTC 持有；AAVE 减持 800U；剩余 2,700U 保持为稳定币。

or:

> 本轮执行建议：NO TRADE，5000U 全部保留为稳定币，等待风险收益比改善。

## 11. Confidence and performance chain

Finalized reports show Data Confidence dimensions, Regime Confidence, Decision
Confidence, raw/final scores, bands, caps, evidence IDs, and their action
effects. They also show NAV status, `performance_finality`,
`cash_flow_resolution_status`, cash-flow-adjusted return, current/max drawdown,
BTC benchmark/excess return, and EventScanner state. Only
`CONFIRMED_NONE`/`CONFIRMED_AMOUNT` or `BASELINE_RESET` can produce
`performance_finality=FINAL`; an unresolved historical cash flow keeps the
packet `PROVISIONAL`/`BLOCKED`. The writer never recomputes these values.
