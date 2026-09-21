# Pending investment-policy decisions (phase 8)

Status: 2026-09-21. Items 8A-8E were decided by the 2026-09-15 structural
refactor plan (portfolio strategy/scoring/allocation refactor) and
implemented on the `strategy-structural-refactor` branch; each entry below
records the decision and where it landed. Items 8F and 8G were opened by the
2026-09-21 scoring-layer review and remain PENDING: they record the current
behavior, the candidate direction, and the evidence required before approval.
New pending items follow the same convention. Nothing in the PENDING entries
has been applied — the thresholds, multipliers, and tier logic they describe
are unchanged in `config/policy.json`.

| ID | Decision | Status |
|---|---|---|
| 8A | Satellite hysteresis across the entry boundary | DECIDED: continuous target curve (phase 1) |
| 8B | Core targets vs temporary confidence/event limits | DECIDED: multipliers stay in core raw proportions; water-fill redistribution only to ELIGIBLE_INCREASE (phase 3) |
| 8C | Risk budget, BTC eligibility, AAVE risk multiplier | DECIDED: risk tier becomes an exposure cap, not a multiplier (phase 2); BTC eligibility and the reactive drawdown budget stay unchanged |
| 8D | Regime debounce / idempotence | DECIDED: weighted severity regime model + existing one-notch cap (phase 8); dwell-time requirements not adopted |
| 8E | Sub-threshold risk-repair trades and friction | DECIDED: no repair trades; staged execution (phase 4/5) replaces one-review corrections, unresolved constraints stay explicit |
| 8F | Score-threshold recalibration after the trend neutral-point fix | PENDING: thresholds left unchanged; requires a frozen point-in-time score panel |
| 8G | Deterministic `risk_tier` estimate from volatility/beta | PENDING: tier stays an assessment input; requires out-of-sample calibration evidence |

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

## 8F — Score-threshold recalibration after the trend neutral-point fix

Current behavior: the trend factor has a structural base of 50, but its
moving-average comparison counted `spot >= ma` as bullish, so a fully
undetermined trend (price exactly on each MA, no alignment, no momentum)
scored 81 instead of neutral. Trend carries the largest profile weight (0.30
`default`, 0.35 `btc`), so every base score for an asset in a flat or
indeterminate trend state was inflated. The 2026-09-21 correction makes MA
authority strictly directional — above adds, below subtracts, an exact tie
contributes nothing — so a no-information trend now reads 50
(`tests/test_trend_neutral_calibration.py`). The score thresholds were
calibrated against the old, higher distribution and were deliberately left
unchanged:

- satellite curve: `satellite_entry_score` 67, `satellite_exit_score` 62,
  `satellite_soft_exit_score` 57, `satellite_full_score` 85;
- relative-strength gate: `increase_min_score` 50, `hard_block_below_score` 30;
- ETH core gate: `increase_min_score` 55, `hold_min_score` 45,
  `relative_increase_min_score` 45, `relative_reduce_below_score` 30.

Candidate direction: re-derive those breakpoints from the corrected score
distribution — percentile-anchored on a frozen historical panel, or otherwise
calibrated rather than inherited — instead of adjusting the legacy integers by
inspection. The directional trend weight (0.30/0.35) is NOT part of this
decision: the review concluded the weight is not too low, so it stays.

Required evidence: a frozen point-in-time panel of scored snapshots (per
asset, per review date) produced by the corrected deterministic trend factor,
covering enough regime variety to place the entry/exit breakpoints at
meaningful percentiles; the before/after score distributions; and a check that
recalibrated thresholds neither admit chase entries nor push turnover beyond
the existing bands. Until then thresholds stay unchanged and only the
diagnostic distributions are reported.

## 8G — Deterministic `risk_tier` estimate

Current behavior: `risk_tier` (`normal` / `high_beta` / `high`) is an
assessment input. The contract already reserves `POLICY_DEFAULT`,
`MANUAL_ASSESSMENT`, and `DETERMINISTIC_ESTIMATE` as provenance values, but
nothing in the repository ever emits `DETERMINISTIC_ESTIMATE`: the tier is
either the policy default `normal` or a semantic/manual declaration
(`crypto_portfolio/engine/allocation.py`). `allocation.risk_tier_caps` then
caps the satellite envelope (normal 1.0, high_beta/high 0.5). Horizon-matched
realized volatility and beta-to-BTC data are already collected for other
factors, so the tier is the one remaining purely semantic risk input.

Candidate direction: derive the tier deterministically in Python from
horizon-matched realized volatility and/or beta to BTC (e.g. fixed percentile
bands over a rolling window), keep the semantic route available as an explicit
override, and label each producer. The tier must remain a structural envelope
cap and never a return-enhancing scalar.

Required evidence: a frozen point-in-time volatility/beta panel showing stable
tier assignment across the universe; confirmation that the deterministic tier
reproduces or safely dominates the current assignments (AAVE `high_beta`,
others `normal`) without boundary flapping between tiers; and an out-of-sample
check that reclassification does not silently change the portfolio drawdown
budget. Until approved, `risk_tier` stays an assessment input and empirical
calibration remains a separate task.

## Post-review corrections (2026-09-21)

The 2026-09-21 scoring-layer review produced four changes that are
implemented, not pending:

- LINK is removed from the universe entirely (no longer monitored or scored):
  `config/policy.json` satellites are now `SOL`, `BNB`, `AAVE`; LINK is gone
  from the metric registry, metric plan, availability profiles, provider
  symbol/identifier maps (Binance, Bybit, CoinGecko, DeFiLlama, LunarCrush),
  and routes. A repository-wide grep for the asset returns nothing.
- The duplicate fee/revenue ratio metric (`valuation.fee_revenue_multiple`) is
  removed for every asset, not only BNB: it double-counted the same DeFiLlama
  fee series already used by `fundamentals.fees_30d`/`revenue_30d`.
- A semantic `FactorJudgment.confidence` (HIGH 1.0 / MEDIUM 0.75 / LOW 0.5)
  now enters reliability instead of being ignored, so a low-confidence
  semantic factor shrinks toward neutral and lowers coverage
  (`tests/test_semantic_judgment_reliability.py`).
- `stress_scenario` now covers every core and satellite asset
  (BTC/ETH/SOL/AAVE/BNB) and is validated as such in
  `crypto_portfolio/models/policy.py`, so `portfolio_stress` no longer returns
  UNAVAILABLE for an uncovered satellite.

## Verification boundaries

- Phases 0-6 fixes are covered by the regression suite (668 -> 688 tests).
- The 2026-09-21 scoring-layer corrections bring the suite to 920 tests
  (`python -m unittest discover -s tests`), with dedicated regression files for
  the trend neutral point (`tests/test_trend_neutral_calibration.py`) and the
  semantic-judgment reliability path
  (`tests/test_semantic_judgment_reliability.py`).
- Phase 7 delivered tooling validated on synthetic fixtures only; no claim of
  historical strategy validation is made, and none is possible until frozen
  point-in-time evidence exists outside the repository. This is precisely why
  8F and 8G stay PENDING.
- The repository Skill (`crypto-portfolio-manager`) was not read or modified
  per the execution contract; if its orchestration text references the old
  confidence-label or relative-strength-number semantics, syncing it is a
  separate follow-up task for the owner.
