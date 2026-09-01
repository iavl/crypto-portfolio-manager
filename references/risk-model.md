# Portfolio Risk Model

## Core principle

The approximately 20% maximum-loss preference applies to the **whole portfolio**, not each individual asset.

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

- Stablecoin: 10–20%
- BTC: 35–50%
- ETH: 20–35%
- Satellites combined: 10–25%

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

- Stablecoin: 20–40%
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

- Stablecoin: 40–80%+ when justified
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

When reliable historical portfolio values exist, calculate peak-to-current drawdown.

Suggested response bands:

- 0% to -8%: normal monitoring.
- -8% to -12%: reassess risk concentration and weak satellites.
- -12% to -16%: bias defensive; new risk requires strong evidence.
- -16% to -20%: capital preservation becomes primary; reduce avoidable high-beta risk.
- below -20%: treat as risk-budget breach; do not attempt to “win it back” with more beta.

These are portfolio-level bands and should be interpreted alongside regime and volatility.

## Concentration and volatility

Dynamic single-asset limits are preferred over fixed caps.

Sizing should decrease when:

- realized/implied volatility rises;
- liquidity deteriorates;
- confidence falls;
- correlation with existing holdings is high;
- event/tokenomics risk rises;
- asset is a satellite rather than core.

Sizing may increase when:

- asset is BTC/ETH core;
- score and confidence are high;
- valuation is not excessively extended;
- portfolio correlation/risk remains acceptable;
- regime is NORMAL.

## Stress test

Before recommending a new target, perform a simple scenario stress test when data permits.

At minimum consider:

- BTC: severe but plausible medium-term decline;
- ETH: larger decline than BTC;
- satellites: materially larger decline than BTC;
- stablecoins: nominally stable but not risk-free.

Do not present a stress test as a probability forecast. Its purpose is to expose hidden concentration and beta.

## Risk hierarchy

When reducing risk, generally prefer:

`weak/high-beta satellites -> stronger satellites -> ETH -> BTC -> stablecoin`

This is a default hierarchy, not an absolute rule. A severe asset-specific event can make a core asset reduce faster than a satellite.
