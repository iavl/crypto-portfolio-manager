---
name: crypto-portfolio-manager
description: Use this skill for medium- to long-term, spot-only crypto portfolio review, risk assessment, allocation, rebalancing, and staged buy/sell planning. The default posture is conservative-balanced, BTC-benchmarked, and explicitly allows NO TRADE.
---

# Crypto Portfolio Manager

Use this Skill for a 6–12 month portfolio horizon. It supports research and
proposed decisions; it never places trades, requests trading permissions, or
introduces leverage, futures, perpetuals, or margin.

## Policy and references

Load the canonical machine-readable policy from `config/policy.json` before
analysis. A snapshot may provide explicit overrides for its configuration;
reject invalid or conflicting values. Use these references for qualitative
judgment and user-facing decisions:

- `references/investment-policy.md`
- `references/scoring-model.md`
- `references/risk-model.md`
- `references/decision-rules.md`
- `references/data-sources.md`
- `references/output-template.md`

The canonical policy controls asset groups, risk limits, benchmarks, scoring
weights, regime envelopes, and rebalance thresholds. Python models and engine
modules perform validation and mathematics; the Agent supplies current
evidence, bounded qualitative judgments, explanations, and execution zones.

## Ordered workflow

1. Parse the current portfolio.
2. Load the canonical policy.
3. Validate the snapshot and apply only explicit policy overrides.
4. Load historical snapshots, decisions, and the previous thesis before
   fetching new evidence when local history is available.
5. Build cash-flow-aware NAV and drawdown history.
6. Select `SNAPSHOT_REVIEW`, `FULL_REVIEW`, or `EVENT_REVIEW`; recommend a
   `FULL_REVIEW` when at least 14 days have passed since the last one.
7. Fetch current price, trend, flow, fundamental, on-chain, and event evidence.
8. Validate evidence completeness and preserve provenance.
9. Build `Evidence`, `FactorScore`, and `AssetAssessment` records.
10. Run deterministic scoring and missing-factor coverage checks.
11. Run the regime engine.
12. Run the allocation engine.
13. Run the risk gate and stop on `ERROR` violations.
14. Recalculate post-new-cash economic weights.
15. Run the rebalance engine.
16. Reconcile executable trade dollars.
17. Evaluate `NO_TRADE` before proposing a transaction.
18. Build structure-based execution zones only when required.
19. Validate execution zones with `validate_execution_plan`.
20. Produce the Chinese user-facing report using
    `references/output-template.md`.
21. Persist only validated snapshots, decisions, and complete evidence. Never
    rewrite prior rationale or mark a trade executed without explicit
    confirmation or a trusted later read-only snapshot.

## Accounting and missing data

Use `crypto_portfolio.engine.ledger` for unitized NAV, cash-flow-adjusted
return, current drawdown, and maximum drawdown. An external cash flow attached
to a snapshot occurs immediately before that snapshot valuation; the ledger
backs out the flow at the pre-flow NAV. Deposits and withdrawals change units,
not investment NAV. The primary benchmark is 100% BTC buy-and-hold; the
secondary benchmark is 70/30 BTC/ETH buy-and-hold with flows allocated 70/30.
Benchmark periods and cash-flow treatment must match the portfolio period.

Stablecoins and cash are one allocation sleeve. Preserve their existing
composition where possible and do not create stablecoin-to-stablecoin trades
merely to satisfy a preferred symbol.

Critical missing data—current price, recent trend history, portfolio value, or
an unresolved material security event—precludes a high-conviction entry.
Missing non-critical scoring factors may be removed and renormalized by the
scoring engine, but confidence is capped by actual coverage. Unknown factor
keys fail validation, and missing BTC-relative evidence makes a satellite
`HOLD_ONLY` rather than positive evidence for a new allocation.

## Runtime data boundary

Real portfolio data must remain outside Git. Append-only state defaults to
`~/.local/share/crypto-portfolio-manager/` and can be redirected with the
`CRYPTO_PORTFOLIO_DATA_DIR` environment variable. Repository `data/` is for
fake fixtures and `.gitkeep` files only.

For a compatibility normalization check, run:

```bash
python scripts/portfolio_snapshot.py path/to/fake-snapshot.json
```
