# How It Works

This document describes the current architecture of `crypto-portfolio-manager`,
the Python/Agent boundary, the data flow, history records, and
reproducibility. For strategy concepts and decision principles, see
[Investment Strategy](../references/investment-strategy.md).

## 1. System Overview

`.agents/skills/crypto-portfolio-manager/SKILL.md` owns workflow orchestration;
`AcquisitionManager` and `ProviderRouter` own cache-first data acquisition;
normalization, accounting, scoring, regime, allocation, risk, rebalance, and
execution are done by Python.

```text
screenshot or structured snapshot
    -> Python snapshot validation and Position P&L
    -> historical context and resolved policy
    -> Python metric collection plan
    -> fresh observation / provider cache / structured provider
    -> MetricObservation and CollectionEvent
    -> Python Facts and deterministic indicators
    -> bounded semantic judgment
    -> Python score / regime / allocation / risk / rebalance
    -> DecisionReviewPacket
    -> ReportPacket
    -> report in the invocation language and append-only history
```

The system only provides advice and execution ranges; it never connects to
order placement, withdrawals, leverage, margin, or autonomous trading.

## 2. Python and Agent Boundaries

If a result can be derived deterministically from structured data, Python owns
it. The Agent only handles screenshot field extraction, unresolved-source
retrieval, bounded semantic judgment, material-event interpretation, and
report prose.

| Responsibility | Owner | Notes |
|---|---|---|
| Screenshot field extraction | Agent | Reads visible Binance rows; computes no financial results. |
| Metric plan | Python | Selects applicable metric keys from the registry. |
| Provider fetching and normalization | Python | Owns routing, cache, units, times, freshness, provenance. |
| Position P&L | Python | Computes cost basis, unrealized P&L, return, and coverage. |
| Indicator math | Python | MA, ATR, returns, volatility, drawdown, Volume Profile. |
| Semantic judgment | Agent | Interprets fundamentals, events, conflicts, and uncertainty. |
| Scoring, regime, allocation, risk, rebalance | Python | The Agent cannot override deterministic outputs. |
| High-impact review | Agent, when a Python predicate triggers | High-impact criticism only; never changes financial math. |
| Final report | Agent | May only explain the finalized `ReportPacket`. |

`Agent` here means the current model/session selected by the user in the host.
The repository does not choose or switch models, nor reasoning settings.
Packets never carry raw web pages, full OHLCV, full history, or private
reasoning. Evidence, sources, times, and hashes are preserved in normalized
records and finalized packets.

## 3. Policy and Asset Classification

`config/policy.json` is the canonical policy, containing the universe,
benchmark, stablecoin floor, drawdown budget, scoring weights, regime limits,
rebalance thresholds, high-impact review thresholds, and execution constants.

```text
canonical policy
    -> resolved policy
    -> per-symbol core/satellite/stablecoin/cash/other classification
```

A snapshot may supply an explicit partial `config` override. The resolved
policy and policy hash are saved after resolution so historical decisions do
not depend on future checkout config changes. Asset hints must agree with the
resolved classification; overlapping groups and conflicting hints fail.

## 4. Portfolio Inputs

A standard Binance wallet screenshot is processed through:

```text
visible screenshot fields
    -> symbol/quantity/value/price/cost/P&L
    -> PortfolioSnapshot
    -> Position P&L engine
    -> normalized snapshot
```

In the row layout, the top value of the price/cost column is the current price
and the bottom value is the average cost; the top value of the quantity column
is the quantity and the bottom value is the current position value. `--` costs
and P&L stay unknown. With a positive quantity and a `$0.00` current price,
Python uses `value / quantity` as the current price and records a rounding
note.

Python validates:

```text
quantity * current price  ≈ current value
quantity * average cost   ≈ cost basis
current value - cost basis ≈ exchange floating P&L
```

Differences smaller than the larger of `$0.05` or `0.5%` of the expected
value are kept as rounding warnings; material mismatches should not be
persisted directly. When visible rows disagree with the reported total, the
result keeps visible-value coverage and notes that the screenshot may be
incomplete.

## 5. History and Accounting

Runtime state defaults to `~/.local/share/crypto-portfolio-manager/` and can
be moved with `CRYPTO_PORTFOLIO_DATA_DIR`.

```text
portfolio/snapshots.jsonl
decisions/decisions.jsonl
decisions/status-events.jsonl
metrics/observations.jsonl
metrics/collection-events.jsonl
market-data/sha256/<ohlcv_hash>.json
volume-profiles/sha256/<profile_hash>.json
provider-cache/
```

Snapshots and decisions are append-only. Status changes are written as
separate status events and never overwrite old rationale. Decision status may
only move forward from `PENDING` to `CONFIRMED` or `NOT_EXECUTED` (both
terminal); status events referencing an unknown `decision_id`, duplicating a
status, or moving backward are rejected. MetricObservation keeps successful
normalized observations; CollectionEvent keeps every collection outcome,
including failures and skips.

The repository supports only the current internal runtime contract.
Incompatible generated state after a breaking change must be regenerated
manually; user state is never auto-migrated or silently deleted.

Cash-flow-adjusted NAV uses unitized NAV. An external cash flow attached to a
snapshot is treated as occurring immediately before that snapshot's
valuation. Each snapshot carries a unique `cash_flow_resolution_status`:
`ASSUMED_NONE` when undisclosed (`0`, `NONE`, NAV stays `FINAL`), or
`CONFIRMED_NONE`, `CONFIRMED_AMOUNT`, `UNRESOLVED`, or `BASELINE_RESET`.
Explicitly disclosed but unresolved flows stay `PROVISIONAL`; confirmed
records are appended to `cash-flow-resolutions.jsonl` and old snapshots are
never rewritten.

## 6. Metric Registry and Collection Plan

`crypto_portfolio/metrics_registry.py` defines, for each metric:

- factor, expected type, unit, and direction;
- freshness window;
- asset scope and criticality;
- a role of `SCORING_FACTOR`, `EVENT_RISK`, `POSITIONING_OVERLAY`,
  `CYCLE_CONTEXT`, `EXECUTION_CONTEXT`, or `STRUCTURAL_RISK`.

`engine.metric_plan.build_metric_collection_plan()` selects metrics in Python.
It never lets a model invent keys, and never requests inapplicable asset
metrics for stablecoin/cash rows.

Collection order:

```text
fresh MetricObservation
    -> provider cache
    -> free structured provider
    -> optional API-key provider
    -> explicit WebFallbackRequest
```

Every request must produce exactly one result; missing, duplicate, or extra
results fail. Status meanings:

- `SUCCESS`: a normalized observation is available for the metric;
- `STALE`: an old observation exists, but the current refresh is unavailable
  or past freshness;
- `FAILED`: data is expected to exist for an applicable metric, but no usable
  value was obtained;
- `CONFLICT`: sources disagree;
- `NOT_APPLICABLE`: semantically inapplicable;
- `SKIPPED`: an optional or premium provider is unconfigured; provider
  entitlement, rate limit, and circuit states are never disguised as success.

Explicit dependency graphs such as `market.flow_state`, BTC-relative returns,
ETH/BTC opportunity ratios, ETH staking/flow normalization, and
OI/market-cap ratios are derived by Python. Missing derived inputs never
produce fabricated neutral values.

## 7. Provider and Event Boundaries

For concrete sources, fields, metric keys, and limits, see
[`../references/data-providers.md`](../references/data-providers.md); for
source quality and methodology, see
[`../references/data-sources.md`](../references/data-sources.md).

Providers return only normalized structured data. Chain liveness is a special
chain-specific structured provider and never falls back to ordinary web
search. EventScanner uses only the fixed allowlist source catalog; page
content may not add URLs, change scope, execute commands, or leak secrets.

Broad market valuation and protocol fundamentals are handled separately:

```text
valuation.market_cap / valuation.fdv
    -> CoinGecko
    -> Coin Metrics CapMrktEstUSD fallback for market cap only
    -> Python-derived FDV / market-cap ratio; ETH FDV is NOT_APPLICABLE

fundamentals.tvl / fees / revenue / fee-revenue multiple
    -> DeFiLlama
```

### Provider Reliability

Provider fetching keeps failure boundaries clearly visible:

```text
request
 -> cache
 -> provider router
 -> HttpClient
 -> normalization
 -> diagnostics
 -> fallback
 -> unresolved evidence
```

`--status` is an offline readiness check. Only explicit `--doctor`, `--probe`,
`--contract`, and `--smoke` commands diagnose or run live fetches; these
commands never change portfolio calculations. Transport, HTTP, plan, schema,
normalization, cache, and circuit-breaker failures are all preserved as
structured provider diagnostics. A fallback may supply a usable observation,
but it never erases the original attempts from telemetry.

The report generator never replaces a successful structured observation with
web or model inference. Historical valuation requests use only evidence from
at or before the review cutoff.

Event scanning uses a two-phase flow:

The normal operating order of an event request is: pass 1 produces
EventSourceScanRequests, the external stage returns exactly one
EventSourceScanResponse per request (returning reachable=false and a bounded
error when unreachable), pass 2 re-runs acquisition, then
require_scoring_ready() is called before scoring. Security events use the
fixed official source catalog; regulation reuses the shared MARKET source;
governance proposals are no longer scanned automatically.

BTC-relative return requests treat the asset's and BTC's
market.return_30d/90d/180d as one dependency cohort. When cached dates
disagree, both sides are refreshed/rebuilt together; Python subtracts only on
the same venue, quote, completed daily candle, and common calendar anchor,
and never accepts misaligned scalars.

```text
Python source catalog
    -> EventSourceScanRequest
    -> StructuredEventTransport
    -> candidate EventSourceScanResponse
    -> EventResolver / host classifier
    -> classified EventSourceScanResponse
    -> Python coverage/materiality/status
```

`MATERIAL_EVENT_FOUND` means a material proposal/announcement was scanned; it
is not proof of an exploit, approval, or execution. Full coverage with no
events uses `NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES`; partially
unreachable sources use `INSUFFICIENT_SOURCE_COVERAGE`, which is not proof of
safety.

Event debugging bypasses the ordinary provider router and can run standalone:

```bash
python3 scripts/events.py --plan --asset BTC --asset ETH
python3 scripts/events.py --smoke --asset BTC --asset ETH
```

The transport only discovers bounded candidates; Ethereum Foundation
security, Aave security, and ESMA/MiCA respectively use the Blog RSS, the
Aave Risk Discourse JSON, and the ESMA RSS. GitHub commits are bounded by the
`since`/`until` review window, and Discourse follows only same-origin bounded
`more_topics_url`; truncated pagination stays incomplete. With no classifier,
a failed classification, or insufficient coverage, results stay
`CLASSIFICATION_PENDING`, `FETCH_FAILED`, or `INSUFFICIENT_SOURCE_COVERAGE`,
never a default `CLEAR`. The regulatory source is fetched once under `MARKET`
and mapped to BTC/ETH via `affected_assets`.

## 8. Deterministic Decision Flow

### Facts and factors

Python builds compact Facts and metric history from MetricObservations.
Trend, flow interpretation, BTC-relative strength, and more already have
deterministic implementations; other context-dependent fundamentals,
valuation, and event-risk judgments may be performed by the model within
bounded packets.

The current policy's canonical factor namespace has eight keys; the
positively weighted factors are profile-dependent: the default non-BTC
profile has six positive-weight factors, the BTC profile has four.

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

The default non-BTC profile sets `btc_valuation` and `macro_liquidity` to
zero weight; the BTC profile uses positive weights for `trend`,
`btc_valuation`, `capital_flows`, and `macro_liquidity`. For the full
strategy explanation see
[Investment Strategy](../references/investment-strategy.md); for weights and
scoring semantics see [Scoring Model](../references/scoring-model.md).

Event/security risk uses a separate typed gate; positioning and BTC cycle are
non-scoring overlays. Missing factors keep their configured weight and shrink
toward neutral 50 via reliability; scores are never raised by vanished data.
Missing critical current price, trend, portfolio value, or an unresolved
material security event blocks high-conviction entries.

The current policy additionally groups ETH evidence into monetary economics,
staking security, L2/DA settlement, DeFi/stablecoin, and
developer/ecosystem; these are semantic groupings only — the numbers remain
owned by Python's MetricObservations and deterministic derived functions.
ETH core gating and the 70/30 BTC/ETH core-sleeve anchor are independent of
the base score; core classification is never a target guarantee.

### Regime

Python combines BTC trend, volatility, portfolio drawdown, flows, breadth,
and systemic event risk to output `NORMAL`, `DEFENSIVE`, or
`CAPITAL_PRESERVATION`. A single noisy indicator should never switch the
regime; drawdown floors and severe events can form hard lower bounds. When
the previous decision's regime is supplied, the result moves at most
`regime_transitions.max_notches_per_review` notches per review (default 1):
vote-driven two-notch jumps (including `CAPITAL_PRESERVATION` straight back
to `NORMAL`) must first pass an intermediate-notch review; severe events and
the `-0.6D`/`-0.8D` drawdown floors are never delayed by that bound.

### Allocation, risk, and rebalance

Portfolio allocation follows:

```text
regime -> score -> confidence -> risk tier -> volatility/correlation
       -> stable floor -> concentration cap -> target
```

The risk gate checks target sum, stablecoin floor, core minimum, satellite
envelope, single-asset cap, chain liveness, and overlays. Rebalancing uses
post-new-cash economic amounts and the 2pp/4pp/8pp thresholds to decide HOLD,
WATCH, or trade priority. Trade amounts must be positive; HOLD/WAIT/NO_TRADE
amounts must be zero.

## 9. Technical Execution

The technical layer is entered only after rebalancing approves an
`INCREASE`. It uses timestamped `SpotPrice` and completed `1D` OHLCV,
preferring at least 200 daily candles (ideally 240), and checks freshness,
cadence, calendar coverage, and provenance. Daily-indicator freshness is
computed against the close boundary where
`freshness_reference_at == metadata.completed_through`; older observations
missing that field must be refreshed/rebuilt.

The technical snapshot computes MA20/50/100/200, execution-specific calendar
30D/90D/180D returns, ATR14, realized volatility, relative volume, drawdown,
and confirmed swings.

Volume Profile prefers completed `1H`/`4H` bars from the same liquid spot
venue, allocates volume by `(high + low + close) / 3`, and outputs POC, VAL,
VAH, HVN, and LVN. It is a proxy for historical volume concentration, not a
holder cost basis; LVN is background only.

Only `PULLBACK` is currently generated. `BREAKOUT` returns `WAIT` and `MIXED`
is rejected. The technical layer may stage less or return WAIT, but it cannot
increase the approved USD amount or submit orders.

## 10. Packets and Reporting

The main handoff packets:

- `AssetFactorPacket`: per-asset Facts, coverage, previous assessment,
  Evidence IDs, and optional `ManualAssetContext`;
- `DecisionReviewPacket`: asset summaries, current/target weights, actions,
  risk flags, missing data, manual contexts, and overlays;
- `ReportPacket`: finalized regime, scores, weights, actions, amounts, zones,
  historical changes, risk flags, data quality, overlay results, final data
  fetch failures, and script execution failure logs.

Packets are frozen validated models. The reporting stage may explain
finalized values but cannot recompute or modify a score, weight, amount,
zone, Action, or risk flag. The report's decision basis should be expressed
in this order:

```text
Evidence -> fact meaning -> portfolio constraint -> risk gate
         -> rebalance threshold -> Action
```

## 11. Failure and Safety Modes

The system fails toward reduced actionability:

- `FAILED`, `STALE`, `CONFLICT` lower coverage/confidence;
- a chain liveness transport failure does not mean HALTED;
- `NOT_APPLICABLE` stays out of applicable coverage;
- optional/premium `SKIPPED` stays visible and out of the applicable
  denominator;
- critical missing data, material conflicts, or low confidence can block new
  positions;
- a risk gate `ERROR` blocks unsafe targets;
- technical freshness, coverage, setup quality, or CAPITAL_PRESERVATION can
  return `WAIT` and keep capital undeployed;
- `NO_TRADE` is a valid outcome, not a system error.
- Provider tracebacks and script `stderr` are kept only as sanitized,
  length-bounded debug-report context; with no direct log, the structured
  failure reason is kept and log content is never guessed.

Core principle:

```text
Uncertainty reduces actionability; it is never erased by guesswork.
```

## 12. Implementation Index

| Area | Implementation |
|---|---|
| Policy | `config/policy.json`, `crypto_portfolio/models/policy.py` |
| Metric registry | `crypto_portfolio/metrics_registry.py` |
| Collection | `crypto_portfolio/engine/metric_plan.py`, `crypto_portfolio/acquisition.py` |
| Normalization/history | `crypto_portfolio/engine/metric_normalization.py`, `crypto_portfolio/state/metrics.py` |
| Events | `crypto_portfolio/events/sources.py`, `crypto_portfolio/events/scanner.py` |
| Factors/scoring | `crypto_portfolio/engine/factors/`, `crypto_portfolio/engine/scoring.py` |
| Regime/risk/rebalance | `crypto_portfolio/engine/regime.py`, `risk.py`, `rebalance.py` |
| Accounting | `crypto_portfolio/engine/ledger.py`, `benchmark.py`, `position_pnl.py` |
| Technical/execution | `crypto_portfolio/engine/technical.py`, `volume_profile.py`, `entry.py` |
| Packets | `crypto_portfolio/models/*packet.py`, `crypto_portfolio/engine/report_packet.py` |
| Providers | `crypto_portfolio/providers/` |
| Runtime state | `crypto_portfolio/state/` |

## Confidence Hierarchy

The deterministic path is
`Data Confidence -> Regime Confidence -> Decision Confidence`. Each layer
keeps a bounded score, level, reasons, caps, and evidence IDs. Missing data
stays missing; even a normal regime label grants no permission to add risk.

Data Confidence no longer uses cross-factor signal agreement or a global
weakest asset; Decision Confidence first constructs the action scope, then
aggregates relevant asset evidence by current exposure. Watchlist-only assets
do not pollute HOLD; hard gates on security, chain liveness, cash flows, and
target assets remain fail-closed.

Run the checks:

```bash
python3 -m unittest discover -s tests -v
ruff check .
python3 -m compileall crypto_portfolio scripts
```

## Metric History and Provider Notes

Metric history is explicitly owned by
`crypto_portfolio/metric_history_requirements.py`: 30D/90D/180D requests use
about 45D/105D/195D respectively, 365D uses about 380D, and
`btc_valuation.mvrv_zscore` uses `FULL_AVAILABLE`. 240D belongs only to the
execution layer's OHLCV and never truncates monetary, tokenomics, or
valuation history.

Coin Metrics keeps per-metric successful values and independent diagnostics
in one bundle; Community is the no-key first choice and Pro is only an
optional fallback. Numeric metrics use `STRUCTURED_ONLY` and are never
converted into random web fallbacks. ETH monetary keeps only decision inputs
such as supply, issuance, and net supply growth; Etherscan serves only as an
optional current-supply cross-check.

EventScanner accepts an injected `StructuredEventTransport` using bounded
GitHub, RSS/Atom, Discourse JSON, and the allowlisted BNB Governor RPC. The
transport only discovers and deduplicates candidates; the Agent judges only
candidate materiality. URLs of the same authority complete coverage as a
source group; independent security domains are still kept separate.
