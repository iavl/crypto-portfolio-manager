# Legacy (live) vs Strategy V2.3 — Strict Replay Comparison

Run: 2026-09-26 · git `4024533` (main, post-V2.3 merge) · identical frozen
structural point-in-time reviews (scope `full/core_existing`, all-cash
start, daily cadence, costs 10/5 bps) · **one cash carry convention for both
sides** (RISK_FREE_PROXY, point-in-time FRED DFF) · artifact
`research/backtests/legacy-vs-v23-comparison.json` (runtime, outside Git).

- **legacy_canonical** — `config/policy.json` exactly as live today
  (`risk_engine.mode=legacy_drawdown`, TACTICAL_FRACTION satellite path,
  70/30 anchor core, drawdown ladder).
- **v23_full** — the full V2.3 assembly as validated (volatility budget +
  HARD_TARGET stress budget + regime R1 + BNB admitted tilt
  `rel_return_30d`; AAVE/ETH tilts locked; SOL research-only).

## 2024-01-01 → 2026-09-24 (995 reviews, bull window)

| metric | legacy | V2.3 | |
| --- | ---: | ---: | --- |
| CAGR | 9.05% | **15.47%** | +6.4pp |
| MaxDD | **−12.49%** | −18.14% | legacy shallower |
| Volatility | 13.32% | 15.77% | |
| Sharpe / Sortino | 0.72 / 1.06 | **0.99 / 1.51** | |
| Calmar | 0.72 | **0.85** | |
| Worst 30D / 90D | −7.95% / −9.17% | −10.84% / −13.05% | |
| Avg cash | 79.1% | 67.4% | |
| Turnover / cost | 4.1x / $730 | **1.4x / $251** | 3× less trading |
| vs vol-matched BTC/cash | −4.30%/yr | **+0.59%/yr** | |
| vs 100% BTC | −20.99%/yr | −14.57%/yr | BTC 30.04% CAGR, −52.97% MaxDD |
| vs 70/30 | −14.95%/yr | −8.53%/yr | |
| Days below −15% | **0** | 179 | |

Average book: legacy 19.5% BTC / <1% each ETH·SOL·BNB; V2.3 32.3% BTC / 0.3%
BNB / 0% ETH. Regime labels: legacy spent **448 of 995 days in
CAPITAL_PRESERVATION** (its own drawdown ladder + trend votes); V2.3 spent
699 days NORMAL (drawdown authority moved to the emergency overlay; stress
budget bound 784 reviews).

## 2021-07-01 → 2023-12-31 (913 reviews, bear window)

| metric | legacy | V2.3 | |
| --- | ---: | ---: | --- |
| CAGR | 6.51% | **10.73%** | +4.2pp |
| MaxDD | **−16.99%** | −27.81% | legacy shallower |
| Volatility | 15.47% | 19.39% | |
| Sharpe / Sortino | 0.49 / 0.68 | **0.62 / 0.91** | |
| Calmar | 0.38 | 0.39 | ≈ equal |
| Worst 30D / 90D | −9.88% / −12.34% | −11.09% / −16.66% | |
| Avg cash | 90.0% | 74.6% | |
| Turnover / cost | 2.3x / $384 | **1.6x / $297** | |
| vs vol-matched BTC/cash | −0.66%/yr | **+2.77%/yr** | |
| vs 100% BTC | −1.08%/yr | **+3.14%/yr** | BTC 7.59% CAGR, −76.63% MaxDD |
| vs 70/30 | +1.05%/yr | **+5.27%/yr** | |
| Days below −15% / −20% / −25% | **7 / 0 / 0** | 658 / 532 / 216 | |

Average book: legacy 5.3% BTC / 4.7% ETH (effectively a cash fund);
V2.3 21.3% BTC / 4.0% ETH. Legacy sat in CAPITAL_PRESERVATION for **726 of
913 days**.

## Reading the comparison honestly

1. **V2.3 wins every risk-adjusted comparison in both windows**: Sharpe,
   Sortino, Calmar (≈tie in bear), and — the primary metric per policy —
   vol-matched excess: +0.59%/yr bull, +2.77%/yr bear, against legacy's
   −4.30%/−0.66%. In the bear window V2.3 even beats 100% BTC buy-and-hold
   (+3.14%/yr) with a −27.8% drawdown against BTC's −76.6%.
2. **Legacy is nearly a cash fund**: 79–90% average cash, 45–80% of days in
   CAPITAL_PRESERVATION, average risky exposure 5–25%. Of its 9.05% bull
   CAGR, roughly 3.9%/yr is cash carry (11.0pp total over ~2.7 years) —
   ex-carry the legacy book earned ≈5%/yr from risk-taking vs V2.3's
   ≈11.6%/yr on the same convention.
3. **The risk story is the mirror image**: legacy never breached −15%
   (0/7 days beyond); V2.3 spent 179 days (bull) / 658 days (bear) beyond
   −15% with MaxDD −18.1%/−27.8%. The HARD_TARGET stress budget prices
   instantaneous crash scenarios, not grinding drawdown paths — this is
   precisely the HARD_TARGET vs WARNING_BAND decision input recorded in
   `docs/PENDING_POLICY_DECISIONS.md`.
4. **V2.3 trades 3× less** (1.4x vs 4.1x turnover, $251 vs $730 costs in
   the bull window): alpha-gated entries plus the BTC baseline replace the
   legacy tactical churn.
5. BTC buy-and-hold still wins the bull window on raw CAGR (30.0%) — at
   53% MaxDD, four times V2.3's risk; the risk-matched line is the fair
   comparison and V2.3 is the only side that clears it in both windows.

## Fix landed during this run

The comparison run exposed a real legacy-path bug: `_execution_timeout_plan`
/ pullback planning used a 1e-9 dust-reserve threshold while the
`ExecutionPlan` contract requires > 1e-7 for a non-NONE reserve policy, so
approved amounts within 1e-7 USD of full deployment crashed replay with
`TIMEOUT_RESERVE requires a positive reserve_amount_usd`. Both thresholds
now match the model contract (dust → NONE), with a regression test in
`tests/test_execution_wait_expiry.py`.
