# How It Works

`SKILL.md` orchestrates reviews. Python owns validation, normalized evidence,
accounting, scoring, regime, allocation, risk, rebalance, and execution math;
the model supplies bounded interpretation and report prose only.

```text
snapshot -> history -> metric plan -> observation/cache/provider
         -> deterministic facts -> score/regime/allocation/risk/rebalance
         -> finalized report packet -> append-only history
```

Acquisition is cache-first and on demand. `CACHE_ONLY` never uses the network;
`AUTO` reuses fresh data and refreshes mutable gaps; `REFRESH` refreshes mutable
current data while preserving immutable historical series. Failed, stale,
conflicting, inapplicable, and skipped requests remain visible.

## Provider boundaries

```text
Binance / Bybit       spot, OHLCV, derivatives
SoSoValue             ETF flows
CoinGecko             structured market cap and FDV
Coin Metrics          catalog-aware market-cap fallback and on-chain data
DeFiLlama             TVL, fees, revenue, protocol fundamentals
Python                FDV / market-cap ratio and other deterministic metrics
```

`valuation.market_cap` routes CoinGecko, then catalog-supported
`CapMrktEstUSD` Community/Pro fallback. `valuation.fdv` routes only CoinGecko.
`valuation.fdv_market_cap_ratio` has no provider route and is derived only from
same-asset, fresh, non-future inputs. Missing FDV never discards a valid market
cap. Historical requests use only data at or before `as_of`; current
`/coins/markets` values are never used for historical replay.

DeFiLlama is not a broad market-cap/FDV source. Existing TVL, fees, revenue,
lightweight AAVE TVL, partial-bundle, TLS, and response-size behavior remain
provider-owned.

## Safety boundary

The system is advisory and spot-only. It never stores or uses trading keys,
places orders, enables leverage, or performs autonomous execution. Stablecoin
floors, portfolio-level drawdown controls, `NO_TRADE`, chain-liveness gates,
SoSoValue ETF semantics, scoring weights, and append-only history remain
authoritative.
