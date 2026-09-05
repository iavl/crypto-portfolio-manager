# Portfolio Risk Model

Python builds the structured `RegimeInputs` (BTC trend, volatility, flow,
breadth, and cash-flow-aware drawdown) before the deterministic regime engine.
Semantic event risk may override the normal confirmation path, but no model
may replace the portfolio-level risk authority.

## Core principle

The configured maximum-loss preference applies to the **whole portfolio**, not each individual asset. The default `max_portfolio_drawdown` is 20%.

It is a risk budget / drawdown objective, not a guarantee. Crypto can gap, correlate toward 1 during stress, and exceed modeled losses.

Portfolio-level risk always overrides single-asset conviction.

## Market regimes

### NORMAL

Typical characteristics:

- BTC medium/long-term structure healthy;
- broad liquidity conditions not deteriorating materially;
- volatility manageable;
- no cluster of systemic negative events;
- breadth/relative strength not signaling broad risk-off.

Indicative allocation envelope, not a fixed target:

- Stablecoin: 10–20% by default, never below the configured minimum
- BTC: 35–50% (default core allocation)
- ETH: 20–35% (default core allocation)
- Satellites combined: 10–25%

The BTC/ETH rows are the default core allocation. If the user changes `core_symbols`, apply the core risk posture to the configured core group instead of treating BTC/ETH as mandatory holdings. In every regime, the stablecoin lower bound is the larger of the regime default and `min_stablecoin_weight`; if the configured floor exceeds the default upper bound, raise that upper bound to the floor because stablecoins have no fixed maximum.

### DEFENSIVE

Typical triggers include several of:

- BTC loses important medium-term structure;
- volatility rises materially;
- alt/BTC relative strength deteriorates broadly;
- ETF/capital flows weaken materially;
- market breadth contracts;
- macro/regulatory/security risk rises;
- portfolio drawdown consumes a meaningful portion of its risk budget.

Indicative envelope:

- Stablecoin: 20–40% by default, never below the configured minimum
- BTC: 35–50%
- ETH: 15–30%
- Satellites combined: 0–15%

Actions:

- stop aggressive altcoin additions;
- reduce weakest BTC-relative assets first;
- prefer BTC over marginal satellite exposure;
- preserve capacity for later entries.

### CAPITAL_PRESERVATION

Typical triggers include:

- major BTC trend breakdown plus confirmation;
- systemic security/regulatory/liquidity shock;
- multiple risk indicators deteriorate simultaneously;
- portfolio drawdown approaches the allowed risk budget with no clear stabilization;
- correlations spike and downside volatility dominates.

Indicative envelope:

- Stablecoin: 40–80%+ when justified, never below the configured minimum
- BTC: 15–40%
- ETH: 0–20%
- Satellites combined: 0–5%

Actions:

- prioritize capital preservation over BTC outperformance;
- reduce high-beta satellites aggressively when thesis/risk fails;
- do not bottom-fish merely because drawdowns are large;
- re-risk only after objective improvement.

## Risk transition

Do not flip regimes because of one noisy indicator. Prefer confirmation across independent dimensions:

1. trend/structure;
2. volatility/drawdown;
3. liquidity/flows;
4. breadth/relative strength;
5. material event risk.

A single extreme event can override this confirmation rule when it directly threatens asset safety or market functioning.

## Drawdown guardrails

When reliable historical portfolio values exist, calculate peak-to-current drawdown. Let `D` be the positive configured `max_portfolio_drawdown` fraction. Apply these response bands:

- 0% to `-0.40D`: normal monitoring.
- `-0.40D` to `-0.60D`: reassess risk concentration and weak satellites.
- `-0.60D` to `-0.80D`: bias defensive; new risk requires strong evidence.
- `-0.80D` to `-D`: capital preservation becomes primary; reduce avoidable high-beta risk.
- below `-D`: treat as risk-budget breach; do not attempt to “win it back” with more beta.

With the default `D = 0.20`, these bands are 0%, -8%, -12%, -16%, and -20%.

These are portfolio-level bands and should be interpreted alongside regime and volatility.

The deterministic regime engine applies a floor from these bands: drawdown at
or below `-0.60D` cannot remain `NORMAL`, drawdown at or below `-0.80D` cannot
remain below `CAPITAL_PRESERVATION`, and drawdown below `-D` is a risk-budget
breach. Allocation and the risk gate both require the stable sleeve to be at
least the larger of the global minimum and the selected regime target.

## Concentration and volatility

Dynamic single-asset limits are preferred over fixed caps.

Sizing should decrease when:

- realized/implied volatility rises;
- liquidity deteriorates;
- confidence falls;
- correlation with existing holdings is high;
- event/tokenomics risk rises;
- asset is a configured satellite rather than core.

Sizing may increase when:

- asset is a configured core asset;
- score and confidence are high;
- valuation is not excessively extended;
- portfolio correlation/risk remains acceptable;
- regime is NORMAL.

## Stress test

Before recommending a new target, perform a simple scenario stress test when data permits.

At minimum consider:

- BTC: severe but plausible medium-term decline;
- configured core assets: larger decline than BTC where their risk profile warrants;
- configured satellites: materially larger decline than BTC;
- stablecoins: nominally stable but not risk-free.

Do not present a stress test as a probability forecast. Its purpose is to expose hidden concentration and beta.

## Risk hierarchy

When reducing risk, generally prefer:

`weak/high-beta configured satellites -> stronger configured satellites -> configured core assets -> stablecoin`

This is a default hierarchy, not an absolute rule. A severe asset-specific event can make a core asset reduce faster than a satellite.

## Positioning and cycle warnings

Derivatives positioning and BTC Cycle Context are execution overlays, not
additional portfolio risk budgets. The risk gate may emit
`POSITIONING_CROWDED_LONG`, `POSITIONING_EXTREME`,
`BTC_CYCLE_RISK_ELEVATED`, or `BTC_CYCLE_RISK_HIGH` as warnings. They do not
change target weights, independently create `REDUCE`/`EXIT`, or override the
base portfolio risk gate.

For an approved `INCREASE`, immediate deployment uses the configured minimum
of the base, positioning, and cycle factors. High long-crowding may cap the
first deployment to 50% and extreme positioning to 25% under the default
policy; elevated and high cycle risk default to 80% and 50%. A confirmed
technical extension plus long crowding can produce `WAIT`. Deleveraging only
removes a crowding penalty and never boosts exposure. The halving clock alone
has no risk or trade authority.

## Review-specific event criticality

Event metrics remain requested and visible even when they are not hard
critical. The canonical matrix is:

| Metric | `SNAPSHOT_REVIEW` | `FULL_REVIEW` | `EVENT_REVIEW` |
|---|---|---|---|
| Security | Critical | Critical | Critical |
| Chain liveness | Critical | Critical | Critical |
| Governance / protocol-change context | Context | Required | Critical |
| Regulatory | Context | Required | Critical |

`Context` and `Required` failures lower coverage/confidence and remain in the
collection log. `Critical` failures trigger hard-critical handling and block
high-conviction action. A current scan with no material result is
`NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES`; it is risk hygiene, not bullish
evidence. The scan timestamp controls freshness, not the date of an older
incident article.

Before scoring, a pending hard-critical event-source request is a resolution
state, not a failed scan result. After an explicit source response is
processed, incomplete coverage remains a critical failure and keeps the
recommendation fail-closed.
