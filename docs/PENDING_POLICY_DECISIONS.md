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

## 8H — Score reachability under partial factor coverage

Current behavior: `engine.scoring._score_factors` composes the base score as
`sum(weight_i * effective_i)`, where an AVAILABLE factor contributes
`50 + reliability_i * (raw_i - 50)` and a MISSING factor with positive weight
contributes exactly 50. Because `raw_i` is bounded to [0, 100], the score an
asset can possibly attain is `50 +/- 50 * coverage` with
`coverage = sum(weight_i * reliability_i)`. The configured thresholds are
expressed in the full-information space, so their reachability depends on how
much of the profile weight is actually backed by evidence: a threshold `t`
needs `coverage >= (t - 50) / 50`. On the 2024-01-01 to 2026-09-21 panel,
coverage never exceeded 0.45 (`btc` profile 0.35), giving reachable bands of
[32.5, 67.5] and [27.5, 72.5]. `satellite_full_score` 85 therefore requires
coverage 0.70 and fired in 0 of 1990 core and 0 of 4975 full readings, and the
ETH gate at 55 sits close to the ceiling. Separately, coverage below
`scoring.minimum_investable_coverage` 0.6 forces the LOW band, and
`execution.confidence_deployment_factor.LOW` is a hard `0.0`, so a
score-driven increase is blocked regardless of score; on that panel the score
still reached 67 in 13.0% of readings, but the strategy executed no buys in the
strict mode. This is the mechanism behind the observed "only reduces, never
re-enters" path; it is a property of the evidence set, not of the market.

Candidate direction: express thresholds as positions inside the reachable band
for the coverage actually available (equivalently rescale
`score' = 50 + (score - 50) / coverage`) so a threshold and a score are compared
in the same space, or make the semantic factors evidence-required rather than
neutral-pinned. Define an explicit low-evidence contract at the same time:
either "de-risk only and label the run `NOT_A_TEST`", or a declared
trend-only conservative entry path. Do not adjust the legacy integers by
inspection, and do not weaken the missing-factor rule that keeps absent
evidence counted at its configured weight.

Required evidence: a frozen per-review-date panel carrying per-factor
availability and reliability, so the achievable band per profile per date is
known independently of the score; the before/after distribution of rescored
values; a demonstration that rescaled thresholds restore the intended
percentile separation across the entry/exit/soft-exit/full tiers rather than
merely making them attainable; and a turnover and chase-entry check. Until
then thresholds stay unchanged.

## 8I — Drawdown-budget feasibility under the configured regime floors

Current behavior: `risk.max_portfolio_drawdown` (0.15) is compared against a
realized drawdown, but nothing asks whether the configured regimes can satisfy
it in the first place. The most defensive portfolio a regime permits holds
`stablecoin_target` in stables, so the implied drawdown is
`(1 - stablecoin_target) * stressed_risky_return`. Under the policy's own
`stress_scenario` with the core anchor (0.7 BTC at -0.2, 0.3 ETH at -0.3, i.e.
-0.23) this projects -0.1955 in NORMAL and -0.1610 in DEFENSIVE, both beyond the
0.15 budget; only CAPITAL_PRESERVATION fits at -0.1150. Concentrating the risky
sleeve in the worst configured asset (-0.40) breaches in every regime: NORMAL
-0.34, DEFENSIVE -0.28, CAPITAL_PRESERVATION -0.20. Holding the budget under
the core-anchor stress needs a stablecoin target of at least 0.3478, and under
the worst-asset stress at least 0.6250, which no regime reaches. The window
itself realized a BTC drawdown of -0.5306, a factor of 2.65 over the configured
-0.2, so the budget was calibrated against a materially milder tail than the
one that occurred. The observed strategy maximum drawdown of -32.13% is
consistent with the DEFENSIVE projection, which is evidence the engine follows
its configuration rather than evidence of slow execution.

Candidate direction: decide first whether the 15% budget is defined against the
`stress_scenario` or against realized history. If against the stress scenario,
raise `stress_scenario` toward observed tail behavior. If against realized
history, lift the `regimes.*.stablecoin_target` targets (and confirm
`core_risky_min` no longer floors exposure above what the budget allows) so the
most defensive regime can actually meet the budget. Then add a load-time
feasibility assertion, per regime and under the configured scenario, so the
contradiction cannot be reintroduced silently. The conservative-balanced
posture, the core sleeve anchor, and `min_stablecoin_weight` are not part of
this decision.

Required evidence: an explicit statement of what the budget is measured
against; a drawdown tail study over a window longer than one cycle to set the
stress scenario; confirmation that revised targets simultaneously satisfy
`min_stablecoin_weight`, `core_risky_min`, `satellite_max`, and
`single_asset_max`; and out-of-sample evidence that the revised budget is met
rather than merely declared. Both 8H and 8I are reported by
`crypto_portfolio/engine/feasibility.py` and `scripts/strategy_validity.py`;
those checks assert the properties, they do not change any number.

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
