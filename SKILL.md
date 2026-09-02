---
name: crypto-portfolio-manager
description: Use this skill for medium- to long-term, spot-only crypto portfolio review, risk assessment, allocation, rebalancing, and staged buy/sell planning. The default posture is conservative-balanced, BTC-benchmarked, and explicitly allows NO TRADE.
---

# Crypto Portfolio Manager

Use this Skill for a 6–12 month portfolio horizon. It supports research and
proposed decisions; it never places trades, requests trading permissions, or
introduces leverage, futures, perpetuals, or margin.

## Policy and references

Load the canonical machine-readable policy from `config/policy.json` before
analysis. A snapshot may provide explicit overrides for its configuration;
reject invalid or conflicting values. Use these references for qualitative
judgment and user-facing decisions:

- `references/investment-policy.md`
- `references/scoring-model.md`
- `references/risk-model.md`
- `references/decision-rules.md`
- `references/data-sources.md`
- `references/output-template.md`

The canonical policy controls asset groups, risk limits, benchmarks, scoring
weights, regime envelopes, rebalance thresholds, and technical execution
constants. Python models and engine modules perform validation and mathematics;
the Agent supplies current evidence, bounded qualitative judgments, and
explanations.

## Binance screenshot intake

When the user provides the standard Binance wallet-overview screenshot, do
these steps before portfolio analysis:

1. Inspect the visible asset rows and reported total.
2. Extract each visible row's symbol, quantity, current value, current price,
   average cost price, and displayed floating P&L.
3. Treat `--` as `null`; never convert an unknown cost or P&L to zero.
4. Require Binance display currency to be USD. Do not put CNY values in
   `*_usd` fields.
5. Pass the extracted fields to the deterministic snapshot normalizer and
   Position P&L engine. Do not calculate P&L manually in the report.
6. Check quantity × price, quantity × average cost, and value − cost against
   the visible values. Clarify material mismatches before persistence.
7. Assess visible-value coverage against the reported total; do not treat
   visible rows as the whole portfolio when the screenshot is partial.
8. Continue the review and include the Position P&L table and known-cost
   coverage summary in the Chinese output.

The Binance row convention is: the top number in `资产价格 / 成本价` is the
current price and the bottom number is average cost; the top number in
`数量` is quantity and the bottom number is current position value. A
displayed `$0.00` current price with positive quantity and value is rounded
display data, so the engine uses value ÷ quantity and records a note.

## Ordered workflow

1. Parse the current portfolio.
2. Load the canonical policy.
3. Validate the snapshot and apply only explicit policy overrides.
4. Load historical snapshots, decisions, and the previous thesis before
   fetching new evidence when local history is available. Use the structured
   Position P&L history context for latest/previous asset returns.
5. Build cash-flow-aware NAV and drawdown history.
6. Select `SNAPSHOT_REVIEW`, `FULL_REVIEW`, or `EVENT_REVIEW`; recommend a
   `FULL_REVIEW` when at least 14 days have passed since the last one.
7. Fetch current price, trend, flow, fundamental, on-chain, and event evidence;
   emit a visible `Data Collection Log` for each requested decision-relevant
   metric.
8. Validate evidence completeness and preserve provenance. Never silently omit
   a requested metric: record its collection status and scoring effect.
9. Build `Evidence`, `FactorScore`, and `AssetAssessment` records.
10. Run deterministic scoring and missing-factor coverage checks; publish the
    collection summary with weighted coverage and resulting confidence.
11. Run the regime engine.
12. Run the allocation engine.
13. Run the risk gate and stop on `ERROR` violations.
14. Recalculate post-new-cash economic weights.
15. Run the rebalance engine.
16. Reconcile executable trade dollars.
17. Evaluate `NO_TRADE` before proposing a transaction.
18. After a rebalance approves an `INCREASE` amount, obtain a timestamped
    `SpotPrice` and normalized completed daily OHLCV.
19. Validate observation freshness, UTC-day cadence, calendar coverage, and
    provenance; build `TechnicalSnapshot`, evaluate setup quality, run the
    deterministic entry planner, and validate the resulting `ExecutionPlan`
    with `validate_execution_plan`.
20. Bind the plan to exactly one matching approved `RebalanceAction`, create
    `execution_technical` evidence, and cache the normalized OHLCV by hash
    before persistence.
21. Produce the Chinese user-facing report using
    `references/output-template.md`. Every normal review shows Position P&L
    when available, using `平均成本`, `持仓成本`, `当前价值`, `未实现盈亏`,
    `持仓收益率`, and `成本数据覆盖率`; do not label it total portfolio
    return. `FULL_REVIEW` also compares the prior/current return by asset in
    percentage points; `SNAPSHOT_REVIEW` shows the current table without
    treating cost basis as a buy signal.
22. Persist only validated snapshots, decisions, execution plans, and complete
    evidence. Never
    rewrite prior rationale or mark a trade executed without explicit
    confirmation or a trusted later read-only snapshot.

When the user explicitly requests a dry run or no persistence, do not append
runtime state.

## Visible evidence collection

During workflow steps 7–10, show a compact `Data Collection Log` to the user as
metrics complete, either one entry at a time or in short batches. This is
mandatory execution telemetry, not an internal note. Log every requested
decision-relevant metric, including failures and metrics that do not apply; do
not dump every raw candle or data row.

Use these exact statuses:

```text
SUCCESS | FAILED | STALE | CONFLICT | NOT_APPLICABLE
```

Use this format:

```text
[DATA] <asset/scope> <metric> <STATUS> <value or short summary>
       source: <source or N/A>
       observed_at: <UTC timestamp or N/A>
       fetched_at: <UTC timestamp or N/A>
       reason: <required for FAILED/STALE/CONFLICT/NOT_APPLICABLE>
       scoring_effect: <coverage, confidence, or entry effect>
```

Use `NOT_APPLICABLE` when a metric is not meaningful for the asset (for
example, TVL for BTC), and `FAILED` when an applicable metric has no
sufficiently current and reliable result. `STALE` and `CONFLICT` must state
which data is old or disagreeing. A critical failure—current price, recent
trend history, portfolio value, or unresolved material security status—must
say `CRITICAL DATA FAILURE` and `high-conviction trade blocked`.

At minimum, request and log these applicable metrics:

- Market context: BTC spot price, MA50/MA100/MA200, 30D/90D trend,
  drawdown, volatility, dominance/breadth, stablecoin liquidity trend,
  relevant ETF or other capital flows, and major current events.
- Each held or considered risk asset: current price, 30D/90D/180D return,
  MA50/MA100/MA200, drawdown/historical position, asset-appropriate
  fundamentals, on-chain activity, capital flow, 1M/3M/6M performance versus
  BTC, token unlock/supply events, and security/governance/regulatory events.

After collection, show a compact summary and use weighted scoring coverage—not
the raw number of log lines—as `Overall evidence coverage`:

```text
Data Collection Summary
Requested metrics: <N>
SUCCESS: <N>  STALE: <N>  FAILED: <N>
CONFLICT: <N>  NOT_APPLICABLE: <N>
Critical failures: <N>
Overall evidence coverage: <percent>
Decision confidence: <HIGH|MEDIUM|LOW>
```

Collection statuses are presentation telemetry. They do not replace the
validated `Evidence` records or change the persistent `freshness` contract;
carry source, observed/fetched timestamps, value/summary, confidence, and
factor links into the canonical records.

## Deterministic staged execution

The portfolio engine remains authoritative for total USD exposure:

```text
rebalance approved amount -> timestamped SpotPrice + completed daily OHLCV
-> time/cadence/provenance checks -> TechnicalSnapshot -> setup quality
-> structural zones -> tranches and estimated quantities -> validated plan
```

Use at least 120 completed daily candles, preferably 365, plus a timestamped
spot observation and reliable volume where available. When adequate OHLCV exists, do not
manually invent moving averages, ATR, swing levels, zone prices, tranche
arithmetic, or estimated quantities. The technical engine may return `WAIT` or
leave part of the approved amount unallocated, but it can never increase the
approved amount or place an order. v1 generates pullback plans only:
`BREAKOUT` returns `WAIT` and `MIXED` is rejected until those semantics are
implemented.

## Accounting and missing data

Use `crypto_portfolio.engine.ledger` for unitized NAV, cash-flow-adjusted
return, current drawdown, and maximum drawdown. An external cash flow attached
to a snapshot occurs immediately before that snapshot valuation; the ledger
backs out the flow at the pre-flow NAV. Deposits and withdrawals change units,
not investment NAV. The primary benchmark is 100% BTC buy-and-hold; the
secondary benchmark is 70/30 BTC/ETH buy-and-hold with flows allocated 70/30.
Benchmark periods and cash-flow treatment must match the portfolio period.

Stablecoins and cash are one allocation sleeve. Preserve their existing
composition where possible and do not create stablecoin-to-stablecoin trades
merely to satisfy a preferred symbol.

Critical missing data—current price, recent trend history, portfolio value, or
an unresolved material security event—precludes a high-conviction entry.
Missing non-critical scoring factors may be removed and renormalized by the
scoring engine, but confidence is capped by actual coverage. Unknown factor
keys fail validation, and missing BTC-relative evidence makes a satellite
`HOLD_ONLY` rather than positive evidence for a new allocation.

## Runtime data boundary

Real portfolio data must remain outside Git. Append-only state defaults to
`~/.local/share/crypto-portfolio-manager/` and can be redirected with the
`CRYPTO_PORTFOLIO_DATA_DIR` environment variable. Repository `data/` is for
fake fixtures and `.gitkeep` files only.

For a compatibility normalization check, run:

```bash
python scripts/portfolio_snapshot.py path/to/fake-snapshot.json
```

If no prior history exists, establish the baseline with an initial validated
snapshot and current review; do not claim historical performance without
sufficient history.
