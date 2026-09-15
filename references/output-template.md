# User-facing Output Template

The report is rendered from a finalized immutable `ReportPacket`. Numeric
scores, weights, actions, approved amounts, execution zones, historical
changes, and risk flags are authoritative Python outputs. The report model may
explain them in prose but must not recompute, alter, or invent them.

Default language: English. Keep asset tickers and metric names verbatim.

## 1. Conclusion

Start with the actual decision in 2–5 concise bullets, for example:

- Current regime: `DEFENSIVE (MEDIUM confidence)`
- This round's recommendation: `NO TRADE` / deploy only part of available cash / rebalance specific assets
- Top-priority action
- Stablecoin target after actions

### Decision basis

After the bullets, give a compact portfolio-level bridge:

```text
Evidence → fact meaning → portfolio constraint → risk gate → rebalance threshold → Action
```

Name the matching Evidence IDs, sources, observed times, collection status,
and the effect on confidence or trade eligibility. State the current-versus-
target deviation, active threshold, stablecoin floor, concentration and
turnover constraints. If evidence is missing, say `cannot be confirmed` and
state the decision effect; never replace missing evidence with a neutral
assumption. For daily OHLCV-derived metrics, also show the completed-candle
close boundary as `freshness_reference_at` and verify it equals
`metadata.completed_through`.

Report compliance from the frozen post-action projection, not the strategic
target: if the recommended actions leave the stable sleeve below its floor or
a concentration cap unmet, carry the `*_UNRESOLVED` constraint verbatim and
say the risk gap remains open. Reproducing the same direction three times on
the same evidence within one day is "repeat calculation agreement", not three
independent confirmations; never label it as such. Every number in the report
comes from the validated packets — scores, dates, confidence, scope, targets,
and actions are read, never recomputed by hand.

### Debug report

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

### Failed data fetches this round

Render every item from `ReportPacket.failed_data_fetches` using this table. The
list is metric-centric: one row per final `(asset/scope, metric)` result, with
matching provider attempts shown as one compact ordered path under the failure
reason when needed.

| Asset/Scope | Metric | Final status | Stage / Provider | Error code | Failure reason | Program failure log | Last usable data | Decision impact |
|---|---|---|---|---|---|---|---|---|

If the list is empty, write `No final data fetch failures this round.` For a
`STALE` refresh failure, write `The current refresh failed; the last usable data is <timestamp>, so this round is treated as STALE.`
Use the structured reason, error code, stage/provider, timestamp, and decision
effect exactly as supplied by the packet. `SKIPPED` and `NOT_APPLICABLE` are not failed fetches
and must not appear in this subsection; keep them in section 9.

When multiple attempts belong to one final metric, keep them under the same
row, for example: `Attempt path: CoinGecko HTTP_429 → Coin Metrics Community PROVIDER_UNSUPPORTED`.
For each attempt, show `exception_class`, `detail`, and the bounded `log` when
present. If no direct program log was captured, write `no direct log captured`
and keep the structured reason unchanged.

### Script execution failures this round

Render every item from `ReportPacket.script_failures`:

| Script | Status | Exit code | Log source | Direct failure log |
|---|---|---:|---|---|

If the list is empty, write `No script execution failures this round.` The log
must be the sanitized, bounded `stderr` excerpt from
`scripts/run_with_debug.py`, or the captured `stdout`/launcher error when
stderr is empty. Do not treat a successful script with ordinary warnings as an
execution failure.

### Excluded holdings

If a snapshot contains an asset from `policy.universe.excluded`, show it as
`EXCLUDED / UNMANAGED` with its supplied quantity/value when available. Keep it
out of scored asset, target-allocation, and rebalance-recommendation tables;
exclusion is not an automatic sell signal. Pass-1 diagnostics may separately
show `PENDING_EXTERNAL_RESOLUTION`, but a final portfolio report requires
`pending_external_resolution == 0`.

## 2. Portfolio diagnostics

Include:

- total portfolio value;
- stablecoin share;
- configured core share;
- satellite share;
- current drawdown when known;
- major concentration issue.

When position cost data is available, also include:

### Current position returns

| Asset | Quantity | Current price | Average cost | Current value | Position cost | Unrealized P&L | Position return | Portfolio share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|

Rows with unknown cost show `--` for cost, unrealized P&L, and Position
return. Stablecoin/cash rows still contribute to portfolio value and the
stable sleeve. Below the table, report:

- unrealized P&L of positions with known cost;
- the weighted unrealized return of positions with known cost;
- cost data coverage (`pnl_value_coverage_ratio`);
- reported total and visible-value coverage when the screenshot is partial.

Position unrealized return is not Portfolio NAV Return and must not be called
`total portfolio return`.

## 3. Market Regime

Explain the regime using only the most decision-relevant evidence:

- BTC trend;
- volatility/drawdown;
- dominance/breadth;
- flows;
- major current events.

Separate fact from judgment.

Use explicit labels such as `Fact` and `Judgment`. For a phrase such as
"the overall state is mixed", explain that market breadth and core-asset
trend disagree, identify the measured breadth/trend evidence, and state that
this lowers confidence for broad satellite additions rather than
automatically creating a sell signal.

### Term explanations and decision impact

Add this subsection only for terms used in the report that could be
misunderstood. Keep entries short and use this format:

| Term | Meaning | Decision impact this round |
|---|---|---|
| `MATERIAL_EVENT_FOUND` | Scanned security or regulatory sources contain a material event. It is not proof of an exploit, approval, or execution. | State whether it lowers confidence, blocks an increase, supports HOLD, or creates a concrete REDUCE/EXIT trigger. |
| `Protocol proposal/upgrade activity` | An Ethereum protocol roadmap, upgrade, or developer-economics proposal. | State the affected risk assumption and what confirmation would change the Action. |
| `ManualAssetContext` | User-supplied governance, tokenomics, legal, or protocol context with explicit impact, severity, and scope. | Preserve `source=MANUAL_USER_INPUT`; apply only the selected scope and do not infer a provider-confidence penalty. |
| `STALE` / `FAILED` / `SKIPPED` | Old evidence / attempted collection without usable evidence / intentionally omitted decision-active optional evidence. | State the affected factor, confidence, and trade eligibility. |
| `Position P&L` | Remaining-position unrealized P&L based on usable cost basis. | Do not call it Portfolio NAV Return or use it as a buy signal. |
| `NAV Return` | Cash-flow-adjusted portfolio performance. | No disclosure is `ASSUMED_NONE` and `FINAL`; mark it `PROVISIONAL` only for explicit unresolved cash flows. |
| `Cost data coverage` | Current-value share of positions with usable cost data. | Explain how much of the Position P&L is known. |

Use the actual source term when it is more precise. Do not call a proposal a
"governance case" or a security incident unless the Evidence explicitly
supports that classification.

## 4. Per-Asset Assessment

Start with the overview table:

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

- **Supporting evidence**: the strongest positive factors and their evidence.
- **Counterevidence / risks**: the strongest negative, missing, stale, or
  conflicting factors and their effect on confidence.
- **Portfolio-level constraints**: current weight, target weight, deviation,
  active rebalance threshold, regime envelope, stablecoin floor,
  concentration, and funding/turnover constraints that matter.
- **Why this Action**: explain why the chain ends in `HOLD`/`WAIT`,
  `INCREASE`, `REDUCE`, or `EXIT`, rather than mapping the score directly to a
  trade. If a high score still produces `HOLD`, state the deviation and
  threshold explicitly and explain any retained stablecoin optionality or
  turnover concern.
- **What would change the recommendation**: link to the concrete
  invalidation/catalyst in section 8.

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
remaining approved amount is held as `WAIT`/`GATE_HOLD` conditional reserve.

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

## 5. Current vs Target Allocation

| Asset | Current | Target | Deviation | Action | Priority |
|---|---:|---:|---:|---|---|

Targets should sum to approximately 100%.

## 6. Action Plan

For each approved recommendation:

| Asset | Action | Amount | Target weight after trade | Execution |
|---|---|---:|---:|---|

For staged orders, give zones and tranche percentages, not false exact precision.

For staged buys, use the validated execution plan:

| Asset | Tranche | Price Zone | USD | Est. Qty | Structural Basis |
|---|---:|---|---:|---:|---|
| ETH | 1 | structural zone | 600U | ~0.157 ETH | MA50 + confirmed swing support |

When available, add:

```text
Volume Profile: 4H / 120D / MEDIUM
POC: $...
VAL / VAH: $... / $...
Key HVN: $...
Basis: MA50 + confirmed swing + 60D POC + 120D HVN
Note: proxy for historical volume concentration, not the true cost of all holders; LVN is transition-zone context only.
```

Label quantity as approximate and use the zone midpoint as the reference price.
Also report:

- Approved total capacity;
- Staged amount (`planned_amount_usd`), which is a recommendation rather than a fill;
- Conditional reserve amount (`reserve_amount_usd` with its `reserve_policy`);
- Technical confidence;
- Data confidence and setup quality;
- Spot `observed_at`, OHLCV observation freshness, and `ohlcv_hash` when available;
- Structured invalidation review trigger.

If new capital is supplied, explicitly state:

- available capital;
- amount staged now;
- amount retained as approved conditional reserve;
- reason not to deploy the remainder.

## 7. Risk Checks

State:

- stablecoin floor after recommendation;
- whether portfolio risk is rising or falling;
- key concentration/beta risk;
- whether the portfolio appears consistent with the configured drawdown risk budget (15% by default);
- note that the risk budget cannot guarantee a loss ceiling.

## 8. What Would Change the Recommendation

List 2–5 concrete invalidation/catalyst conditions.

## 9. Data Quality

State:

- analysis timestamp;
- the compact `Data Collection Summary`: requested metrics, counts for each
  status, critical failures, coverage with its named denominator (required
  request completion, applicable required coverage, decision-scope weighted
  coverage), optional/premium availability, and decision confidence;
- missing or stale factors;
- score confidence impact;
- any conflicting data;
- screenshot cross-check status and material mismatches;
- whether visible rows reconcile with the reported total.

The detailed `Data Collection Log` is shown during evidence acquisition. The
final report must still list every `FAILED`, `STALE`, or `CONFLICT` metric and
its scoring/decision effect; do not silently omit collection failures. Explain
which missing evidence lowered confidence, blocked an increase, forced
`HOLD_OR_REDUCE` (formerly `HOLD_ONLY`), or left the decision `NO_TRADE`. Optional and premium `SKIPPED`
items must state that they were excluded from applicable coverage.
Detailed final fetch failures are shown in `Failed data fetches this round`;
keep this section as the aggregate data-quality report rather than duplicating
that table.

Render `ReportPacket.optional_data` separately as `Optional data not collected
this round` with asset, metric, reason/provider/history issue, and the
explicit effect `non-blocking`. Optional `SKIPPED` values are excluded from
applicable coverage; they must not be promoted to final required failures or
silently counted as success.

For `FULL_REVIEW`, compare the previous and current Position P&L by asset and
show the change in percentage points when both cost bases are usable. For
`SNAPSHOT_REVIEW`, always show the current table when cost observations are
present.

## 10. Final action line

End with one unambiguous sentence such as:

> This round's execution recommendation: stage 1,500U of ETH buys across three tranches; hold BTC; reduce AAVE by 800U; keep the remaining 2,700U in stablecoins.

or:

> This round's execution recommendation: NO TRADE; keep all 5,000U in stablecoins while waiting for the risk/reward balance to improve.

## 11. Confidence and performance chain

Finalized reports show Data Confidence dimensions, Regime Confidence, Decision
Confidence, raw/final scores, bands, caps, evidence IDs, and their action
effects, action scope, component breakdown, and soft penalties. They also show NAV status, `performance_finality`,
`cash_flow_resolution_status`, cash-flow-adjusted return, current/max drawdown,
BTC benchmark/excess return, and EventScanner state. Only
`CONFIRMED_NONE`/`CONFIRMED_AMOUNT` or `BASELINE_RESET` can produce
`performance_finality=FINAL`; an unresolved historical cash flow keeps the
packet `PROVISIONAL`/`BLOCKED`. The writer never recomputes these values.
