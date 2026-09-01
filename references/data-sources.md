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
