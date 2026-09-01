# crypto-portfolio-manager

An AI-assisted, conservative-balanced crypto portfolio research Skill with a
deterministic portfolio accounting, risk, allocation, and rebalance engine.
It is spot-only and intended for a medium-term 6–12 month horizon.

## What it does

- Loads one canonical policy from `config/policy.json`.
- Validates and classifies structured holdings deterministically.
- Separates Agent research and bounded judgments from Python accounting and
  portfolio mathematics.
- Uses unitized NAV so deposits and withdrawals do not become investment
  returns or losses; a flow attached to a snapshot occurs immediately before
  that snapshot valuation.
- Produces reproducible scoring, market-regime, target-allocation, risk-gate,
  and rebalance results.
- Preserves structured evidence, factor scores, and append-only decision
  history.
- Benchmarks portfolios over aligned periods against 100% BTC buy-and-hold and
  70/30 BTC/ETH buy-and-hold.
- Treats stablecoins and cash as one sleeve, preserving existing composition
  instead of creating mechanical stablecoin-to-stablecoin trades.
- Loads historical NAV and decision context before a new review; full reviews
  are due every 14 days and event reviews may override that cadence.

## What it is not

This is not an automatic trading bot, short-term trading system, leverage or
margin system, futures/perpetuals system, or custodial exchange integration.
It creates proposed actions and execution zones only; it never places real
orders.

## Architecture

The flow is:

```text
Agent evidence and judgments
        ↓
policy → models → ledger/metrics → scoring → regime
        ↓
allocation → risk gate → rebalance → execution-plan validation
        ↓
Chinese report and optional append-only history
```

Persisted snapshots and decisions include a canonical policy hash and the
resolved policy so historical results remain reproducible. Input and
normalized-output contracts are separate under `schemas/`.

Market-data provider protocols are extension points only; this repository does
not yet implement exchange or web-data integrations.

## Install

Place the whole `crypto-portfolio-manager` directory in a Skill location supported by your Codex/Agent Skills environment, preserving `SKILL.md` at the skill root.

The Skill intentionally keeps data retrieval abstract in v1. The runtime should use its current web/data tools. A future CLI/API layer can provide exchange read-only adapters and dedicated market-data connectors without rewriting the policy layer.

## Local checks

Install development checks once:

```bash
python3 -m pip install ".[dev]"
```

```bash
python -m unittest discover -s tests -v
```

On systems without a `python` alias, use `python3 -m unittest discover -s tests -v`.

Normalize a structured snapshot:

```bash
python scripts/portfolio_snapshot.py path/to/snapshot.json
```

Optional per-snapshot policy overrides:

```json
{
  "timestamp": "2026-09-01T00:00:00Z",
  "positions": [
    {"symbol": "BTC", "value_usd": 8000},
    {"symbol": "USDC", "value_usd": 2000}
  ],
  "config": {"min_stablecoin_weight": 0.10}
}
```

Omit `config`, or any individual field, to use the canonical policy. Symbol
lists replace their policy groups, must not overlap, and an explicit position
type is accepted only when it matches policy classification.

## Safety boundary

This project creates portfolio analysis and proposed execution plans. It does
not contain exchange trading credentials or automatic order execution.

## Privacy

Real portfolio quantities, balances, cost basis, transaction history, and other
financial data must be stored outside this Git repository. The default runtime
location is `~/.local/share/crypto-portfolio-manager/`; set
`CRYPTO_PORTFOLIO_DATA_DIR` to override it. Repository `data/` directories are
reserved for fake fixtures and `.gitkeep` files.
