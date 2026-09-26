# Strategy V2.3 Validation Report

Validated on: 2026-09-26 · git `ef0fb42` (strategy-v2.3 branch, Phases 0–5)
· policy hash frozen in each rung manifest · dataset manifests
`strategy-validation-2024-present` (2024-01-01 → 2026-09-24) and
`strategy-validation-2021-2023-bear` (2021-07-01 → 2023-12-31) · alpha
registry hash `cc016387…` · risk budget mode `HARD_TARGET` · cash carry
convention `RISK_FREE_PROXY` (point-in-time FRED DFF).

Artifact: `research/backtests/strategy-validation-2024-present/v23-validation.json`
(runtime, outside Git). Everything below is preregistered: thresholds
57/62/67/85, tilt fractions (10% satellites / 20% ETH core), ensemble
threshold 0.5, stress scenarios, and vol bands were NOT tuned during V2.3.

## Ablation ladder (plan 10.2)

Primary window 2024–present (structural point-in-time reviews, all_cash
start, scope `full/core_existing`, costs 10/5 bps on rungs H–I):

| rung | adds | CAGR | MaxDD | Sharpe | Sortino | Calmar | vol-matched exc. | avg cash | carry | days < −15% |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | BTC-only vol targeting | 18.95% | −21.45% | 0.97 | 1.49 | 0.88 | +1.28% | 60.0% | 10.7% | 292 |
| B | + stress-loss budget | 15.59% | −19.00% | 0.96 | 1.47 | 0.82 | +0.01% | 65.8% | 10.9% | 214 |
| C | + Regime V2 (R1) | 15.59% | −19.05% | 0.96 | 1.47 | 0.82 | +0.01% | 65.8% | 10.9% | 213 |
| D | + BNB admitted alpha | 15.25% | −18.12% | 0.98 | 1.47 | 0.84 | +0.12% | 67.5% | 11.1% | 178 |
| E | + AAVE admitted alpha | 15.25% | −18.12% | 0.98 | 1.47 | 0.84 | +0.12% | 67.5% | 11.1% | 178 |
| F | + ETH admitted alpha | 15.25% | −18.12% | 0.98 | 1.47 | 0.84 | +0.12% | 67.5% | 11.1% | 178 |
| G | + emergency FSM | 15.56% | −18.12% | 1.00 | 1.51 | 0.86 | +0.41% | 67.4% | 11.2% | 178 |
| H | + execution costs | 15.47% | −18.14% | 0.99 | 1.50 | 0.85 | +0.59% | 67.4% | 11.2% | 179 |
| I | **full V2.3** | **15.47%** | **−18.14%** | **0.99** | 1.51 | 0.85 | **+0.59%** | 67.4% | 11.2% | 179 |

Sister window 2021-07 → 2023-12 (bear):

| rung | CAGR | MaxDD | Sharpe | vol-matched exc. | avg cash | carry | days < −15% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 10.98% | −27.97% | 0.63 | +2.71% | 75.0% | 6.9% | 701 |
| B | 11.21% | −27.77% | 0.64 | +2.94% | 74.7% | 6.9% | 673 |
| I | 10.72% | −27.82% | 0.62 | +2.78% | 74.6% | 6.8% | 658 |

E and F are honest diagnostic no-ops: AAVE and ETH admissions failed (below),
so their rungs add exactly nothing — by design, never forced on.

## Signal admission (unified rule, both windows, 90D decision horizon)

- **BNB: `rel_return_30d` ADMITTED** (positive 90D Spearman IC in both
  windows, monotone terciles, ≥10 independent blocks). The BNB ensemble in
  production would therefore be the single admitted 30-day relative-return
  signal (its vote alone decides the POSITIVE/NEUTRAL state; every other BNB
  signal stays RESEARCH_ONLY).
- **AAVE: nothing admitted.** The relative-price family is consistently
  NEGATIVE in both windows (top tercile below bottom — recent relative
  strength predicts relative UNDERPERFORMANCE at 90D). 6 REJECTED, 5
  RESEARCH_ONLY. No production tilt.
- **ETH: nothing admitted.** Same consistent-negative pattern on the
  relative-price family (3 REJECTED, 5 RESEARCH_ONLY). The V2.2 conclusion
  holds under the unified rule; the ETH tilt stays locked.
- **SOL: research-only sentinel** — no production signals by declaration.

Registry summary: BNB 1/12 admitted · AAVE 0/11 · ETH 0/8 · SOL 0/1.

## Alpha attribution (CAGR deltas, primary / sister)

| module | primary | sister |
| --- | ---: | ---: |
| BTC baseline (A) | 18.95% | 10.98% |
| stress-loss budget | −3.36pp | **+0.23pp** |
| Regime V2 (R1) | −0.00pp | −0.49pp |
| BNB admitted alpha | −0.33pp | +0.14pp |
| AAVE / ETH alpha (no-op) | 0 | 0 |
| emergency FSM | +0.31pp | −0.02pp |
| execution costs | −0.09pp | −0.11pp |
| cash carry (total-return contribution) | +11.2% | +6.8% |
| interaction residual | 0 | 0 |

On the risk-adjusted basis the decision matrix actually uses
(vol-matched excess delta vs the previous rung), BNB alpha is positive in
BOTH windows — the CAGR delta is negative in the primary window only
because the tilt deploys more capital that the stress cap then scales.

## Risk attribution

- Cap binding shares (primary, rung I): stress budget 78.8%, emergency
  overlay 13.2%, volatility budget 8.0%. The stress budget is the dominant
  sizing authority; the three layers bind disjointly (never multiply).
- Worst per-scenario stress loss at full sleeve: −24.68% (both windows);
  average stress utilization 1.32–1.39× the 15% budget at raw sleeve size,
  i.e. the cap binds almost every review.
- Drawdown reality check: even with the HARD_TARGET stress budget the
  realized MaxDD is −18.1% / −27.8% and days beyond −15% fall from
  292→179 / 701→658 but do not reach zero. The stress budget prices
  instantaneous crash scenarios; it cannot cap a grinding multi-month
  drawdown path (that is the emergency overlay's job, binding 13–33%).
  **This is the core input for the HARD_TARGET vs WARNING_BAND decision.**

## Regime authority variants (primary window, zero-carry basis)

| variant | CAGR | MaxDD | CAGR sacrificed per 1pp MaxDD saved |
| --- | ---: | ---: | ---: |
| R0 current | 12.12% | −20.86% | — |
| R1 no volatility | 12.36% | −20.72% | **−1.72 (dominates R0)** |
| R2 flows+breadth | 12.41% | −21.22% | n/a (DD worsened) |
| R3 severe-event only | 12.41% | −21.26% | n/a (DD worsened) |

R1 strictly dominates R0 in this window (better CAGR and better MaxDD);
R2/R3 add CAGR at slightly worse drawdown. The ladder's rung C measured
R1's marginal effect on top of the stress budget as ~0 — once crash sizing
binds, the regime's volatility vote has little left to remove.

## Policy decision matrix

| module | verdict | basis |
| --- | --- | --- |
| btc_baseline_core | KEEP | foundation |
| bnb_admitted_alpha | **KEEP** | positive risk-adjusted contribution in both windows, admission passed |
| execution_layer | KEEP | positive in both windows (costs are smaller than the de-risking they accompany) |
| stress_loss_budget | RESEARCH_ONLY | window-inconsistent (+0.23pp bear, −3.36pp bull CAGR; risk-adjusted basis mixed) |
| regime_v2 (R1) | RESEARCH_ONLY | window-inconsistent; dominates in bull, −0.49pp in bear |
| aave_admitted_alpha | RESEARCH_ONLY | admission failed — production authority stays locked |
| eth_admitted_alpha | RESEARCH_ONLY | admission failed — production authority stays locked |
| emergency_fsm | RESEARCH_ONLY | window-inconsistent (+0.31pp / −0.02pp) |

## Honest negatives

- BTC-only volatility targeting (rung A) still has the highest raw CAGR in
  the bull window. Every layer added after it is justified on risk-adjusted
  or bear-window grounds, not on bull-window CAGR — exactly what the
  no-CAGR-only-selection rule (plan rule 11) demands.
- Cash carry changes the economics materially: +11.2pp total return in the
  primary window (~+4pp/yr on a ~67%-cash book). V2.2 comparisons that
  ignored carry overstated the strategy's lag vs cash-holding benchmarks.
- The BNB "ensemble" is currently a single-signal model; one admitted
  signal is thin evidence for a production tilt and should be re-verified
  as data extends (the admission rule already re-evaluates per window).

## Parameters explicitly not tuned

57/62/67/85 score thresholds · `tactical_fraction` 0.30 (legacy path only) ·
satellite tilt 10% · ETH core tilt 20% · ensemble threshold 0.5 ·
target/max volatility 25%/35% · stress scenario returns · regime weights,
`normal_max`/`defensive_max`, and multipliers · conviction thresholds
(both preregistered at the existing entry score 67).

## Known limitations

- Two windows, one regime cycle each; no rolling walk-forward yet.
- Event/security evidence remains unresolved in replay (frozen records
  predate the point-in-time event archive); hard-risk paths are tested via
  the stress scenarios and the emergency overlay, not realized events.
- The regime variant table runs on the zero-carry basis (internally
  consistent across variants); the ladder runs carry-adjusted.

## Ready for Phase 7

YES — mechanically (migration tooling and gates in
`docs/STRATEGY_V23_MIGRATION.md`). The canonical switch itself remains a
human decision: accept the decision matrix, choose HARD_TARGET vs
WARNING_BAND, then stage per the migration doc.
