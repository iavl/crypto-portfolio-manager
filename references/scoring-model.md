# Asset Scoring Model

## Purpose and ownership

The base score is a deterministic 0–100 measure of medium-term asset
attractiveness. It is an input to portfolio construction, never a trade signal.
Python owns applicability, metric history, reliability, coverage, and all
weighted arithmetic. A semantic model may provide bounded raw factor judgments
from the supplied Facts, but it cannot change weights, reliability, or the
resulting portfolio action.

Event/security risk, derivatives positioning, BTC cycle context, portfolio
regime, and technical execution timing are separate gates or overlays. They may
reduce eligibility, confidence, target size, or deployment, but cannot change
the v2 base score.

## v2 scoring profiles

The default profile has exactly six factors:

| Factor | Weight | Ownership |
|---|---:|---|
| `trend` | 20% | market structure, momentum, moving averages, volatility and support |
| `valuation` | 20% | historical position, market cap, FDV and valuation ratios |
| `fundamentals` | 25% | adoption, fees/revenue, ecosystem, utility and economic durability |
| `onchain` | 15% | active usage, settlement, blockspace demand and network activity |
| `capital_flows` | 10% | ETF, exchange and liquidity migration flows |
| `relative_strength_btc` | 10% | asset-versus-BTC risk-adjusted performance |

`config/policy.json` stores profiles under `scoring_profiles`; their weights
must contain these six keys and sum to 1. Assets use `default` unless mapped in
`asset_scoring_profiles`.

BTC uses an explicit profile with weights:

```text
trend 22.2222%, valuation 22.2222%, fundamentals 27.7778%,
onchain 16.6667%, capital_flows 11.1111%, relative_strength_btc 0%.
```

BTC relative strength is `NOT_APPLICABLE`; no BTC-versus-BTC request or
calculation is made. A zero-weight factor is excluded from score and coverage.

## Metric ownership

Each registered `SCORING_FACTOR` metric has one owner among the six factors.
Price returns belong to `trend`; asset/BTC returns belong only to
`relative_strength_btc`; flow amounts belong to `capital_flows`; and fees,
revenue, token economics and competitive durability belong to `fundamentals`.
Active usage, settlement activity and blockspace demand belong to `onchain`.
The same economic observation may be cited as context, but it must not add to
two factor totals.

Event-risk metrics use the `EVENT_RISK` role. Positioning and BTC-cycle metrics
remain overlays and are not scoring factors.

## Availability and reliability

Every factor is explicitly `AVAILABLE`, `MISSING`, or `NOT_APPLICABLE`.

For an applicable factor, v2 uses neutral shrinkage:

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

The v2 score and coverage are:

```text
base_score = sum(profile_weight[f] * effective_factor_score[f])
coverage   = sum(profile_weight[f] * reliability[f])
```

The profile weights are fixed resolved weights, not renormalized weights.
Coverage can permit at most `HIGH` at 90%, `MEDIUM` at 70%, and investability
at 60%. Critical incompleteness forces `LOW`; a user-supplied confidence cannot
raise a coverage cap.

## Relative strength versus BTC

Raw 30D, 90D and 180D excess returns, relative drawdown, pair trend and the
normalized signal remain visible for explanation. In v2 each horizon uses:

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
clamped to 0–100 outside it. v1 replay retains its raw ±5% threshold behavior.

For non-BTC satellites, missing BTC-relative evidence is `HOLD_ONLY`, while a
materially negative comparison is ineligible for new risk.

## Capital flows

V2 flow scoring prefers normalized ratios such as ETF net flow / ETF AUM or
exchange net flow / circulating market cap. If the denominator is unavailable,
the normalized flow is missing; an absolute USD value is not substituted.

The initial dead zone is `abs(normalized_flow) <= 0.001` and saturation is
`0.01`. The dead zone scores neutral at 50; values between the dead zone and
saturation map continuously and monotonically to 0–100, then clamp. 7D/30D
evidence receives more authority than 1D noise. The state remains
`POSITIVE`, `NEUTRAL`, `NEGATIVE`, or `UNKNOWN`. v1 replay retains exact-zero
ternary behavior.

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

Satellite entry uses `satellite_entry_score=67`, existing holdings remain
`HOLD_ONLY` through `satellite_exit_score=60`, and full score strength is
reached at `satellite_full_score=85`. A new/non-held satellite below 67 receives
no new risk. A held satellite from 60 through 66 is held without adding risk;
below 60 it becomes an ineligible/reduction candidate. Score strength is
monotonic from 67 to 85 and is still multiplied by confidence, risk, event and
relative-strength gates.

Scores do not directly imply `BUY`, `SELL`, or a full deployment. `HOLD`,
`WAIT`, and `NO_TRADE` remain valid outcomes.

## v1 replay

Policy version 1 is parsed explicitly and dispatched to legacy arithmetic:
legacy `scoring_weights`, missing-factor renormalization, legacy event-risk
weighting where present, raw relative-strength thresholds, and binary flow
classification. Historical JSONL is not rewritten. New live decisions use the
version-2 profiles, typed availability, reliability-aware score, and separate
event-risk gate; the resolved policy and hash remain the replay authority.
