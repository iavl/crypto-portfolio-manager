# User-facing Output Template

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

Suggested table:

| Asset | Score | Confidence | Trend | Fundamentals | vs BTC | Thesis |
|---|---:|---|---|---|---|---|

Keep the thesis concise and identify the strongest positive and negative factor.

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
- missing or stale factors;
- score confidence impact;
- any conflicting data;
- screenshot cross-check status and material mismatches;
- whether visible rows reconcile with the reported total.

For `FULL_REVIEW`, compare the previous and current Position P&L by asset and
show the change in percentage points when both cost bases are usable. For
`SNAPSHOT_REVIEW`, always show the current table when cost observations are
present.

## 10. Final action line

End with one unambiguous sentence such as:

> 本轮执行建议：ETH 分三档增持 1,500U；BTC 持有；AAVE 减持 800U；剩余 2,700U 保持为稳定币。

or:

> 本轮执行建议：NO TRADE，5000U 全部保留为稳定币，等待风险收益比改善。
