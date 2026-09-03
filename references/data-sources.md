# Data Source Policy

Collection is registry-driven. Python selects applicable metrics and freshness
requirements; `LUNA_MAX` retrieves only those requests and returns normalized
observations with source and timestamps. Python validates units, timestamps,
freshness, conflicts, and history. Downstream semantic stages consume the
normalized records/Facts rather than repeatedly reading raw webpages.

## Principle

Current portfolio recommendations require current data. Prefer authoritative primary sources and triangulate material claims.

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

- CoinGecko / CoinMarketCap for broad market reference;
- DefiLlama for DeFi TVL, stablecoins, fees/revenue where applicable;
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
- at least several months of daily history for medium-term trend;
- preferably 1Y history for cycle/historical context;
- volume and volatility when reliable.

The execution layer consumes normalized `OHLCVSeries` data on the `1D`
timeframe plus a typed `SpotPrice` with `observed_at` and `source`. Indicators
use only completed candles; the spot observation is never inferred from the
last candle for historical replay. The preferred minimum is 120 completed
candles and the preferred coverage is 365 calendar days. Preserve source,
venue/market/quote metadata, fetched time, range, candle count, calendar
coverage, and the canonical SHA-256 OHLCV hash for replay.

Freshness is based on the latest observed completed UTC date versus the
decision `as_of`. `fetched_at` is retrieval metadata and may be after a
historical `as_of`; a recent download does not make old candles current.
Duplicate UTC dates are invalid. Small gaps lower confidence; large gaps or
observation lag produce low confidence and `WAIT`. Calendar lookbacks never
substitute an arbitrary number of candles when their date window is missing.
`ATR14` uses a simple mean of 14 fully defined true ranges and therefore
requires at least 15 candles in v1; it is not silently substituted with
Wilder smoothing.

Normalized OHLCV used by a plan may be stored as public, immutable content at
`~/.local/share/crypto-portfolio-manager/market-data/sha256/<ohlcv_hash>.json`
(or the configured runtime data directory). The plan also retains a compact
technical summary and `execution_technical` evidence; no full candle array is
embedded in every decision.

`policy_version` remains the investment-policy version so existing historical
records stay readable; execution serialization changes are tracked by
`execution_plan_version` (new plans use version 2).

Successful review metrics are normalized as `MetricObservation` records in the
append-only runtime `metrics/observations.jsonl` series. The next review uses
latest/previous observations for compact trend comparison, but refetches
current values because historical freshness does not make old data current.
Every attempt, including `FAILED`, `STALE`, `CONFLICT`, and
`NOT_APPLICABLE`, is retained in `metrics/collection-events.jsonl`.

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

### ETF / institutional flows

Primarily relevant to BTC and ETH where spot products exist.

Use daily and multi-week context; avoid overreacting to one day of flow unless exceptional.

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

### Events

Check for:

- exploits/security incidents;
- chain outage/liveness issues;
- governance actions;
- regulatory decisions;
- material token unlocks/emissions changes;
- ETF/product changes;
- protocol upgrades with economic consequences.

## Freshness standards

### Derivatives positioning

Prefer official exchange derivatives endpoints, then reputable multi-venue
aggregators or providers with documented methodology. Preserve venue or
aggregation scope, funding interval, contract basis methodology, source,
observed time, and fetched time. Long/short ratios from different venues or
methodologies are not comparable; mark the comparison `CONFLICT` or unavailable
and lower confidence.

Collect funding rate and compatible 24H/7D averages, open interest and its
1D/7D changes, long/short ratios, liquidations, and annualized futures basis
when available. Open-interest growth or decline is context, not a standalone
directional signal.

### Community sentiment

Prefer transparent structured analytics with bot/spam filtering, unique-author
counts, engagement quality, sample size, and methodology. Social bullish share,
mention counts/changes, and market fear/greed are lower-authority context.
Unstructured posts may support a short low-confidence narrative, but never
become a fabricated numeric metric or a standalone trade trigger.

### BTC cycle and on-chain context

Use original or established on-chain analytics with explicit methodology for
MVRV, realized price, SOPR, NUPL, and holder metrics. Halving timestamps are
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

Non-critical missing data may be removed from the scoring model with renormalized weights and reduced confidence.

## Conflict handling

When sources disagree materially:

1. prefer primary source;
2. check timestamps and methodology;
3. identify whether one source is stale;
4. state unresolved discrepancy;
5. reduce confidence and position size.
