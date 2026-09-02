# Data Source Policy

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
