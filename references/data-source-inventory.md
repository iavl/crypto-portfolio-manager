# Data Source Inventory

本文档记录当前代码实际可用的数据源、endpoint、认证方式、输出字段、
`MetricObservation` 映射和限制。路由与能力的最终来源仍是
`config/data-providers.json`、`crypto_portfolio/providers/routes.py`、provider
capabilities、`crypto_portfolio/metrics_registry.py` 和 EventScanner source
catalog。

每个成功值都会变成带有 `observation_id`、`asset`、`metric_key`、`value`、
`unit`、`period`、`observed_at`、`fetched_at`、`source`、`freshness`、
`confidence` 和 `metadata` 的 `MetricObservation`。每次尝试也会写入
`CollectionEvent`。

## 状态与 provenance

| Status | 含义 |
|---|---|
| `SUCCESS` | 数据通过验证并可用于对应 metric。 |
| `STALE` | 有历史值，但刷新失败或已超出 freshness window。 |
| `FAILED` | 适用数据应当存在，但采集或验证失败。 |
| `CONFLICT` | 来源或重复记录不一致，系统没有安全地选值。 |
| `NOT_APPLICABLE` | 该 asset/metric 组合没有合理含义。 |
| `SKIPPED` | 可选或 premium 数据没有符合条件的 provider。 |

Provider cache 只是加速层，不替代 `metrics/observations.jsonl`。历史
observation 即使原 provider 已停用，也必须保持可读取。

## Provider 总览

| Provider/source | 当前状态 | 认证 | 主要信息 |
|---|---|---|---|
| Binance | 已配置并注册 | 无 | 现价、OHLCV、funding、OI、ratios、delivery basis |
| Bybit | 已配置并注册 | 无 | OHLCV、funding、OI、account ratio |
| CoinGecko | 凭证门控 | `COINGECKO_API_KEY` | BTC/ETH/SOL/BNB/LINK/AAVE market cap、FDV |
| DeFiLlama | 已配置并注册 | 无 | TVL、fees、revenue、fee/revenue multiple |
| Alternative.me | 已配置并注册 | 无 | Fear & Greed |
| Chain liveness | 已配置并注册 | 无 | BTC/ETH/BNB/SOL 进度和 finality |
| Coin Metrics Community | 已配置并注册 | 无 | BTC/ETH 链上、供应、周期、exchange attribution |
| Coin Metrics Pro | 可选、凭证门控 | `COINMETRICS_API_KEY` | authenticated Coin Metrics 数据 |
| GitHub | `AUTO`，有 `GITHUB_TOKEN` 才注册 | `GITHUB_TOKEN` | ETH/AAVE 固定仓库的 30D commits |
| SoSoValue | 凭证门控 | `SOSOVALUE_API_KEY` | BTC/ETH ETF flow history |
| EventScanner | 已配置 source catalog | 无 | security/governance/regulatory scans |
| LunarCrush | 默认停用/无 adapter | `LUNARCRUSH_API_KEY` | social metrics，默认 skipped |

## Binance

公共 HTTPS 请求使用共享的 verified-TLS `HttpClient`，不需要 API key。

| Endpoint | 获取的信息 | Metric keys |
|---|---|---|
| `GET https://api.binance.com/api/v3/ticker/price` | approved USDT spot price | `market.spot_price` |
| `GET https://api.binance.com/api/v3/klines` | completed daily/1H/4H OHLCV | `market.return_30d`, `market.return_90d`, `market.return_180d`, `market.ma20`, `market.ma50`, `market.ma100`, `market.ma200`, `market.atr14`, `market.realized_vol_30d`, `market.realized_vol_90d`, `market.relative_volume`, `market.drawdown`, `market.btc_trend`, `market.volatility_state` |
| `GET https://fapi.binance.com/fapi/v1/fundingRate` / `premiumIndex` | funding current/history | `derivatives.funding_rate`, `derivatives.funding_rate_24h_avg`, `derivatives.funding_rate_7d_avg`, `derivatives.funding_rate_percentile` |
| `GET https://fapi.binance.com/fapi/v1/openInterest` / `futures/data/openInterestHist` | OI and 1D/7D changes | `derivatives.open_interest_usd`, `derivatives.open_interest_change_1d`, `derivatives.open_interest_change_7d` |
| `GET https://fapi.binance.com/futures/data/globalLongShortAccountRatio` / `topLongShortAccountRatio` | account/top-trader ratios | `derivatives.long_short_account_ratio`, `derivatives.top_trader_long_short_ratio` |
| `GET https://fapi.binance.com/fapi/v1/exchangeInfo` / `premiumIndex` | nearest unexpired USDT delivery basis | `derivatives.futures_basis_annualized` |

OHLCV 只使用 completed candles，并保留 venue/market/quote、fetched time、
calendar range 和 OHLCV hash。Binance 不提供本项目配置的 liquidation
aggregates、generic exchange netflow 或 protocol market cap。

## Bybit

公共 HTTPS 请求不需要 API key。

| Endpoint | 获取的信息 | Metric keys |
|---|---|---|
| `GET https://api.bybit.com/v5/market/kline` | completed spot OHLCV 和技术指标输入 | market return/MA/ATR/volatility/volume/drawdown keys，按 capabilities 输出 |
| `GET https://api.bybit.com/v5/market/funding/history` | funding history/averages | `derivatives.funding_rate`, `derivatives.funding_rate_24h_avg`, `derivatives.funding_rate_7d_avg` |
| `GET https://api.bybit.com/v5/market/open-interest` | OI 和变化 | `derivatives.open_interest_usd`, `derivatives.open_interest_change_1d`, `derivatives.open_interest_change_7d` |
| `GET https://api.bybit.com/v5/market/account-ratio` | linear account ratio | `derivatives.long_short_account_ratio` |

Bybit 是支持的 market/derivatives fallback，不实现 Binance 的 delivery-basis
methodology。

## CoinGecko

公共 HTTPS 请求使用共享的 verified-TLS `HttpClient`。Demo API key 只通过
`COINGECKO_API_KEY` 环境变量传入，并发送为 `x-cg-demo-api-key` header；不会
进入 query string、cache identity、history 或 probe output。

| Endpoint | 获取的信息 | Metric keys |
|---|---|---|
| `GET https://api.coingecko.com/api/v3/coins/markets` | 当前 USD market cap、FDV、source `last_updated` | `valuation.market_cap`, `valuation.fdv` |
| `GET https://api.coingecko.com/api/v3/coins/{id}/history` | 按日期的历史 market cap；FDV 仅在官方响应明确提供时使用 | `valuation.market_cap`, `valuation.fdv` |

当前估值一次请求绑定一个精确 allowlisted CoinGecko ID，并允许 market cap 成功
而 FDV 缺失。历史请求只使用 `as_of` 日期的历史 endpoint；不能用今天的
`/coins/markets` 填补历史 FDV。`valuation.fdv_market_cap_ratio` 由 Python
在同资产、非未来、仍新鲜的输入上计算。

## DeFiLlama

公共 HTTPS 请求不需要 API key。当前显式 identifier 为
`ETH → ethereum`、`AAVE → aave`、`SOL → solana`、`BNB → bsc`、
`LINK → chainlink`。

| Endpoint | 获取的信息 | Metric keys |
|---|---|---|
| `GET https://api.llama.fi/v2/chains` | chain TVL | `fundamentals.tvl` for ETH/SOL/BNB |
| `GET https://api.llama.fi/tvl/{identifier}` | lightweight protocol TVL | `fundamentals.tvl` for AAVE |
| `GET https://api.llama.fi/summary/fees/{identifier}` | fees/revenue summaries | `fundamentals.fees_30d`, `fundamentals.revenue_30d`, `valuation.fee_revenue_multiple` |
| `GET https://api.llama.fi/protocol/{identifier}` | protocol payload when usable | selected TVL/fundamental fields |
| stablecoin/fee overview routes | stablecoin liquidity and fee context | `fundamentals.stablecoin_liquidity`, fees/revenue where supported |

DeFiLlama 不再是 broad market-cap/FDV provider。缺少协议 fundamentals 时仍
按 provider failure/stale 处理；不从 TVL 或价格伪造 market cap、FDV 或其比率。

## Alternative.me

`GET https://api.alternative.me/fng/?limit=30&format=json` 提供
`sentiment.market_fear_greed`、分类、观察日期和来源 metadata。它是市场级
context，不是单币情绪，也不是单独的买卖信号。

## Chain liveness

只适用于 chain-native assets：`BTC`、`ETH`、`BNB`、`SOL`。AAVE 和 LINK
在本系统中是 protocol/token，不是独立链。

| Asset | Structured sources | 获取的信息 |
|---|---|---|
| BTC | `https://blockstream.info/api/blocks`; `https://mempool.space/api/v1/blocks` | block height/hash、timestamp、head age、independent source group |
| ETH | `https://ethereum-rpc.publicnode.com`; `https://rpc.flashbots.net` | latest/finalized block、head/finality age、finalized distance |
| BNB | `https://bsc-dataseed.bnbchain.org`; `https://bsc-dataseed-public.bnbchain.org` | latest block、timestamp、independent progress |
| SOL | `https://api.mainnet-beta.solana.com`; `https://solana-rpc.publicnode.com` | health、finalized slot、block time、slot age |

输出为 `risk.chain_liveness_status`：`HEALTHY`、`DEGRADED`、`HALTED` 或
`UNKNOWN`。DNS/TLS/timeout/HTTP/rate-limit failure 是 unavailable evidence，
不是 halt 证据。source failure、age、finality 和 quorum 会保留在 metadata。

## Coin Metrics

Community endpoint 为 `https://community-api.coinmetrics.io`；authenticated
tier 为 `https://api.coinmetrics.io`，使用 `COINMETRICS_API_KEY`。当前适用
assets 为 BTC/ETH/BNB/AAVE，且先检查 catalog 和 1D availability。当前
Community catalog 已确认 BNB 的 canonical asset ID 为 `bnb`，并支持
`CapMrktEstUSD` 的 `1d` 数据。`CapMrktEstUSD`
只作为 CoinGecko 不可用时的 market-cap fallback；不使用
`CapMrktCurUSD` 代替，也不把 `CapFutExp10yrUSD` 当作 FDV。

| 数据组 | 信息 | Metric keys |
|---|---|---|
| Market-cap fallback | estimated circulating-supply market cap | `valuation.market_cap` via catalog-supported `CapMrktEstUSD` |
| Network | active addresses、transfer volume、blockspace fees、transactions | `onchain.active_addresses`, `onchain.transfer_volume`, `onchain.blockspace_fees`, `onchain.transaction_count` when the 1D catalog supports the metric/asset |
| BTC cycle | MVRV、realized price、SOPR、LTH/STH、NUPL | `onchain.btc.mvrv`, `onchain.btc.mvrv_zscore`, `onchain.btc.realized_price`, `onchain.btc.market_to_realized_price`, `onchain.btc.sopr`, `onchain.btc.lth_supply_pct`, `onchain.btc.lth_net_position_change`, `onchain.btc.sth_realized_price`, `onchain.btc.lth_realized_price`, `onchain.btc.nupl` |
| Tokenomics inputs | issuance/current supply | `tokenomics.annualized_emissions`, `tokenomics.supply_growth` |
| Exchange attribution | exchange inflow/outflow | `flows.exchange_netflow` when catalog/tier supports it |

unsupported catalog combinations remain failed/skipped/unsupported and are not
filled with unrelated market data。

## GitHub developer activity

公共 REST endpoint 为 `GET https://api.github.com/repos/{repository}/commits`，
可选 `GITHUB_TOKEN`。固定 allowlist 为：

- ETH: `ethereum/go-ethereum`, `ethereum/consensus-specs`, `ethereum/EIPs`;
- AAVE: `aave/aave-v3-core`, `aave/aave-v3-deploy`.

provider 统计 default branch 最近 30D 的 unique commits，输出
`fundamentals.developer_activity`，并保留 repository、window 和 dataset
metadata。不做 repository discovery、HTML scraping；GitHub rate-limit 不会
被解释为 developer inactivity。

## SoSoValue ETF flows

Endpoint 为 `POST https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart`，
使用 `x-soso-api-key` / `SOSOVALUE_API_KEY`，body 仅为
`{"type":"us-btc-spot"}` 或 `{"type":"us-eth-spot"}`。Python 将美国
交易日按 `America/New_York` 16:00 转为 observation timestamp，并在本地
按 `as_of` 过滤最多 300 天的返回历史。

- `BTC`: `flows.etf_net_1d`, `flows.etf_net_7d`, `flows.etf_net_30d`;
- `ETH`: 同上；
- `MARKET`: complete-date BTC + ETH sum。

metadata 保留 source date、scope、window、rows used、source assets、aggregation
和 excluded incomplete dates。负流量有效。如果源历史不足 30D，1D/7D
仍可成功，只有 30D 标记为 `PROVIDER_INSUFFICIENT_HISTORY`。

SoSoValue does not provide liquidation history in the active contract；liquidation metrics 继续
optional/skipped，绝不路由到 SoSoValue。

## EventScanner

EventScanner 使用固定 source catalog，保留 `source_id`、canonical URL、scan
time、lookback、reachability、materiality、affected assets 和 summary。

| Event class | Source families | Output |
|---|---|---|
| BTC security/protocol | Bitcoin Core security advisories、Core releases、BIPs | `risk.security_event_status` |
| ETH security | Ethereum.org security guidance、go-ethereum advisories、consensus-spec advisories | `risk.security_event_status` |
| ETH protocol/governance | EIPs、AllCoreDevs、Ethereum Foundation protocol notices | `risk.governance_event_status` |
| AAVE security | Official Aave security page、Aave V3 repository advisories | `risk.security_event_status` |
| AAVE governance | Official Aave governance forum、proposal scope | `risk.governance_event_status` |
| BNB security | BNB Smart Chain security advisories、official release notes | `risk.security_event_status` |
| BNB governance | BNB Evolution Proposals、official BNB Chain governance | `risk.governance_event_status` |
| Regulatory | SEC、CFTC、ESMA/MiCA primary-source scope | `risk.regulatory_event_status` mapped to affected assets |

`NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES` 只表示 configured sources 在
scan_as_of 时没有发现 material item；partial reachability 为
`INSUFFICIENT_SOURCE_COVERAGE`。`MATERIAL_EVENT_FOUND` 表示找到 proposal 或
announcement，不等于 exploit、approval 或 execution。

EventSourceScanRequest 必须在外部阶段逐项得到一个
EventSourceScanResponse；不可达来源使用 `reachable=false` 和有界错误。
Acquisition pass 1 只生成请求，pass 2 消费完整响应并在
`require_scoring_ready()` 通过后才允许评分。

## Local data layers

| Layer | Role | Contents |
|---|---|---|
| Binance screenshot | User portfolio input | symbol、quantity、display value/price/cost/P&L、reported total、display warnings |
| `portfolio/snapshots.jsonl` | Append-only portfolio history | validated positions、resolved policy/hash、cash-flow classification、snapshot ID |
| `decisions/decisions.jsonl` | Append-only decision history | actions、weights、constraints、complete Evidence、status、policy snapshot |
| `metrics/observations.jsonl` | Canonical normalized metric history | source observations、freshness、provenance、previous/current comparisons |
| `metrics/collection-events.jsonl` | Collection audit trail | every success/failure/stale/conflict/not-applicable/skipped attempt |
| `provider-cache/` | Acquisition accelerator | provider responses and OHLCV series; not decision authority |

Portfolio quantities, cost basis and account information stay outside Git. A
screenshot `$0.00` cost with insufficient precision remains unknown; it is not
verified zero cost.

## Unavailable and fallback boundaries

- Liquidation aggregates have no configured structured provider.
- Social metrics have no configured adapter by default.
- Web/LLM fallback is unresolved work, not a fixed data source and not a
  replacement for a successful structured observation.
- Failed, stale, conflicting, skipped and unsupported evidence remains visible
  with its reason and scoring/decision effect.
- No provider in this inventory places trades or uses exchange execution keys.
