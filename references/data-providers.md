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
| BGeometrics | latest BTC MVRV Z-score | None | `btc_valuation.mvrv_zscore` from `/v1/mvrv-zscore/last` | free/no-token; cache at least 24h; low quota |
| DeFiLlama | protocol fundamentals | None | TVL, fees, revenue, fee/revenue multiple | protocol route; registered |
| Alternative.me | market sentiment context | None | Fear & Greed | market-context route; registered |
| Chain liveness | current canonical chain progress | None | BTC/ETH/BNB/SOL progress and finality | structured chain route; registered |
| Coin Metrics Community | catalog-aware network and valuation fallback | None | network metrics, cycle inputs, `CapMrktEstUSD`, attribution | fallback after CoinGecko where supported |
| FRED | official U.S. macro/liquidity series | `FRED_API_KEY` | DFF, DFII10, DTWEXBGS, WALCL, M2SL and Python-derived changes | BTC macro factor route; credential-gated |
| GitHub | bounded developer activity | optional `GITHUB_TOKEN` | fixed ETH/AAVE repository commit counts | optional and allowlisted |
| SoSoValue | BTC/ETH ETF flows | `SOSOVALUE_API_KEY` | settled 1D/7D/30D ETF flow history | ETF route when configured; credential-gated |
| growthepie | Ethereum L2 TVS, activity, fees, rent and DA economics | None | `eth.l2.tvs_usd` from `master.json` + `export/tvl.json`, `all_l2s` activity, Ethereum fees, L2 rent, DA/blob data and tracked-DA shares | ETH-specific public route; CC BY 4.0 attribution required |
| Blobscan | Ethereum blob demand history | None | blob count, bytes, blob transactions, utilization | ETH-specific public route; RPC is a protocol cross-check |
| Rated | Ethereum staking network aggregates | `RATED_API_KEY` via Bearer header | effective balance, daily rewards/APR, queue balances | optional Free-tier route; cache daily primitives; no count×32 conversion |
| Ethereum Beacon API | bounded Beacon node health/fallback | None | node version, genesis and finality checks | configurable `ETH_BEACON_API_URL`; no validator-registry scan |
| Ethereum protocol | canonical execution fields and bounded burn parser | None | `baseFeePerGas`, `gasUsed`, `blobGasUsed`, `excessBlobGas` | enabled by default; one latest-block probe; no per-block review fan-out |
| Ultrasound Money | ETH burn-rate history | None | public `d30.rate.eth_per_minute` | structured 30D burn route; medium confidence unless methodology changes |
| Etherscan v2 | current ETH supply cross-check | `ETHERSCAN_API_KEY` | `ethsupply2` current fields | optional; not historical burn authority |
| Beaconchain | not registered | N/A | no stable aggregate contract verified | intentionally omitted; staking metrics remain optional |
| EventScanner | current security/governance/regulatory scans | None | event status and source coverage | fixed source catalog; no generic fallback |
| LunarCrush | social positioning context | `LUNARCRUSH_API_KEY` via Bearer header | completed daily social sentiment and attention metrics | optional API v4 route; credential and plan gated |

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
Metrics for market cap and BTC-native valuation; FRED for macro/liquidity;
DeFiLlama for protocol fundamentals; SoSoValue for ETF flows; Coin Metrics for
supported exchange attribution and network data; and
the fixed EventScanner catalog for events. BGeometrics is the no-key BTC MVRV Z
route; ETH monetary/realized valuation routes
use catalog-aware Coin Metrics with Ultrasound and optional Etherscan fallbacks;
staking routes require an exact aggregate source and are currently optional;
growthepie is the only active route for ETH L2 TVS and activity, and also
supplies Ethereum fees and L2 rent/DA. Blobscan owns blob history, and
SoSoValue owns structured ETH ETF flow/AUM. LunarCrush supplies lower-authority
social context only. Derived metrics such as
`valuation.fdv_market_cap_ratio`, `derivatives.open_interest_to_market_cap`,
ETH/BTC opportunity ratios, ETH staking/exchange-flow normalization, market
flow state, and BTC-relative returns are computed by Python and have no
provider route.

Before acquisition, provider preflight reports configuration, adapter,
credential requirement/presence, runtime readiness, and one of
`CONFIG_DISABLED`, `CREDENTIAL_MISSING`, or `ADAPTER_UNAVAILABLE` when not
ready. Network/request failures remain separate diagnostics such as `HTTP_401`,
`HTTP_403`, `HTTP_429`, `TLS_CERTIFICATE_VERIFY_FAILED`, and
`PROVIDER_SCHEMA_ERROR`.

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
`fundamentals.revenue_30d`, and `valuation.fee_revenue_multiple` where the response supplies the required
inputs. DeFiLlama is not the canonical broad market-cap/FDV provider; TVL or
price is never used to fabricate those values.

Chain stablecoin supply is a separate route:
`GET https://stablecoins.llama.fi/stablecoincharts/{Ethereum|Solana|BSC}`;
global supply uses `/stablecoincharts/all`. The parser reads
`totalCirculatingUSD.peggedUSD`, filters by `as_of`, and retains the chain or
global scope. It never reads `stablecoinLiquidity` from
`api.llama.fi/protocol/{identifier}`.

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

Coin Metrics Community uses `https://community-api.coinmetrics.io`. The provider
checks the official asset-metric catalog and 1D availability before requesting
data; no authenticated Coin Metrics tier is part of the current contract.

Current approved asset IDs include `btc`, `eth`, `bnb`, and `aave`. Catalog
support, not this document, decides whether a particular asset/metric is usable.

| Data group | Implemented inputs |
|---|---|
| Market-cap fallback | `CapMrktEstUSD` → `valuation.market_cap` only |
| Network | Catalog-checked `AdrActCnt`, `TxTfrValAdjUSD`, `FeeTotUSD`, `TxCnt` → on-chain metrics |
| BTC cycle | MVRV, MVRV z-score, realized price, SOPR, LTH/STH, NUPL inputs |
| Tokenomics | `IssTotNtv`, `SplyCur` for annualized emissions and supply growth |
| Exchange attribution | `FlowInExUSD` and `FlowOutExUSD` for `flows.exchange_netflow` |

`CapMrktEstUSD` is a methodology-compatible market-cap fallback after
CoinGecko. `CapMrktCurUSD` and future-supply values are not silently substituted
for it, and unsupported catalog combinations remain unavailable.

For BTC-native valuation, the provider checks the Community catalog for
`CapMVRVCur`, `CapMVRVZ`, `CapRealUSD`, `CapMrktCurUSD`, `SplyCur`, and
`PriceRealizedUSD` at `btc`/`1d`. MVRV and realized price are derived in Python
from free primitives when the exact metric is unavailable. MVRV Z is derived
only when aligned `CapMrktCurUSD` and `CapRealUSD` history is present; otherwise
it is optional and is never replaced by Web snippets. `SOPR` and `NUPL` are
context-only holder/cycle inputs, not BTC base-score factors. Community is the
only active Coin Metrics provider.

## FRED

The active endpoint is:

```text
GET https://api.stlouisfed.org/fred/series/observations
query: series_id, api_key, file_type=json, observation_end
```

`FRED_API_KEY` is read at runtime only and never stored or logged. The provider
fetches each raw series once per request, treats `.` as missing, enforces the
`as_of` cutoff, and derives macro changes in Python. Current reviews use the
latest revision; historical replay is explicitly `LATEST_REVISION`, not
point-in-time ALFRED fidelity.

FRED freshness is publication-aware and series-specific: DFF/DFII10 are
bounded at 7 days, DTWEXBGS at 14 days, WALCL at 14 days, and M2SL at 75 days.
These bounds account for normal publication lag without making a series
indefinitely current.

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
- BTC normalized: `flows.btc_etf_net_to_aum_7d` and
  `flows.btc_etf_net_to_aum_30d`;
- ETH normalized: `flows.eth_etf_aum_usd`,
  `flows.eth_etf_net_to_aum_7d`, and `flows.eth_etf_net_to_aum_30d`;
- MARKET: complete-date BTC + ETH aggregation.

Rows are settled U.S. trading dates, normalized to `America/New_York` 16:00,
and filtered locally by `as_of`. Up to 300 daily rows are accepted. If the
history is too short, 1D/7D can still succeed while 30D reports
`PROVIDER_INSUFFICIENT_HISTORY`; this is not unsupported capability. Negative
flows are valid. The active contract does not provide liquidation history, so
liquidation metrics remain optional/skipped and are never routed here.
The normalized BTC metrics divide completed net inflows by AUM on the same
ending ETF date; missing AUM is unavailable and never zero-filled.

## Ethereum-specific public data

The ETH route is split by economic meaning. For `eth.l2.tvs_usd`, growthepie
uses `master.json` plus the bulk `export/tvl.json` contract. Python derives the
source-defined production L2 universe, excludes Ethereum L1, aggregate keys,
and documented non-L2 sidechains, then sums only exact USD `tvl` rows from the
latest common completed UTC day. Missing chain data is unavailable, never zero.
The normal path uses at most one master request and one TVL export request per
fresh provider instance, within the source's 10 calls/minute fair-use limit;
DeFiLlama protocol TVL is not a semantic substitute. For the remaining metrics,
growthepie uses
`/v1/master.json` and `/v1/fundamentals.json` (or the documented
`/v1/export/rent_paid.json`) for L2 rent and compatible DA/blob metrics,
retaining `growthepie / orbal GmbH` and `CC BY 4.0` attribution. The
`daoverview.json` and `datimeseries.json` endpoints are attempted only when
the current contract permits them; a 403 remains a bounded provider failure.
Blobscan uses `https://api.blobscan.com/stats/timeseries` with
`timeFrame`, `metrics`, and `sort`, then parses
`data.timestamps` plus `data.series[].metrics`; no guessed `data[]` wrapper is
accepted. Growthepie is the only active structured route for the supported
Ethereum L2 TVS and activity metrics; no unrelated metric is substituted.

Coin Metrics Community is checked for catalog-supported ETH `SplyCur`,
`IssTotNtv`, MVRV, realized-cap, and realized-price primitives. The current
Community catalog does not provide the staking primitives required to name a
value "active effective stake", so staking quantity/change/APR/participation
metrics are optional until an exact aggregate source is configured. Python derives supply growth,
exchange-flow/market-cap, active-stake-change/supply, and ETH flow/AUM ratios.
Provider failure, unsupported catalog metrics, missing denominators, and
conflicting rows remain unavailable; they never become zero or neutral
positive evidence.
Qualitative ETH structural-risk evidence may use a bounded Web fallback when no
structured source is available; it remains non-scoring and is never treated as
deterministic numerical evidence.

## BGeometrics

BGeometrics provides the no-key endpoint
`GET https://bitcoin-data.com/v1/mvrv-zscore/last`. The verified 2026-09-09
response contains `d`, `unixTs`, and numeric `mvrvZscore`; `d` is the source
observation date and is never replaced by `fetched_at`. The adapter makes one
request per refresh, uses a cache TTL of at least 24 hours, and marks values
older than the seven-day metric freshness window unavailable. It does not
download the full history during a normal review.

## LunarCrush

The active adapter uses API v4 at `https://lunarcrush.com/api4` with
`LUNARCRUSH_API_KEY` in `Authorization: Bearer <key>`. It requests
`GET /public/coins/:coin/time-series/v2` with `bucket=day`, `start`, and `end`.
Only `BTC`, `ETH`, `SOL`, `BNB`, `LINK`, and `AAVE` are allowlisted. The
normalized metrics are:

| Local metric | LunarCrush source and deterministic method |
|---|---|
| `sentiment.social_bullish_share` | completed daily `sentiment / 100` |
| `sentiment.social_mentions_24h` | completed daily `posts_active` |
| `sentiment.social_mentions_change_7d` | exactly aligned `posts_active` 7D change |
| `sentiment.social_sentiment_percentile` | same-asset trailing 90-day `sentiment` empirical midrank |
| `sentiment.social_attention_percentile` | same-asset trailing 90-day `posts_active` empirical midrank |

`posts_active` is the project's operational social-volume/mention proxy: unique
social posts with interactions for the bucket, not a literal textual token
mention count. Percentiles are same-asset trailing-history ranks, not
cross-sectional ranks across cryptocurrencies. Social metrics are lower-authority
positioning context and cannot by themselves produce a strong allocation change.
The endpoint may require a LunarCrush subscription plan; credential presence is
not entitlement proof. Missing or malformed rows remain unavailable evidence.

## Rated Free tier

The current Rated OpenAPI document is OpenAPI `3.1.0` and uses an HTTP Bearer
credential. Probes without `RATED_API_KEY` return HTTP 401. The adapter uses
`/v0/eth/network/dailyRewards` for `sumEffectiveBalance`,
`sumConsensusRewards`, and `sumExecutionRewards`, and `/v1/eth/queues` for
`activatingStake`, `exitingStake`, and `totalWithdrawingBalance`. These source
fields are Gwei and are converted to ETH by `1e9`; validator count is never
multiplied by 32. APR is calculated as
`window_rewards_gwei / average_effective_balance_gwei * 365 / window_days`
and declares consensus plus execution rewards in metadata. Queue records must
include an explicit source date/timestamp; a queue delay or validator count is
not substituted for an ETH amount. `participation_rate` remains skipped until
an exact bounded aggregate definition is available.

## Ethereum Beacon API

`EthereumBeaconProvider` uses `ETH_BEACON_API_URL` or the documented candidate
`https://ethereum-beacon-api.publicnode.com`. The 2026-09-09 bounded probes
for node version, genesis, and finality checkpoints returned HTTP 200. The
provider is registered for diagnostics and future exact fallbacks, but it does
not scan the validator registry during a normal review and does not expose a
staking aggregate without a bounded, semantically exact source.

## Ethereum protocol

`EthereumProtocolProvider` is enabled by default and uses
`https://ethereum-rpc.publicnode.com`; `ETHEREUM_RPC_URL` is an optional local
override and no API key is required. `--probe ethereum_protocol` performs one
`POST` request using `eth_getBlockByNumber("latest", false)` and validates the
execution fields `number`, `timestamp`, `baseFeePerGas`, and `gasUsed`.
`blobGasUsed` and `excessBlobGas` are validated when present. Exact burn
calculations still consume caller-supplied bounded block batches and retain the
EIP-1559 plus EIP-4844 formula. A READY provider or a successful latest-block
probe does not mean that a normal review has a 30D/365D historical block index;
the project does not perform an unbounded per-block scan.

### ETH monetary providers

`UltrasoundMoneyProvider` uses the public
`GET https://ultrasound.money/api/v2/fees/burn-rates` contract. The `d30.rate`
`eth_per_minute` value is converted deterministically with
`* 60 * 24 * 30`; `since_merge` and `since_burn` are never relabeled as 365D.
Etherscan v2 is optional and uses `stats/ethsupply2` with `chainid=1`; only the
documented `EthSupply` and `BurntFees` counters are accepted. Cumulative burn
snapshots are persisted before any same-source, date-aligned window delta is
derived. Missing history is `INSUFFICIENT_HISTORY`, not a fabricated value.

### Structured event transports

The event catalog can use bounded GitHub releases/advisories/commits, RSS/Atom,
Aave Discourse JSON, and the allowlisted BNB Governor RPC contract
`0x0000000000000000000000000000000000002004`. Transport code only returns
metadata candidates. Python filters lookback and deduplicates; `LUNA_MAX`
classifies bounded candidates for materiality. A complete reachable source with
zero candidates is a valid empty response. Same-authority URLs share a
`source_group`; independent security domains do not.

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

For event-only debugging use `scripts/events.py`; it supports fixed request plans,
candidate export, validated host-assisted response import, and an event-only smoke
without scoring or allocation. `EventResolver` distinguishes `FETCHED`,
`FETCH_FAILED`, `CLASSIFICATION_PENDING`, `CLASSIFICATION_FAILED`, `CLASSIFIED`,
`INSUFFICIENT_SOURCE_COVERAGE`, `CONFLICT`, and `SUCCESS`. The host exchange file
retains the original `pending_responses` so candidate identity, canonical URL and
timestamp changes are rejected. Optional API classification is OpenAI-compatible,
bounded, explicitly configured, and fail-closed when its key or endpoint is absent.

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

`FULL_AVAILABLE` Coin Metrics responses retain a validated public provider
payload inside the content-addressed cache. Historical replay reparses that
payload at the requested `as_of`; a later cutoff must not silently reuse a
payload that ends before the cutoff. The router requests only the missing
daily tail, merges it with the cached payload, and reparses the full history.

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
at a trusted bundle. When Python has no default CA paths, an installed `certifi`
bundle is used before the macOS `/etc/ssl/cert.pem` fallback. Credentials and
response bodies are not printed.

Fallback success preserves the primary failed attempt and applies the configured
quality penalty. A partial EventScanner response is `WATCH`, never `CLEAR`;
provider transport failure is not chain halt. The report retains bounded,
redacted failure telemetry and confidence caps.
