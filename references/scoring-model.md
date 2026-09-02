# Asset Scoring Model

## Deterministic ownership

Python is authoritative for metric applicability, historical changes,
technical trend, relative strength versus BTC, numeric flow state, weighted
factor arithmetic, coverage, and confidence caps. Terra may interpret
Valuation/Fundamental/On-chain Facts where asset-specific semantics are needed,
but it receives structured current/previous values and must not recalculate
their deltas or the weighted score. A score is an input to portfolio
construction, never a direct trade signal.

Deterministic threshold inputs for the technical trend, BTC-relative, and
numeric-flow factors live under `factor_rules` in `config/policy.json`, so
changing them is explicit, hashable, and replayable with the resolved policy.

## Purpose

Create a repeatable 0–100 measure of portfolio attractiveness. The score is not a forecast and must not directly map to a trade without the portfolio risk model.

## Base weights

| Factor | Weight | What it measures |
|---|---:|---|
| Trend & price structure | 25% | medium-term trend, major moving averages, higher-high/lower-low structure, breakout quality |
| Valuation & historical position | 20% | distance from historical extremes, drawdown, cycle context, valuation proxies where meaningful |
| Fundamentals | 20% | adoption, revenue/fees, developer/ecosystem health, token utility/economics, supply quality |
| On-chain activity | 10% | active usage, TVL/liquidity, transaction/settlement activity, network demand as appropriate |
| Capital flows | 10% | ETF flows for BTC/ETH when applicable; otherwise credible fund/exchange/liquidity flow proxies |
| Relative strength vs BTC | 10% | price and risk-adjusted performance relative to BTC |
| Event / risk adjustment | 5% | security, governance, regulatory, unlock, chain stability, custody/venue-specific risk |

Total: 100%.

## Factor scoring

Each factor receives 0–100:

- 80–100: unusually strong / favorable
- 65–79: favorable
- 50–64: neutral to mildly favorable
- 35–49: weak
- 0–34: materially unfavorable

Do not force granular precision. A score of 73 is not meaningfully more certain than 71 unless the data supports that distinction.

## Trend & price structure — 25%

Consider:

- price vs 50D / 100D / 200D moving averages;
- slope and ordering of medium/long moving averages;
- weekly structure;
- breakout/breakdown quality;
- volume confirmation where reliable;
- distance from support and invalidation levels;
- volatility-adjusted momentum rather than raw percentage gain.

Penalize parabolic extension even if the trend is positive when forward risk/reward is poor.

## Valuation & historical position — 20%

Possible inputs:

- distance from ATH;
- drawdown from recent cycle high;
- realized-value/on-chain valuation metrics when robust;
- market-cap / fee or market-cap / revenue proxies when meaningful;
- FDV and future supply pressure;
- cycle percentile and prior valuation bands.

Do not assume “far below ATH” means cheap.

## Fundamentals — 20%

Evaluate asset-appropriate evidence:

- product/network adoption;
- fees/revenue/economic activity;
- ecosystem durability;
- developer traction where reliable;
- competitive position;
- token value capture;
- inflation/emissions;
- unlock schedule;
- governance quality;
- credible roadmap execution.

For BTC, use network/security/adoption/institutional factors rather than forcing application-protocol metrics.

## On-chain — 10%

Use robust, hard-to-game indicators appropriate to the asset. Examples:

- TVL and stablecoin liquidity trends;
- active addresses/users with caution around sybil activity;
- settlement/transfer volume;
- fees and blockspace demand;
- staking/security participation;
- bridge flows when materially informative.

Avoid double counting metrics already captured under fundamentals.

## Capital flows — 10%

For BTC/ETH, include ETF flows when current and reliable.

For assets without relevant ETF products, use credible proxies such as:

- exchange net flows;
- stablecoin/liquidity migration;
- institutional/fund flows when verifiable;
- spot market liquidity changes.

If no reliable flow metric exists, mark this factor unavailable and renormalize.

Missing BTC-relative evidence for a satellite is not positive evidence. The
allocation engine treats that case as `HOLD_ONLY`: existing exposure may be
preserved, but incomplete comparison data cannot justify new risk.

## Relative strength vs BTC — 10%

This factor enforces the user's objective of outperforming BTC.

Consider:

- 1M / 3M / 6M performance vs BTC;
- trend of the asset/BTC pair;
- relative drawdown;
- volatility-adjusted excess return.

A satellite with weak BTC-relative strength requires unusually strong valuation/fundamental evidence to justify new capital.

## Event / risk adjustment — 5%

This factor is asymmetric: severe negative events can override the numerical score.

Examples:

- exploit or unresolved security incident;
- chain halt/liveness problem;
- governance capture;
- adverse regulation;
- concentrated unlock/supply event;
- material custody or venue issue;
- major favorable institutional/regulatory catalyst.

A severe unresolved event may trigger `REDUCE` or `EXIT` even if the weighted score remains numerically moderate.

## Missing-data renormalization

For non-critical missing factors:

1. Remove the unavailable factor.
2. Divide remaining factor weights by their remaining sum.
3. Compute the score using the renormalized weights.
4. Reduce confidence.

Example: if On-chain (10%) is unavailable, the remaining 90% is rescaled to 100%.

Never invent a proxy solely to preserve the original weights.

## Confidence

Assign one of:

### HIGH

- critical market data is current;
- most factor data is available;
- important claims are primary-source or independently cross-checked;
- no material unresolved contradiction.

### MEDIUM

- critical market data is current;
- one or more non-critical factors are incomplete/stale;
- thesis remains analyzable.

### LOW

- material data gaps;
- conflicting sources;
- major event still developing;
- inadequate liquidity/fundamental information.

A high score with LOW confidence must be capped to a smaller target weight or `WATCH/WAIT`.

Confidence is also capped by weighted data coverage. The canonical thresholds
are 90% for possible `HIGH`, 70% for possible `MEDIUM`, and 60% minimum
investable coverage. Below the minimum, confidence is `LOW`; critical data
incompleteness also forces `LOW`. Unknown factor keys are validation errors.

## Suggested interpretation

| Score | Interpretation | Default posture before risk gate |
|---|---|---|
| 80–100 | Exceptional | Candidate to overweight, subject to valuation and regime |
| 70–79 | Strong | Eligible for meaningful allocation |
| 60–69 | Constructive | Hold / moderate allocation |
| 50–59 | Neutral | Hold small or wait |
| 40–49 | Weak | Reduce / avoid new capital |
| <40 | Unattractive | Strong reduce / exit candidate |

These are starting points, not mechanical trade triggers.
