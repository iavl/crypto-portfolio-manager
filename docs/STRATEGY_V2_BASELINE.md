# Strategy V2 Baseline (Frozen)

This document freezes the Strategy V1 state that Strategy V2 work is measured
against. It is the control group for every later phase: no V2 comparison is
valid against anything other than the values recorded here. The frozen policy
file itself is `references/policy-v1-baseline.json` (byte-identical copy of
`config/policy.json` at the freeze commit).

## Freeze identity

| Field | Value |
| --- | --- |
| Strategy version | V1 (drawdown-controlled portfolio + multi-factor permission system) |
| Git SHA | `a61634b` (branch `strategy-v2`, created from `main`) |
| Policy hash (canonical, full scope) | `0aa9fc65704d7180dcb617f7c1aacdf91ccd7c1333f6c366a0a0eb098e2498cb` |
| Freeze date | 2026-09-25 |
| Test count | 1157 tests, all passing (`python3 -m unittest discover -s tests`) |
| Checks | `ruff check .` clean; `python3 -m compileall crypto_portfolio scripts` clean |

The replayed scoped policies hash as `core=9c8807d7…`, `full=0aa9fc65…`
(recorded in each `run.json` under `policy_hashes`).

## Universe

| Class | Assets |
| --- | --- |
| Core | BTC, ETH |
| Satellites | SOL, BNB, AAVE |
| Stable sleeve | USDT, USDC, DAI, FDUSD, TUSD, USD, CASH, U, USD1 |
| Excluded | LUNC, LINK |

Scoring profiles: `default` (generic satellites), `btc`, `defi_protocol`
(AAVE). Asset→profile mapping: BTC→`btc`, AAVE→`defi_protocol`, others→
`default`.

## Risk settings

- `risk.min_stablecoin_weight` = 0.15
- `risk.max_portfolio_drawdown` (D) = 0.15
- `risk.drawdown_budget_overlay` = enabled, `recovery_reviews` 5,
  `recovery_risky_floor` 0.25
- Drawdown floors (mandatory, from `references/risk-model.md`):
  `<= -0.60D` at least DEFENSIVE, `<= -0.80D` CAPITAL_PRESERVATION,
  `< -D` breach
- Position-level cap while the overlay binds: `risky_cap = 1 - |drawdown| / D`
- Regime sleeve targets: NORMAL stable 15% / satellite max 25% / single-asset
  50%; DEFENSIVE 30% / 15% / 50%; CAPITAL_PRESERVATION 50% / 5% / 40%
- Configured stress scenario (single, applied to all regimes):
  BTC −20%, ETH −30%, SOL −40%, BNB −35%, AAVE −40%

## Scoring thresholds

| Threshold | Value | Notes |
| --- | --- | --- |
| `satellite_entry_score` | 67 | |
| `satellite_exit_score` | 62 | hard exit below |
| `satellite_soft_exit_score` | 57 | SOFT_EXIT band 57–62 |
| `satellite_full_score` | 85 | full envelope fraction |
| `relative_strength.increase_min_score` | 50 | |
| `relative_strength.hard_block_below_score` | 30 | |
| ETH `increase_min_score` / `hold_min_score` | 55 / 45 | core sleeve |
| ETH relative increase / reduce below | 45 / 30 | |
| Coverage gates | 0.90 HIGH / 0.70 MEDIUM / 0.60 minimum investable | |

Base factors and weights (`default` profile): trend 0.30, valuation 0.15,
fundamentals 0.20, onchain 0.10, capital_flows 0.10, relative_strength_btc
0.15. `MISSING` factors keep their weight and shrink toward neutral 50 by
deterministic reliability (no renormalization).

## Regime settings

- Mode `weighted`; domain weights: trend 0.30, volatility 0.25, flows 0.25,
  breadth 0.20
- Severity ceilings: `normal_max` 0.35, `defensive_max` 0.65 (above ⇒
  CAPITAL_PRESERVATION)
- Transitions: `enabled`, `max_notches_per_review` bounds per-review notch
  movement (severe events and drawdown floors are never delayed by it)

## Rebalance settings

- Deviation bands: HOLD < 2pp, WATCH 2–4pp, eligible > 4pp, high priority
  > 8pp; relative floor 0.02, relative watch 0.5, relative high 1.0
- Staging: enabled, `max_gap_close_fraction` 0.5, `max_step_pp` 4.0; bypass
  reasons THESIS_BROKEN / EVENT_RISK / HARD_EXIT_SCORE / RISK_BUDGET_BREACH
- Direction flip confirmation: enabled, 2 closes or 2pp immediate overshoot,
  same bypass reasons

## Baseline control-group results (Phase 0.3)

Both frozen windows were replayed on the freeze commit (dataset built
2026-09-24, series cache reused, no network fetch):

```bash
python3 scripts/backtest.py run    <runtime>/research/backtests/strategy-validation-2024-present
python3 scripts/backtest.py report <runtime>/research/backtests/strategy-validation-2024-present/run.json
python3 scripts/backtest.py run    <runtime>/research/backtests/strategy-validation-2021-2023-bear
python3 scripts/backtest.py report <runtime>/research/backtests/strategy-validation-2021-2023-bear
python3 scripts/strategy_validity.py <run_dir>   # per window
```

`<runtime>` = `~/.local/share/crypto-portfolio-manager`. Costs: fee 10bps +
slippage 5bps (`main_cost`), cadence daily with 14-day full reviews, initial
value $100,000. `full/core_existing` starts 59.5% BTC / 25.5% ETH / 15% USD;
`core/all_cash` starts 100% USD.

### Window A: 2024-01-01 → 2026-09-22 (995 reviews)

| Metric | full/core_existing strict | core/all_cash strict |
| --- | --- | --- |
| CAGR | 5.67% | 4.10% |
| Total return | 16.22% | 11.56% |
| Maximum drawdown | −14.98% | −14.93% |
| Annualized volatility | 12.21% | 14.36% |
| Sharpe (rf 0) | 0.513 | 0.351 |
| Sortino (target 0) | 0.767 | 0.508 |
| Total turnover (Σ daily fractions) | 2.572 | 2.807 |
| Average cash weight | 84.97% | 74.62% |
| Average risky weight | 15.04% | 25.41% |
| Exposure timing contribution | +1.75pp | −17.45pp |
| Drawdown overlay binding share | 95.48% | 83.22% |
| Regime ≥ DEFENSIVE label share | 84.42% | 54.57% |
| Trades (BUY/SELL) | 196 (86/110) | 164 (116/48) |
| Cost USD | 455 | 477 |
| DRAWDOWN_GUARD / breach days | 590 / 0 | 354 / 0 |

Regime distribution (995 reviews): NORMAL 155, DEFENSIVE 250,
CAPITAL_PRESERVATION 590 (full/core_existing); NORMAL 452, DEFENSIVE 178,
CAPITAL_PRESERVATION 365 (core/all_cash).

Review-level planned actions (full/core_existing): INCREASE 483, REDUCE 593,
WAIT 241, HOLD 1668. (core/all_cash: 458 / 506 / 368 / 658.)

Benchmark comparison (full/core_existing strict, investable):

| Benchmark | CAGR | MaxDD | Vol | Excess ann. | Sharpe Δ | MaxDD Δ |
| --- | --- | --- | --- | --- | --- | --- |
| 100% BTC | 30.04% | −52.97% | 47.47% | −24.37pp | −0.277 | +37.99pp |
| 70/30 BTC/ETH | 24.00% | −56.06% | 49.67% | −18.33pp | −0.167 | +41.08pp |
| Static initial weights | 20.91% | −52.62% | 44.70% | −15.24pp | −0.135 | +37.64pp |
| Vol-matched BTC/cash | 9.10% | −16.45% | 12.20% | −3.43pp | −0.262 | +1.47pp |

core/all_cash vs vol-matched BTC/cash: excess −6.54pp/yr (benchmark CAGR
10.64%). Cash-yield sensitivity (diagnostic only): re-crediting the stable
leg at 4%/5% lifts full/core_existing CAGR to 9.25%/10.14%.

### Window B (bear): 2021-07-01 → 2023-12-31 (913 reviews)

| Metric | full/core_existing strict | core/all_cash strict |
| --- | --- | --- |
| CAGR | 0.72% | 2.58% |
| Total return | 1.80% | 6.58% |
| Maximum drawdown | −15.00% | −15.00% |
| Annualized volatility | 12.51% | 10.54% |
| Sharpe (rf 0) | 0.120 | 0.295 |
| Sortino (target 0) | 0.162 | 0.408 |
| Total turnover | 1.734 | 1.172 |
| Average cash weight | 91.61% | 93.36% |
| Average risky weight | 8.40% | 6.65% |
| Exposure timing contribution | −4.61pp | +1.61pp |
| Drawdown overlay binding share | 93.65% | 89.92% |
| Regime ≥ DEFENSIVE label share | 86.75% | 86.31% |
| Trades (BUY/SELL) | 112 (31/81) | 77 (43/34) |
| Cost USD | 270 | 189 |
| DRAWDOWN_GUARD / breach days | 747 / 0 | 756 / 0 |

Regime distribution (913 reviews): NORMAL 121, DEFENSIVE 41,
CAPITAL_PRESERVATION 751 (full/core_existing); NORMAL 125, DEFENSIVE 32,
CAPITAL_PRESERVATION 756 (core/all_cash).

Review-level planned actions (full/core_existing): INCREASE 363, REDUCE 444,
WAIT 63, HOLD 1869. (core/all_cash: 363 / 397 / 52 / 1014.)

Benchmark comparison (full/core_existing strict, investable):

| Benchmark | CAGR | MaxDD | Vol | Excess ann. | Sharpe Δ | MaxDD Δ |
| --- | --- | --- | --- | --- | --- | --- |
| 100% BTC | 7.59% | −76.63% | 57.28% | −6.87pp | −0.295 | +61.63pp |
| 70/30 BTC/ETH | 5.47% | −76.74% | 59.91% | −4.75pp | −0.270 | +61.74pp |
| Static initial weights | 4.67% | −70.50% | 49.86% | −3.95pp | −0.222 | +55.50pp |
| Vol-matched BTC/cash | 4.31% | −24.44% | 12.51% | −3.59pp | −0.280 | +9.44pp |

core/all_cash vs vol-matched BTC/cash: excess −1.13pp/yr (benchmark CAGR
3.72%). Cash-yield sensitivity: 4%/5% lifts full/core_existing CAGR to
4.40%/5.32%.

### Validity verdicts (both windows)

- `strategy_validity.py` verdict: `DEGENERATE_NOT_A_TEST_OF_THE_STRATEGY` in
  both windows; all strict experiments flagged `TRADING_STALLED` (2024
  window: last trade 85 days before the end; bear window: up to 404 days).
- `REGIME_PINNED_BY_OWN_DRAWDOWN` (WARNING) on all strict core_existing
  experiments: the regime label sat at DEFENSIVE-or-worse on 84.4% of 2024
  reviews while own drawdown was at or below its floor on 84.1% (market
  domains pushed defense beyond the floor on 3 of 995 reviews); bear window
  86.8% vs 84.5% (16–18 of 913 reviews).
- `STRESS_UNDERSTATES_REALIZED` (WARNING): BTC realized −53.06% in the 2024
  window against a configured stress of −20% (factor 2.65).
- `REGIME_BELOW_REQUIRED_STABLE_TARGET` (WARNING ×5): under the configured
  POLICY_STRESS, regime stable targets (15/30/50%) are below the stable share
  the stress math requires (34.78% for CORE_ANCHOR, 62.50% for
  WORST_CONFIGURED_ASSET compositions).

## Known structural issues (inherited by V2 work)

1. **Normal risk sizing is drawdown-driven.** The overlay
   `risky_cap = 1 − |DD| / D` binds on 83–95% of reviews across both windows;
   there is no volatility/correlation/beta-based normal risk budget. Drawdown
   is both the normal sizing engine and the emergency brake.
2. **The regime label tracks the book's own P&L, not the market**
   (`REGIME_PINNED_BY_OWN_DRAWDOWN`): market domains almost never push
   defense beyond the drawdown floor, so the regime layer adds little
   information beyond the overlay.
3. **Coverage shrinks scores toward neutral.** `MISSING` factors keep weight
   and shrink toward 50, so low coverage compresses the reachable score range
   while thresholds (57/62/67/85) stay fixed; coverage simultaneously gates
   deployment through confidence factors, conflating attractiveness with
   evidence quality.
4. **Trend information is counted at multiple layers.** Trend drives the
   asset score, the regime trend domain (weight 0.30), and execution
   direction/volume confirmation — correlated information compounds rather
   than having one owner.
5. **Risk tiers are policy defaults, not measurements.** Tier provenance is
   `POLICY_DEFAULT`; `high_beta`/`high` halve the strategic satellite
   fraction (0.5×) with no volatility/beta estimation behind the label.
6. **Objective conflict.** The policy benchmark is 100% BTC, which is not
   risk-matched; on the fair vol-matched BTC/cash comparison V1 shows negative
   excess return in both windows (−3.43pp and −3.59pp/yr for
   full/core_existing).
7. **Unmanaged tails and cash lock-ins.** Strict runs end `TRADING_STALLED`
   (85–404 days without a trade); after breach/CP lock-in the book sits
   ~85–94% in cash for years (average cash weight 84.97% in the 2024 window,
   91.61% in the bear window).
8. **Replay evidence limits.** The judgment layer (fundamentals, satellite
   valuation, events, liveness) is deliberately MISSING in replay, so strict
   runs test the mechanism, not the full strategy — the DEGENERATE verdicts
   reflect this and stand as the V1 baseline state.
9. **Stress calibration understates realized tails.** Configured BTC stress
   −20% vs realized −53%; regime stable targets do not clear the configured
   stress math (see warnings above).

## V2 comparison protocol

Every V2 phase must compare against this document's numbers on the same two
windows, the same dataset cache, and the same canonical experiment names
(`full/core_existing/strict/main_cost`, `core/all_cash/strict/main_cost`).
Policy changes in any phase require re-freezing the policy hash in the phase
report and keeping `references/policy-v1-baseline.json` untouched.
