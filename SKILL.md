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

1. Extract or load the portfolio from a screenshot, holdings input, or a
   runtime snapshot.
2. Load the canonical policy and apply only explicit snapshot overrides.
3. Validate and normalize the snapshot with the domain models. Classification
   comes from policy; a supplied asset type is only a validated hint.
4. Obtain current price, trend, flow, fundamental, on-chain, and event
   evidence. Preserve source, timestamps, freshness, and confidence.
5. Build `Evidence`, `FactorScore`, and `AssetAssessment` records. Never
   fabricate unavailable data.
6. Run `crypto_portfolio.engine.scoring` for weighted scores and missing-factor
   renormalization.
7. Run `crypto_portfolio.engine.regime` for `NORMAL`, `DEFENSIVE`, or
   `CAPITAL_PRESERVATION`.
8. Run `crypto_portfolio.engine.allocation` for deterministic target weights.
9. Run `crypto_portfolio.engine.risk` and stop on `ERROR` violations.
10. Run `crypto_portfolio.engine.rebalance` with current weights, target
    weights, portfolio value, and available new cash.
11. Evaluate `NO_TRADE` before proposing a transaction. Avoid turnover below
    the configured thresholds and do not treat existing holdings as entitled
    to remain.
12. If a trade is justified, propose structure-based price zones and tranche
    fractions; do not invent exact prices or mechanical percentage ladders.
13. Validate the proposed execution plan with
    `validate_execution_plan`.
14. Produce the Chinese user-facing report using
    `references/output-template.md`, retaining English tickers and metric
    names.
15. When history is enabled, append the validated snapshot and decision to
    JSONL state. Do not rewrite prior rationale or mark a trade executed
    without explicit confirmation or a trusted later read-only snapshot.

## Accounting and missing data

Use `crypto_portfolio.engine.ledger` for unitized NAV, cash-flow-adjusted
return, current drawdown, and maximum drawdown. Deposits and withdrawals must
change units, not investment NAV. Portfolio return calculations must fail
explicitly when a held asset's required return is missing. Benchmark periods
and cash-flow treatment must match the portfolio period.

Critical missing data—current price, recent trend history, portfolio value, or
an unresolved material security event—precludes a high-conviction entry.
Missing non-critical scoring factors may be removed and renormalized by the
scoring engine, with reduced confidence.

## Runtime data boundary

Real portfolio data must remain outside Git. Append-only state defaults to
`~/.local/share/crypto-portfolio-manager/` and can be redirected with the
`CRYPTO_PORTFOLIO_DATA_DIR` environment variable. Repository `data/` is for
fake fixtures and `.gitkeep` files only.

For a compatibility normalization check, run:

```bash
python scripts/portfolio_snapshot.py path/to/fake-snapshot.json
```
