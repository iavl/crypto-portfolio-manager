# Decision and Rebalancing Rules

## Rule 1 — No-trade is first-class

Before proposing any transaction, explicitly test whether `NO TRADE` is superior after accounting for:

- current vs target weight deviation;
- uncertainty;
- market regime;
- transaction friction;
- risk of chasing;
- benefit of preserving stablecoin optionality.

The user asking “what should I buy?” does not imply that capital must be deployed.

Stablecoins and cash are treated as one portfolio sleeve. Preserve the current
stable composition when changing the sleeve size; do not generate
stablecoin-to-stablecoin conversion solely to select one settlement symbol.

For historical performance, an external cash flow attached to a snapshot is
applied immediately before that snapshot valuation. The primary benchmark is
100% BTC buy-and-hold and the secondary benchmark is 70/30 BTC/ETH buy-and-hold.

## Rule 2 — Minimum rebalance thresholds

Use absolute portfolio-weight deviation:

- <3 percentage points: normally `HOLD`.
- 3–5 pp: `WATCH`; trade only with strong supporting evidence or when using new cash to rebalance naturally.
- >5 pp: eligible for active rebalance.
- >10 pp: high-priority rebalance unless a deliberate temporary tactical deviation is documented.

For a very small target position, also consider relative deviation so that a 2 pp error on a 4% target is not ignored blindly.

## Rule 3 — Use new cash before forced selling when sensible

If the portfolio is broadly healthy and only underweight assets need capital, prefer directing new inflows to underweights rather than selling acceptable positions and creating unnecessary turnover.

Do not use this rule to preserve an asset whose thesis has failed.

## Rule 4 — Altcoin burden of proof

A satellite allocation should normally require:

- adequate overall asset score;
- MEDIUM or HIGH confidence;
- acceptable valuation/entry structure;
- clear reason to prefer it over BTC for the same risk budget;
- no severe event/tokenomics concern.

Weak BTC-relative strength plus weak fundamentals = no new allocation even if the token is “down a lot.”

## Rule 5 — Target-weight mapping

Do not use score alone. Start with a regime envelope from `risk-model.md`, then map assets within that risk budget.

A practical qualitative mapping:

- Configured core assets: baseline core allocation and highest sizing tolerance for the selected risk tier.
- Default core assets are BTC and ETH; BTC remains the benchmark even when the user changes the core list.
- high-conviction satellite: meaningful but clearly smaller than core.
- moderate satellite: small allocation.
- low-confidence or weak satellite: 0% / exit.

Always account for correlation and aggregate satellite exposure.

## Rule 6 — Entry plans

Prefer staged entries when:

- volatility is elevated;
- price is not at an unusually favorable support zone;
- the intended position is meaningful;
- event risk is near-term;
- market regime is not strongly NORMAL.

Possible tranche structures are context-dependent, such as 30/35/35 or 25/35/40. These are examples, not fixed formulas.

Choose zones with the deterministic execution engine after rebalance approval,
using:

- support/resistance;
- prior swing levels;
- 20D/50D/100D/200D moving averages where relevant;
- ATR/realized volatility;
- volume/market structure;
- invalidation point.

Avoid arbitrary “-5%, -10%, -15%” ladders without structural justification.
The engine may deploy less than the approved amount or return `WAIT` when
history, structure, volatility, or freshness is inadequate.
The v1 `ATR14` is the simple mean of the most recent 14 true ranges; it is not
silently substituted with Wilder smoothing.

## Rule 7 — Breakout/chase entries

Allowed only when evidence is unusually strong, such as:

- clean breakout from a major multi-week/month structure;
- supportive volume/liquidity;
- healthy BTC/market regime;
- strong BTC-relative trend;
- no excessive parabolic extension;
- fundamentals/flows support the move.

Use a smaller policy-configured initial tranche than a normal pullback entry and
define invalidation clearly. Breakout gates must be supplied by the caller;
OHLCV cannot establish fundamentals or security status.

## Rule 8 — Reductions and profit-taking

Reduce when one or more of these are true:

- current weight materially exceeds target;
- valuation becomes stretched and forward risk/reward deteriorates;
- trend weakens materially;
- BTC-relative case deteriorates;
- event/tokenomics risk rises;
- portfolio drawdown/risk regime requires de-risking;
- investment thesis breaks.

Staged profit-taking is preferred when the thesis remains intact but position size/valuation is excessive.

## Rule 9 — Exit

`EXIT` is appropriate when the forward-looking case no longer justifies holding the asset, especially after:

- thesis failure;
- severe unresolved security/governance failure;
- structural liquidity deterioration;
- persistent relative underperformance with deteriorating fundamentals;
- tokenomics/supply change that invalidates expected return.

Do not refuse to exit simply because the position is below cost basis.

## Rule 10 — Recommendation invalidation

Every material trade recommendation should state what would change it, such as:

- loss/reclaim of a major price level;
- regime transition;
- new security/regulatory event;
- unexpected ETF/flow reversal;
- fundamental metric deterioration;
- token unlock/supply information change.

## Rule 11 — Turnover control

Do not reverse a medium-term recommendation based solely on one or two noisy daily candles.

A new recommendation that contradicts the prior two-week thesis should identify the new evidence that justifies the change.
