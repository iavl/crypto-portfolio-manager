# Policy Feasibility and Backtest Validity

A backtest can be arithmetically correct and still not be a test of the
strategy. This subsystem answers two questions that the historical research
harness cannot ask about itself, using only the policy and the artifacts a run
already wrote.

1. **Policy feasibility** — can the configured score thresholds and the
   configured risk budget be satisfied at all, given the policy's own weights
   and its own stress scenario?
2. **Run validity** — does a finished backtest directory actually exercise the
   strategy, or does it measure a degenerate path (missing evidence, blocked
   entries, one-way trading, unpowered inference)?

Everything here is read-only. It never rewrites the policy, the run, or any
artifact, and it changes no threshold, budget, or regime target. Findings are
registered for decision in `docs/PENDING_POLICY_DECISIONS.md` (8H, 8I) rather
than applied.

## Score reachability

`engine.scoring._score_factors` composes the base score as

```text
coverage    = sum_i(weight_i * reliability_i)
effective_i = 50 + reliability_i * (raw_i - 50)   # AVAILABLE
effective_i = 50                                  # MISSING with positive weight
score       = sum_i(weight_i * effective_i)
```

`raw_i` is bounded to [0, 100], so an AVAILABLE factor contributes within
`50 +/- 50 * reliability_i` and a MISSING factor contributes exactly 50. The
reachable score interval is therefore

```text
score in [50 - 50 * coverage, 50 + 50 * coverage]
```

The half-width is `50 * coverage`. A threshold `t` needs

```text
coverage >= (t - 50) / 50
```

before it is reachable at all, and that requirement depends only on how much of
the profile weight is backed by evidence — never on market conditions.

A second, sharper failure: `engine.scoring._coverage_gate_band` forces the LOW
band whenever coverage is below `scoring.minimum_investable_coverage`, and
`execution.confidence_deployment_factor.LOW` is a hard `0.0` in the canonical
policy. When both hold, every score-driven increase is blocked regardless of
score, so the strategy can only ever reduce exposure.

## Drawdown-budget feasibility

`risk.max_portfolio_drawdown` is compared against a realized drawdown, but
nothing asks whether the configured regimes can satisfy it. The most defensive
portfolio a regime permits holds `stablecoin_target` in stables, so

```text
implied drawdown = (1 - stablecoin_target) * stressed_risky_return
required stablecoin target = 1 - budget / |stressed_risky_return|
```

Two compositions are evaluated: `CORE_ANCHOR` (the risky sleeve held as the
`core_allocation` anchor, the expected composition) and
`WORST_CONFIGURED_ASSET` (the whole risky sleeve in the asset with the worst
configured stress return, a conservative bound). Stables are assumed to return
zero, which is the existing diagnostic convention and not a claim that a peg is
risk-free.

Passing `--realized-drawdown` style inputs makes the checker compare the
configured stress against observed history, which is what distinguishes "the
execution was too slow" from "the budget was never reachable".

## Commands

```bash
# policy only, at an assumed coverage level
python3 scripts/strategy_validity.py --coverage 0.45

# policy plus a finished run directory (coverage is read from the run)
python3 scripts/strategy_validity.py \
  ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present

# machine-readable
python3 scripts/strategy_validity.py <run_dir> --json
```

Exit status is 1 when any ERROR-severity finding is present.

## Finding codes

Policy feasibility (`crypto_portfolio/engine/feasibility.py`):

| Code | Severity | Meaning |
|---|---|---|
| `SCORE_BAND_COLLAPSED` | WARNING | reachable band is narrower than the full scale |
| `SCORE_THRESHOLD_UNREACHABLE` | ERROR for entry-style, else WARNING | threshold needs more coverage than is available |
| `ENTRY_LOCKED_BY_COVERAGE` | ERROR | coverage gate is LOW and the deployment factor zeroes it, so increases are blocked |
| `DRAWDOWN_BUDGET_INFEASIBLE` | ERROR | a regime at its most defensive target still breaches the budget |
| `REGIME_BELOW_REQUIRED_STABLE_TARGET` | WARNING | the regime's stable target is below what the budget needs |
| `STRESS_UNDERSTATES_REALIZED` | WARNING | realized drawdown exceeds the configured stress |

Run validity (`crypto_portfolio/research/validity_gate.py`):

| Code | Severity | Meaning |
|---|---|---|
| `MANIFEST_MISSING` | ERROR | no data manifest to review |
| `MANIFEST_BLOCKERS_IGNORE_SIGNAL_LAYER` | INFO | blockers cover series completeness only, not factor coverage |
| `COVERAGE_BELOW_INVESTABLE` | ERROR | median coverage never reaches the investable floor |
| `SORTING_UNDERPOWERED` | ERROR | too few independent blocks behind the rank correlation |
| `ONE_WAY_RATCHER` | ERROR | every trade is the same direction |
| `NO_TRADES_IN_WINDOW` | WARNING | the experiment never traded |
| `TRADING_STALLED` | WARNING | last trade is far from the end of the window |
| `DECISION_SAMPLE_EMPTY` | WARNING | no usable decision mark-to-market sample |

A run whose errors include `COVERAGE_BELOW_INVESTABLE`,
`ENTRY_LOCKED_BY_COVERAGE`, or `ONE_WAY_RATCHER` is reported as
`DEGENERATE_NOT_A_TEST_OF_THE_STRATEGY`: its performance numbers describe the
degenerate path, not the strategy.

## Status on the canonical policy

As of `policy_hash faf7248884362ae6f39132427799f012e784d435ba9682bdefe14c6740ee339a`,
the checker reports errors. This is the expected outcome: it is reporting the
contradictions registered as 8H and 8I, not a defect in the check.

- NORMAL projects `-0.1955` and DEFENSIVE `-0.1610` against a `0.1500` budget
  under the policy's own stress scenario; CAPITAL_PRESERVATION fits at
  `-0.1150`, but breaches at `-0.2000` if the risky sleeve is concentrated in
  the worst configured asset.
- `satellite_full_score` 85 needs coverage `0.70`; `satellite_entry_score` 67
  needs `0.34`; `satellite_exit_score` 62 needs `0.24`.

## What this subsystem deliberately does not do

- It does not change any number in `config/policy.json`. Deciding whether the
  budget is measured against the stress scenario or against realized history is
  a product decision.
- It does not run on the policy load path. The canonical policy currently fails
  the feasibility check, so wiring it into `Policy.__post_init__` would break
  every command at once.
- It does not recompute scores, targets, trades, or performance. It reads what
  the run wrote.
- It does not treat the sorting correlation as a verdict. It reports the
  independent-block count alongside it, because overlapping samples inflate the
  apparent sample size by one to two orders of magnitude.
