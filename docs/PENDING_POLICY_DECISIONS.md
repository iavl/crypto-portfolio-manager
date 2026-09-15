# Pending investment-policy decisions (phase 8)

Status: 2026-09-15. Items 8A-8E were decided by the 2026-09-15 structural
refactor plan (portfolio strategy/scoring/allocation refactor) and
implemented on the `strategy-structural-refactor` branch; each entry below
records the decision and where it landed. New pending items would follow the
same convention: current behavior, candidate direction, and the synthetic
evidence required before approval.

| ID | Decision | Status |
|---|---|---|
| 8A | Satellite hysteresis across the entry boundary | DECIDED: continuous target curve (phase 1) |
| 8B | Core targets vs temporary confidence/event limits | DECIDED: multipliers stay in core raw proportions; water-fill redistribution only to ELIGIBLE_INCREASE (phase 3) |
| 8C | Risk budget, BTC eligibility, AAVE risk multiplier | DECIDED: risk tier becomes an exposure cap, not a multiplier (phase 2); BTC eligibility and the reactive drawdown budget stay unchanged |
| 8D | Regime debounce / idempotence | DECIDED: weighted severity regime model + existing one-notch cap (phase 8); dwell-time requirements not adopted |
| 8E | Sub-threshold risk-repair trades and friction | DECIDED: no repair trades; staged execution (phase 4/5) replaces one-review corrections, unresolved constraints stay explicit |

## 8A — Satellite hysteresis across the entry boundary

Current behavior (reproduced in `tests/test_strategy_review_repro.py`,
F1): with SOL held at 10%, scores 62-66 keep the position (HOLD_ONLY band),
score 67 turns ELIGIBLE with score-strength 0 and the strategic target drops
to 0%, so a held satellite's target falls exactly when its score improves
across the entry line. The exit side already has a soft-exit band
(`satellite_soft_exit_score=57`, fraction 0.5).

Candidate directions to choose between (not both silently):

1. strategic-target band controlling trades, with deferred reduction handled
   independently of the target;
2. a bounded position-aware hysteresis curve (monotone in score for a fixed
   portfolio and holding).

Decision (2026-09-15): the continuous target curve implements direction 1
plus a pure score-only curve — `satellite_target_fraction` is bounded,
monotonic, continuous at every breakpoint, and independent of current weight,
so repeated reviews of identical evidence are idempotent and the 66.9 -> 67.0
cliff is gone. The score-grid sweep is a regression test
(`tests/test_satellite_target_curve.py`); `max(current, target)` and a
one-off special case at 67 were both rejected.

## 8B — Core targets vs temporary confidence/event limits

Current behavior (F3): core allocation multiplies confidence multipliers
(HIGH 1.0 / MEDIUM 0.75 / LOW 0.25) and event-risk multipliers into the
strategic target, so a temporary confidence drop mechanically REDUCEs core
exposure while satellites already separate strategic targets from deployment
allowances. Documentation conflicts: the general strategy says temporary
uncertainty must not alone lower targets; core tests encode the old behavior.

Candidate direction: treat core like satellites - confidence and
ELEVATED/HIGH events limit only new deployment; severe/critical events,
broken theses, and independent portfolio risk keep independent reduce rights.
Temporary data missing must never be the sole executable reduce reason, and
score shrinkage from missing data is not removed.

## 8C — Risk budget, BTC eligibility, AAVE risk multiplier

Current behavior: the 15% drawdown budget is enforced only reactively
(drawdown floors in the regime engine); `stress_diagnostic` now exists as
DIAGNOSTIC_ONLY output. BTC's core state never reads the BTC score, so
BTC 0/20/100 can all receive the same core budget when ETH is gated. AAVE
satellite sizing applies the fixed `high_beta` 0.5 risk multiplier with no
calibration note beyond attribution.

Decisions required: which scenarios (if any) become binding constraints on
new risk; whether volatility/correlation inputs enter; when BTC itself
becomes HOLD_ONLY/reduce; whether the fixed 0.5 multiplier stays. Until
approved with out-of-sample support, weights and multipliers stay unchanged
and only phase-6 diagnostics are reported.

## 8D — Regime debounce / idempotence

Current behavior (F7): transitions are capped at
`regime_transitions.max_notches_per_review=1` per review, so the *same*
BEARISH/HIGH/OUTFLOW evidence escalates DEFENSIVE -> CAPITAL_PRESERVATION on
the second review without any new market time. Drawdown floors and severe
systemic events are immediate and never delayed.

Decisions required: minimum dwell time or independent-observation
requirement; whether recovery is slower than deterioration; keeping
asset-level ELEVATED/HIGH distinct from portfolio systemic status. Status
events remain append-only.

## 8E — Sub-threshold risk-repair trades and friction

Current behavior (F6): rebalance keeps the 2/4pp turnover bands, and the
post-action projection now reports `STABLECOIN_FLOOR_UNRESOLVED` /
`*_UNRESOLVED` constraints instead of hiding them - no small repair trade is
created.

Decisions required: whether sub-threshold minimal repair recommendations are
allowed; funding priority when cash is short; whether fees/min-trade-size
become production inputs. Until approved, unresolved constraints stay
explicit and risk-increasing actions remain blocked while a hard floor is
unmet.

## Verification boundaries

- Phases 0-6 fixes are covered by the regression suite (668 -> 688 tests).
- Phase 7 delivered tooling validated on synthetic fixtures only; no claim of
  historical strategy validation is made, and none is possible until frozen
  point-in-time evidence exists outside the repository.
- The repository Skill (`crypto-portfolio-manager`) was not read or modified
  per the execution contract; if its orchestration text references the old
  confidence-label or relative-strength-number semantics, syncing it is a
  separate follow-up task for the owner.
