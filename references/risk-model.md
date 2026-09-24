# Portfolio Risk Model

See [Investment Strategy](investment-strategy.md) for the overview; this file
owns regimes, drawdown, event/liveness, and portfolio risk authority.

Python builds the structured `RegimeInputs` (BTC trend, volatility, flow,
breadth, and cash-flow-aware drawdown) before the deterministic regime engine.
The regime flow domain is market-level, not a BTC alias: BTC and ETH ETF
flows aggregate via `aggregate_market_flow` (dollar flows over combined
AUM, AUM-weighted ratios, or an explicit single-component fallback with
reduced confidence), while BTC-specific ETF flow remains a BTC scoring
input.
Semantic event risk may override the normal confirmation path, but no model
may replace the portfolio-level risk authority.

## Core principle

The configured maximum-loss preference applies to the **whole portfolio**, not each individual asset. The default `max_portfolio_drawdown` is 15%.

It is a risk budget / drawdown objective, not a guarantee. Crypto can gap, correlate toward 1 during stress, and exceed modeled losses.

Portfolio-level risk always overrides single-asset conviction.

The implementation reports three separate facts:

1. allocation and concentration constraints under the configured envelope;
2. historical cash-flow-aware drawdown and its mandatory regime floor;
3. forward fixed-scenario diagnostics for the current, proposed, fully approved,
   and strategic portfolios.

Passing the first two does not imply the third is within 15%. A stress result
above the risk budget remains `DIAGNOSTIC_ONLY` until a separately approved
policy defines whether it is a soft deployment cap or a hard action gate.
Missing volatility, correlation, or risk-contribution estimates cannot be
described as completed dynamic portfolio risk control.

## Market regimes

### NORMAL

Typical characteristics:

- BTC medium/long-term structure healthy;
- broad liquidity conditions not deteriorating materially;
- volatility manageable;
- no cluster of systemic negative events;
- breadth/relative strength not signaling broad risk-off.

Indicative allocation envelope, not a fixed target:

- Stablecoin: 15–25% by default, never below the configured minimum
- BTC: 35–50% (default core allocation)
- ETH: 20–35% (default core allocation)
- Satellites combined: 10–25%

The BTC/ETH rows are an indicative risk envelope, not an entitlement. Core
classification never guarantees a target or score floor. The current policy starts the
core risky sleeve from a configurable 70/30 BTC/ETH anchor, then applies score
quality, confidence, ETH/BTC opportunity cost, event risk, liveness, and the
existing portfolio caps. If the user changes `core_symbols`, apply the core
risk posture to the configured core group instead of treating BTC/ETH as
mandatory holdings. In every regime, the stablecoin lower bound is the larger
of the regime default and `min_stablecoin_weight`; if the configured floor
exceeds the default upper bound, raise that upper bound to the floor because
stablecoins have no fixed maximum.

The current policy regime stablecoin targets are `NORMAL` 15%, `DEFENSIVE`
30%, and `CAPITAL_PRESERVATION` 50%; the effective floor is the larger of the
global 15% minimum and the selected regime target.

For ETH, `ELIGIBLE_INCREASE`, `HOLD_ONLY`, `UNDERWEIGHT`, `REDUCE`, and
`INELIGIBLE` are deterministic states. Missing ETH/BTC evidence and LOW
confidence block new high-conviction ETH risk; severe/critical events,
`HALTED` liveness, or a broken thesis make ETH ineligible. A weak ETH/BTC
opportunity-cost case can underweight or reduce ETH without changing its base
score. Residual unsafe capital goes to BTC only when BTC is eligible;
otherwise it remains in the stable sleeve.

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

- Stablecoin: 30–45% by default, never below the configured minimum
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

On top of the multi-dimension vote, `determine_regime` accepts the prior
review's effective regime and bounds the result to
`regime_transitions.max_notches_per_review` notches (default 1) away from it:
a vote-based jump straight to (or straight back from) `CAPITAL_PRESERVATION`
must pass through one defensive review first, so one noisy observation cannot
rotate the stable sleeve by 35 points in either direction. Severe systemic
events and the mandatory drawdown floors (`-0.60D`/`-0.80D`) are never delayed
by this bound, and the result is never less defensive than the drawdown floor
requires.

## Drawdown guardrails

When reliable historical portfolio values exist, calculate peak-to-current drawdown. Let `D` be the positive configured `max_portfolio_drawdown` fraction. Apply these response bands:

- 0% to `-0.40D`: normal monitoring.
- `-0.40D` to `-0.60D`: reassess risk concentration and weak satellites.
- `-0.60D` to `-0.80D`: bias defensive; new risk requires strong evidence.
- `-0.80D` to `-D`: capital preservation becomes primary; reduce avoidable high-beta risk.
- below `-D`: treat as risk-budget breach; do not attempt to “win it back” with more beta.

With the default `D = 0.15`, these bands are 0%, -6%, -9%, -12%, and -15%.

These are portfolio-level bands and should be interpreted alongside regime and volatility.

The deterministic regime engine applies a floor from these bands: drawdown at
or below `-0.60D` cannot remain `NORMAL`, drawdown at or below `-0.80D` cannot
remain below `CAPITAL_PRESERVATION`, and drawdown below `-D` is a risk-budget
breach. Allocation, the risk gate, and the rebalance engine all require the
stable sleeve to be at least the larger of the global minimum, the selected
regime target, and the drawdown budget overlay floor below.

## Drawdown budget overlay

The response bands above relabel the regime; they cannot by themselves keep a
portfolio inside `D`; a `CAPITAL_PRESERVATION` target of 50% stable still
implies roughly a 30% portfolio drawdown when core assets fall 60%, which is
twice the default budget. The drawdown budget overlay (`risk.drawdown_budget_overlay`)
therefore enforces the budget at position level, on top of the regime targets:

- **Ladder**: every unit of budget consumed removes one unit of risky-weight
  allowance, `risky_cap = 1 - |drawdown| / D`. At zero drawdown the cap does
  not bind (regime targets govern); at `-0.60D` the cap is 40% risky, at
  `-0.80D` it is 20%, and at `-D` the book is fully stable. The floor this
  imposes on the stable sleeve is applied by allocation, validated by the
  risk gate, and re-validated by the rebalance engine. A reactive overlay
  always absorbs the first gap at pre-crash exposure; its job is to stop the
  compounding afterwards, so drawdown converges toward `D` instead of running
  to a multiple of it.
- **Execution speed**: while the overlay floor exceeds the regime's own
  stable target, every overweight risky position is a hard
  `RISK_BUDGET_BREACH` reduction that bypasses staging, watch bands, and
  direction-flip confirmation. Positions at or below target are untouched;
  the overlay already capped them.
- **Recovery path**: a book pinned at `-D` cannot heal from a fully stable
  position, because its peak is fixed and stable assets return ~zero. When
  the ordinary market domains alone (trend, volatility, flows, breadth — no
  drawdown, no events) read `NORMAL` for
  `drawdown_budget_overlay.recovery_reviews` consecutive reviews, the book
  may re-risk up to `recovery_risky_floor` (default 25%) even while the
  ladder would allow less. This relaxes only the overlay floor; the regime
  label, its mandatory floors, and every other constraint still apply, and
  the ladder re-tightens immediately if drawdown worsens again.

The `core_risky_min` regime constraint applies to the composition of whatever
risky sleeve remains after the overlay, not to the sleeve's size: capital
preservation overrides the regime's risky minimum, which is the stated
policy hierarchy.

`drawdown_budget_stress` in the research package asserts the mechanism
directly: the floor is monotone, a severe configured single-asset decline
applied to the whole risky sleeve in steps stays within `D` with the overlay
enabled and clearly breaches with it disabled, and a confirmed recovery heals
a budget-limit drawdown instead of locking the book in stable forever.

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

The canonical policy includes a fixed diagnostic scenario for reproducible
reviews. Stable assets use an explicit zero-return assumption; this is a
calculation convention, not a claim that stablecoins are risk-free.

Python owns the arithmetic through `engine.risk.stress_diagnostic`: scenario
inputs are explicit decimal-fraction returns for every asset carrying weight
(stables included), a missing input is an error rather than a zero fill, and
spot losses cannot be below -100%. The output carries the weighted scenario
return, per-asset contributions, the peak-relative projected drawdown
`(1+d)(1+s)-1` clamped at zero with the untruncated change preserved, and the
remaining budget capacity `max(0, 1-(1-D)/(1+d))`; a total loss (`d=-1`) is
explicitly unrecoverable. Which scenarios bind on allocation remains a
pending policy decision — until then the diagnostic reports `DIAGNOSTIC_ONLY`
and never blocks or rescales targets by itself.

## Risk hierarchy

When reducing risk, generally prefer:

`weak/high-beta configured satellites -> stronger configured satellites -> configured core assets -> stablecoin`

This is a default hierarchy, not an absolute rule. A severe asset-specific event can make a core asset reduce faster than a satellite.

## Event-risk gate

Event risk is independent from the attractiveness score. The typed states are
`NORMAL`, `ELEVATED`, `HIGH`, `SEVERE`, and `CRITICAL`; the default new-risk
deployment multipliers are 1.00, 0.75, 0.50, 0.00, and 0.00 respectively.
`SEVERE` and `CRITICAL` block new risk even when the base score is 100. Existing
exposure remains subject to thesis, rebalance, and portfolio-risk rules rather
than an unconditional liquidation instruction.

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

## Chain liveness consequences

`risk.chain_liveness_status` is hard-critical for chain-native assets. Python
classifies structured progress as:

- `HEALTHY`: recent canonical head/finalized progress; no liveness restriction.
- `DEGRADED`: the chain is progressing but age or finality is abnormal; no
  high-conviction increase is allowed and immediate new deployment is capped
  by the policy factor (25% by default).
- `HALTED`: severe stale canonical progress corroborated by at least two
  independent source groups; new exposure/`INCREASE` is blocked while
  `HOLD`/`REDUCE`/`EXIT` analysis remains possible.
- `UNKNOWN`: structured evidence is insufficient. It fails closed for a new
  high-conviction increase.

RPC/DNS/TLS/timeouts, rate limits, and provider outages are collection
failures, not `HALTED`. They remain hard-critical missing evidence. AAVE does
not get a separate chain-liveness assessment.

## Review-specific event criticality

Event metrics remain requested and visible even when they are not hard
critical. The canonical matrix is:

| Metric | `SNAPSHOT_REVIEW` | `FULL_REVIEW` | `EVENT_REVIEW` |
|---|---|---|---|
| Security | Critical | Critical | Critical |
| Chain liveness | Critical | Critical | Critical |
| Manual governance / protocol-change context | Optional manual input | Optional manual input | Optional manual input |
| Regulatory | Context | Required | Critical |

Automatic governance context has no collection failure state. A
`ManualAssetContext` is retained with its explicit scope and provenance;
absence does not lower confidence. `Context` and `Required` failures lower
coverage/confidence and remain in the collection log. `Critical` failures trigger
hard-critical handling and block high-conviction action. A current scan with no material result is
`NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES`; it is risk hygiene, not bullish
evidence. The scan timestamp controls freshness, not the date of an older
incident article.

Before scoring, a pending hard-critical event-source request is a resolution
state, not a failed scan result. After an explicit source response is
processed, incomplete coverage remains a critical failure and keeps the
recommendation fail-closed.

Coverage status semantics are part of the risk gate: `NOT_APPLICABLE` excludes
an asset/metric pair that has no meaningful interpretation, while optional or
premium `SKIPPED` evidence is excluded when no eligible provider exists.
Required `FAILED`, `STALE`, and `CONFLICT` evidence stays in the denominator
and lowers coverage; a hard-critical required failure remains a
`CRITICAL DATA FAILURE`.

## Regime Confidence

Regime labels remain deterministic Python output. Regime confidence is a
separate fixed weighted score over trend, volatility, breadth, flows, portfolio
drawdown, and systemic risk. `NORMAL` therefore does not mean “safe to buy”.
Unknown or stale drawdown/systemic evidence applies a configured cap; severe
events and drawdown floors still take precedence over the confidence score.
