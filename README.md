# crypto-portfolio-manager

A conservative-balanced, medium-term crypto portfolio management Skill for Codex / Agent Skills compatible environments.

## What it does

- Reviews exchange portfolio screenshots or structured holdings.
- Uses current market, trend, flow, on-chain, fundamental, and event data.
- Treats BTC and ETH as core assets.
- Allows selective large-cap satellites such as SOL, BNB, LINK, and ARB.
- Controls risk at the portfolio level with an approximately 20% drawdown risk budget.
- Keeps at least 10% in stablecoins/cash.
- Generates increase/reduce/hold/exit/no-trade decisions and staged execution zones.
- Benchmarks against 100% BTC and 70% BTC / 30% ETH.
- Supports append-oriented portfolio and decision history.
- Never auto-executes trades.

## Install

Place the whole `crypto-portfolio-manager` directory in a Skill location supported by your Codex/Agent Skills environment, preserving `SKILL.md` at the skill root.

The Skill intentionally keeps data retrieval abstract in v1. The runtime should use its current web/data tools. A future CLI/API layer can provide exchange read-only adapters and dedicated market-data connectors without rewriting the policy layer.

## Local checks

```bash
python -m unittest discover -s tests -v
```

Normalize a structured snapshot:

```bash
python scripts/portfolio_snapshot.py path/to/snapshot.json
```

## Safety boundary

This project creates portfolio analysis and proposed execution plans. It does not contain exchange trading credentials or automatic order execution.
