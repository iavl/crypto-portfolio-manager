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
| trend | 25% / 27.8% | 82 | evidence ID, source, observed time, concise fact | ++ |
| valuation | 20% / 22.2% | 61 | ... | + |
| fundamentals | 20% / 22.2% | 78 | ... | ++ |
| onchain | 10% / 11.1% | 70 | ... | + |
| capital_flows | 10% / 11.1% | 66 | ... | + |
| relative_strength_btc | 10% / 11.1% | 54 | ... | - |
| event_risk | 5% / 5.6% | 85 | ... | + |

Use the canonical factor names from `config/policy.json`. Show the policy
weight and the renormalized effective weight when factors are missing. Every
key evidence statement must come from a matching persisted `Evidence` record
and identify its evidence ID, source, and observed time; never fill a failed,
stale, or conflicting metric with an invented value.

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
Volume Profile：4H / 180D / MEDIUM
POC：$...
VAL / VAH：$... / $...
重要 HVN：$...
依据：MA50 + confirmed swing + 90D POC + 180D HVN
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
- whether the portfolio appears consistent with the configured drawdown risk budget (20% by default);
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
its scoring/decision effect; do not silently omit collection failures.

For `FULL_REVIEW`, compare the previous and current Position P&L by asset and
show the change in percentage points when both cost bases are usable. For
`SNAPSHOT_REVIEW`, always show the current table when cost observations are
present.

## 10. Final action line

End with one unambiguous sentence such as:

> 本轮执行建议：ETH 分三档增持 1,500U；BTC 持有；AAVE 减持 800U；剩余 2,700U 保持为稳定币。

or:

> 本轮执行建议：NO TRADE，5000U 全部保留为稳定币，等待风险收益比改善。
