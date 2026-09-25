# Strategy V2 — Phase 3 Report: Signal Ownership

Branch `strategy-v2-signal-ownership` (from `strategy-v2` @ `ecdc3ac`).

## Changed files

- `docs/SIGNAL_OWNERSHIP.md` (new): the canonical ownership table, layer
  questions, Phase 3 decisions, and the intentionally flagged residual
  overlaps.
- `crypto_portfolio/engine/signal_ownership.py` (new):
  `signal_ownership_report(policy)` — deterministic, policy-derived report
  mapping each signal family to its primary owner and consumers, flagging
  `MULTIPLE_POLICY_AUTHORITY` where more than one target-authority layer
  reads a family.
- `config/policy.json` + `crypto_portfolio/models/policy.py`:
  - `regime_model.domain_weights`: trend 0.30 → 0.15, volatility 0.25 →
    0.30, flows 0.25 → 0.30, breadth 0.20 → 0.25 (structural; Phase 6
    validates). With trend at 0.15 the trend domain alone cannot move the
    regime out of NORMAL (`normal_max` 0.35).
  - `execution_overlay.wait.expiry_reviews` = 5 (placeholder; Phase 6
    calibrates): the bounded WAIT lifetime.
- `crypto_portfolio/engine/entry.py`: `build_entry_plan(wait_streak=...)`;
  after `expiry_reviews` consecutive WAIT reviews with the strategic
  approval standing (thesis intact, regime not CAPITAL_PRESERVATION, wait
  gate enabled), the plan becomes a partial market deployment —
    `entry_mode = MARKET_TIMEOUT`, single tranche at the current price,
    `max_initial_tranche` fraction of the approved amount, remainder under
    `TIMEOUT_RESERVE`; overlay deployment caps still apply.
- `crypto_portfolio/models/execution.py` +
  `schemas/execution-plan.schema.json`: new enums `MARKET_TIMEOUT` and
  `TIMEOUT_RESERVE`.
- `crypto_portfolio/engine/strategy_replay.py` +
  `crypto_portfolio/research/orchestrator.py`: deterministic per-symbol
  WAIT-streak tracking across replayed reviews, fed into entry planning.
- `scripts/strategy_validity.py`: signal-ownership section in the text
  report.
- `references/risk-model.md`: regime domain-weight documentation.
- Tests: `tests/test_signal_ownership.py`,
  `tests/test_execution_wait_expiry.py`,
  `tests/test_signal_ownership_scenarios.py` (plan scenarios A–D).

## Strategy behavior changed

- Regime severity now weighs systemic domains (volatility/flows/breadth)
  over BTC trend; labels flip only where trend was the decisive domain
  vote.
- A technical WAIT can no longer veto a strategic approval indefinitely:
  after 5 consecutive WAIT reviews the entry plan deploys the maximum
  initial tranche at market (with overlay caps applied).

## Strategy behavior intentionally unchanged

- Scoring, thresholds, allocation construction, risk gates, deployment
  caps (already minimum-composed via `deployment_factor_composition =
  minimum_cap`), mandatory drawdown floors, and hard risk gates. Entry
  planning below the expiry, and every non-WAIT plan path, are unchanged.

## New configuration

- `execution_overlay.wait.expiry_reviews` = 5 (placeholder).
- `regime_model.domain_weights` = {trend 0.15, volatility 0.30, flows
  0.30, breadth 0.25} (structural, pending Phase 6).

## New diagnostics

- `signal_ownership_report` (also rendered by `strategy_validity.py`):
  currently flags `trend_momentum` (asset score + 0.15-weight regime
  context + execution direction confirmation) and `macro_liquidity`
  (regime + BTC profile factor) as residual multi-authority reads.
- `ExecutionPlan.planning_context.wait_streak` records the streak behind
  any timeout plan; the gate details carry
  `EXECUTION_TIMEOUT_PARTIAL_DEPLOYMENT`.

## Tests added

18 tests: ownership report determinism/flags, regime authority reduction
(trend alone cannot leave NORMAL; systemic domains still drive defense),
WAIT expiry (below/at expiry, thesis-broken and capital-preservation hard
vetoes, disabled config, schema round-trip), and scenarios A–D (strong
fundamentals + weak technical keeps a positive strategic target while
execution waits; one strong signal cannot oversize; long-rise timeout
deploys partially; systemic volatility reduces sizing through the risk
engine with the score unchanged).

## Test result

Full suite 1280 tests passing; `ruff check .` and `compileall` clean.

## Baseline comparison

No replay re-run in this phase. The regime weight change does shift live
regime labels at the margin (trend-decisive boundaries), and the WAIT
expiry only binds after five consecutive gated reviews — both effects are
measured together with Phase 6's walk-forward validation rather than
re-running the frozen strict windows for a mid-refactor comparison.

## Known limitations

- The two flagged residual overlaps (trend in regime context, macro
  liquidity in the BTC profile) are intentional until Phase 6.
- `expiry_reviews` = 5 and the reduced domain weights are placeholders by
  the plan's own instruction; Phase 6 calibrates both.
- Production review drivers pass `wait_streak` through the replay paths
  only; the live pipeline derives streaks from decision history when the
  mode is promoted (the parameter defaults to 0, which preserves current
  live behavior exactly).

## Open questions

- Should the regime trend domain be removed entirely (weight 0) if Phase 6
  shows no incremental information over volatility/flows/breadth?

## Ready for next phase?

YES — every signal family has a primary owner with named residual reads;
trend no longer holds multi-layer target authority (reduced to context);
execution owns timing only and its veto is bounded; risk signals compose
by minimum, not multiplication.
