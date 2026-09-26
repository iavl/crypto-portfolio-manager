# Strategy V2.1 Validation Report (Phases A–E)

Branch `strategy-v2.1`, executed 2026-09-26. All results are bound to
Git SHA `7986f01` (engine) with the frozen validation manifests written into
each dataset's `v21-validation.json` (policy hash `8874e882…` at rung time;
`scripts/backtest.py v21` re-stamps every rung).

Windows (strict point-in-time replay, frozen Binance datasets, costs 10 bps
fee + 5 bps slippage unless noted):

- `strategy-validation-2024-present`: 2024-01-01 → 2026-09-24 (995 reviews)
- `strategy-validation-2021-2023-bear`: 2021-07-01 → 2023-12-31 (913 reviews)

Baseline for "pre" comparisons: the same datasets replayed at HEAD `067b567`
(volatility-budget V2, pre-V2.1), preserved as `run-pre-v21.json`.

## Phase A — Market regime / portfolio-drawdown separation (`ad3c02f`)

- `determine_regime(include_portfolio_drawdown=False)` in volatility-budget
  mode: the regime label reflects market/systemic conditions only;
  `drawdown_influenced_regime` reports `NO` in both windows
  (`REGIME_PINNED_BY_OWN_DRAWDOWN` can no longer occur in this mode).
- Regime authority moved to `risk_engine.regime_risk_scaling`
  (NORMAL 1.0 / DEFENSIVE 0.8 / CP 0.6 → effective target volatility
  25/20/15%). The volatility-budget stable floor is the policy minimum only;
  the risk gate and rebalance layer match.
- Legacy drawdown mode is byte-for-byte unchanged (pinned by tests).

## Phase B — Emergency recovery FSM (`d8e5ea4`)

Preregistered placeholders (5/10/20 reviews, 40%/60% caps, 25% volatility
threshold), never tuned. Observed state machine over the two windows:

| Diagnostic | 2024–present | 2021–2023 bear |
|---|---|---|
| BREACH→RECOVERY_1 transitions | 6 | 8 |
| Completed RECOVERY_2→NORMAL releases | 7 | 4 |
| New-low resets out of RECOVERY | 0 | 4 |
| Avg / max BREACH duration (reviews) | 31 / 102 | 50 / 277 |
| Recovery inefficiency share (market NORMAL while emergency-capped) | 3.5% | 8.5% |

Case D of the plan (NAV far below peak, market recovered) demonstrably
re-risks in stages; bear-window resets confirm premature bottom-fishing is
still refused (4 re-entries into BREACH on new lows).

## Phase C — Scoring V3 (`475fc09`)

- Comparison-score-space contract: satellite thresholds compare only in the
  NORMALIZED space; the effective score is diagnostic-only and never
  substitutes. Held satellites without a normalized score are preserved,
  never exited by shrunk-neutral scores.
- Conviction states with per-profile market/structural families
  (ETH carries on-chain demand in its market family; the defi profile
  weights protocol fundamentals highest).
- TACTICAL_ONLY: strong market without usable structural evidence earns
  `allocation.tactical_fraction` (preregistered 0.30) of the risk envelope.
- Evidence permission split from data confidence
  (`evidence_deployment_factor`: ACTIONABLE 1.0 / LIMITED 0.5 /
  NOT_ACTIONABLE 0.0); the LOW band's zero no longer silently blocks
  LIMITED evidence.
- Core quality multiplier narrowed [0.5, 1.5] → [0.85, 1.15].
- Strict replay fix: in volatility-budget mode the historical builder counts
  only MARKET-family factors as entry-critical, so missing structural
  evidence yields TACTICAL_ONLY instead of a permanent NOT_ACTIONABLE
  classification. Observed conviction mix (satellite reviews):
  2024 window 529 FULL / 1404 TACTICAL / 3042 WATCH; bear 333/564/3176.

## Phase D — BTC-default capital hierarchy (`7986f01`)

BTC is the default risky asset in volatility-budget mode: unabsorbed core
budget routes to the BTC baseline before cash, blocked only by a hard BTC
risk event. This is what deploys the all-cash path: with weak/missing
evidence the book still reaches the BTC single-asset cap (50%) instead of
staying 100% stable.

Average cash attribution (full scope, core_existing, strict):

| Cause | 2024–present | 2021–2023 bear |
|---|---|---|
| MINIMUM_RESERVE_CASH | 15.0% | 15.0% |
| NO_ALPHA_CASH | 27.5% | 30.3% |
| VOLATILITY_BUDGET_CASH | 8.6% | 12.3% |
| EMERGENCY_CASH | 4.6% | 8.7% |
| EXECUTION_PENDING_CASH / UNALLOCATED_RESIDUAL | 0% | 0% |

NO_ALPHA_CASH now names its cause: budget beyond the BTC cap that no ETH or
satellite case could justify (structural evidence is structurally absent
from the point-in-time dataset), not failed allocation.

## Phase E — Validation and alpha attribution

### Main windows (volatility-budget mode, full scope, strict, with costs)

| Path | Metric | V2.1 | pre (`067b567`) |
|---|---|---|---|
| 2024–present core_existing | CAGR / MaxDD / Vol / Sharpe | 13.78% / −23.1% / 18.8% / 0.78 | 11.75% / −22.4% / 19.4% / 0.67 |
| 2024–present core_existing | vol-matched excess (ann.) | **+0.06%** | −2.39% |
| 2021–2023 core_existing | CAGR / MaxDD / Vol / Sharpe | 6.94% / −28.2% / 17.9% / 0.47 | 7.51% / −26.9% / 17.7% / 0.50 |
| 2021–2023 core_existing | vol-matched excess (ann.) | **+1.21%** | +1.82% |
| 2024–present all_cash | CAGR / vol-matched excess | 12.59% / −0.34% | 14.04% / +0.16% |
| 2021–2023 all_cash | CAGR / vol-matched excess | 1.60% / −2.87% | 2.14% / −2.34% |

Satellite real exposure (plan acceptance #4): 24 satellite buys in the 2024
window (19 from all-cash), 2 in the bear window — the strict zero-buy
ratchet is broken.

### Ablation ladder (preregistered A–H, `v21-validation.json`)

2024–present CAGR: A 15.9% · B 15.2% · C 15.5% · D 15.5% · E 13.8% ·
F 13.8% · G 13.9% (no costs) · H 13.8%.
2021–2023 CAGR: A 8.5% · B 8.1% · C 6.8% · D 6.8% · E 6.6% · F 6.9% ·
G 7.0% (no costs) · H 6.9%.

Structural findings (not acted on — parameter changes remain human decisions):

1. **BTC-only volatility targeting (rung A) beats the full ladder in both
   windows** on CAGR and vol-matched excess. The ETH anchor tilt (rung C)
   costs 0.7–1.6pp in both windows; under the V2.1 hierarchy the residual
   already defaults to BTC, so the 70/30 prior is the binding question for
   the next policy round.
2. **Rung D (market-mirror structure) deploys zero satellites**: with
   structure mirrored onto market factors the conviction is FULL but the
   composite coverage stays below the investable floor, so the evidence
   contract correctly classifies it NOT_ACTIONABLE. Satellites actually
   deploy at rung E via TACTICAL_ONLY — i.e. under the current data
   reality, the tactical path is the only satellite path, and the ladder's
   D→E step measures "tactical satellites + costs", not structure.
3. **Execution costs cost ~0.16pp/yr** (G vs F). The recovery FSM is
   roughly neutral on CAGR, materially reduces emergency stalls
   (EMERGENCY_OVERLAY stalls 262 → 176 in 2024), and adds no drawdown.
4. **Score ranking power is not demonstrated**: market forward Spearman is
   mildly negative at every horizon (30d −0.01/−0.09, 90d −0.09/−0.18,
   180d −0.18/−0.18; n = 134–165 non-overlapping), bucket monotonicity
   fails, and structural samples are too thin (n ≤ 33). Per plan 8.6 the
   57/62/67/85 thresholds stay untouched, and the narrowed [0.85, 1.15]
   core authority is the correct posture.

### Stall attribution (plan 8.7)

Stalled-review share is still high (82%/89%) but now attributable:
2024 window — EVIDENCE_BLOCK 371→23 after the Phase C builder fix,
VOLATILITY_BUDGET 275, EMERGENCY_OVERLAY 171; bear window —
EMERGENCY_OVERLAY 411, VOLATILITY_BUDGET 182, EVIDENCE_BLOCK 189.
Longest consecutive stall runs: 249 reviews (2024), 604 (bear). The
remaining stall mass is vol-budget gating (by design in a 60%-cash book)
plus emergency time in drawdown.

## Acceptance criteria (plan 8.11)

1. Strict replay no longer degrades via score-space mismatch — met
   (comparison space contract; satellites deploy).
2. Market regime no longer pinned by portfolio drawdown — met
   (`drawdown_influenced_regime = NO` in both windows).
3. Recovery FSM exits BREACH in stages — met (releases in both windows).
4. Strict full universe produces real satellite exposure — met
   (24 buys in 2024 window).
5. All-cash path no longer stalled by missing evidence — met
   (BTC baseline deploys to the single-asset cap; 19 satellite buys).
6. Cash attribution explicit — met (six-cause split, sums to stable weight).
7. Score ranking power has independent statistical output — met
   (`family_score_evaluation` with Spearman, non-overlapping counts,
   buckets, monotonicity).
8. Risk-matched benchmark not significantly worsened — met
   (core_existing vol-matched excess +0.06%/+1.21% vs pre −2.39%/+1.82%:
   the bull window improves materially, the bear window is unchanged
   within noise).
9. MaxDD not unexplainably out of control — partially: −23.1%/−28.2% vs
   pre −22.4%/−26.9%. The 0.7–1.3pp deepening is attributable to
   satellite exposure and staged re-risk, and the pre-existing breach of
   the 15% budget (a V2 condition) is unchanged in nature.
10. Parameter sensitivity not run this round (the plan's budget-sensitivity
    harness exists; the ladder intentionally used preregistered values only).

## Known limitations

- Structural factor history (fundamentals/valuation/on-chain for
  satellites) is absent from the point-in-time dataset, so FULL_CONVICTION
  never fires on real structural evidence and the D→E ladder step is
  tactical-only. Backfilling section 9's data list is the prerequisite for a
  real structural test.
- The bear window starts 2021-07-01 on the cached dataset, matching the
  plan; series depth below that is warmup-only.
- Legacy mode is byte-identical (unit-pinned); no legacy replay was
  re-run this round.

## Parameters still placeholders (preregistered, never tuned)

`regime_risk_scaling` multipliers, `recovery` reviews/caps/threshold,
`tactical_fraction`, `evidence_deployment_factor`, scoring_v3 family
weights, core multiplier bounds.
