# Strategy V2 — Phase 6 Report: Walk-Forward Validation

Branch `strategy-v2-validation` (from `strategy-v2` @ `8f9d203`).

## Changed files

- `crypto_portfolio/research/validation.py` (new): `PARAMETER_CLASSIFICATION`
  (structural / calibrated / forbidden registry), `walk_forward_windows`
  (rolling non-overlapping train/validate calendar),
  `dynamic_universe_eligibility` (point-in-time history + liquidity rules),
  `threshold_rank_monotonicity` (score-bucket ranking power vs forward and
  BTC-relative returns), seeded `block_bootstrap` (MaxDD/CAGR/Sharpe
  distributions + budget-breach frequency), `gap_risk_stress` (−10/−20/−30%
  one-day shocks at maximum exposure), `stablecoin_stress` (configured
  depeg scenario + single-issuer −20% + custody loss + issuer
  concentration), `ablation_policy` (zero-weight factor ablation with
  renormalization, satellite/overlay switches).
- `scripts/backtest.py`: `validate` and `ablation` subcommands producing
  `validation.json` / `ablation.json` beside the run artifacts.
- `docs/STRATEGY_V2_VALIDATION_PROTOCOL.md` (new): freeze record, data
  rules, dynamic-universe rule, window policy, walk-forward discipline,
  parameter classification, threshold-calibration question, ablation set,
  bootstrap/gap/stablecoin diagnostics, and the ten acceptance criteria
  (passing is never `CAGR > X`).
- `docs/BACKTEST_VALIDATION.md`: the new commands.
- Tests: `tests/test_walk_forward_validation.py` (20 tests).

## Strategy behavior changed

- None. Phase 6 is measurement and protocol; every function is read-only
  over frozen artifacts and every randomization is seeded.

## Strategy behavior intentionally unchanged

- Everything (all engines, policy values). No parameter was tuned against
  any result below, per the plan's hard rule.

## Validation run (2024-present frozen dataset, full/core_existing strict)

Freeze: git `8f9d203`, policy hash `25c8eed5…`, manifest
`strategy-validation-2024-present:historical-data` (strict_ready).

**Walk-forward calendar**: one 2y-train (2024-01→2025-12) / 1y-validate
(2026-01→2026-09-24) step fits the window; the bear window supplies the
second regime.

**Dynamic universe**: BTC/ETH/SOL/BNB/AAVE are eligible at every quarterly
boundary under the 365-day history + $1M median-volume rule. Note: this
dataset was built with today's universe, so the eligibility timeline is the
audit surface for survivorship, not yet a rebuilt dynamic-membership
backtest (protocol §6.3's stated open item).

**Threshold ranking power (6.7, the plan's most important experiment)**:
normalized scores show adjacent-monotonicity 0.25 at every horizon (30/90/
180d); the top bucket's mean 180-day forward return is **−18.6%** and the
effective score's is −45.4% (42 samples). In the strict replay — where the
judgment layer is deliberately MISSING — **the score has no monotone
ranking power at medium horizons**. That is the finding the plan said must
be faced; it does not by itself condemn the live score (replay coverage is
missing-factor-driven, the live book scores on complete evidence), but the
57/62/67/85 thresholds cannot be validated on this evidence and stay
placeholders.

**Block bootstrap (6.10)**: 21-day blocks, 200 draws, seed 20260925 —
median MaxDD −14.2% (p05 −23.7%), median CAGR +4.4%, **budget-breach
frequency 36%**. Holding the 15% budget is the median outcome, not a
guarantee; the p05 tail breaches by ~9pp.

**Gap risk (6.11)**: at the strategy's maximum exposure (85% risky, fresh
peak) a −10% gap costs −8.5% (inside budget), −20% costs −17.0%, −30%
costs −25.5% (both breaches). The reactive overlay cannot pre-cut a gap —
quantified, as the plan required.

**Stablecoin stress (6.12)**: the window's final sleeve is 100% USD (the
unmanaged all-cash tail), so issuer concentration reads 1.0 on USD — no
stablecoin risk carried at the endpoint. On live books the sleeve is
USDT-heavy; the pending issuer/venue-cap decision keeps its numbers here.

**Layer ablation (6.8/6.9)**, canonical experiment:

| Variant | CAGR | MaxDD | Vol | Sharpe | Trades |
| --- | --- | --- | --- | --- | --- |
| baseline | 5.67% | −14.98% | 12.29% | 0.510 | 187 |
| without fundamentals | 5.58% | −14.98% | 12.29% | 0.503 | 191 |
| without valuation | 5.57% | −14.98% | 12.30% | 0.502 | 185 |
| without onchain | 5.67% | −14.98% | 12.28% | 0.510 | 185 |
| without relative strength | 5.70% | −14.98% | 12.28% | 0.513 | 185 |
| without regime trend domain | 5.67% | −14.98% | 12.40% | 0.507 | 181 |
| without execution overlay | 5.67% | −14.98% | 12.20% | 0.513 | 190 |
| without satellites | 5.67% | −14.98% | 12.29% | 0.510 | 187 |
| **without drawdown emergency overlay** | **15.63%** | **−31.36%** | 25.50% | 0.697 | 48 |

Reading: in strict replay the judgment-layer factors are ablated inputs,
so removing them is a near no-op (consistent with the DEGENERATE verdict);
the **drawdown emergency overlay is the dominant module** — it costs ~10pp
of CAGR to hold the 15% budget, and without it the book doubles the
budget breach (−31.4%). That is the intended trade, now quantified. The
regime trend domain and the execution overlay show marginal effects on
this window.

**Acceptance criteria (6.13)** against the ten-item list: criteria 3
(thresholds reachable in the used score space — reachable yes, validated
no), 4 (no one-way ratchet — buys and sells both occur, though the window
ends TRADING_STALLED), 7 (walk-forward — only one validate step fits;
more history needed), and 10 (survivorship — timeline audited, membership
rebuild pending) are **partial**; the rest pass. No criterion was met by
lowering the bar.

## Tests added

20 tests: window calendar overlap rules, universe eligibility
(history/liquidity/determinism), monotonicity statistics (monotone,
inverse, empty-bucket, insufficient-samples), bootstrap (seed
reproducibility, fingerprint sensitivity, breach ordering, distributions),
gap stress (anchoring, monotonic losses), stablecoin scenarios
(concentration arithmetic, weight validation), ablation variants
(zero-weight contract, empty-profile rejection, switches), and the
parameter-classification registry shape.

## Test result

Full suite 1321 tests passing; `ruff check .` and `compileall` clean.

## Known limitations

- The judgment layer cannot be replayed without look-ahead; every
  factor-level conclusion above is about the mechanical layer only.
- One validate step fits the 2024 window; the bear window and longer
  histories are required for criterion 7.
- Dynamic-membership backtests (universe rebuilt per boundary) remain
  future work; the eligibility timeline is the audit surface.

## Open questions for the next calibration round

1. Ranking power must come from complete-evidence samples (live decision
   history once it accumulates) or a judgment-layer replay protocol —
   thresholds stay placeholders until then.
2. The emergency overlay's 10pp cost vs the 36% bootstrap breach tail:
   whether D=15% stays, or the budget/stage caps are recalibrated, is the
   headline policy decision, now with numbers on both sides.
3. Issuer/venue caps for the stable sleeve (live USDT concentration).

## Ready for next phase?

YES — Phase 6 is the final phase of the plan. All six phases are
implemented; the validation infrastructure, protocol, freeze records, and
first full diagnostic round exist, and every finding above is documented
with its evidence rather than absorbed into a passing summary.
