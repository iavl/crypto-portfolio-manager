# Strategy V2 — Phase 5 Report: Benchmark and Objective

Branch `strategy-v2-benchmark` (from `strategy-v2` @ `d0ffc1d`).

## Changed files

- `config/policy.json` + `crypto_portfolio/models/policy.py`: the
  `benchmarks` contract now declares the comparison hierarchy —
  `risk_matched_primary` (type marker `VOL_MATCHED_BTC_CASH`),
  `opportunity_cost_btc` ({BTC: 1.0}), `secondary_static` (70/30). The
  marker round-trips through canonical records; static weight-map
  consumers reject it explicitly.
- `crypto_portfolio/engine/benchmark.py`: per-period benchmark defaults
  anchor on `opportunity_cost_btc`; the vol-matched primary has no static
  weight map and says so.
- `crypto_portfolio/research/orchestrator.py`:
  - `_benchmark_comparison` adds `tracking_difference` (named cumulative
    gap) and `sortino_delta`.
  - new `strategy_attribution` block per experiment: the strategy's own
    time-average weights held constantly (with costs) as the
    active-re-weighting counterfactual, `risk_scaling_effect` = strategy
    total return − average-weights-hold total return, the average weights
    themselves, and the vol-matched annualized excess. Exposure timing and
    cash-yield sensitivity remain their own blocks.
- `crypto_portfolio/research/reporting.py`: benchmark family reading order
  now leads with the vol-matched comparison; the decision notes name 100%
  BTC as the opportunity-cost reference, not a risk-matched benchmark.
- `AGENTS.md`, `references/investment-policy.md`,
  `references/investment-strategy.md`: objective hierarchy — primary:
  improve risk-adjusted return versus vol-matched BTC/cash; secondary:
  positive absolute return; constraint: the configured risk budget;
  reference: opportunity cost versus BTC. The 70/30 static benchmark and
  flow-matching rules are unchanged.
- Tests: `tests/test_benchmark_objective.py`.

## Strategy behavior changed

- None in sizing, gating, or execution. This phase changes measurement,
  declaration, and reporting: which comparison is primary, what the
  metrics are called, and what the objective says.

## Strategy behavior intentionally unchanged

- Benchmark construction math (vol-matched solving, buy-and-hold with
  70/30 flow allocation, cost conventions), evaluation windows, and flow
  timing.

## New configuration

- `benchmarks` renamed entries (contract change): `risk_matched_primary`
  (type marker), `opportunity_cost_btc`, `secondary_static`.

## New diagnostics

- `tracking_difference`, `sortino_delta` on every benchmark comparison;
  `strategy_attribution` (average weights, average-weights hold, risk
  scaling effect, vol-matched excess) on every experiment result.

## Tests added

8 tests: the policy contract (marker exactness, legacy-key rejection,
round-trip, static-consumer rejection, per-period default anchor), the
excess-metric family on a synthetic pair, and the attribution block on a
real replay fixture.

## Test result

Full suite 1301 tests passing; `ruff check .` and `compileall` clean.

## Baseline comparison

No replay re-run needed: the underlying benchmark paths are unchanged;
the 2024-window baseline comparison table already carried vol-matched
excess (−3.43pp/yr for full/core_existing strict). The newly named
tracking difference equals the previously reported excess return; the new
attribution block will populate on the next research run.

## Known limitations

- Asset-selection alpha (plan 5.5's first bucket) is not yet separable
  from risk scaling: the average-weights counterfactual holds the
  strategy's own average mix, so selection and steady-state sizing remain
  entangled until Phase 6's ablation runs compare universes.
- The report's decision table already led with the risk-matched columns;
  this phase formalized the ordering and the policy declaration rather
  than inventing new tables.

## Open questions

- None blocking Phase 6.

## Ready for next phase?

YES — the primary benchmark is the risk-matched BTC/cash comparison;
100% BTC is explicitly the opportunity-cost reference; excess metrics
(cumulative, annualized, Sharpe/Sortino/vol/drawdown deltas) are reported
against it; and at least risk-scaling, timing, and cash effects are
separable in the attribution block.
