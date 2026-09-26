# Strategy V2.2 Validation Report

## Phase

All five phases (A–E) landed in order; this report is the Phase E
validation of the complete preregistered ladder.

## Git SHA / Policy Hash / Dataset Manifest

- Git SHA: `d6328135a131` (ladder harness; rung policies carry per-rung
  frozen manifests in `v22-validation.json`)
- Policy hash (harness base, volatility-budget override):
  `6fcf7e9a5d9d…` (full hash in each `v22-validation.json`)
- Datasets: `strategy-validation-2024-present` (2024-01-01 → 2026-09-24,
  18 evidence series incl. 9 new structural series) and
  `strategy-validation-2021-2023-bear` (2021-07-01 → 2023-12-31), strict
  point-in-time inputs, USD-converted, cached series reused
- Experiment identity: `full/core_existing/strict`, fees 10 bps +
  slippage 5 bps

## What V2.2 Changed

- The fixed 70/30 BTC/ETH core anchor is gone from the strategy path:
  `core_allocation.mode = btc_baseline_with_active_tilts` gives the whole
  approved core budget to the BTC baseline; ETH earns core budget only
  through an admitted ETH/BTC relative-alpha tilt (preregistered at 20%
  of core budget). The anchor survives as `legacy_anchor` for legacy
  replay and A/B comparison; the legacy drawdown engine (live pipeline)
  keeps legacy anchor semantics.
- Every satellite dollar is now independently measured against the BTC it
  displaced (opportunity-cost attribution over the real position path).
- AAVE/SOL/BNB structural point-in-time evidence (DeFiLlama TVL, borrow,
  fees, stablecoin supply; publication-lag contract, `available_at =
  observed + 1 day`) is harvested, with research-only factor scores gated
  behind `include_structural`.
- The ETH tilt and structural deployment stay research-only in canonical
  policy (`tilt_enabled = false`) until their admission rules pass.

## Behavior Explicitly Frozen

Risk-engine parameters (target/max volatility, regime scaling, emergency
overlay, recovery FSM, 15% drawdown budget, stable floor), satellite
score thresholds (57/62/67/85), `tactical_fraction`, rebalance bands,
risk-tier thresholds, WAIT expiry, and live legacy-mode behavior. Nothing
in this round tuned any of them.

## Preregistered Ablation Ladder (results)

| Rung | 2024–present CAGR / MaxDD / vol-matched excess | 2021–2023 bear CAGR / MaxDD / vol-matched excess |
|---|---|---|
| A BTC-only volatility targeting | +15.89% / −23.00% / +1.43% | +8.49% / −28.79% / +2.36% |
| B + market regime machinery | +15.18% / −22.35% / +1.25% | +8.10% / −28.61% / +2.04% |
| C + ETH relative-alpha tilt | +15.18% / −22.35% / +1.25% | +8.10% / −28.61% / +2.04% |
| D + satellite TACTICAL_ONLY | +13.87% / −23.13% / +0.39% | +8.33% / −28.40% / +2.26% |
| E structural ranking power | diagnostic only | diagnostic only |
| F + FULL_CONVICTION structural | +13.63% / −23.52% / +0.24% | +8.37% / −28.35% / +2.30% |
| G execution-layer reference (no costs) | +13.81% / −23.48% / +0.13% | +8.16% / −28.95% / +1.78% |
| H full V2.2 | +13.63% / −23.52% / +0.24% | +8.37% / −28.35% / +2.30% |

Full V2.2 vs the BTC-only baseline: −2.26 pp CAGR in 2024–present,
−0.12 pp in the bear window. Sharpe/Sortino follow the same ordering
(2024: A 0.840/1.287 vs H 0.789/1.188; bear: A 0.515/0.752 vs H
0.513/0.747 — essentially flat).

## Alpha Attribution (module CAGR deltas)

| Module | 2024–present | 2021–2023 bear |
|---|---|---|
| BTC baseline (rung A CAGR) | +15.89% | +8.49% |
| Market regime machinery (B−A) | −0.72 pp | −0.39 pp |
| ETH relative-alpha tilt (C−B) | 0.00 pp (never deployed) | 0.00 pp (never deployed) |
| Satellite TACTICAL_ONLY (D−C) | −1.31 pp | +0.24 pp |
| Structural FULL_CONVICTION (F−D) | −0.23 pp | +0.03 pp |
| Execution costs (G vs F/D) | $541 on $100k | $307 on $100k |
| Cash carry | 0 (no cash yield assumed) | 0 |
| Interaction residual | 0.00 | 0.00 |
| Full V2.2 (H) | +13.63% | +8.37% |

## ETH Relative Alpha

- Discrete alpha states over the reviews: 2024 window 183 POSITIVE / 317
  NEUTRAL / 495 NEGATIVE; bear window 273 / 288 / 352.
- The tilt never deployed in either window despite 183/273 POSITIVE
  states: ETH's replay evidence coverage stays 0.58 (LOW confidence —
  ETH's own structural factors are not reconstructable point-in-time), and
  `eth_core_eligibility` correctly holds ETH at HOLD_ONLY. Missing ETH
  evidence blocking a new ETH increase is the intended fail-defensive
  gate, not a wiring defect.
- Signal ranking power (90D Spearman IC, decision horizon): mostly
  negative or window-inconsistent — e.g. `rel_return_180d` −0.195 (2024)
  vs +0.013 (bear), `relative_drawdown_180d` −0.195 vs −0.532 (consistent
  negative: ETH/BTC mean-reverts), `momentum_persistence_90d` +0.095 vs
  −0.109. Realized ETH-vs-BTC forward excess was negative on average in
  2024 (90D −3.1%, 180D −6.9%). The ETF flow differential had usable
  samples only in 2024 (IC +0.02).
- **Admission: FAILED.** `eth_tilt_admitted = false`; the canonical tilt
  stays research-only.

## Structural Ranking Power

Ranking power is real but uneven: BNB chain TVL growth factors show the
strongest consistent positive ICs (e.g. `chain_tvl_growth_90d` 90D IC
+0.46 monotone; `chain_tvl_growth_30d` +0.38), AAVE
`protocol_tvl_growth_180d` +0.31 and `protocol_utilization_change_90d`
+0.22 pass, while AAVE 90D TVL growth and SOL fee factors are clearly
negative (−0.35, −0.41). Eight factors pass the two-window admission rule
(`structural_admitted = true`), so rung F ran the FULL_CONVICTION
deployment experiment: it cost −0.23 pp (2024) and added +0.03 pp (bear).
Admission unlocks the experiment, not production authority.

## Satellite Opportunity Cost

2024–present window (bear window: almost no satellite entries):

| Asset | Entries | Avg weight | 90D excess (mean) | 90D win rate vs BTC | Position-weighted contribution | BTC foregone |
|---|---|---|---|---|---|---|
| BNB | 6 | 1.38% | +29.4% | 0.833 | +2.29% | −0.81% |
| AAVE | 11 | 0.65% | +0.7% | 0.364 | −1.02% | −0.08% |
| SOL | 9 | 1.11% | −3.5% | 0.444 | −0.94% | +1.44% |

BNB — the satellite whose structural (chain TVL/stablecoin) factors also
show the best ranking power — was the only one that beat BTC. Sample
sizes are small (6–11 entries) and the bear window contributes almost
nothing, so these are per-asset hypotheses to re-test, not verdicts.

## Policy Decision Matrix (cross-window, vol-matched excess deltas)

| Module | Decision | Basis |
|---|---|---|
| BTC baseline core | KEEP | preregistered V2.2 foundation |
| Market regime machinery | REMOVE (return lens) | negative vol-matched delta in both windows (−0.18 pp / −0.32 pp); it did shave MaxDD slightly in both — it is risk machinery, not alpha, and its parameters are frozen this round, so this verdict is an input to a future risk-calibration round, not an immediate deletion |
| ETH relative-alpha tilt | RESEARCH_ONLY | never deployable (coverage gate) and admission failed |
| Satellite TACTICAL_ONLY | RESEARCH_ONLY | −0.86 pp (2024) vs +0.22 pp (bear): window-inconsistent |
| Structural FULL_CONVICTION | RESEARCH_ONLY | ranking power admitted but deployment deltas ≈ 0/negative |

## Ranking Power Summary

Market-family composite scores were already known to carry ≈ zero
forward ranking power (V2.1 finding). V2.2 adds: ETH/BTC relative signals
— no admitted signal; structural factors — a subset (chain TVL growth,
stablecoin growth, AAVE 180D TVL growth, utilization change) with
consistent positive ICs in both windows.

## Known Limitations

- Two windows, one path each; satellite samples are small (6–11 entries).
- Structural scores are aggregations of free DeFiLlama history with a
  +1-day publication assumption; SOL fee history starts 2022-02.
- The decision matrix's return lens ignores the regime machinery's
  drawdown benefit (−0.65 pp / −0.18 pp MaxDD vs rung A in the two
  windows).
- Both windows breach the frozen 15% drawdown budget (−23% / −28%); risk
  calibration is explicitly out of scope (V2.3 per plan §12).

## Parameters Still Frozen

Everything listed in plan §2: target/max volatility, regime risk scaling,
emergency overlay and recovery FSM, 15% drawdown budget, stable floor,
satellite thresholds 57/62/67/85, tactical_fraction, rebalance bands,
risk-tier thresholds, WAIT expiry. Score thresholds stay frozen pending
an independent threshold-calibration project.

## Verdict Against the V2.2 Acceptance Criteria

1. Fixed ETH anchor removed — yes (strategy path; live legacy mode
   unchanged by design).
2. BTC is the default opportunity-cost asset — yes.
3. ETH tilts only on explainable relative alpha — enforced, and the
   alpha case currently fails admission.
4. Every satellite independently measured vs BTC — yes (Phase C).
5. Structural score validated on real point-in-time data — yes (Phase D);
   a subset shows genuine ranking power.
6. Market/structural ranking power separately observable — yes.
7. Full-portfolio alpha decomposed by module and asset — yes (ladder
   deltas + opportunity-cost attribution).
8. Unvalidated signals hold no sizing authority — enforced
   (`tilt_enabled=false`, structural evidence research-gated).
9. Risk engine/FSM isolated from this round — yes (frozen, untouched).
10. Which modules earn their keep — answered above: the BTC baseline
    carries the strategy; every active layer is currently flat-to-negative
    on the return lens and stays research-only.

**Ready for next phase: YES** — for a risk-calibration round (V2.3) and a
structural-signal production case built on the admitted TVL-growth
factors. Not ready: enabling the ETH tilt or structural deployment in
canonical policy.
