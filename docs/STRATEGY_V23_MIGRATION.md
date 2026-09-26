# Strategy V2.3 Production Migration

Status: **GATED — awaiting explicit human acceptance.** The complete V2.3
mechanism is implemented, validated on both windows, and runnable in shadow,
but the canonical `risk_engine.mode` switch is deliberately NOT applied by
code. This document is the migration gate checklist (plan section 11).

## What V2.3 changes

| Layer | V2.2 (live today) | V2.3 (validated) |
| --- | --- | --- |
| FULL_CONVICTION | structural data *available* | structural score **strong** (>= 67) |
| Satellite new risk | generic market score / TACTICAL_ONLY tactical slice | the asset's own **admitted** BTC-relative alpha ensemble POSITIVE (BNB, AAVE; SOL research-only) |
| Regime volatility | volatility domain votes and weighs | volatility authority removed (R1); realized vol owned solely by the volatility budget |
| Drawdown budget | nominal 15% "max", not enforced ex ante | explicit `drawdown_budget_mode=HARD_TARGET`; stress-loss budget sizes the sleeve so the worst crash scenario fits 15% |
| Cash | 0% return in replay | point-in-time risk-free proxy carry credited identically to strategy and cash-holding benchmarks |
| Composite score | drives satellite positions | diagnostics only |

## Migration gate (all four required, plan 11.2)

1. **Full suite green** — `python -m unittest discover -s tests`, `ruff check .`,
   `python -m compileall crypto_portfolio scripts`.
2. **Strict replay complete** — `scripts/backtest.py v23 <primary-window>
   --sister-dataset <bear-window>` produced `v23-validation.json` on both
   windows with frozen manifests (git SHA, policy hash, dataset manifest,
   alpha registry hash, risk budget mode, carry convention).
3. **Policy decision matrix accepted** — the human owner accepts the
   KEEP / RESEARCH_ONLY / REMOVE verdicts in `docs/STRATEGY_V23_VALIDATION.md`
   (in particular any REMOVE that simplifies the strategy).
4. **Drawdown budget mode decided** — HARD_TARGET (15% binds ex-ante crash
   sizing) vs WARNING_BAND (a human-chosen `hard_stress_loss_limit`, which
   must exceed 15% and is never derived from historical returns).

## Migration checks (plan 11.3) — how to run each

```bash
# dry-run live review + shadow allocation + legacy-vs-V2.3 target comparison
python3 scripts/shadow_allocation.py \
  --risk-closes ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present/series

# with the accepted registry unlocking tilts (once the gate passes):
python3 scripts/shadow_allocation.py --v23-registry <dataset>/v23-validation.json ...

# scenario dry-run of a specific tilt before enabling it:
python3 scripts/shadow_allocation.py --alpha-state BNB=BNB_ALPHA_POSITIVE ...
```

The report (`reports/v23-shadow-allocation.json`) contains, per policy
variant: target weights vs the latest snapshot, the risk-engine binding
constraint, per-asset transition deltas with rebalance-band classification
and reviews-to-converge under the staging cap, and read-only open-order
reconciliation (resting BUY orders that conflict with the V2.3 target are
flagged, never touched).

## Staged migration (plan 11.4)

If the shadow target delta is large (expected: the stress budget shrinks the
risky sleeve materially), the switch MUST be staged:

1. Flip `risk_engine.mode` to `volatility_budget` with every tilt still
   locked (`satellite_alpha.*.tilt_enabled=false`, ETH `tilt_enabled=false`).
2. Let the ordinary rebalance machinery converge toward the smaller sleeve
   under its own staging caps (`max_step_pp=4`, `max_gap_close_fraction=0.5`);
   the shadow report's reviews-to-converge column estimates the horizon.
3. Only after the book has converged, unlock tilts whose admission passed
   (registry ADMITTED lists into `satellite_alpha.*.admitted_signals` +
   `tilt_enabled=true`), one asset per review, smallest tilt first.
4. Cancel or re-place resting orders that conflict with the staged target
   manually — the system never cancels or places orders itself.

A policy switch followed by a one-shot mass rebalance is prohibited.

## What must NOT change

- The safety boundary: analysis and recommendation only, never execution.
- The investment policy (conservative-balanced, 3–6 month horizon, stable
  floor, drawdown discipline, BTC benchmark).
- Preregistered values: thresholds 57/62/67/85, tilt fractions (10%
  satellites / 20% ETH core), ensemble threshold 0.5, target volatility
  bands, stress scenarios. None were tuned during V2.3.
