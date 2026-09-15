[Usage Guide](USAGE.md) · [How It Works](HOW_IT_WORKS.md) · [README](../README.md)

# Glossary (Beginner Edition)

This document explains the most common terms and abbreviations in the current
crypto-portfolio-manager documentation, reports, and code. Each entry has four
parts: the term, a plain-language explanation, its specific meaning in this
project, and how it can affect portfolio decisions.

This document describes the current internal contract; old generated state
after a breaking change is unsupported and must be regenerated manually.
Code identifiers are kept verbatim so they can be searched in reports, JSON,
configuration, and source. `--`, missing data, and failure statuses mean
"unknown or unavailable" — never "fill it with zero or treat it as safe".

## 1. Review Types, Conclusions, and Actions

| Term | Plain meaning | In this project | Decision impact |
|---|---|---|---|
| **SNAPSHOT_REVIEW** | Snapshot review | Reads current holdings, history, and the latest evidence for a current-state check. | Used for routine updates; still checks risk gates and rebalance thresholds. |
| **FULL_REVIEW** | Full review | Additionally compares NAV, drawdown, the BTC benchmark, scores, target allocation, and the previous round. | Recommended at least every 14 days; suitable for judging portfolio change. |
| **EVENT_REVIEW** | Event review | Focused on security, protocol, governance, regulatory, or other events that could change the investment thesis. | Insufficient event-source coverage or an unresolved event can block new risk. |
| **Action** | Action conclusion | The action Python derives from evidence, portfolio constraints, risk gates, and rebalance thresholds. | Cannot be replaced by a single score. |
| **INCREASE** | Increase | Increase the portfolio's exposure to an asset. | Must pass risk, evidence, stable-funding, and concentration constraints, with a positive amount. |
| **REDUCE** | Reduce | Lower an asset while possibly keeping a position. | Can result from target deviation, worsening risk, or a weakening thesis. |
| **EXIT** | Exit | Take an asset's target weight to zero. | Usually requires a broken thesis or a clear risk/eligibility problem; not done to make weights exact. |
| **HOLD** | Hold | Temporarily keep the current position; produces no trade amount. | Means there is currently not enough reason to change the position. |
| **WAIT** | Wait | Hold off executing until better data, price structure, or risk conditions appear. | Not a failure; appears when technical data is insufficient, chasing breakouts is discouraged, or structure is unclear. |
| **NO_TRADE** | No trade | No qualified trade action this round. | A valid and frequently sensible outcome, not a system error. |
| **HOLD_ONLY** | Hold only (legacy name) | Former name of the satellite eligibility state, replaced by `HOLD_OR_REDUCE`. | Semantic change: no longer "stay untouched"; trimming toward the strategic target is allowed. |
| **HOLD_OR_REDUCE** | Hold or reduce | Add no new risk; existing overweight may be trimmed stepwise toward the strategic target along the target curve. | Common in the sub-threshold score band, moderately weak BTC-relative strength (30–50), or when critical evidence is missing. |
| **ELIGIBLE_INCREASE** | Eligible to increase | Evidence is complete and the score reaches the entry line, so exposure may be added within strategic/deployment targets. | Together with SOFT_EXIT and INELIGIBLE it forms the four satellite eligibility states; eligibility constrains deployment only, not the target-curve shape. |
| **WATCH** | Watch | Adjusts priority; not an independent trade action. | Usually means the deviation is worth watching but execution conditions are not met. |
| **thesis_broken** | Broken thesis | The premises that originally supported holding the asset no longer hold. | Do not exit solely for temporary missing data; once confirmed, it can trigger a reduce or exit. |
| **dry run** | Dry run | Runs validation, analysis, and the report without appending snapshots, decisions, or other history. | Good for checking the flow first; does not change runtime state. |

## 2. Portfolio and Risk

| Term | Plain meaning | In this project | Decision impact |
|---|---|---|---|
| **portfolio** | Portfolio | A set of assets, stablecoins, and cash with their quantities/values. | Risk and allocation are computed at portfolio level, not optimized per coin. |
| **position** | Position | The held quantity, value, and related info for one asset in the portfolio. | Position P&L describes only that remaining position, not whole-portfolio performance. |
| **asset** | Asset | An identifiable symbol such as BTC, ETH, SOL, and its unified classification. | Classification comes from the canonical policy; conflicting asset hints are rejected. |
| **spot-only** | Spot only | Only directly held spot assets are studied. | No leverage, margin, futures, perpetuals, or liquidation logic. |
| **core** | Core asset | The default long-term core sleeve, currently BTC and ETH. | Core classification is not an unconditional buy or a target-allocation guarantee. |
| **satellite** | Satellite asset | Selected large-cap assets outside the core with limited size, currently SOL, BNB, LINK, AAVE. | Requires a higher evidence bar and is bounded by satellite caps, confidence, and risk tier. |
| **stablecoin / cash sleeve** | Stablecoin/cash sleeve | Stablecoins and cash are treated as one low-risk money basket. | Must keep at least the global minimum and the higher share the current regime requires. |
| **allocation** | Allocation | Deciding what fraction of the portfolio each asset should hold. | A deterministic portfolio-level result, not decided by any single score. |
| **weight** | Weight | An asset's value divided by total portfolio value. | The gap between current and target weight decides whether rebalancing is worthwhile. |
| **target weight** | Target weight | The desired fraction derived jointly from risk, score, confidence, regime, and caps. | Targets must sum correctly and respect the stable floor and concentration caps. |
| **rebalance** | Rebalance | Moving the current allocation toward the target allocation. | Deviations under 2 percentage points are usually HOLD, 2–4 points WATCH, and only beyond thresholds are trades considered. |
| **benchmark** | Benchmark | The reference used to compare portfolio performance. | The primary benchmark is 100% BTC; the secondary is cash-flow-aware 70/30 BTC/ETH buy-and-hold. |
| **risk budget** | Risk budget | The maximum drawdown the portfolio accepts, about 15% by default. | It constrains overall risk; each asset does not get its own budget. |
| **drawdown** | Drawdown | The decline from a historical peak to current value. | Worsening drawdown pushes the regime more defensive and can never produce a more aggressive result. |
| **volatility** | Volatility | A statistical measure of how much prices move. | Higher volatility usually means more conservative positions, targets, or immediate deployment. |
| **concentration** | Concentration | How dependent the portfolio is on one asset or asset group. | Single-asset and satellite caps stop high-scoring assets from growing without limit. |
| **risk tier** | Risk tier | A layering of asset risk and portfolio fit. | Higher tiers face tighter targets and new-position limits. |
| **risk regime** | Risk regime | The risk environment the portfolio and market are currently in. | NORMAL, DEFENSIVE, and CAPITAL_PRESERVATION correspond to normal, defensive, and capital preservation. |
| **NORMAL** | Normal regime | A regime where no higher-risk protection has triggered. | Normal caps apply, but all risk gates must still pass. |
| **DEFENSIVE** | Defensive regime | Trend, volatility, drawdown, liquidity, or events have raised risk. | Raises the stablecoin target and tightens satellite caps. |
| **CAPITAL_PRESERVATION** | Capital preservation regime | Severe drawdown, a major event, or systemic risk is present. | Focus on protecting capital: raise the stable target and block unnecessary new risk. |
| **stable floor** | Stable floor | The minimum stablecoin/cash weight: the higher of the global floor and the current regime target. | No target allocation may violate it. |
| **opportunity cost** | Opportunity cost | The alternative given up by choosing an asset, especially BTC's risk/reward opportunity. | Altcoins must prove their risk/reward case relative to BTC. |

## 3. Accounting and Performance

| Term | Plain meaning | In this project | Decision impact |
|---|---|---|---|
| **NAV** | Net asset value | The total value of all portfolio assets at a point in time. | Used to compare the portfolio's real change over time. |
| **unitized NAV** | Unitized NAV | Converts external deposits/withdrawals into unit changes so flows are separated from investment performance. | Avoids mistaking "newly deposited money" for investment gains. |
| **external cash flow** | External cash flow | Money deposited into or withdrawn from the portfolio, as opposed to price changes. | Must be explicitly marked DEPOSIT, WITHDRAWAL, or NONE. |
| **cash_flow_resolution_status** | Cash flow resolution status | `ASSUMED_NONE` (`0`, `NONE`) when undisclosed; may also be `CONFIRMED_NONE`, `CONFIRMED_AMOUNT`, `UNRESOLVED`, or `BASELINE_RESET`. | Undisclosed flows are booked as market performance and stay `FINAL`; only explicitly unresolved flows are `PROVISIONAL`. |
| **cash-flow timing** | Cash-flow timing | A flow attached to a snapshot is treated as occurring immediately before that snapshot's valuation. | Affects NAV, benchmark, and drawdown math; timing must never be ignored. |
| **cost basis** | Cost basis | The known cost corresponding to buying the remaining position. | Stays unknown when cost is unknown; it is never fabricated as zero. |
| **P&L** | Profit and loss | The difference or ratio between current value and cost basis. | Position P&L must be distinguished from portfolio-level NAV Return. |
| **realized P&L** | Realized P&L | P&L from portions already sold. | The current feature makes no claim of complete realized P&L, tax lots, or fee analysis. |
| **unrealized P&L** | Unrealized P&L | Paper P&L of the still-held position at current prices. | Describes only the remaining position; not a lifetime return. |
| **return** | Return | The price/NAV change ratio of an asset, portfolio, or benchmark over a matched period. | Portfolio returns must use a cash-flow-adjusted method, never raw balance changes. |
| **coverage** | Coverage | Depending on context, cost data coverage or evidence coverage. | Low coverage lowers confidence; missing critical data can directly block high-conviction increases. |
| **PROVISIONAL** | Provisional | An unclassified material balance change means NAV performance cannot be confirmed as real investment performance. | Treated as tentative only; do not call it a reliable return or drawdown. |
| **performance_finality** | Performance finality | `FINAL`, `PROVISIONAL`, or `UNAVAILABLE` — whether NAV/benchmark has been confirmed against cash-flow boundaries. | The report only formats this field; it must never upgrade a tentative result to final. |
| **-- / unknown** | Unknown | The screenshot or source lacked enough information, e.g. cost or P&L shown as `--`. | Stays unknown; unknown is not zero and not default-safe. |

## 4. Market Data and Metrics

| Term | Plain meaning | In this project | Decision impact |
|---|---|---|---|
| **OHLCV** | Open, High, Low, Close, Volume | The candle's opening, high, low, closing prices, and traded volume. | The base history for trend, ATR, volatility, and Volume Profile. |
| **spot price / SpotPrice** | Spot price | An asset's current price on the spot market; SpotPrice must also carry observation time and source. | Execution plans must not use prices without timestamps. |
| **completed candle** | Completed candle | A candle whose time period has fully ended. | Indicators use only completed candles, keeping unfinished data out of results. |
| **freshness_reference_at** | Freshness reference time | The latest completed candle close boundary used by daily OHLCV indicators. | Must equal `metadata.completed_through`; older data missing the field cannot count as a fresh hit. |
| **completed_through** | Completed through | The latest completed candle close boundary recorded in provider metadata. | Used for freshness and cache reuse; the candle open time is never substituted. |
| **no-lookahead** | No lookahead | Computations use only data available at decision time. | Prevents replay results from seeing prices or events that did not exist yet. |
| **MA / MA20 / MA50 / MA100 / MA200** | Moving average | The average price over the specified window, used to read medium/long-term trend. | One piece of trend evidence only; not a standalone buy/sell signal. |
| **ATR / ATR14** | Average true range | Reflects the price's typical range; ATR14 currently uses a simple average of 14 full true ranges. | Used to set volatility-aware zones and judge over-extension. |
| **realized volatility** | Realized volatility | Historical volatility computed from past price changes. | High volatility usually reduces new allocation or immediate execution size. |
| **relative volume** | Relative volume | Current volume as a ratio of volume over a reference period. | Auxiliary trend/structure evidence only; never triggers a trade alone. |
| **Volume Profile** | Volume profile | Distributes historical volume across price ranges to show where volume concentrated. | A proxy for historical volume concentration, not the exact cost basis of position holders. |
| **POC** | Point of Control | The price range with the most volume in the Volume Profile. | A structural reference; cannot by itself create portfolio risk or a trade. |
| **VAL / VAH** | Value Area Low / High | The bounds of the price range covering the configured volume share. | Help describe structural zones; not guaranteed support or resistance. |
| **HVN / LVN** | High/Low Volume Node | Price nodes where volume concentrated (HVN) or was sparse (LVN). | HVN gives zone context; LVN is background only and cannot decide an action alone. |
| **pullback** | Pullback | A temporary retracement and retest within an up- or downtrend structure. | Current execution plans generate only PULLBACK setups. |
| **breakout** | Breakout | Price leaving its prior range or a key structure. | BREAKOUT currently returns WAIT to avoid mechanical chasing. |
| **market cap** | Market capitalization | Current price times the estimated circulating supply. | Mainly for valuation and relative-size comparison; supplied by structured sources such as CoinGecko. |
| **FDV** | Fully diluted valuation | Valuation assuming the full supply is priced at the current price. | The ratio to market cap is used only when economically applicable and inputs are fresh. |
| **FDV / market cap ratio** | FDV/market-cap ratio | FDV divided by market cap, derived by Python from same-asset inputs. | Shows potential dilution differences. |
| **TVL** | Total Value Locked | The value of assets locked in a protocol or application. | A protocol fundamentals metric; neither the token's market cap nor a safety measure. |
| **AUM** | Assets Under Management | Assets managed by an ETF or similar product. | ETF net inflows are usually normalized by AUM; missing AUM is never filled with zero. |
| **capital flow / net flow** | Capital flow / net flow | Inflows minus outflows over a period. | Background evidence; when missing it stays unavailable, never neutral or zero. |
| **ETF / ETF flow** | Exchange-traded fund / ETF flow | Here mainly US BTC/ETH spot products and their daily/weekly/monthly net inflows. | Part of the capital-flows factor; a single day of flow is not by itself a trade trigger. |
| **on-chain** | On-chain data | Data taken directly from blockchain activity, e.g. addresses, transfers, transactions, and block-space fees. | Requires asset applicability, source, and time validation; on-chain metrics are not a universal signal. |
| **staking / APY** | Staking / annual percentage yield | Staking rewards from participating in network validation, expressed annually. | High APY alone never proves an asset is worth holding; inflation, lockups, slashing, custody, and liquidity risks all matter. |
| **L2 / DA** | Layer 2 / Data availability | L2 networks and DA; here whether Ethereum actually gains settlement or DA value capture. | L2 activity is not automatically bullish ETH; the link to Ethereum settlement or DA must be demonstrated. |
| **BTC dominance** | BTC dominance | BTC market cap as a share of total crypto market cap. | Market-environment and BTC-relative-strength context, not an automatic trade signal. |
| **breadth** | Market breadth | In the current implementation, the share of the deterministic top-20 market-cap assets with positive 30D returns. | Describes market participation; weak breadth makes overall allocation more conservative. |
| **BTC-relative strength** | BTC-relative strength | The return gap between an asset and BTC on the same venue, quote, completed dates, and common time anchor. | Altcoins missing this critical comparison are limited to HOLD_OR_REDUCE and add no new risk. |
| **funding rate** | Funding rate | The periodic rate paid between perpetual longs and shorts. | Reads crowding and leverage context; not a spot buy/sell signal. |
| **open interest** | Open interest | The size of derivative contracts not yet closed. | Growth or decline is positioning context; not directional by itself. |
| **long/short ratio** | Long/short ratio | The ratio of long to short accounts or positions. | Different exchanges and methodologies must not be mixed; conflicts lower confidence. |
| **futures basis** | Futures basis | The gap between futures mark price and index price, currently annualizable by maturity. | Reflects derivatives pricing and crowding; a background constraint only. |
| **MVRV / SOPR / LTH net-position change** | On-chain cycle metrics | Compare market value to realized value, watch spent-output profit, and long-term-holder net position change. | BTC cycle/on-chain context; missing data lowers cycle confidence but never triggers a trade alone. |

## 5. Evidence, Scoring, and System

| Term | Plain meaning | In this project | Decision impact |
|---|---|---|---|
| **Evidence** | Evidence record | A structured record with ID, asset, factor, source, observed/fetched times, freshness, confidence, and summary/value. | Decisions must trace to evidence; hidden reasoning never substitutes for it. |
| **source** | Source | The official institution, exchange, protocol, or analytics platform providing raw facts or announcements. | Source tier and methodology determine evidence quality. |
| **provenance** | Provenance | Records where data came from, when it was fetched, and what normalization/derivation it went through. | Unclear origin, unknown times, or methodology conflicts lower confidence. |
| **Fact** | Fact | Compact verifiable facts extracted by Python from normalized observations. | The model may explain facts but never rewrite Python facts. |
| **Factor** | Scoring factor | A dimension that groups related facts for scoring. | The current six base factors are exactly trend, valuation, fundamentals, onchain, capital_flows, and relative_strength_btc. |
| **score** | Score | A factor or asset score, usually 0–100. | An allocation input, not a direct `score > X means buy` rule. |
| **confidence** | Confidence | A combined judgment of evidence completeness, quality, and consistency. | Low confidence cannot be quietly raised by user input, nor produce a high-conviction increase. |
| **reliability** | Reliability | How far a piece of data or factor can be trusted in the current decision. | Missing factors keep their configured weight but shrink their score toward neutral 50. |
| **evidence coverage** | Evidence coverage | How much of the applicable policy-weighted evidence is available. | NOT_APPLICABLE, optional SKIPPED, and overlays stay out of the applicable scoring denominator; required failures lower coverage. |
| **event-risk gate** | Event-risk gate | Security, regulatory, and material-event limits independent of the six base factors. | SEVERE/CRITICAL can block new risk but is never disguised as a seventh base score. |
| **ManualAssetContext** | Manual asset context | User-supplied governance, tokenomics, legal, or protocol facts with explicit impact, severity, scope, and `MANUAL_USER_INPUT` provenance. | Can add explanation or feed a review within its explicit scope; its absence never lowers confidence. |
| **overlay** | Overlay | Extra context such as positioning, BTC cycle, execution context, or structural risk. | May cap immediate deployment or lower confidence; not a new weighted base factor. |
| **chain liveness** | Chain liveness | Whether a chain still progresses normally, judged from structured block/slot/RPC data. | Applies only to BTC, ETH, SOL, BNB; a chain data transport failure does not mean the chain is HALTED. |
| **HEALTHY / DEGRADED / HALTED / UNKNOWN** | Chain status values | The chain-liveness enum; HALTED requires configured independent sources to jointly prove severe stagnation. | DEGRADED limits deployment; HALTED blocks new exposure; UNKNOWN is never treated as healthy. |
| **SUCCESS / FAILED / STALE / CONFLICT** | Collection statuses | The result status of each metric collection. | FAILED, STALE, and CONFLICT stay visible and affect confidence; never silently converted to neutral values. |
| **NOT_APPLICABLE** | Not applicable | The asset/metric pair is semantically meaningless, e.g. TVL for BTC. | Stays out of applicable coverage; must not be misreported as a failure nor used to inflate coverage. |
| **SKIPPED** | Skipped | Optional or premium evidence has no eligible configured provider. | Only for optional scenarios that allow skipping; not the same as NOT_APPLICABLE or a required-data failure. |
| **freshness** | Freshness | Whether an observation is still inside the metric's allowed time window. | Old data is marked STALE, lowering confidence or blocking high-conviction action. |
| **as_of** | As-of time | The latest time the current analysis may use. | Historical replay must not use data beyond the cutoff. |
| **observed_at** | Observed time | The market, block, or scan time the data fact actually corresponds to. | Determines which point in time the evidence represents; the fetch time is never a substitute. |
| **fetched_at** | Fetched time | When the client obtained the data. | Freshly fetched old history does not become current data. |
| **provider** | Provider | The component that fetches and normalizes data from an external API or structured source. | A fetch path, not the source's authority itself. |
| **router** | Provider router | Selects providers, caches, and fallback paths by fixed priority. | Routing failures keep their attempt details; successful values are never fabricated. |
| **normalized data** | Normalized data | Structured data with unified units, times, assets, sources, and statuses. | The engine consumes only normalized data and never depends on exchange or web page formats. |
| **MetricObservation** | Metric observation | One validated metric history record with value, source, time, and freshness. | Available for next-round comparison; history is append-only. |
| **CollectionEvent** | Collection event | The record of every metric collection attempt, including failures, staleness, conflicts, N/A, and skips. | Lets the report explain why data was unavailable instead of hiding gaps. |
| **cache** | Cache | Local storage of fetched provider responses or immutable historical series. | A fetch accelerator, not a decision authority; expired and mismatched caches cannot masquerade as current evidence. |
| **AUTO / CACHE_ONLY / REFRESH** | Fetch modes | AUTO reuses fresh data first, CACHE_ONLY never touches the network, REFRESH refreshes mutable current data. | CACHE_ONLY keeps data missing when absent; it must never quietly go online or fill in values. |
| **snapshot** | Snapshot | A validated holdings/valuation input at one moment. | The time anchor for history, NAV, drawdown, and reviews. |
| **decision** | Decision | The asset assessments, targets, actions, and rationale produced from a snapshot and policy. | Historical decisions are appended, never overwriting old rationale. |
| **append-only** | Append-only | New records go to the end; existing history is never modified. | Preserves auditability; status changes become new events. |
| **JSONL** | JSON Lines | A persistence format with one JSON record per line. | Suits appending and per-record validation; invalid records never enter history. |
| **schema** | Schema | A machine-readable definition of JSON structure, field types, enums, and constraints. | Models, schemas, fixtures, and documentation must stay consistent. |
| **canonical policy** | Canonical policy | The single machine-readable policy source in config/policy.json. | Stable floors, scoring weights, caps, and thresholds must never be quietly duplicated elsewhere. |
| **resolved policy** | Resolved policy | The exact policy after resolving defaults, allowed input overrides, and classification rules. | Decisions persist it for future replay, independent of later config file changes. |
| **policy hash** | Policy hash | The deterministic SHA-256 identity of the resolved policy. | Confirms a historical decision matches the policy actually in use at the time. |
| **DecisionReviewPacket** | Decision review packet | The packet of asset summaries, weights, actions, risks, missing data, and overlays used by the reporting stage. | A validated handoff boundary; the report cannot recompute or override financial results. |
| **ReportPacket** | Report packet | The finalized regime, scores, weights, actions, zones, historical changes, data quality, and failure logs. | The report explains its final values only; it never changes an Action or risk flag itself. |
| **Python-first / deterministic** | Python-first / deterministic | Math, validation, and risk conclusions derivable from structured data belong to Python. | The Agent makes only bounded semantic judgments; it never recomputes or overrides accounting, scoring, allocation, or risk results. |
| **Agent** | Current host session | The model/session the user selected in the Agent Skills host. | The repository does not choose or switch models; deterministic financial results remain Python's. |
| **API / API key** | API / API key | An API is a programmatic interface to external data; an API key is optional authentication. | Keys are supplied only via environment variables, never written into config, caches, JSONL, logs, or reports. |
| **TLS / DNS** | Transport security / name resolution | TLS verifies HTTPS certificates and hostnames; DNS resolves names to network addresses. | TLS, DNS, timeout, or HTTP errors mean data is unavailable, not that a chain stopped or a conclusion is zero. |
| **RPC** | Remote procedure call | The interface for reading blockchain node data, e.g. latest block, slot, or finalized state. | An observation transport, not a security, governance, or regulatory authority. |
| **EventScanner / source catalog** | Event scanner / source catalog | Checks security, protocol, governance, and regulatory sources against a fixed allowlist. | Page content may not add URLs, widen scope, or change scan instructions; insufficient coverage must be reported explicitly. |

## 6. A Decision Chain

When reading a report, follow this order to understand a conclusion:

    Evidence
      -> Fact
      -> Factor score / confidence
      -> market regime + event-risk gate + overlay
      -> target weight
      -> current vs target deviation
      -> rebalance threshold
      -> Action

A high score does not guarantee INCREASE: the stable floor, concentration,
risk regime, event risk, BTC-relative opportunity cost, data coverage, and
technical execution conditions can all turn the final result into HOLD, WAIT,
HOLD_OR_REDUCE, or NO_TRADE.

`Data Confidence` measures only evidence coverage, freshness, source quality,
and independent-source redundancy for the same fact; cross-factor direction
disagreement enters only `Decision Confidence`'s `signal_agreement`.
`Regime Confidence` measures the evidence behind the market-regime call;
`Decision Confidence` measures the evidence behind the current action scope.
All three are Python-derived values and can never be recomputed or raised by
an LLM. `PROVISIONAL` and `BLOCKED` are explicit states — not zero scores and
not safety.

## Further Reading

- [Usage Guide](USAGE.md): input formats, review types, report reading, and troubleshooting.
- [How It Works](HOW_IT_WORKS.md): architecture, data flow, and the Python/model boundary.
- [Investment Policy](../references/investment-policy.md): asset classification, stable cash, and holding rules.
- [Decision and Rebalance Rules](../references/decision-rules.md): thresholds, actions, and event semantics.
- [Scoring Model](../references/scoring-model.md): factors, coverage, scoring, and overlays.
- [Risk Model](../references/risk-model.md): regimes, drawdown, concentration, and risk gates.
- [Data Source Policy](../references/data-sources.md): source tiers, methodology, and missing-data meaning.
- [Data Provider Policy](../references/data-providers.md): routing, caching, authentication, and provider boundaries.
