# Investment Strategy

This document explains the strategy conceptually. It does not override
`config/policy.json` or deterministic runtime logic. Exact active thresholds,
weights, limits, and eligibility rules come from the resolved policy and
Python runtime; the values below are current/default policy examples and may
change with a documented policy update.

## 1. Strategy in One Paragraph

This is a BTC-benchmarked, risk-budgeted, multi-factor Core–Satellite strategy
for approximately 3–6 month, spot-only crypto allocation. It combines
confidence-aware asset evaluation, regime switching, portfolio-level risk
limits, hard event and chain-liveness gates, and staged execution. It seeks
additional return only when the evidence justifies the asset-specific risk;
capital may remain in the stablecoin/cash sleeve when the evidence or timing
is not good enough.

## 2. Objectives and Time Horizon

The current strategy has three objectives, in order of practical priority:

1. Seek to outperform BTC over comparable periods.
2. Seek a positive absolute return.
3. Avoid unacceptable portfolio drawdowns.

The working horizon is approximately 3–6 months. These are active-allocation
portfolio decisions, not intraday signals. The default portfolio drawdown
risk budget is 15%; it is an objective and risk budget, not a guaranteed loss
ceiling. When aggregate risk deteriorates, capital preservation can override
the pursuit of BTC outperformance. See the [investment policy](investment-policy.md)
and [risk model](risk-model.md).

## 3. BTC as Benchmark and Opportunity Cost

BTC has two strategic roles:

- It is the primary benchmark for comparable portfolio performance.
- It is the default crypto opportunity-cost asset for risky capital.

An ETH or satellite increase should answer: “Why take this additional,
asset-specific risk instead of increasing BTC?” A positive absolute return is
not enough if BTC offers a better risk-adjusted use of the same capital.

BTC is not assumed to always win, to receive an automatic score bonus, or to
be impossible to reduce. BTC has its own trend, valuation, capital-flow,
macro/liquidity, event, and liveness risks. The benchmark role does not remove
the need to evaluate BTC.

## 4. Core–Satellite Portfolio Structure

The current/default universe is:

| Sleeve | Assets | Meaning |
|---|---|---|
| Core | `BTC`, `ETH` | Default core risk assets with greater sizing tolerance, not guaranteed holdings or targets. |
| Satellites | `SOL`, `BNB`, `LINK`, `AAVE` | Optional large-cap exposures that must earn their extra risk budget. |
| Stablecoin/cash | `USDT`, `USDC`, `DAI`, `FDUSD`, `TUSD`, `USD`, `CASH`, `U`, `USD1` | Intentional risk-budget capacity and optionality, not merely leftover money. |
| Excluded | `LUNC` | Unmanaged by default; not researched, scored, allocated, or rebalanced. |

The exact universe is policy-driven. Core classification is not an allocation
entitlement, the satellite list is not a buy list, and stablecoins are not
risk-free because issuer, depeg, custody, venue, and contract risks remain.
An excluded holding that appears in a snapshot remains visible as
`EXCLUDED / UNMANAGED` for accounting and visibility; exclusion is not an
automatic sell instruction. `U` and `USD1` remain members of the stablecoin
sleeve under the current policy.

## 5. Decision Hierarchy

The strategy applies portfolio constraints in layers:

```text
Portfolio / market evidence
        ↓
Asset attractiveness score
        ↓
Data confidence / coverage
        ↓
Market regime
        ↓
Event + chain-liveness gates
        ↓
BTC opportunity-cost / Core–Satellite constraints
        ↓
Portfolio allocation + risk limits
        ↓
Rebalance threshold
        ↓
Execution timing / staging
        ↓
INCREASE / HOLD / WATCH / REDUCE / EXIT / WAIT / NO_TRADE
```

The score measures medium-term attractiveness. Confidence controls how much
that evidence can support action. Regime and portfolio risk determine the
available risk budget. Event and liveness gates can block otherwise attractive
exposure. Allocation decides the total approved USD amount; execution decides
whether and how much of that approved amount can be staged now.

## 6. Asset Attractiveness Is Not a Trade Signal

The score is not a target weight, a rebalance action, or an instruction to
deploy capital immediately. It answers:

> How attractive is this asset over the strategy horizon, given the supplied
> evidence?

Later layers answer different questions:

- How much exposure can the portfolio hold within its risk budget?
- Should the target weight change after regime, correlation, concentration,
  stablecoin, and BTC opportunity-cost checks?
- Should an approved amount be deployed now, staged, reduced, or left as
  `WAIT`?

Missing evidence does not become positive evidence. Low confidence can turn an
otherwise reasonable score into `HOLD_ONLY`, a smaller allocation, or
`NO_TRADE`.

### Confidence is action-scoped

Decision Confidence describes the evidence for the contemplated action, not the
weakest asset anywhere in the watchlist. A `HOLD` review uses current exposure;
an `INCREASE` uses the target asset and portfolio constraints. Watchlist-only
assets with zero exposure do not contaminate a held-portfolio decision.

Data Confidence measures coverage, freshness, source quality, and same-metric
redundancy. Mixed factor directions are market information and are represented
once as Signal Agreement. Factor evidence is judged by policy-configured
minimum primary/total evidence, so supporting or optional provider outages do
not automatically force `LOW`. Security, chain liveness, unresolved required
cash flow, and confirmed severe events remain conservative gates.

## 7. BTC-Specific Strategy

Under the current/default BTC profile, the positive-weight factors are:

| Factor | Weight | Strategic meaning |
|---|---:|---|
| `trend` | 35% | BTC market structure and trend. |
| `btc_valuation` | 20% | BTC-native realized-cap and holder-cost-basis valuation. |
| `capital_flows` | 25% | BTC-relevant ETF, exchange, and liquidity flows. |
| `macro_liquidity` | 20% | Official macro/liquidity conditions relevant to BTC. |

The generic `valuation`, `fundamentals`, `onchain`, and
`relative_strength_btc` keys have zero BTC profile weight. They may still be
observable or useful as context, but they do not own BTC base-score weight.
BTC-native valuation includes realized price, MVRV, MVRV Z-score, and
price-to-realized-price where supported. Optional supporting cycle context is
not silently promoted to mandatory evidence.

Bitcoin is evaluated as a monetary asset, not as a DeFi/application protocol.
Normal network operation is a prerequisite and risk condition, not a large
generic application-activity bonus. Network or security deterioration remains
a separate event/liveness gate.

## 8. Default Non-BTC Multi-Factor Strategy

The default non-BTC profile has six positive-weight factors:

| Factor | Weight | Strategic meaning |
|---|---:|---|
| `trend` | 30% | Market structure, momentum, moving averages, volatility, and support. |
| `valuation` | 15% | Historical position, market cap, FDV, and valuation ratios. |
| `fundamentals` | 20% | Adoption, fees/revenue, ecosystem, utility, and economic durability. |
| `onchain` | 10% | Active usage, settlement, blockspace demand, and network activity. |
| `capital_flows` | 10% | ETF, exchange, and liquidity migration flows. |
| `relative_strength_btc` | 15% | Risk-adjusted performance relative to BTC. |

The complete canonical factor namespace is:

```text
trend
valuation
fundamentals
onchain
capital_flows
relative_strength_btc
btc_valuation
macro_liquidity
```

Relative-strength evidence uses the configured `30D`, `90D`, and `180D`
horizons when complete history exists; missing horizons reduce
reliability rather than being silently filled.

For the default non-BTC profile, `btc_valuation` and `macro_liquidity` have
zero weight. ETH remains in the default profile with six positive-weight
factors; its monetary,
staking, L2/data-availability, realized-valuation, and normalized ETF-flow
evidence is interpreted within the applicable factors. Ethereum L2 activity
is bullish ETH evidence only when Ethereum settlement or data-availability
value capture is demonstrated.

When applicable evidence is missing, its configured weight stays in the
denominator and the factor shrinks toward neutral 50 according to reliability.
Available factors do not receive the missing weight, and missing evidence is
never treated as bullish evidence. See the [scoring model](scoring-model.md)
for the exact calculation.

The responsive trend factor gives MA20/MA50/MA100 more authority than MA200,
uses `spot > MA20 > MA50 > MA100` for the primary alignment, and scores 30D,
90D, and 180D momentum continuously with 20%/40%/40% authority. This increases
recent-market responsiveness without allowing a one-month spike to override
weak 90D/180D confirmation.

## 9. Satellite Burden of Proof and Hysteresis

A satellite normally needs an adequate score, `MEDIUM` or `HIGH` confidence,
acceptable valuation and entry structure, acceptable event and liveness state,
and a credible BTC-relative case. Weak BTC-relative strength combined with
weak fundamentals means no new allocation, even when the asset has fallen a
long way.

The current/default satellite thresholds are:

- `67`: entry threshold;
- `62`: exit threshold for the hysteresis decision;
- `85`: full score strength.

This creates a deliberate separation between “do not add”, “hold only”,
“reduction candidate”, and “full conviction”. A held satellite in the 62–66
band can remain `HOLD_ONLY`; a score below 62 is an ineligible/reduction
candidate. A score below 62 is not by itself an automatic sell: thesis,
current exposure, event risk, portfolio risk, and rebalance rules still apply.

## 10. BTC / ETH Core Allocation

The current/default core-sleeve anchor is 70% BTC / 30% ETH. It is a prior for
constructing the risky core sleeve, not a fixed portfolio target and not an
entitlement for either asset. The final mix depends on ETH score and
confidence, ETH/BTC opportunity cost, event risk, chain liveness, regime,
concentration, stablecoin requirements, and portfolio caps.

ETH can be attractive in absolute terms but still be held below its anchor or
left `HOLD_ONLY` when its BTC-relative opportunity-cost case is weak. Missing
ETH/BTC evidence blocks a high-conviction ETH increase; an ETH core label does
not restore a score floor or bypass the other gates.

## 11. Market Regimes and Stablecoin Sleeve

The strategy uses three portfolio regimes:

- `NORMAL`: the broadest risk budget, but not automatic full investment;
  satellites still require evidence and caps.
- `DEFENSIVE`: higher stablecoin preference, lower satellite capacity, and a
  stronger burden of proof for new risk.
- `CAPITAL_PRESERVATION`: prioritize capital preservation, sharply limit
  satellite risk, and allow a large stablecoin/cash sleeve.

Stablecoins and cash are one intentional sleeve. The current/default minimum
is 15%, with no fixed maximum; the active regime target can require more. New
cash does not have to be fully invested. Existing safe stable composition is
preserved where possible, and the system does not create stablecoin-to-
stablecoin trades merely to select a preferred symbol.

## 12. Portfolio Drawdown and Risk Budget

The default 15% drawdown budget applies to the whole portfolio, not as a 15%
stop-loss for every asset. Conceptually, the response ladder is:

```text
monitor → reassess concentration → defensive bias → capital preservation
       → risk-budget breach
```

With the current default budget, the deterministic floors are at approximately
9% drawdown for at least `DEFENSIVE`, 12% for `CAPITAL_PRESERVATION`, and a
drawdown beyond 15% is a breach. The detailed bands are defined by the
[risk model](risk-model.md).

High asset conviction cannot override the stablecoin floor, single-asset cap,
satellite cap, core/risky-sleeve constraint, drawdown guardrail, severe or
critical event risk, critical liveness failure, or low confidence.

## 13. Data Confidence and Fail-Closed Behavior

The confidence chain is:

```text
Data Confidence → Regime Confidence → Decision Confidence
```

Confidence is an action and sizing constraint, not a substitute for the score.
For the current scoring coverage policy, 90% is the high-confidence ceiling,
70% is the medium-confidence threshold, and 60% is the minimum investable
coverage. Exact thresholds are policy-controlled.

Hard-critical missing, stale, conflicting, or unresolved evidence reduces
actionability. Missing security or liveness evidence does not mean “no
problem”; it can block a high-conviction increase. The system fails closed by
preserving uncertainty as `HOLD_ONLY`, `WAIT`, `NO_TRADE`, `PROVISIONAL`, or
`BLOCKED` rather than guessing a reassuring value.

Data Confidence does not include cross-factor signal consistency. Decision
Confidence keeps that disagreement in its separate `signal_agreement`
component and scopes asset evidence to the contemplated action.

## 14. Event Risk and Chain Liveness

Event/security risk is separate from the base score. The current/default new-
risk deployment multipliers are:

| Event state | Multiplier | Strategic effect |
|---|---:|---|
| `NORMAL` | 1.00 | Other gates still apply. |
| `ELEVATED` | 0.75 | New deployment is reduced. |
| `HIGH` | 0.50 | New deployment is strongly reduced. |
| `SEVERE` | 0.00 | No new risk; existing exposure is reassessed. |
| `CRITICAL` | 0.00 | No new risk; reduction or exit may be considered. |

These states do not imply automatic liquidation. A severe or critical event
can block new risk even when the base score is high.

Chain liveness is a separate operational gate for chain-native assets:

- `HEALTHY` permits normal liveness treatment.
- `DEGRADED` means progress exists but immediate deployment is capped and a
  high-conviction increase is not allowed.
- `HALTED` requires corroborated severe staleness and blocks new exposure.
- `UNKNOWN` fails closed for a new high-conviction increase.

Provider, RPC, DNS, TLS, timeout, or rate-limit failure is not evidence that a
chain halted. It is unavailable critical evidence and must remain visible.

## 15. Positioning and BTC Cycle Overlays

Derivatives positioning, structured social context, and BTC cycle context are
non-scoring overlays. They answer “Is now a cautious time to deploy?” rather
than “Is the asset fundamentally attractive?” They can cap immediate staged
dollars or produce `WAIT`, but cannot change the target allocation, increase
approved exposure, or independently create `REDUCE`/`EXIT`.

The halving clock alone cannot create a trade. Crowding requires compatible
multi-signal confirmation; social euphoria alone is insufficient. Deleveraging
can remove a crowding penalty but cannot become a positive exposure signal.

## 16. Rebalancing

The current/default rebalance bands are:

| Absolute deviation | Default interpretation |
|---:|---|
| `<2pp` | Normally `HOLD`. |
| `2–4pp` | `WATCH`; act only with strong evidence or sensible new cash. |
| `>4pp` | Eligible for active rebalance after all risk gates. |
| `>8pp` | High-priority rebalance unless a deliberate deviation is documented. |

Deviation alone does not override regime, event, liveness, stablecoin, or
confidence constraints. When the thesis remains sound, new cash should repair
underweights before forcing unnecessary sales. New cash does not justify
preserving a broken thesis.

## 17. Technical Execution and Staging

Allocation and execution are separate:

- The portfolio layer decides how many USD may be added.
- The execution layer decides how that approved amount is staged and whether
  the current setup is good enough to deploy.

Execution uses completed market data and structural context such as moving
averages, ATR, swings, volatility, and Volume Profile. Volume Profile is a
traded-volume concentration proxy, not exact holder cost basis; LVN is context,
not automatic support. The implemented planner generates `PULLBACK` plans.
`BREAKOUT` returns `WAIT` until deterministic breakout/retest planning exists,
and `MIXED` is rejected.

Execution may stage less than the approved amount, retain the remainder as
unallocated cash, or return `WAIT`. It can never increase approved exposure or
place an order.

## 18. NO_TRADE / WAIT as Valid Decisions

`NO_TRADE`, `WAIT`, and `HOLD` are successful, first-class strategy outcomes.
The question “what should I buy?” does not imply that capital must be
deployed. Preserving stablecoin optionality is often the correct action when
the deviation is small, evidence is incomplete, a regime is defensive, event
risk is unresolved, or execution would chase an extended move.

For `NO_TRADE` and `WAIT`, Python persists deterministic gate outcomes and
renders a primary reason plus secondary reason codes. The report does not infer
or replace that attribution with model judgment.

## 19. Illustrative Strategy Examples

These examples are illustrative and do not set target weights:

1. A satellite has a strong score, credible BTC-relative strength, `MEDIUM` or
   `HIGH` confidence, a `NORMAL` regime, and no severe event risk. It may be
   eligible for a bounded satellite allocation, subject to portfolio caps and
   the rebalance threshold.
2. ETH or an altcoin has a reasonable absolute score but weak BTC opportunity-
   cost evidence. It may remain `HOLD_ONLY` or underweight instead of receiving
   new capital.
3. An asset has a high base score but `SEVERE` event risk. It receives no new
   risk; the existing position is evaluated under reduction and exit rules.

## 20. What the Strategy Does Not Do

This strategy is not:

- short-term day trading or high-frequency trading;
- momentum-only trend following;
- blind buy-the-dip averaging or automatic DCA into every eligible token;
- a static fixed-allocation or market-cap indexing portfolio;
- yield farming or a staking-APY justification for a weak asset;
- futures, perpetuals, leverage, margin, or leveraged-token trading;
- automatic execution or custodial order placement;
- BTC-fixed-allocation maximalism.

It is medium-term active portfolio allocation with deterministic risk budgets,
evidence-aware sizing, and staged execution.

## 21. Source of Truth and Related References

The ownership boundaries are:

| Document | Owns |
|---|---|
| `config/policy.json` and deterministic Python | Exact active thresholds, weights, validation, calculations, and risk authority. |
| [Investment Policy](investment-policy.md) | Eligibility, universe, stable sleeve, and portfolio constraints. |
| [Scoring Model](scoring-model.md) | Attractiveness factors, reliability, coverage, and score semantics. |
| [Risk Model](risk-model.md) | Regimes, drawdown, liveness, event, and concentration risk. |
| [Decision Rules](decision-rules.md) | How evidence and constraints become actions. |
| [`HOW_IT_WORKS.md`](../docs/HOW_IT_WORKS.md) | Software ownership, data flow, persistence, and deterministic/semantic boundaries. |
| [Data Sources](data-sources.md) / [Data Providers](data-providers.md) | Evidence methodology and operational acquisition boundaries. |

The repository supports only the current internal contract. Breaking changes
may require manually regenerating local generated state; resolved policy and
policy hash preserve reproducibility for current-format decisions.
