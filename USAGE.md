# Usage Guide

This Skill supports conservative-balanced, spot-only portfolio reviews over a
roughly 6–12 month horizon. It can return `NO_TRADE`, `HOLD`, `WAIT`,
`REDUCE`, or `EXIT` recommendations, but never places trades.

Invoke it with `$crypto-portfolio-manager`, provide a structured snapshot or a
Binance USD wallet screenshot, and request `SNAPSHOT_REVIEW`, `FULL_REVIEW`, or
`EVENT_REVIEW`. History is loaded before new evidence. Unknown cost basis or
P&L stays unknown; unclassified cash flow makes NAV performance provisional.

## Structured providers

```bash
export COINGECKO_API_KEY='...'
python3 scripts/providers.py --status
python3 scripts/providers.py --list
python3 scripts/providers.py --probe coingecko
python3 scripts/providers.py --metric valuation.market_cap
python3 scripts/providers.py --metric valuation.fdv
python3 scripts/providers.py --metric valuation.fdv_market_cap_ratio
```

The CoinGecko key is environment-only and is sent as
`x-cg-demo-api-key`. CoinGecko supplies market cap/FDV; Coin Metrics can supply
catalog-confirmed `CapMrktEstUSD` market-cap fallback; Python derives the
FDV/market-cap ratio. DeFiLlama remains responsible for protocol fundamentals.

Other optional credentials are also environment-only:

```bash
export SOSOVALUE_API_KEY='...'
export COINMETRICS_API_KEY='...'
export GITHUB_TOKEN='...'
```

`--status` is offline readiness. `--probe` performs an explicit read-only
network check and never prints response bodies or credentials. TLS verification
must remain enabled; do not use `verify=False` or `curl -k`.

## Fetch modes and history

```bash
export CRYPTO_PORTFOLIO_FETCH_MODE=AUTO       # default
export CRYPTO_PORTFOLIO_FETCH_MODE=CACHE_ONLY
export CRYPTO_PORTFOLIO_FETCH_MODE=REFRESH
```

Runtime state stays outside Git under
`~/.local/share/crypto-portfolio-manager/`, or the directory named by
`CRYPTO_PORTFOLIO_DATA_DIR`. Do not commit balances, quantities, cost basis,
account identifiers, credentials, private keys, seeds, or cookies.

## Development checks

```bash
python3 -m unittest discover -s tests -v
ruff check .
python3 -m compileall crypto_portfolio scripts
```
