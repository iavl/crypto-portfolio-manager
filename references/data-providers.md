# Data Provider and Cache Policy

This is the operational provider reference for routing, authentication, active
endpoints, cache behavior, fallback semantics, and provider-specific
limitations. Source quality, methodology, and evidence meaning are defined in
[`data-sources.md`](data-sources.md).

Runtime truth remains in `config/data-providers.json`,
`crypto_portfolio/providers/`, `crypto_portfolio/metrics_registry.py`,
`crypto_portfolio/events/sources.py`, and the schemas. This document summarizes
those contracts; it is not a second schema or routing implementation.

## Provider matrix

| Provider | Primary role | Authentication | Active data | Fallback / default availability |
|---|---|---|---|---|
| Binance | spot, OHLCV, derivatives, delivery basis | None | price, candles, funding, OI, ratios, basis | first market/derivatives route; public and registered |
| Bybit | market and derivatives fallback | None | spot candles, funding, OI, account ratio | second market/derivatives route; no delivery basis |
| CoinGecko | broad market valuation | `COINGECKO_API_KEY` | market cap, FDV, historical market cap/FDV | primary valuation; credential-gated |
| DeFiLlama | protocol fundamentals | None | TVL, fees, revenue, fee/revenue multiple | protocol route; registered |
| Alternative.me | market sentiment context | None | Fear & Greed | market-context route; registered |
| Chain liveness | current canonical chain progress | None | BTC/ETH/BNB/SOL progress and finality | structured chain route; registered |
| Coin Metrics Community | catalog-aware network and valuation fallback | None | network metrics, cycle inputs, `CapMrktEstUSD`, attribution | fallback after CoinGecko where supported |
| Coin Metrics Pro | authenticated Coin Metrics fallback | `COINMETRICS_API_KEY` | same catalog-aware datasets at the authenticated tier | optional, credential-gated |
| GitHub | bounded developer activity | optional `GITHUB_TOKEN` | fixed ETH/AAVE repository commit counts | optional and allowlisted |
| SoSoValue | BTC/ETH ETF flows | `SOSOVALUE_API_KEY` | settled 1D/7D/30D ETF flow history | ETF route when configured; credential-gated |
| EventScanner | current security/governance/regulatory scans | None | event status and source coverage | fixed source catalog; no generic fallback |
| LunarCrush | social context | `LUNARCRUSH_API_KEY` | no active adapter | unavailable/optional; remains skipped |

## Fetch modes and routing

- `AUTO` reuses fresh normalized observations and provider cache entries, then
  fetches missing or stale mutable data.
- `CACHE_ONLY` never makes a network request; missing or stale data remains
  visible as `FAILED` or `STALE`.
- `REFRESH` refreshes mutable current data while completed historical series
  remain reusable.

Set `CRYPTO_PORTFOLIO_FETCH_MODE` or pass a run-level mode; the run-level choice
wins. Runtime state defaults to `~/.local/share/crypto-portfolio-manager/` and
can be redirected with `CRYPTO_PORTFOLIO_DATA_DIR`.

Provider priority is deterministic: Binance then Bybit for spot/OHLCV and
derivatives; Binance only for delivery basis; CoinGecko then catalog-aware Coin
Metrics for market cap; DeFiLlama for protocol fundamentals; SoSoValue for ETF
flows; Coin Metrics for supported exchange attribution and network data; and
the fixed EventScanner catalog for events. Derived metrics such as
`valuation.fdv_market_cap_ratio`, `derivatives.open_interest_to_market_cap`,
market flow state, and BTC-relative returns are computed by Python and have no
provider route.

The current repository has no daemon, scheduler, database, queue, or exchange
execution service. API keys are referenced by environment-variable name only;
they are never written to config, cache, history, or logs.

## Binance

Binance uses the shared verified-TLS HTTP client and requires no API key.

| Method | Endpoint | Metrics / purpose |
|---|---|---|
| GET | `https://api.binance.com/api/v3/ticker/price` | `market.spot_price` |
| GET | `https://api.binance.com/api/v3/klines` | completed 1H/4H/1D OHLCV and derived market return, MA, ATR, volatility, volume, trend, and drawdown metrics |
| GET | `https://fapi.binance.com/fapi/v1/fundingRate` + `premiumIndex` | current funding, 24H/7D averages, percentile |
| GET | `https://fapi.binance.com/fapi/v1/openInterest` + `futures/data/openInterestHist` | OI and 1D/7D changes |
| GET | `https://fapi.binance.com/futures/data/globalLongShortAccountRatio` + `topLongShortAccountRatio` | global and top-trader account ratios |
| GET | `https://fapi.binance.com/fapi/v1/exchangeInfo` + `premiumIndex` | nearest unexpired USDT delivery contract and exact-symbol mark/index quote |

Approved public spot mappings are limited to the configured supported assets.
OHLCV is normalized with completed-candle status, venue/market/quote, range,
calendar coverage, and an immutable OHLCV hash. Binance does not provide this
project's historical liquidation aggregates, global exchange-address
attribution, or broad protocol market valuation.

Delivery basis uses the exact `CURRENT_QUARTER` or `NEXT_QUARTER` contract:

```text
annualized_basis = (markPrice / indexPrice - 1) * 365 * 86400 / seconds_to_expiry
```

It is a signed contextual fraction, not executable yield. Historical requests
cannot use today's contract catalogue; only a previously verified compatible
observation/cache can satisfy a historical request. Perpetual premium, funding,
and zero are never substitutes. Unsupported, stale, expired, future, or
malformed contracts remain unavailable.

## Bybit

Bybit is a public market/derivatives fallback and does not require an API key.

| Method | Endpoint | Metrics / purpose |
|---|---|---|
| GET | `https://api.bybit.com/v5/market/kline` | completed spot OHLCV and derived market metrics |
| GET | `https://api.bybit.com/v5/market/funding/history` | funding and 24H/7D averages |
| GET | `https://api.bybit.com/v5/market/open-interest` | OI and 1D/7D changes |
| GET | `https://api.bybit.com/v5/market/account-ratio` | linear account ratio |

Only implemented capabilities are routed. Bybit does not implement Binance's
delivery-basis methodology and is not presented as a basis fallback.

## CoinGecko

CoinGecko is enabled only when `COINGECKO_API_KEY` is present. The Demo key is
sent as the `x-cg-demo-api-key` header and is excluded from query strings, cache
identity, history, and diagnostics.

| Method | Endpoint | Metrics |
|---|---|---|
| GET | `https://api.coingecko.com/api/v3/coins/markets` | current `valuation.market_cap` and `valuation.fdv` |
| GET | `https://api.coingecko.com/api/v3/coins/{id}/history` | historical market cap and explicitly supplied historical FDV |

The exact allowlisted IDs are `bitcoin`, `ethereum`, `solana`, `binancecoin`,
`chainlink`, and `aave`. Market cap may succeed when FDV is absent; FDV absence
is retained as a diagnostic (`COINGECKO_NO_FDV`).
Historical valuation uses the history endpoint at the requested `as_of` date.
The current `/coins/markets` value is never substituted for historical data.
Python derives `valuation.fdv_market_cap_ratio` only from same-asset,
non-future, valid inputs.

## DeFiLlama

DeFiLlama is the protocol-fundamentals provider and requires no API key. Current
explicit identifiers are `ETH → ethereum`, `AAVE → aave`, `SOL → solana`,
`BNB → bsc`, and `LINK → chainlink`.

| Method | Endpoint | Metrics |
|---|---|---|
| GET | `https://api.llama.fi/v2/chains` | chain TVL for ETH/SOL/BNB |
| GET | `https://api.llama.fi/tvl/{identifier}` | lightweight Aave TVL |
| GET | `https://api.llama.fi/summary/fees/{identifier}` | fees and fee context |
| GET | `https://api.llama.fi/summary/fees/{identifier}?dataType=dailyRevenue` | revenue history |
| GET | `https://api.llama.fi/protocol/{identifier}` | protocol payload where implemented |

Outputs include `fundamentals.tvl`, `fundamentals.fees_30d`,
`fundamentals.revenue_30d`, `fundamentals.stablecoin_liquidity`, and
`valuation.fee_revenue_multiple` where the response supplies the required
inputs. DeFiLlama is not the canonical broad market-cap/FDV provider; TVL or
price is never used to fabricate those values.

## Alternative.me

The registered market-sentiment route uses:
`GET https://api.alternative.me/fng/?limit=30&format=json`.
It produces `sentiment.market_fear_greed` with source and observation metadata.
This is market-wide context, not per-asset sentiment or a trading signal.

## Chain liveness

The structured provider applies only to `BTC`, `ETH`, `BNB`, and `SOL`. AAVE and
LINK are protocol/token assets and are not treated as independent chains.

| Asset | Structured sources | Operational input |
|---|---|---|
| BTC | `https://blockstream.info/api/blocks`; `https://mempool.space/api/v1/blocks` | canonical block height/hash and timestamp |
| ETH | `https://ethereum-rpc.publicnode.com`; `https://rpc.flashbots.net` | latest/finalized EVM blocks |
| BNB | `https://bsc-dataseed.bnbchain.org`; `https://bsc-dataseed-public.bnbchain.org` | latest EVM blocks |
| SOL | `https://api.mainnet-beta.solana.com`; `https://solana-rpc.publicnode.com` | health, finalized slot, and block time |

Python produces `risk.chain_liveness_status` as `HEALTHY`, `DEGRADED`,
`HALTED`, or `UNKNOWN`. Source groups, age, finality and quorum remain in
metadata. DNS/TLS/timeout/HTTP/rate-limit/provider failure is unavailable
evidence, not a halt. Chain-liveness responses use a 300-second cache TTL.
Optional local URL/RPC overrides are redacted in diagnostics and never persist
credentials.

## Coin Metrics

Community uses `https://community-api.coinmetrics.io`; the authenticated tier
uses `https://api.coinmetrics.io` and `COINMETRICS_API_KEY`. The provider checks
the official asset-metric catalog and 1D availability before requesting data.

Current approved asset IDs include `btc`, `eth`, `bnb`, and `aave`. Catalog
support, not this document, decides whether a particular asset/metric is usable.

| Data group | Implemented inputs |
|---|---|
| Market-cap fallback | `CapMrktEstUSD` → `valuation.market_cap` only |
| Network | `AdrActCnt`, `TxTfrValAdjUSD`, `FeeTotUSD`, `TxCnt` → on-chain metrics |
| BTC cycle | MVRV, MVRV z-score, realized price, SOPR, LTH/STH, NUPL inputs |
| Tokenomics | `IssTotNtv`, `SplyCur` for annualized emissions and supply growth |
| Exchange attribution | `FlowInExUSD` and `FlowOutExUSD` for `flows.exchange_netflow` |

`CapMrktEstUSD` is a methodology-compatible market-cap fallback after
CoinGecko. `CapMrktCurUSD` and future-supply values are not silently substituted
for it, and unsupported catalog combinations remain unavailable.

## GitHub developer activity

GitHub uses the public
`GET https://api.github.com/repos/{repository}/commits` endpoint. `GITHUB_TOKEN`
is optional but enables authenticated access and rate-limit headroom. The
allowlist is fixed:

- ETH: `ethereum/go-ethereum`, `ethereum/consensus-specs`, `ethereum/EIPs`;
- AAVE: `aave/aave-v3-core`, `aave/aave-v3-deploy`.

The provider counts unique default-branch commits in the trailing 30-day
window with bounded pagination and emits `fundamentals.developer_activity`.
Rate-limit or unavailable data is not interpreted as zero developer activity.
There is no repository discovery or HTML scraping.

## SoSoValue ETF flows

SoSoValue is registered only when `SOSOVALUE_API_KEY` is configured. The active
read-only contract is:

```text
POST https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart
header: x-soso-api-key
body: {"type":"us-btc-spot"} or {"type":"us-eth-spot"}
```

The provider derives:

- BTC: `flows.etf_net_1d`, `flows.etf_net_7d`, `flows.etf_net_30d`;
- ETH: the same three metrics;
- MARKET: complete-date BTC + ETH aggregation.

Rows are settled U.S. trading dates, normalized to `America/New_York` 16:00,
and filtered locally by `as_of`. Up to 300 daily rows are accepted. If the
history is too short, 1D/7D can still succeed while 30D reports
`PROVIDER_INSUFFICIENT_HISTORY`; this is not unsupported capability. Negative
flows are valid. The active contract does not provide liquidation history, so
liquidation metrics remain optional/skipped and are never routed here.

## EventScanner

EventScanner uses the fixed catalog in `crypto_portfolio/events/sources.py`.
The operational flow is:

```text
EventSourceScanRequest -> external source response -> Python coverage/materiality
                         -> pass 2 acquisition -> require_scoring_ready()
```

It produces `risk.security_event_status`,
`risk.governance_event_status`, and `risk.regulatory_event_status`. The catalog
covers:

- Bitcoin Core security/releases/BIPs;
- Ethereum security guidance, go-ethereum and consensus-spec advisories, EIPs,
  AllCoreDevs and Foundation protocol notices;
- Aave security advisories and governance forum/proposals;
- BNB Smart Chain security/release sources and BEPs/governance sources;
- shared MARKET regulatory scans over SEC, CFTC, and ESMA/MiCA sources.

BNB security and governance are first-class catalog scopes. Regulatory scans
run once at MARKET scope and map results to affected assets. A complete scan
without a material item is `NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES`;
incomplete reachability is `INSUFFICIENT_SOURCE_COVERAGE`; a
`MATERIAL_EVENT_FOUND` result is not an exploit, approval, or execution.
Pass 1 must stop before scoring when a hard-critical event group is unresolved.
Pass 2 consumes exactly one response per request, including bounded
`reachable=false` errors.

## Availability and collection status

Every requested attempt remains visible as one of:

| Status | Meaning |
|---|---|
| `SUCCESS` | normalized value passed validation and is usable for the metric |
| `STALE` | an old value exists but current freshness is not established |
| `FAILED` | applicable evidence was expected but collection/validation failed |
| `CONFLICT` | sources or records disagree and no safe value was selected |
| `NOT_APPLICABLE` | the asset/metric pair has no meaningful interpretation |
| `SKIPPED` | optional/premium evidence has no eligible configured provider |

Provider diagnostics retain bounded distinctions such as `AUTH`, `RATE_LIMIT`,
`PROVIDER_UNSUPPORTED`, `PROVIDER_INSUFFICIENT_HISTORY`, and cache misses. A
provider outage is not converted into a successful neutral value.

Successful normalized values are persisted as `MetricObservation` records; all
attempts are persisted as `CollectionEvent` records. Their exact contracts are
owned by `schemas/metric-observation.schema.json` and
`schemas/collection-event.schema.json`.

## Cache and diagnostics

Provider cache is acquisition infrastructure, not decision authority:

```text
provider-cache/responses/<provider>/sha256/<request-hash>.json
provider-cache/series/<provider>/<series-key-hash>/manifest.json
market-data/sha256/<ohlcv-hash>.json
```

Mutable response TTLs come from `config/data-providers.json` and are bounded by
metric freshness. Historical OHLCV and other verified series are reusable and
are not silently overwritten. Current incomplete candles are not persisted as
completed evidence.

Offline status and explicit probes are separate:

```bash
python3 scripts/providers.py --status
python3 scripts/providers.py --list
python3 scripts/provider_cache.py --stats
python3 scripts/providers.py --probe binance
python3 scripts/providers.py --probe coingecko
python3 scripts/providers.py --probe defillama
python3 scripts/providers.py --probe alternative_me
python3 scripts/providers.py --probe sosovalue
python3 scripts/providers.py --probe chain_liveness --asset BTC
```

`--status` reports configuration, adapter and credential readiness without
network access; it is not endpoint health. `--probe` is opt-in network,
authentication, entitlement, schema, and history diagnostics. TLS certificate
and hostname verification remain enabled. `CRYPTO_PORTFOLIO_CA_BUNDLE` may point
at a trusted bundle; credentials and response bodies are not printed.
