# Strategy V2 — Phase 1 Report: Risk Engine

Branch `strategy-v2-risk-engine` (from `strategy-v2` @ `cda8d2f`).

## Changed files

- `crypto_portfolio/engine/portfolio_risk.py` (new): 30D/90D blended realized
  volatility, 90D correlation matrix, beta to BTC, `sigma_p = sqrt(w'Σw)`,
  marginal risk contributions, volatility-budget scale, staged emergency
  drawdown state, `combined_risk_cap` (minimum, never product), and the
  validated `PortfolioRiskInputs` carrier with a point-in-time constructor
  from trailing daily closes.
- `crypto_portfolio/models/policy.py`: new required `risk_engine` block
  (`mode`, `portfolio_risk`, `emergency_overlay`); `stress_scenario`
  replaced by the canonical seven-entry `stress_scenarios` framework.
- `config/policy.json`: `risk_engine.mode = legacy_drawdown` (default),
  placeholder targets (25%/35% vol, 40/60 30D/90D blend, 90D windows,
  emergency fractions 0.60/0.80/1.00 aligned with the mandatory regime
  floors, stage caps 0.90/0.60/0.25); seven stress scenarios with
  `moderate` byte-identical to the V1 single scenario.
- `crypto_portfolio/engine/risk.py`: `emergency_drawdown_overlay_floor`
  (staged brake + shared recovery re-risk floor) and `risk_overlay_floor`
  (mode dispatcher); `run_risk_gate` dispatches through it.
- `crypto_portfolio/engine/allocation.py`: `risk_inputs` parameter; in
  `volatility_budget` mode the strategic risky sleeve is scaled by
  `min(1, target_vol / sigma_p)` and bounded by the combined cap minimum
  (volatility budget, emergency brake, base stable floor); new
  `risk_engine` diagnostics block on `AllocationResult` (portfolio
  volatility, risk target, scaling factor, asset risk contributions,
  emergency state, binding constraint).
- `crypto_portfolio/engine/rebalance.py`: stable-floor validation dispatches
  on the risk-engine mode (was: always the legacy ladder — this aborted
  every volatility-budget replay once any drawdown existed).
- `crypto_portfolio/engine/feasibility.py`,
  `crypto_portfolio/engine/review_diagnostics.py`,
  `crypto_portfolio/research/stress.py`: consumers moved to
  `stress_scenarios["moderate"]`.
- `crypto_portfolio/research/orchestrator.py`:
  `build_risk_inputs_for_reviews` (point-in-time, mirrors the historical
  builder's completed-candle convention), `risk_inputs_by_review` on
  `run_historical_backtest`, per-experiment `risk_engine_diagnostics`
  aggregate, overlay-binding diagnostic dispatches by mode.
- `scripts/backtest.py`: `run --risk-engine-mode {legacy_drawdown,
  volatility_budget}` A/B override (research-only; canonical policy never
  rewritten; run envelope stamped with `risk_engine_mode_override`).
- `references/risk-model.md`, `docs/BACKTEST_VALIDATION.md`: dual-mode risk
  engine, staged emergency overlay, multi-scenario stress framework,
  placeholder-parameter disclaimers.
- Tests: `tests/test_portfolio_volatility.py`,
  `tests/test_correlation_matrix.py` (incl. beta to BTC),
  `tests/test_marginal_risk_contribution.py`,
  `tests/test_volatility_budget.py`,
  `tests/test_emergency_drawdown_overlay.py`,
  `tests/test_stress_scenarios.py`.

## Strategy behavior changed

- **None in production.** Default mode `legacy_drawdown` reproduces V1
  allocation byte-for-byte (pinned by
  `test_legacy_mode_reproduces_v1_allocation_exactly` and the full legacy
  suite). The volatility-budget engine is reachable only through the
  research replay flag until Phase 6 calibration promotes it.
- Research A/B replay (`--risk-engine-mode volatility_budget`) now exists.

## Strategy behavior intentionally unchanged

- Legacy ladder `risky_cap = 1 - |drawdown| / D`, recovery re-risk floor,
  mandatory regime floors (−0.60D/−0.80D/−D), stable floors, satellite
  envelopes, risk tiers, scoring, thresholds, benchmarks.

## New configuration

- `risk_engine.mode`: `legacy_drawdown` (default) | `volatility_budget`.
- `risk_engine.portfolio_risk`: `target_volatility` 0.25, `max_volatility`
  0.35, `volatility_window_weights` {30d: 0.4, 90d: 0.6},
  `correlation_window_days` 90, `beta_window_days` 90,
  `annualization_days` 365, `minimum_history_days` 100.
- `risk_engine.emergency_overlay`: caution/emergency/breach fractions
  0.60/0.80/1.00; stage risky caps 0.90/0.60/0.25.
- `stress_scenarios`: moderate (V1 values), severe_crypto_crash,
  liquidity_shock, correlation_one (uniform), btc_gap_down, eth_alt_crash,
  stablecoin_depeg (only scenario allowed to stress stables).

All numeric values are mechanism placeholders pending Phase 6 walk-forward
calibration; none were tuned against a backtest.

## New diagnostics

Per review (allocation `risk_engine` block): estimated portfolio
volatility, risk target, risk scaling factor, per-asset risk contribution
shares, emergency overlay state, binding risk constraint, cap candidates.
Per experiment (`risk_engine_diagnostics`): average/max estimated
volatility, emergency-state distribution, binding-constraint counts.

## Tests added

74 new tests across six files, including the plan's Cases A–E (all-cash
zero vol, single asset, two-asset closed form, high-vol asset raises risk,
correlated assets are not diversification), band-boundary and monotonicity
tests for the staged brake, the mode-dispatch regression (rebalance no
longer validates volatility-budget targets against the legacy ladder), and
the stress contract (canonical set, moderate preservation, correlation_one
uniformity, depeg-only stable stress).

## Test result

`python3 -m unittest discover -s tests` → 1231 tests, all passing.
`ruff check .` clean; `python3 -m compileall crypto_portfolio scripts` clean.

## Baseline comparison (A/B replay, 2024 window, full/core_existing strict)

| Metric | legacy_drawdown (baseline) | volatility_budget (placeholders) |
| --- | --- | --- |
| CAGR | 5.67% | 11.93% |
| MaxDD | −14.98% | −22.52% |
| Ann. vol | 12.21% | 19.36% |
| Sharpe | 0.513 | 0.679 |
| Avg cash | 84.97% | 61.68% |
| Trades | 196 | 120 |
| Regime CP/DEF/NOR | 590/250/155 | 393/144/458 |

Volatility-budget diagnostics: average estimated portfolio volatility
22.3%; binding constraint {emergency_overlay 288, strategic_target 426,
volatility_budget 281}; emergency states {NORMAL 520, CAUTION 92,
EMERGENCY 95, BREACH 288}.

**Diagnostic finding (not acted on):** with the placeholder parameters the
staged brake does not hold D = 15% — the book breaches to −22.5% because
25%-vol normal sizing carries more pre-gap exposure than the legacy ladder
and the breach stage (25% risky cap) is reactive. Closing that gap between
the vol target, the stage caps, and D belongs to Phase 6 calibration; per
the plan's agent rules no parameter was adjusted to improve this result.

## Known limitations

- The volatility-budget mode has no production wiring yet: the live review
  pipeline does not assemble multi-asset daily closes into
  `PortfolioRiskInputs`. That wiring lands when the mode is promoted after
  calibration.
- Emergency stage caps and vol targets are placeholders (see above).
- Stress scenario values are framework placeholders; only `moderate` is
  calibrated to V1.
- The combined-cap minimum currently joins the two portfolio-level caps
  (volatility budget, emergency brake) plus the base stable floor; per-asset
  event/liveness deployment caps were already minimum-combined per asset and
  are reorganized in Phase 3 (permission vs sizing).

## Open questions

- Phase 6 must reconcile `target_volatility` (25%), the breach-stage risky
  cap (25%), and D = 15% — the A/B finding above shows they cannot all hold
  simultaneously under 2024-style gaps.

## Ready for next phase?

YES — all Phase 1 acceptance criteria hold: portfolio volatility,
correlation, beta, and MRC are reproducibly computable; normal risk
budgeting no longer depends on drawdown in `volatility_budget` mode;
drawdown is only the emergency brake there; legacy mode replays unchanged
(1231-test suite green, A/B flag verified on the frozen 2024 window).
