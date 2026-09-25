# Strategy V2 — Phase 4 Report: Deterministic Risk Tier

Branch `strategy-v2-risk-tier` (from `strategy-v2` @ `49cea27`).

## Changed files

- `crypto_portfolio/engine/risk_tier.py` (new):
  `deterministic_risk_tier` (volatility-ratio + beta with entry/exit
  hysteresis, provenance-carrying basis), `estimate_risk_tiers`
  (BTC-anchored batch), `tier_strategic_fraction` (engine-aware fraction
  semantics).
- `config/policy.json` + `crypto_portfolio/models/policy.py`: required
  `risk_tier_estimation` block — beta 1.5/1.3 and relative-vol 1.6/1.3
  enter/exit thresholds, `minimum_history_days` 100 (placeholders pending
  Phase 6).
- `crypto_portfolio/engine/allocation.py`: the strategic envelope fraction
  resolves through `tier_strategic_fraction` — a `DETERMINISTIC_ESTIMATE`
  tier competes for the full envelope under `volatility_budget` mode
  (hard caps still bound exposure), while manual and policy-default tiers
  keep their configured fractions; `legacy_drawdown` keeps V1 fractions
  for every source.
- `crypto_portfolio/research/historical_builder.py`: replay assessments
  carry measured tiers computed from the same completed candles the
  factors use (90D vol + beta, hysteresis carried across boundaries via
  the previous boundary's tiers; an undefined measurement leaves the tier
  unmeasured, never fabricated).
- `references/risk-model.md`: deterministic-tier documentation.
- Tests: `tests/test_deterministic_risk_tier.py`,
  `tests/test_risk_tier_hysteresis.py`,
  `tests/test_risk_tier_provenance.py`.

## Strategy behavior changed

- Replay (research) assessments now carry measured tiers with
  `DETERMINISTIC_ESTIMATE` provenance.
- Under `volatility_budget` mode, a measured high/high_beta tier no longer
  halves the strategic satellite envelope (sizing belongs to the risk
  engine; the tier's hard cap still bounds maximum exposure).

## Strategy behavior intentionally unchanged

- Tier names, `risk_tier_caps` values, hard-cap buffers, and every
  production/live behavior: default mode is `legacy_drawdown`, where all
  sources keep the V1 fractions exactly; manual assessments are honored
  with their configured fractions in both engines.

## New configuration

- `risk_tier_estimation`: {beta_enter 1.5, beta_exit 1.3,
  relative_vol_enter 1.6, relative_vol_exit 1.3, minimum_history_days
  100} — placeholders pending Phase 6 calibration.

## New diagnostics

- Tier derivation basis (volatility ratio, beta, previous tier, entry
  reason) returned by the estimator and available to reports; allocation
  allowances already carry `risk_tier_source`.

## Tests added

13 tests: derivation (normal vs high_beta, measurement never yields the
manual-only `high`, fail-closed on zero/undefined inputs), hysteresis
(threshold oscillation, band semantics, 60-step oscillation sequence with
zero flips), provenance (source survives allocation; fraction semantics
matrix across source × engine), and the secondary-constraint behavior
(measured tier envelope = 2 × manual envelope under the vol budget).

## Test result

Full suite 1293 tests passing; `ruff check .` and `compileall` clean.

## Baseline comparison

No replay re-run: live behavior is unchanged (legacy mode, policy-default
tiers in production), and the replay's measured tiers affect only
`volatility_budget` A/B runs, whose systematic comparison belongs to
Phase 6.

## Known limitations

- Thresholds are placeholders; 90-day max drawdown and liquidity inputs
  (plan 4.2's fuller input set) are deliberately deferred to keep the
  first version simple, per the plan's own instruction.
- Tier estimation in the replay swallows the whole boundary's tiers if any
  single asset's series is degenerate at that boundary (documented
  fail-closed coarseness).

## Open questions

- Should the `high` tier remain distinct from `high_beta` once every
  long-held asset is measured, or merge into one measured band?

## Ready for next phase?

YES — long-held assets get tiers from deterministic estimates with
provenance and hysteresis; the tier is a secondary constraint under the
V2 risk engine; volatility/correlation/MRC own sizing; no daily tier
flapping.
