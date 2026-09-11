# Data Source Policy

Collection is registry-driven. Python selects applicable metrics and freshness
requirements; `AcquisitionManager` first reuses fresh observations and local
cache, then routes grouped requests through free structured providers. Only
unresolved requests reach the Agent/Web stage, which returns normalized observations
with source and timestamps. Python validates units, timestamps, freshness,
conflicts, and history. Downstream semantic stages consume the normalized
records/Facts rather than repeatedly reading raw webpages.

Event metrics are a separate event-source path, not ordinary provider metrics:

```text
provider metric  -> structured provider router -> normalized observation
event-source metric -> fixed source catalog -> transport candidate
                     -> bounded classifier -> EventScanner observation
derived metric   -> Python dependency calculation
web fallback     -> only the explicitly allowed qualitative fallback path
```

Event transport failure, classification pending, and insufficient source coverage
remain distinct from provider failure. A missing classifier never becomes a clean
event result.

## Principle

Current portfolio recommendations require current data. Prefer authoritative primary sources and triangulate material claims.

## Provider hierarchy

For acquisition, use structured public exchange APIs for market and
derivatives data, CoinGecko for broad market valuation, DeFiLlama for protocol
fundamentals, Blockchair for ETH's rolling native transfer volume, Coin Metrics
Community first for BTC realized-cap/holder-cost-basis valuation and
catalog-checked on-chain context, FRED for official U.S.
macro/liquidity data, and current official/Web
scans for security and regulatory events. Optional API-key
providers may fill advanced ETF or social gaps; they are never required for a
normal review. See
`references/data-providers.md` for routing and cache details.

BTC MVRV Z uses the no-key BGeometrics latest-value endpoint when available.
Ethereum L2 activity, TVS, and Ethereum blockspace fees use growthepie's
structured aggregate data; L2 TVS is not inferred from an unrelated metric. The
`eth.l2.tvs_usd` stock metric is derived from growthepie's TVL/TVS data: the
runtime selects the growthepie-defined production L2 universe, excludes
Ethereum L1, aggregate keys, and documented non-L2 sidechains, selects one
common completed UTC day, and sums exact USD TVL rows for that day. Missing
chain rows make the observation unavailable and are never treated as zero.
The upstream API calls the series `tvl`; this project's semantic metric is L2
TVS. Rated is an
optional exact aggregate source for Ethereum staking primitives, while the
standard Beacon API is bounded to node/finality checks unless a semantically
exact aggregate is available.

## Source hierarchy

### Tier 1 — primary / first-party

Use whenever available:

- exchange spot market data for price/OHLCV;
- protocol/foundation official announcements;
- official token supply/unlock documentation;
- regulator filings and notices;
- ETF issuer/exchange/regulatory filings;
- chain explorers and canonical network data.

### Tier 2 — established aggregators / analytics

Useful examples, subject to availability and coverage:

- CoinGecko for broad market-cap and FDV reference;
- DeFiLlama for DeFi TVL, stablecoins, fees/revenue where applicable;
- Token Terminal or similar analytics when methodology is understood;
- reputable ETF flow aggregators when issuer-level primary data is impractical;
- reputable on-chain analytics platforms.

### Tier 3 — reputable reporting / research

Use for context and rapidly developing events, then trace important claims back to primary evidence where possible.

Social posts, anonymous claims, and influencer commentary are leads, not sufficient evidence for a meaningful allocation change.

## Data categories

### Price and trend

Need:

- current spot price;
- at least 200 calendar days of daily history for the active trend horizon;
- preferably 240 days for MA200, 180D momentum, and context;
- volume and volatility when reliable.

The execution layer consumes normalized `OHLCVSeries` data on the `1D`
timeframe plus a typed `SpotPrice` with `observed_at` and `source`. Indicators
use only completed candles; the spot observation is never inferred from the
last candle for historical replay. The preferred minimum is 200 completed
candles and the preferred coverage is 240 calendar days. Preserve source,
venue/market/quote metadata, fetched time, range, candle count, calendar
coverage, and the canonical SHA-256 OHLCV hash for replay.

Freshness for daily OHLCV-derived metrics is based on
`freshness_reference_at`, which must equal `metadata.completed_through`: the
close boundary of the latest completed UTC candle versus the decision `as_of`.
`observed_at` remains the metric data anchor and `fetched_at` is retrieval
metadata; a recent download does not make old candles current. An old
observation without the completed boundary is not an AUTO fresh hit.
Duplicate UTC dates are invalid. Small gaps lower confidence; large gaps or
observation lag produce low confidence and `WAIT`. Calendar lookbacks never
substitute an arbitrary number of candles when their date window is missing.
`ATR14` uses a simple mean of 14 fully defined true ranges and therefore
requires at least 15 candles in the current implementation; it is not silently substituted with
Wilder smoothing.

Normalized OHLCV used by a plan may be stored as public, immutable content at
`~/.local/share/crypto-portfolio-manager/market-data/sha256/<ohlcv_hash>.json`
(or the configured runtime data directory). The plan also retains a compact
technical summary and `execution_technical` evidence; no full candle array is
embedded in every decision.

Only the current internal data contract is supported. Breaking changes may
require manually regenerating local generated state; no migration or legacy
record reader is provided.

Successful review metrics are normalized as `MetricObservation` records in the
append-only runtime `metrics/observations.jsonl` series. The next review uses
latest/previous observations for compact trend comparison, but refetches
current values because historical freshness does not make old data current.
Every attempt, including `FAILED`, `STALE`, `CONFLICT`, `NOT_APPLICABLE`, and
optional/premium `SKIPPED`, is retained in `metrics/collection-events.jsonl`.

For Volume Profile, prefer completed `1H` or `4H` OHLCV from one consistent,
liquid spot venue. Do not mix incompatible raw-volume sources in one profile.
The `1D` fallback is a low-resolution bar approximation and is capped at
`MEDIUM` confidence.
When bin volumes tie, the deterministic POC tie-break selects the lower-price
bin; value-area expansion also selects the lower-price side on an equal next
bin. Profile values are replayed from their cached hashes.

### BTC market context

Useful:

- BTC dominance;
- total crypto market capitalization;
- stablecoin supply/liquidity trend;
- breadth/alt relative strength;
- BTC volatility and drawdown.

`market.breadth` is defined as the fraction of the deterministic top-20
CoinGecko market-cap universe with a positive completed 30D return. Stablecoin
symbols and wrapped/staked duplicate IDs are excluded; rows without a defined
30D return are unavailable rather than negative. Python derives
`market.breadth_state` from that fraction: `HEALTHY` at or above 0.60, `WEAK`
at or below 0.40, and `NEUTRAL` between those bounds. This is context, not an
automatic trade signal.

### Chain liveness

`risk.chain_liveness_status` is a structured operational metric for chain-native
assets only: `BTC`, `ETH`, `SOL`, and `BNB`. Bitcoin uses recent canonical
block JSON from Blockstream Esplora or mempool.space. Ethereum uses
read-only `eth_getBlockByNumber("latest", false)` and, when supported,
`"finalized"`; BNB uses the latest-block call from official dataseeds.
Solana uses `getHealth`, finalized `getSlot`, and
`getBlockTime`. Python computes timestamp age, finality delay, and independent
source quorum.

`HEALTHY` requires recent canonical progress, `DEGRADED` represents progressing
but abnormal age/finality, and `HALTED` requires severe stale chain state from
the configured independent source groups. Provider reachability, DNS/TLS,
timeouts, HTTP errors, or rate limits are unavailable evidence, not proof of a
halt. RPC endpoints are observation transports, not authorities on security,
governance, or regulation. AAVE and LINK are not independent chains.

### ETF / institutional flows

Primarily relevant to BTC and ETH where spot products exist.

Use daily and multi-week context; avoid overreacting to one day of flow unless exceptional.

The optional SoSoValue adapter provides U.S. BTC and ETH ETF products. Python
derives absolute 1D/7D/30D values and BTC/ETH 7D/30D net-flow-to-AUM ratios from completed trading-date rows after local
`as_of` filtering; `MARKET` is the complete-date BTC+ETH sum, not BTC-only
flow. A short response is `PROVIDER_INSUFFICIENT_HISTORY`, not unsupported
capability. See `references/data-providers.md` for the active endpoint and
authentication contract.

For ETH, normalized 7D/30D flow-to-AUM ratios are preferred over raw USD flow
for scoring. Ethereum supply/issuance, staking, realized valuation, L2 rent,
DA share, blob demand, and explicitly Ethereum-secured L2 activity are separate
evidence groups; provider failure is unavailable evidence, never zero. ETH
FDV/market-cap is not economically applicable, and structural client/entity/
builder concentration remains non-scoring context unless a stable structured
source is available.

### Ethereum metric methodologies

`ETH onchain.transfer_volume` uses Blockchair's current `/ethereum/stats`
response. It treats `volume_24h_approximate` as wei and converts it to USD with
the same response's `market_price_usd`:

```text
volume_24h_approximate / 1e18 * market_price_usd
```

This is a rolling 24-hour approximate native ETH monetary transaction volume,
not adjusted transfer value. One structured request supplies both inputs;
provider failure or malformed fields leaves the observation unavailable rather
than filling it with zero. The endpoint is current-only and cannot provide an
arbitrary historical `as_of`; cached observations may be reused only when the
existing router considers them eligible. ERC-20 transfer volume is not assumed
to be included, and remains separate from Ethereum L2 statistics.

BTC/BNB transfer volume remains the catalog-checked Coin Metrics
`TxTfrValAdjUSD` route where supported; that methodology is not used for ETH.

BNB on-chain demand uses DeFiLlama's chain-fees endpoint with catalog-aware Coin
Metrics fallback. The current plan does not perform expensive per-block
transaction-count collection for BNB.

30D active-stake change uses the current observation minus the closest cached
same-source observation at or before the target date, within the configured
tolerance; validator counts are not converted with a 32 ETH shortcut.

If a required primitive, timestamp, history segment, or methodology match is
missing, the metric remains unavailable or `SKIPPED`. No provider outage is
converted to zero.

### Fundamentals

Use asset-appropriate metrics, not one universal template.

Examples:

- fees/revenue;
- TVL/liquidity;
- stablecoin balances;
- active usage;
- validator/staking/security data;
- developer/ecosystem activity;
- supply/emissions/unlocks;
- token value capture.

Broad market valuation is a separate data category: `valuation.market_cap` and
`valuation.fdv` come from CoinGecko when configured, with catalog-aware Coin
Metrics `CapMrktEstUSD` as market-cap-only fallback. Python derives
`valuation.fdv_market_cap_ratio`; DeFiLlama failure must not make
BTC/ETH/BNB/AAVE market cap unavailable when these routes are available.

`market.btc_dominance` and `market.total_crypto_market_cap` share one
CoinGecko `/global` request. Chain-native `fundamentals.stablecoin_liquidity`
uses the DeFiLlama stablecoin API's latest
`totalCirculatingUSD.peggedUSD` value for Ethereum, Solana, or BSC; it is not
protocol TVL. `market.stablecoin_supply` uses the same global stablecoin
methodology from `/stablecoincharts/all`.

### Events

Check for:

- exploits/security incidents;
- chain outage/liveness issues;
- manually supplied governance or protocol context;
- regulatory decisions;
- material token unlocks/emissions changes;
- ETF/product changes;
- protocol upgrades with economic consequences.

The on-demand EventScanner uses a deterministic allowlist rather than a
generic crawler. BTC security sources are Bitcoin Core security advisories and
Core releases. ETH security sources are Ethereum Foundation Blog RSS,
go-ethereum advisories, and consensus-spec advisories; Ethereum security is not
represented by one client. AAVE security uses Aave Risk Discourse JSON plus the
Aave V3 repository advisories. BNB security uses the BNB Smart Chain
security-advisory repository and official release notes. Regulatory collection
remains one shared MARKET scan mapped to affected assets, with ESMA/MiCA
collected through the ESMA RSS feed. Governance and protocol-change information
is supplied only through explicit manual asset context.

Structured event transports remain bounded and source-specific: GitHub commit
requests use the review window as `since`/`until`, and Aave Discourse follows
same-origin `more_topics_url` pagination until a reliable ordered `created_at`
page crosses the requested lookback boundary or until its configured page cap.
Reaching that cap before window coverage leaves the source incomplete.
`NO_STRUCTURED_TRANSPORT` means
the fixed source has no configured supported structured endpoint; it is not a
DNS, TLS, HTTP, or provider transport failure.

Regulatory collection is one shared market-level scan over the configured SEC,
CFTC, and ESMA/MiCA primary-source scope and is then mapped to affected assets.
It does not claim worldwide regulatory coverage. Tier 2/3 material claims may
confirm or discover leads, but cannot replace an inaccessible required Tier 1
source.

Event scan freshness is based on `scan_as_of`, the time the configured source
set was checked. It is not based on the publication time of the last incident.
Full required-source coverage with no material item is
`NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES`; incomplete coverage is
`INSUFFICIENT_SOURCE_COVERAGE` and is never reported as safety.

## Freshness standards

### FRED publication-aware macro freshness

Macro freshness uses a bounded age for each FRED series rather than one global
TTL. The current policy is DFF and DFII10 at 7 days, DTWEXBGS at 14 days,
WALCL at 14 days, and M2SL at 75 days. These windows account for the expected
publication lag of daily, weekly, and monthly official observations while
still marking genuinely old data `STALE`. Historical replay still applies the
`as_of` cutoff and uses the latest revision; it does not claim ALFRED vintage
fidelity.

### Derivatives positioning

Prefer official exchange derivatives endpoints, then reputable multi-venue
aggregators or providers with documented methodology. Preserve venue or
aggregation scope, funding interval, contract basis methodology, source,
observed time, and fetched time. Long/short ratios from different venues or
methodologies are not comparable; mark the comparison `CONFLICT` or unavailable
and lower confidence.

Collect funding rate and compatible 24H/7D averages, open interest and its
1D/7D changes, long/short ratios, and annualized futures basis
when available. Open-interest growth or decline is context, not a standalone
directional signal.

### Community sentiment

Prefer transparent structured analytics with bot/spam filtering, unique-author
counts, engagement quality, sample size, and methodology. Social bullish share,
aligned mention changes, attention, and market fear/greed are lower-authority context.
Unstructured posts may support a short low-confidence narrative, but never
become a fabricated numeric metric or a standalone trade trigger.

LunarCrush v4 supplies the optional structured social route when
`LUNARCRUSH_API_KEY` has endpoint entitlement. `posts_active` is the explicit
operational proxy for social volume/mentions. Sentiment and attention
percentiles are empirical midranks inside each asset's trailing 90 completed
daily observations, not cross-sectional cryptocurrency ranks.

Execution-block history is outside the current provider registry and normal
reviews do not fan out over execution blocks.

The normal metric plan does not request LunarCrush social metrics unless
`optional_context.collect_optional_social` is enabled. Rated inactivity and
LunarCrush entitlement/rate-limit errors remain provider diagnostics or optional
`SKIPPED` events; they do not
reduce required scoring coverage.

### BTC cycle and on-chain context

Use original or established on-chain analytics with explicit methodology for
MVRV, realized price, SOPR, and retained holder metrics. Halving timestamps are
static protocol facts; the next timestamp is an estimate and must be labeled as
such. Missing proprietary cycle metrics lowers cycle confidence but does not
block an otherwise valid portfolio review. Cycle timing alone is not a
top/bottom or execution signal.

Use judgment, but default to:

- spot price: current session / near real time;
- daily trend data: updated through latest completed daily candle where possible;
- flows: latest published daily data;
- news/events: search recent sources at the time of analysis;
- fundamentals: latest published period plus trend vs prior periods;
- token unlocks: current official/credible schedule.

Always state when a key data series is materially stale.

Use one consistent volume source across the lookback. If volume is unavailable,
inconsistent, or explicitly marked unreliable, report `volume_state=UNKNOWN`
and reduce technical confidence rather than treating it as zero.

## Missing data

Critical missing data:

- current price;
- recent trend/price history;
- portfolio valuation;
- unresolved major security event status.

If critical data is missing, do not provide a strong actionable entry.

In the current scoring model, non-critical missing data keeps its configured factor weight, contributes
neutral 50 through reliability shrinkage, and reduces weighted coverage and
confidence.

## Conflict handling

When sources disagree materially:

1. prefer primary source;
2. check timestamps and methodology;
3. identify whether one source is stale;
4. state unresolved discrepancy;
5. reduce confidence and position size.

The current policy defines metric/domain `max_age_seconds` and `half_life_seconds`.
`observed_at` measures the fact; `fetched_at` measures retrieval. Source
quality is tiered, cache reuse is not independent redundancy, and unresolved
material conflicts do not produce a synthetic value.

## Metric history ownership

Investment horizon and calculation history are separate. The canonical
`crypto_portfolio.metric_history_requirements` table assigns `CURRENT`, bounded
`BOUNDED` windows (45D, 105D, 195D, or 380D), or `FULL_AVAILABLE` to each
metric. The 240D execution preference applies to OHLCV only; it cannot
truncate issuance, tokenomics, or full-history valuation inputs.

| Metric class | Requirement | Web policy |
|---|---|---|
| Spot/current scalar | `CURRENT` | `STRUCTURED_ONLY` |
| 30D/90D/180D aggregation | 45D/105D/195D bounded history | `STRUCTURED_ONLY` |
| 365D tokenomics/issuance | 380D bounded history | `STRUCTURED_ONLY` |
| BTC MVRV Z derivation | `FULL_AVAILABLE` | `STRUCTURED_ONLY` |
| Qualitative structural risk | current bounded evidence | `WEB_ALLOWED` when no structured source exists; non-scoring |
| Security/regulatory event | source lookback | structured first, `WEB_ALLOWED` only when incomplete |

Missing numeric history is unavailable evidence. It is never converted into a
generic Web task or a zero.

## Cash-flow finality

Portfolio performance uses the unitized NAV ledger. Each snapshot carries the
single `cash_flow_resolution_status`: `CONFIRMED_NONE`, `CONFIRMED_AMOUNT`,
`UNRESOLVED`, or `BASELINE_RESET`. Only explicit confirmation or a baseline
reset can make `performance_finality=FINAL`; an unresolved amount stays
`PROVISIONAL` and does not produce a NAV or benchmark return. User resolutions
are append-only records in `cash-flow-resolutions.jsonl`; balance changes are
never inferred as deposits, withdrawals, or zero flow.

## Structured event transport

The fixed event catalog may use bounded GitHub REST, RSS/Atom, Discourse JSON,
or allowlisted RPC log transports. Python performs lookback filtering and
deduplication; the Agent only classifies the bounded candidate packet for
materiality. A reachable, complete source with zero candidates is a valid
empty response for that declared scope. Sources from one authority use a
shared source group, while independent security domains remain separate.
