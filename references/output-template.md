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

For each actual trade:

| Asset | Action | Amount | Target weight after trade | Execution |
|---|---|---:|---:|---|

For staged orders, give zones and tranche percentages, not false exact precision.

If new capital is supplied, explicitly state:

- available capital;
- amount deployed now;
- amount retained as cash;
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
- any conflicting data.

## 10. Final action line

End with one unambiguous sentence such as:

> 本轮执行建议：ETH 分三档增持 1,500U；BTC 持有；AAVE 减持 800U；剩余 2,700U 保持为稳定币。

or:

> 本轮执行建议：NO TRADE，5000U 全部保留为稳定币，等待风险收益比改善。
