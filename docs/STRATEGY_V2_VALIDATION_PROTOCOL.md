# Strategy V2 Validation Protocol (Phase 6)

This document freezes the validation protocol: what is validated, how, and
what counts as passing. It is the contract behind
`scripts/backtest.py validate` / `ablation` and
`crypto_portfolio/research/validation.py`.

## Freeze record (6.1)

Every validation artifact records:

- the git SHA it ran on and the canonical policy hash;
- the dataset manifest id and its `strict_ready` flag;
- the window and the canonical experiment name;
- the universe and the point-in-time eligibility rules.

Reproducibility rule: a result that cannot be tied to these four is not a
result. The current freeze lives in `docs/STRATEGY_V2_BASELINE.md` (V1
control group) and each phase report; the validation JSON carries its own
freeze block.

## Data requirements (6.2)

Frozen OHLCV and harvested public evidence enter point-in-time; the
judgment layer (fundamentals, satellite valuation, events, liveness) stays
deliberately `MISSING` in replay rather than being back-filled. Missing
inputs are fail-closed everywhere in this pipeline. A synthetic-backtest
path is never presented as empirical validation of the judgment layer —
it tests mechanism only.

## Dynamic universe (6.3)

Universe membership is point-in-time and enforced in the replay itself
(`dynamic_universe` policy block, enabled by default): BTC is the anchor
member; every other asset must show at least `minimum_history_days` (365)
of completed daily observations and a median dollar volume at or above
`minimum_median_volume_usd` ($1M placeholder) at THAT boundary, computed
from trailing data only. A non-member receives no assessment and no
technical snapshot — no new entries — while prices and returns keep
flowing so held positions stay marked and reducible. This is the
anti-survivorship constraint: no asset is investable before its own
history qualifies it. `dynamic_universe_eligibility` in
`research/validation.py` reports the same rule for the audit timeline.

## Validation windows (6.4)

At minimum: the 2021–2023 bear window (`strategy-validation-2021-2023-bear`,
spanning the 2021 top, the 2022 drawdown, and the 2023 recovery), the
2024–present mixed window, and sideways/recovery sub-windows carved from
them. One complete cycle minimum before any calibration conclusion.

## Walk-forward (6.5)

`walk_forward_windows` emits rolling non-overlapping train/validate
calendars (default 2y train / 1y validate). Calibration happens only on
train windows; validate windows are consumed once, in order, and never
re-consumed after a parameter change — a re-consumed validation window has
become a training window and must be renamed as such in the report.

## Parameter classification (6.6)

See `PARAMETER_CLASSIFICATION` in `crypto_portfolio/research/validation.py`
(the machine-readable registry) — structural parameters (ownership, hard
gates, score semantics, benchmark hierarchy) are never searched; calibrated
parameters (vol targets, emergency caps, coverage floor, satellite
thresholds, tier thresholds, WAIT expiry, regime weights, stress values)
are the tunable surface; forbidden: any grid search maximizing a single
historical window's CAGR or metric. A parameter moves only with a
documented reason that is not a backtest result.

## Threshold calibration (6.7)

`threshold_rank_monotonicity` buckets normalized (and effective) scores
against realized 30/90/180-day forward and BTC-relative returns and reports
per-bucket samples and means plus the adjacent-monotonicity share. The
question is monotone ranking power, not threshold levels: if higher buckets
do not carry better outcomes at a horizon, the score has no ranking power
there and no threshold choice can fix that. The 57/62/67/85 values are
re-examined only after the ranking-power question is answered.

## Layer ablation (6.8 / 6.9)

`scripts/backtest.py ablation` replays the canonical experiment under
policy variants: without fundamentals / valuation / on-chain / relative
strength, without the regime trend domain, without the execution overlay,
without satellites, without the drawdown emergency overlay — each against
the same baseline replay. A module whose removal improves return, risk,
and turnover simultaneously must re-justify its existence before the next
calibration round.

## Block bootstrap (6.10)

`block_bootstrap` (seeded, circular, default 21-day blocks, 200 draws)
resamples the realized daily-return path and reports MaxDD / CAGR / Sharpe
distributions and the risk-budget breach frequency. The standing question
for the 15% budget: is holding it the median outcome or the lucky tail?

## Gap risk (6.11)

`gap_risk_stress` applies −10/−20/−30% one-day shocks at the strategy's
maximum-exposure valuation: pre-shock exposure, shock loss, projected
drawdown, breach flag, remaining capacity. A reactive overlay absorbs the
first gap at pre-shock exposure by construction; the numbers quantify how
much of the budget that costs.

## Stablecoin stress (6.12)

`stablecoin_stress` prices the configured `stablecoin_depeg` scenario over
the sleeve, a −20% single-issuer event on the largest holding, and full
custody loss, alongside issuer concentration. Issuer/venue caps were
deliberately NOT introduced in Phase 6: the numbers feed that pending
policy decision.

## Acceptance criteria (6.13)

Passing is NEVER `CAGR > X`. A validation round passes when:

1. the strategy runs in every market regime window without errors or
   degenerate paths;
2. no structural degenerate path exists (one-way ratchets, zero-trade
   tails flagged by the validity gate are findings, not passes);
3. entry/exit thresholds are reachable in the score space actually used;
4. no one-way ratchet (buys and sells both occur across the windows);
5. risk-budget mechanisms explain their own binding behavior
   (`risk_engine_diagnostics` present and consistent with the overlay
   states);
6. the risk-matched benchmark comparison is fair (vol-match tolerance
   met) and reported first;
7. walk-forward validation windows show no collapse exclusive to
   out-of-sample steps;
8. small parameter perturbations do not reverse conclusions (sign
   stability across the bootstrap distribution);
9. attribution explains where return and risk came from (risk scaling,
   timing, cash, vol-matched excess);
10. no look-ahead or survivorship bias is detectable in the freeze audit
    (point-in-time slices, dynamic-universe timeline).

Any unmet criterion is reported as a finding with its evidence, never
absorbed into a passing summary.
