# Asset Scoring Model

See [Investment Strategy](investment-strategy.md) for the overview; this file
owns scoring, reliability, and coverage semantics.

## Purpose and ownership

The base score is a deterministic 0–100 measure of medium-term asset
attractiveness. It is an input to portfolio construction, never a trade signal.
Python owns applicability, metric history, reliability, coverage, and all
weighted arithmetic. A semantic model may provide bounded raw factor judgments
from the supplied Facts, but it cannot change weights, reliability, or the
resulting portfolio action.

Event/security risk, derivatives positioning, BTC cycle context, portfolio
regime, and technical execution timing are separate gates or overlays. They may
reduce eligibility, confidence, or immediate deployment, but temporary
uncertainty must not change a strategic target by itself.

`SCORING_FACTOR` is an ownership role, not a requirement declaration. Each
applicable metric is classified as `CRITICAL`, `PRIMARY`, `SUPPORTING`, or
`OPTIONAL` (with `PREMIUM_ONLY` retained for entitlement-gated enrichment).
The resolved policy defines minimum primary and total evidence per factor.
Factor sufficiency is `SUFFICIENT`, `PARTIAL`, `INSUFFICIENT`, or
`NOT_APPLICABLE`; one missing supporting provider cannot independently make a
factor insufficient. Positioning, cycle, execution, structural, and
event-risk metrics remain context or gate evidence and never enter base-score
coverage.

## Scoring profiles

The default profiles share eight canonical factor keys:

| Factor | Weight | Ownership |
|---|---:|---|
| `trend` | 30% | market structure, momentum, moving averages, volatility and support |
| `valuation` | 15% | historical position, market cap, FDV and valuation ratios |
| `fundamentals` | 20% | adoption, fees/revenue, ecosystem, utility and economic durability |
| `onchain` | 10% | active usage, settlement, blockspace demand and network activity |
| `capital_flows` | 10% | ETF, exchange and liquidity migration flows |
| `relative_strength_btc` | 15% | asset-versus-BTC risk-adjusted performance |
| `btc_valuation` | 0% for non-BTC assets | BTC-native realized-cap and holder-cost-basis valuation |
| `macro_liquidity` | 0% for non-BTC assets | BTC-relevant official macro/liquidity changes |

`config/policy.json` stores profiles under `scoring_profiles`; their weights
must contain these eight keys and sum to 1. Assets use `default` unless mapped in
`asset_scoring_profiles`.

BTC uses an explicit profile with weights:

```text
trend 35%, btc_valuation 20%, capital_flows 25%, macro_liquidity 20%.
Generic valuation, fundamentals, onchain, and relative_strength_btc are 0%.
```

BTC relative strength is `NOT_APPLICABLE`; no BTC-versus-BTC request or
calculation is made. A zero-weight factor is excluded from score and coverage.

AAVE uses the explicit `defi_protocol` profile:

```text
trend 30%, valuation 20%, fundamentals 35%, relative_strength_btc 15%.
onchain, capital_flows, btc_valuation, and macro_liquidity are 0%.
```

AAVE is evaluated as a DeFi protocol token. Protocol activity is represented
by fundamentals such as TVL, fees, revenue, and token economics; ERC-20
transfer activity is not a substitute for Aave protocol usage. Zero-weight
factors resolve to `NOT_APPLICABLE`, not `MISSING`.

## Metric ownership

Each registered `SCORING_FACTOR` metric has one owner among the eight factors.
Price returns belong to `trend`; asset/BTC returns belong only to
`relative_strength_btc`; flow amounts belong to `capital_flows`; and fees,
revenue, token economics and competitive durability belong to `fundamentals`.
Active usage, settlement activity and blockspace demand belong to `onchain`.
The same economic observation may be cited as context, but it must not add to
two factor totals.

In the current scoring model, technical drawdown remains visible in trend facts for context but adds
no trend points or trend coverage requirement; valuation owns its score.

Event-risk metrics use the `EVENT_RISK` role. Positioning and BTC-cycle metrics
remain overlays and are not scoring factors.

BTC is evaluated as a monetary asset, not as a DeFi or application protocol.
Its base valuation uses MVRV, MVRV Z-score, realized price, and
price-to-realized-price. Generic market-cap/FDV, protocol revenue, TVL,
developer activity, and generic on-chain activity do not add BTC base-score
points. Network health remains a separate risk/context overlay.

### Responsive trend scoring

The trend factor is recalibrated for the 3–6 month horizon. MA20/MA50/MA100
carry the primary moving-average authority (6/8/6 points); MA200 remains a
2-point long-structure context. The main alignment is `spot > MA20 > MA50 >
MA100` or its bearish inverse. 30D/90D/180D returns use continuous magnitude
bands with 20%/40%/40% authority, rather than sign-only points. Trend coverage
is authority-weighted: missing MA50 or 90D/180D momentum removes more authority
than missing MA200, while an evaluated but empty support-zone result is not
treated as missing data. A move more than 2 ATR above support keeps the
8-point extension penalty.

Each deterministic trend result also carries a calculation receipt and signed
contribution breakdown. Reports must use that breakdown for score changes; a
single relative-volume threshold must not be presented as an unexplained score
change. The receipt binds the normalized technical snapshot, completed-candle
boundary, dataset hashes, and resolved policy hash.

WEAK volume confirmation is persistence-gated: the `volume_points` deduction
applies only after the weakness has held across
`factor_rules.trend.volume_weakness_confirmation_closes` (2) completed daily
closes (`previous_relative_volumes`, most recent first). A single weak
reading applies no deduction and states how many closes were confirmed;
SUPPORTIVE volume and all price components (MA crosses, alignment, momentum,
extension) stay immediate. This exists because one weak daily volume reading
combined with a marginal MA20 cross once swung the trend factor 22 points for
a single review.

## Availability and reliability

Every factor is explicitly `AVAILABLE`, `MISSING`, or `NOT_APPLICABLE`.

For an applicable factor, the current scoring model uses neutral shrinkage:

```text
effective_factor_score = 50 + reliability * (raw_score - 50)
```

Reliability is in `[0, 1]`. A missing positive-weight factor has score `None`,
reliability `0`, effective score `50`, and keeps its configured weight. Missing
data is never redistributed as positive evidence. `NOT_APPLICABLE` is valid only
when the resolved profile gives that factor zero weight.

Reliability is derived from structured metadata:

```text
reliability = completeness * freshness_quality * source_quality
```

The initial mappings are `CURRENT=1.00`, `STALE=0.50`, `UNKNOWN=0.00`, and
`HIGH=1.00`, `MEDIUM=0.75`, `LOW=0.50` for source confidence. Do not multiply
the same missing-data penalty twice.

Deterministic factor results retain their coverage and fact freshness when
converted to `FactorScore`, both directly and through `AssetAssessment`.
Result confidence is not multiplied again because it already includes coverage.
An explicit source-confidence field supplies the source-quality multiplier;
Inputs without source confidence use unit source quality.
Explicit reliability cannot raise the metadata-derived value. Numeric
factor inputs retain their documented reliability of 1.

When source-quality or redundancy metadata is absent, the factor result keeps
the current numeric contract but exposes `SOURCE_QUALITY_UNSPECIFIED` or
`REDUNDANCY_UNSPECIFIED` in its confidence reasons. Missing provenance is not
silently presented as independent corroboration; adding new numeric penalties
requires a separate policy decision and offline validation.

The score and coverage are:

```text
base_score = sum(profile_weight[f] * effective_factor_score[f])
coverage   = sum(profile_weight[f] * reliability[f])
```

The profile weights are fixed resolved weights, not renormalized weights.
Explicit custom weights must also sum to 1; invalid sums are rejected.
Coverage can permit at most `HIGH` at 90%, `MEDIUM` at 70%, and investability
at 60%. Critical incompleteness forces `LOW`. The assessment band is derived,
never labeled: the coverage gate and the numeric data-confidence band
(0.80/0.60 thresholds) both apply and the more defensive one wins, so no
caller-supplied label can contradict the evidence the score was built from.

## Relative strength versus BTC

Raw 30D, 90D and 180D excess returns, relative drawdown, pair trend and
the normalized signal remain visible for explanation. Each horizon uses:

```text
excess_h = asset_return_h - btc_return_h
expected_horizon_vol = annualized_std(asset_daily_return - btc_daily_return)
                       * sqrt(horizon_days / 365)
risk_adjusted_h = excess_h / max(expected_horizon_vol, epsilon)
```

OHLCV series are aligned by completed-candle timestamp and only common candles
are used. Positional zipping is reserved for equal-length plain price
sequences. Missing horizons lower reliability/coverage; available horizons do
not silently claim full certainty. The configured neutral band is `0.10` and
saturation is `1.00`; the signal is neutral inside the band and continuously
clamped to 0–100 outside it.

For non-BTC satellites, missing BTC-relative evidence is `HOLD_OR_REDUCE`
(no new risk), while a confirmed materially negative comparison is
ineligible for new risk.

`relative_strength_vs_btc` carries one unit everywhere: a numeric value is the
canonical 0–100 factor score (below 50 is the confirmed weak case, at or above
50 is outperform/neutral), a string is the state, and excess returns stay on
the horizon-scoped fraction facts. No magnitude-based unit guess is applied,
values outside `[0, 100]` are rejected, and core/satellite consumers read the
field identically. Missing factors shrink the weighted score toward neutral
but can never by themselves produce a satellite exit: without independent
negative evidence (broken thesis, severe event, materially weak BTC-relative
case, or hard portfolio risk) an incomplete held satellite stays
`HOLD_OR_REDUCE` at its full current weight.

For BTC macro/liquidity interpretation, 90D rate/real-yield/USD changes, the
13W Fed balance-sheet change, and 6M M2 change are the primary current-horizon
evidence. The 12M M2 change remains long-structure context unless a future
factor methodology explicitly assigns it additional authority; this repository
does not invent a deterministic macro score to force that interpretation.

### Ethereum-specific interpretation

ETH keeps the default six-factor weights: trend 30%, valuation 15%,
fundamentals 20%, on-chain 10%, capital flows 10%, and BTC-relative strength
15%. The current policy enriches the evidence inside those factors with monetary supply
and monetary supply context, proof-of-stake security and staking flows, Ethereum
L2 settlement rent, blob/data-availability demand, DeFi/stablecoin economics,
realized valuation where supported, and ETH ETF flow/AUM ratios.

Within ETH valuation, `eth_valuation.mvrv` is the only primary scoring signal:
MVRV already encodes price relative to realized price, so
`eth_valuation.realized_price`, `eth_valuation.realized_cap_usd`, and
`eth_valuation.price_to_realized_price` are execution-context only. They never
enter the scoring-coverage denominator, their unavailability is reported as an
optional skip, and a realized-price provider failure cannot lower ETH valuation
coverage or asset confidence. BTC keeps its separate valuation model unchanged.
Rated-gated ETH staking values (`eth.staking.active_effective_stake_eth` and its
30D change/normalized-flow dependents) are premium-only: when the Rated
subscription is inactive (config `rated.subscription_active: false`), they are
planned as `SKIPPED_PREMIUM` without network requests instead of surfacing as
provider failures.

L2 activity is not ETH value capture unless Ethereum settlement or DA use is
shown. Rising staking share is not automatically bullish, and deflation is not
automatically bullish; cause, persistence, liquidity, and security context
remain part of the bounded semantic judgment. Raw ETH ETF USD flow is evidence
context; normalized ETH flow/AUM owns capital-flow scoring authority.

The ETH/BTC factor remains part of the base score, while the current policy also
uses it as a hard core-allocation opportunity-cost gate. The same relative
signal is not applied again as a continuous strategic sizing multiplier. This
gate never mutates the base score. ETH FDV/market-cap is `NOT_APPLICABLE` and
cannot contribute positive valuation evidence.

## Capital flows

Two explicitly separate methods share one flow result contract, and the result
always records which one produced it.

Intraday OHLCV is sampled at common 24-hour intervals for the daily-return
volatility calculation. Flow observation objects and serialized observations
use the same normalization path. Multiple observations for one flow horizon
must be resolved upstream; input order must not select the scoring source.
Flow observations retain their weakest source confidence. Relative-strength
OHLCV freshness is evaluated against persisted fetch timestamps, never the
current wall clock; absent fetch timestamps give UNKNOWN freshness.

**Normalized-flow threshold (BTC, ETH, and every other asset).** Flow scoring
prefers normalized ratios such as ETF net flow / ETF AUM or exchange net flow /
circulating market cap. If the denominator is unavailable, the normalized flow
is missing; an absolute USD value is not substituted. The dead zone is
`abs(normalized_flow) <= 0.001` and saturation is `0.01`. The dead zone scores
neutral at 50; values between the dead zone and saturation map continuously and
monotonically to 0–100, then clamp. 7D/30D evidence receives more authority than
1D noise. The state remains `POSITIVE`, `NEUTRAL`, `NEGATIVE`, or `UNKNOWN`.
This method and its thresholds are unchanged for BTC/ETH.

**BNB supply-change percentile (BNB only).** No BNB ETF exists, so BNB scores
the BSC USD-pegged stablecoin supply as an expansion/contraction proxy. The
scored input is the *absolute magnitude* of each horizon's change ranked inside
its own trailing history:

```text
t   = latest complete UTC day at or before as_of (never older than 3 days)
g_h = S_t / S_(t-h) - 1                    for h in {7d, 30d, 90d}
p_h = (below + 0.5 * equal) / n            n >= 180 samples from the 365 days
s_h = 50 + 50 * sign(g_h) * p_h            before t; t itself is excluded
score = sum(s_h * w_h) / sum(w_h)          w = {7d: 0.2, 30d: 0.4, 90d: 0.4}
```

A zero change is exactly 50 and no negative change can exceed 50. A horizon whose
history is shorter than 180 samples, or whose current non-zero change faces an
all-zero history, is uncalibrated and contributes nothing. The weights live in
the canonical policy (`factor_rules.flows.supply_change_horizon_weights`) as a
stated design default, not as a return-optimised parameter. The available raw
scores are renormalised over their own weights while the factor completeness
stays at the effective weight sum `C`, so a missing horizon is penalised exactly
once by the reliability shrink and never re-labelled as full coverage. The
horizon weights, the per-horizon change, rank, sample count, calibration state
and contribution are all reported, and a historical rank is never published as a
`normalized_flow` ratio.

Daily-candle freshness uses one definition everywhere:
`lag_days = expected_latest_completed_date - actual_latest_completed_date`,
where the expected latest completed UTC daily candle is `as_of.date() - 1 day`
(the current UTC day is always still open and never enters indicators). A
series ending on 2026-09-09 at `as_of = 2026-09-11T02:27Z` has `lag_days = 1`,
not two days. The configured `execution.maximum_daily_candle_lag_days` is the
only freshness threshold. The OHLCV cache is checked for tail completeness:
a freshly fetched but tail-stale series is refreshed instead of being served
because its fetch age looks small.

## Event-risk gate and overlays

Event risk is a typed state: `NORMAL`, `ELEVATED`, `HIGH`, `SEVERE`, or
`CRITICAL`, with reasons and evidence IDs. It is not a base-score factor.
Default deployment multipliers are:

| State | Multiplier | New risk |
|---|---:|---|
| `NORMAL` | 1.00 | allowed by other gates |
| `ELEVATED` | 0.75 | allowed with reduced deployment |
| `HIGH` | 0.50 | strongly reduced |
| `SEVERE` | 0.00 | no new risk; existing exposure is a reduction candidate |
| `CRITICAL` | 0.00 | no new risk; existing exposure is a reduction/exit candidate |

The allocation and risk gate still enforce thesis status, confidence, BTC
opportunity cost, regime caps, stablecoin floor, concentration and drawdown.
Positioning and BTC cycle overlays can cap immediate staged dollars or produce
`WAIT`; they cannot increase approved exposure or independently create an exit.

## Hysteresis and interpretation

Satellite strategic sizing is a continuous, policy-configured piecewise-linear
target curve (`allocation.satellite_target_curve`): the target fraction of the
satellite envelope is 0.0 at `satellite_soft_exit_score=57`, 0.20 at
`satellite_exit_score=62`, 0.40 at `satellite_entry_score=67`, and 1.0 at
`satellite_full_score=85`, interpolated linearly between breakpoints. The curve
is bounded to `[0, 1]`, monotonic non-decreasing in score, continuous at every
breakpoint, and independent of the current holding, so a held satellite
crossing the entry score cannot lose its target (the former 66.9 -> 67.0
collapse).

Eligibility gates deployment, never the shape of the curve:

- `ELIGIBLE_INCREASE` (score >= 67 with complete evidence): new exposure may
  be added up to the strategic/deployment target;
- `HOLD_OR_REDUCE` (sub-entry band 62-66, moderate BTC-relative weakness
  30-50, or incomplete evidence on a held position): no new risk; an existing
  overweight may be reduced toward the strategic target along the curve;
- `SOFT_EXIT` (57-61 on a held position): reduce gradually along the curve;
- `INELIGIBLE` (below 57 with complete evidence, broken thesis, SEVERE/CRITICAL
  event, relative strength below 30, or incomplete evidence with no position):
  target zero / exit under hard-risk rules.

A new/non-held satellite below 67 receives no target at all. Repeated reviews
of identical evidence are idempotent because the curve reads only score.
Strategic satellite sizing uses the curve and the risk-tier cap; confidence,
event risk, decision confidence, positioning, and execution overlays are
deployment allowances.

The structural `risk_tier` (`allocation.risk_tier_caps`) defines the asset's
risk envelope as a fraction of the active satellite envelope (normal 1.0,
high_beta/high 0.5): the strategic target is the score curve evaluated inside
that envelope, so a full score reaches exactly the envelope and every lower
score stays strictly below it. The tier is an assessment input rather than a
continuous volatility/beta estimate. Allocation diagnostics label its default
provenance as `MANUAL_ASSESSMENT` and expose `risk_envelope_weight` and
`hard_exposure_cap` (envelope plus the configured `hard_cap_buffer_pp`, a
genuine risk ceiling the rebalance layer enforces via
`RISK_BUDGET_BREACH` reductions); empirical calibration is a separate task.

Materially negative BTC-relative evidence overrides the hold band.
`event_risk.state` is the sole event-risk input; SEVERE and CRITICAL block new risk.

Scores do not directly imply `BUY`, `SELL`, or a full deployment. `HOLD`,
`WAIT`, and `NO_TRADE` remain valid outcomes.

## Current scoring contract

The current scoring model uses fixed profile weights, reliability shrinkage,
and a separate event-risk gate. Old generated records are not replayed or
migrated.

BTC `btc_valuation.mvrv_zscore` is optional unless Community exposes a direct
`CapMVRVZ` or the exact full-history `(CapMrktCurUSD - CapRealUSD) /
population_std(CapMrktCurUSD)` derivation inputs. ETH 365D monetary values are
long-structure context and do not block a 3–6 month decision when history is
unavailable. ETH staking retains only exact `active_effective_stake_eth` and
30D change/normalized-flow evidence when available. BNB on-chain demand is
represented by network gas fees from the `dailyFees` series plus 30/90-day window
totals and changes; no user-count or per-block transaction proxy is requested.
BNB valuation uses the market-cap-to-annualized-network-fees scale and never the
fee/revenue ratio, and BNB fundamentals do not count the same fee series and its
10% revenue derivative as two independent growth inputs.

### Data Confidence

Each applicable metric/factor retains fixed-denominator `coverage`, exponential
`freshness`, `source_quality`, and independent-source `redundancy`. Data
Confidence measures evidence quality only; it does not treat a bullish trend
and bearish valuation as bad data. Cross-factor disagreement is represented
once in the separate Decision Confidence `signal_agreement` component.
Redundancy compares independent observations of the same `(asset, metric,
Each dimension is calculated from its own metadata. If a dimension is not
available, it is omitted and the remaining configured dimension weights are
renormalized; no fixed redundancy value is injected.
Scores are bounded in `[0, 1]` and reported with `LOW`/`MEDIUM`/`HIGH` bands.
Missing, stale, conflict, fallback, and evidence IDs remain explicit. A
hard-critical cap cannot be diluted by ordinary metrics.

Every confidence result ships a deterministic attribution: each dimension or
component reports `score`, `weight`, and `contribution = score * weight`, plus
`raw_score`, cap ceilings, soft penalties, and the final score. A capped result
always shows raw and final side by side (for example `raw 0.85, cap 0.79,
final 0.79`), so a report can state exactly which domain or component moved
the number. Optional or premium data being unavailable is not a decision
blocker and never lowers `score`; it only appears in the separated
optional/premium collection sections.

### Action-scoped Decision Confidence

Decision Confidence combines independent `portfolio_data`, `regime_confidence`,
`asset_evidence`, `portfolio_accounting`, and `signal_agreement` components.
Its `DecisionScope` is deterministic:

- `HOLD`/`NO_TRADE` uses current held assets and ignores zero-exposure watchlist assets;
- `INCREASE` uses the target asset and portfolio constraints;
- `REDUCE`/`EXIT` uses the affected holding and execution evidence;
- rebalance uses only materially participating assets.

Held-asset evidence is exposure-weighted with a concentration guard, so a
material low-confidence position cannot disappear inside a portfolio average.
Hard gates are scoped to `PORTFOLIO` or `ACTION:<name>`. Automatic governance
proposal scanning is not an input; important governance, tokenomics, legal, or
protocol context may be supplied through `ManualAssetContext` with
`source=MANUAL_USER_INPUT`. Unknown security or chain liveness for a
risk-increasing target remains a fail-closed action gate.

## Semantic factor anchors (phase-7 research, not policy)

Semantic factors (fundamentals, valuation for non-priced models) give 0-100
judgments. To keep them reviewable, each protocol-asset judgment must answer a
fixed evidence checklist and be calibrated against explicit anchors before it
can support a material decision. For AAVE (defi_protocol profile) the
checklist is:

1. **Protocol revenue capture**: fees, protocol net revenue, and the share
   actually accrued to the token (buyback/split), stated separately; usage
   without capture is not token-bullish evidence.
2. **Supply and dilution**: current supply, emission/safety-module schedule,
   and the horizon-matched dilution rate.
3. **Growth persistence**: is utilization/fee growth durable or one-off
   (incentive season, rate spike)? State the driver.
4. **Competition and bad debt**: share of the lending market, documented
   bad-debt/liquidation history, and collateral quality trend.
5. **Valuation denominator and period**: price/fees ratio must name its
   denominator (fees vs net revenue) and averaging window.

Proposed score anchors awaiting user confirmation (they are research
calibration, not config, until approved):

| Anchor | Score | Meaning |
|---|---|---|
| Materially weak | 20 | shrinking capture, heavy dilution, or bad-debt stress |
| Weak | 35 | one deteriorating driver, others flat |
| Neutral | 50 | mixed or insufficiently differentiated |
| Strong | 65 | growing capture, contained dilution, stable share |
| Materially strong | 80 | multiple durable drivers with independent evidence |

Repeat-judgment protocol: fix the frozen facts, run a set number of
independent judgments on the host's currently selected model, and record the
score dispersion plus whether any repeat crossed a decision boundary
(`satellite_entry_score`, soft-exit band). Reproducible stability across
repeats is required before a semantic anchor may justify a material action.
The repository neither routes models nor persists private reasoning.

Sensitivity studies stay counterfactual and clearly labeled: varying
`risk_multipliers.high_beta` (0.5 default) or the relative-strength
risk-adjustment band changes only the diagnostic output of
`build_target_allocation` allowances and `stress_diagnostic`/`evaluation`
harnesses; none of those variants is a recommendation, and changing the
default requires the phase-8C decision plus out-of-sample evidence.
