# crypto-portfolio-manager

A conservative-balanced, medium-term crypto portfolio management Skill for Codex / Agent Skills compatible environments.

## What it does

- Reviews exchange portfolio screenshots or structured holdings.
- Uses current market, trend, flow, on-chain, fundamental, and event data.
- Treats BTC and ETH as the default core assets; the core list is configurable per snapshot.
- Allows selective large-cap satellites such as SOL, BNB, LINK, and AAVE.
- Uses a default 20% portfolio drawdown risk budget and a default 10% stablecoin/cash floor; both are configurable per snapshot.
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

Optional per-snapshot configuration:

```json
{
  "config": {
    "core_symbols": ["BTC", "ETH"],
    "satellite_symbols": ["SOL", "BNB", "LINK", "AAVE"],
    "stable_symbols": ["USDT", "USDC"],
    "min_stablecoin_weight": 0.10,
    "max_portfolio_drawdown": 0.20
  }
}
```

Omit `config`, or any individual field, to use the documented defaults. Symbol lists replace the defaults, must not overlap, and unlisted assets are classified as `other` unless a position explicitly provides `asset_type`.

## Safety boundary

This project creates portfolio analysis and proposed execution plans. It does not contain exchange trading credentials or automatic order execution.
