# Strategy V2 — Phase 2 Report: Score / Coverage Math

Branch `strategy-v2-score-coverage` (from `strategy-v2` @ `0b56085`).

## Changed files

- `crypto_portfolio/engine/scoring.py`: `score_reachability` (deterministic
  `[50-50c, 50+50c]` reachable range), `coverage_normalized_score`
  (`50 + (effective-50)/coverage`, clipped, unavailable below the minimum
  normalization coverage), `low_evidence_contract`
  (ACTIONABLE/LIMITED/NOT_ACTIONABLE, `may_force_reduce` always false);
  `ScoreResult` carries `normalized_score`, `reachable_min/max`,
  `minimum_normalization_coverage`, and the raw per-factor inputs.
- `crypto_portfolio/models/evidence.py`: `AssetAssessment.normalized_score`
  (validated [0,100] or null, round-trips through mappings).
- `crypto_portfolio/engine/allocation.py`: the comparison score prefers the
  normalized space, falling back to the effective score when normalization
  is unavailable; deployment allowances report `effective_score`,
  `normalized_score`, and the per-asset `evidence_class`.
- `crypto_portfolio/engine/core_eligibility.py`: the ETH core gates read the
  same normalized-first comparison space as the satellite gates.
- `crypto_portfolio/models/policy.py` + `config/policy.json`:
  `scoring.minimum_normalization_coverage` = 0.6 (placeholder aligned with
  the investable floor; Phase 6 calibrates).
- `schemas/decision.schema.json`: assessment subschema allows the persisted
  `normalized_score`.
- `crypto_portfolio/research/score_evaluation.py` + `scripts/backtest.py`:
  score observations carry `normalized_score` through to
  `score-evaluation.json` (input to Phase 6 threshold calibration).
- `references/scoring-model.md`: the three score spaces, the normalized
  threshold comparison rule, and the low-evidence contract.
- Tests: `tests/test_score_reachability.py`,
  `tests/test_score_coverage_normalization.py`,
  `tests/test_low_coverage_contract.py`,
  `tests/test_score_threshold_space.py`.

## Strategy behavior changed

- Satellite thresholds (57/62/67/85), the satellite target curve, the ETH
  core gates (55/45 + relative bands), and the core quality multiplier now
  compare the coverage-normalized score instead of the reliability-shrunk
  effective score. At coverage 1.0 the two spaces coincide, so fully
  evidenced decisions are unchanged; with degraded-but-complete evidence
  (the production norm, coverage ~0.7–0.9) threshold decisions read the
  normalized attractiveness while coverage keeps capping deployment.
- Scored assessments persist `normalized_score` alongside `weighted_score`.

## Strategy behavior intentionally unchanged

- Effective-score math, reliability derivation, coverage definition,
  confidence bands, deployment factors, the preserve/hold semantics for
  missing evidence, and every risk gate. Coverage below the normalization
  floor leaves thresholds on the effective score exactly as before.

## New configuration

- `scoring.minimum_normalization_coverage` = 0.6 (placeholder; Phase 6).

## New diagnostics

- `ScoreResult.normalized_score` / `reachable_min` / `reachable_max` /
  `raw_factor_scores`; assessment `normalized_score`;
  per-asset `evidence_class` in deployment allowances;
  `normalized_score` in replay score observations.

## Tests added

30 tests across four files, covering the plan's required cases: coverage 1.0
→ normalized == effective; raw maximum reachable at coverage 0.4 →
normalized ≈ 100; coverage below the minimum → NOT_ACTIONABLE and no
normalized score; temporary coverage collapse of a held satellite →
preserve bucket, never a forced REDUCE; satellite and ETH gates verified to
share the normalized space with effective-score fallback.

## Test result

Full suite 1262 tests passing; `ruff check .` and `compileall` clean.

## Baseline comparison

No replay re-run in this phase: the mechanism is score-space only, and the
frozen replay's strict-mode coverage structure (MISSING judgment factors,
not degraded reliability) means the normalized space is largely unavailable
there — the fallback keeps replay semantics identical, which the unchanged
legacy test expectations confirm. The A/B effect of the normalized space on
live-quality evidence (complete factors, reliability < 1) is measured in
Phase 6 alongside threshold recalibration.

## Known limitations

- `minimum_normalization_coverage` is a placeholder pending Phase 6.
- The four satellite threshold values were NOT re-derived (plan 2.7 keeps
  them; Phase 6 re-tests monotonicity of the normalized distribution).
- The plan's "MEDIUM coverage → capped sizing / LOW → no new risk" numeric
  multipliers were left as configured (1.0/0.75→0.7 execution-side, LOW
  0.25): the contract classifies and reports, it does not yet re-tune the
  deployment ladder.

## Open questions

- Whether `LOW`-band coverage should zero new deployment (currently 0.25×)
  is a Phase 6 calibration question, not a Phase 2 mechanism question.

## Ready for next phase?

YES — thresholds and scores share one mathematical space; data missingness
no longer changes the theoretical score ceiling; coverage limits deployment
through the explicit contract; LOW coverage provably cannot manufacture a
sell signal; raw scores are preserved for diagnostics.
