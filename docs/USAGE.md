# Usage Guide

Using the terminology for the first time? Start with the
[Glossary](GLOSSARY.md).

This guide covers only how to actually use `crypto-portfolio-manager`: how to
provide portfolio input, choose a review type, read the report, handle NAV and
external cash flows, control how data is fetched, and quickly locate data
problems.

Related documents:

- Strategy and decision principles: [Investment Strategy](../references/investment-strategy.md)
- Architecture and internals: [How It Works](HOW_IT_WORKS.md)
- Provider / TLS / development debugging: [Development and Provider Debugging](DEVELOPMENT_DEBUGGING.md)
- Source methodology: [Data Source Policy](../references/data-sources.md)
- Providers, authentication, caching, and limits: [Data Provider Policy](../references/data-providers.md)

The repository Skill's single source of truth is
`.agents/skills/crypto-portfolio-manager/SKILL.md`.

Model and reasoning settings are controlled by the Agent Skills host (Codex,
ZCode, Claude Code, etc.). The Skill uses the model and reasoning settings
selected in the user's current session and performs no repository-level model
switching.

## 1. What the Skill Does

The Skill targets conservative-balanced, spot-only cryptocurrency portfolio
research over an approximately 3–6 month active-allocation horizon. Combining
current evidence with deterministic Python calculations, it provides:

- portfolio accounting, cash-flow-adjusted NAV, drawdown, and the BTC benchmark;
- market metrics, scoring, regime, target allocation, and rebalancing;
- Position P&L, cost data coverage, risk checks, and staged execution plans;
- security and regulatory event scanning;
- analysis of user-supplied material governance, tokenomics, legal, or
  protocol context;
- recommendations such as `NO_TRADE`, `HOLD`, `WAIT`, `REDUCE`, and `EXIT`.

It never places orders automatically, never requests trading or withdrawal
permissions, and never uses futures, perpetuals, leverage, or margin. Do not
provide private keys, seed phrases, or trading credentials.

## 2. Quick Start

### 2.1 Using a Binance Screenshot

1. Set the wallet overview display currency to USD.
2. Capture as many of the asset, quantity, price / cost, current value, and
   floating P&L columns as possible.
3. Upload the screenshot and state the review type.
4. If the screenshot shows only some assets, say so explicitly; the Skill also
   checks visible-value coverage against the reported total.

Example:

```text
$crypto-portfolio-manager

This is my latest Binance position screenshot.
Read position history and the previous decision, fetch current market data,
and run a SNAPSHOT_REVIEW.
Only recommend a trade when the risk/reward is clearly sufficient; otherwise
return NO_TRADE.
Please include Position P&L, cost data coverage, confidence, current/target
weight deviations, and the rebalance rationale.
```

Binance row layout convention:

- `资产价格 / 成本价` (asset price / cost): current price on top, average cost below;
- `数量` (quantity): quantity on top, current value below;
- `--` means unknown and must not be converted to 0;
- a positive quantity with a `$0.00` current price may just be insufficient
  display precision; Python can derive an approximate price via
  `value / quantity` and record a note.

Position P&L derived values are computed by Python, never recalculated by
hand in the Agent.

### 2.2 Using the Binance Read-Only API (preferred)

Create a Binance API key with **read-only** permission (no trading, no
withdrawals; an IP whitelist is recommended) and export both values, for
example in `~/.zshenv` (sourced by every shell, so plain `python3` works):

```bash
export BINANCE_API_KEY="..."
export BINANCE_API_SECRET="..."
```

Then fetch a snapshot directly from the exchange:

```bash
python3 scripts/binance_snapshot.py           # dry run, prints the snapshot
python3 scripts/binance_snapshot.py --persist # append to snapshots.jsonl
```

What it does:

- merges spot, Simple Earn (flexible + locked, including redeeming
  amounts), and staked ETH into one snapshot; Binance mirrors Simple Earn
  subscriptions as `LD<SYM>` entries in the spot wallet — the live sapi
  amounts win and each earn position is counted exactly once (mirrors are
  reported as warnings); WBETH (the retired eth-staking service's wrapped
  asset) appears as a normal spot asset valued via the `WBETHUSDT` ticker;
- values positions with public `<ASSET>USDT` tickers (USD-pegged stables
  at 1.0 with a `USDCUSDT` peg cross-check);
- detects completed deposits/withdrawals since the previous snapshot and
  confirms the net flow automatically (`CONFIRMED_AMOUNT` or
  `CONFIRMED_NONE`, classification source `EXCHANGE_DERIVED`); in-flight
  transfers are reported but not counted;
- carries no cost basis (the API has none): position P&L cost checks
  report `INSUFFICIENT_DATA`, while NAV/drawdown/weights are unaffected.

Useful flags: `--exclude SYMBOL` (one-off dust/unknown handling),
`--min-value-usd N`, and `--flow-manual` (mark the flow `UNRESOLVED` and
resolve it later via `scripts/cash_flow_resolutions.py`). Airdropped dust
without a USDT pair can be excluded permanently via the user-local
`~/.config/crypto-portfolio-manager/data-providers.json`:

```json
{"providers": {"binance_account": {"exclude_symbols": ["EON"]}}}
```

Any fetch or valuation failure aborts without writing a partial snapshot.

### 2.3 Using Structured JSON

```json
{
  "timestamp": "2026-09-01T12:00:00Z",
  "positions": [
    {"symbol": "BTC", "value_usd": 10000},
    {"symbol": "ETH", "value_usd": 6000},
    {"symbol": "USDT", "value_usd": 3000}
  ]
}
```

Validate and normalize the snapshot only:

```bash
python3 scripts/portfolio_snapshot.py portfolio.json
```

This command fetches no live market data and produces no full investment
decision. Policy overrides must live in the snapshot's top-level `config`;
invalid values, duplicate assets, overlapping asset groups, or conflicting
`asset_type` values are rejected.

### 2.4 Dry Run

To test without writing history:

```text
Do a dry run: output analysis and recommendations only; do not save the
snapshot, decision, execution plan, or any other runtime state.
```

Validation, data fetching, risk checks, and the report still run.

## 3. Review Types

### SNAPSHOT_REVIEW

For current-position checks and routine updates. It reads historical snapshots
and the previous decision, refreshes current evidence, re-checks regime, the
risk gate, and rebalance thresholds, and outputs current Position P&L.

### FULL_REVIEW

For a complete review cycle, recommended at least once every 14 days. Beyond
the Snapshot Review, it focuses on comparing:

- NAV and drawdown;
- the 100% BTC primary benchmark and the 70/30 BTC/ETH secondary benchmark;
- previous-versus-current Position P&L;
- changes in scores, regime, target allocation, and actual allocation;
- whether the historical thesis has materially changed against current
  evidence.

### EVENT_REVIEW

For material events that could change the investment thesis.

Automated event scanning covers security and regulatory sources only;
automated governance-proposal scanning has been removed. Material governance,
tokenomics, legal, or protocol-upgrade information should be supplied
explicitly by the user and handled as `ManualAssetContext` /
`MANUAL_USER_INPUT`.

Example:

```text
Run an EVENT_REVIEW for AAVE.
Manual input: Aave is currently discussing <proposal / parameter / tokenomics event>.
Treat it as MANUAL_USER_INPUT, not as an automated scan result.
```

Missing human-supplied governance context does not by itself create a
confidence penalty.

## 4. Inputs, Position P&L, and Cash Flows

### 4.1 Screenshot Integrity and Cost Data

The Skill checks:

```text
quantity × current price   ≈ current value
quantity × average cost    ≈ cost basis
current value - cost basis ≈ floating P&L
```

Small display-rounding differences are kept as warnings; material mismatches
should not be persisted directly. If visible asset value is clearly below the
reported total, the report must state that the screenshot may be incomplete.

Position P&L is the unrealized performance of the current remaining position.
It does not represent realized returns, fee-inclusive returns, tax cost lots,
or portfolio lifetime returns.

`--` costs and unconfirmable `$0.00` costs stay unknown. Cost data coverage is
the share of current portfolio value that has usable cost data.

### 4.2 External Cash Flows

If deposits, withdrawals, or cross-account transfers occurred between two
snapshots, tell the Skill explicitly:

```text
Compared with the last round, I additionally deposited 5,000 USDT this time.
```

If the user does not disclose an external cash flow, the system defaults to:

```text
ASSUMED_NONE / 0 / NONE
```

Balance changes are treated as market performance and NAV stays `FINAL`.

If the user says a cash flow exists but the amount or direction cannot be
confirmed, the system uses:

```text
UNRESOLVED / PROVISIONAL
```

NAV, benchmark, and drawdown then stay provisional until that historical cash
flow is explicitly resolved.

## 5. How to Read the Report

Every portfolio conclusion and risk-asset Action should follow:

```text
Evidence → fact meaning → portfolio constraint → risk gate → rebalance threshold → Action
```

The report should give Evidence IDs, sources, observed times, data statuses,
current/target weights, deviations, the actually triggered threshold, the
reason for the Action, and the concrete conditions that would change the
recommendation.

The final report may only explain the finalized packet; it must never
recompute or override Python-determined scores, weights, amounts, zones,
Actions, or risk flags.

### 5.1 Data Statuses

```text
SUCCESS | FAILED | STALE | CONFLICT | NOT_APPLICABLE | SKIPPED
```

- `SUCCESS`: current data is valid;
- `FAILED`: the metric applies and was expected to be fetched, but no valid
  value was obtained;
- `STALE`: an old value exists but is past its freshness requirement;
- `CONFLICT`: sources materially disagree;
- `NOT_APPLICABLE`: the metric does not apply to that asset;
- `SKIPPED`: an optional/premium provider is unconfigured, has no
  entitlement, or no eligible provider exists.

Missing and failed data never automatically becomes a neutral value, and
scores are never raised because data disappeared.

### 5.2 Confidence

Decision Confidence is not a simple API success rate. It considers:

- policy-weighted factor coverage;
- whether critical evidence is complete;
- freshness and source conflicts;
- whether history is sufficient;
- whether NAV is `FINAL` or `PROVISIONAL`;
- whether event-scan coverage is complete.

So even when most APIs succeed, confidence can still be `LOW` if critical
evidence such as current price, recent trend, security events, or portfolio
value is missing.

### 5.3 Event Statuses

`MATERIAL_EVENT_FOUND` only means a material announcement, proposal, or event
was found in this round's scanned sources. It does not mean an exploit has
happened, a proposal has passed, or anything has been executed, and it is not
automatically bullish or bearish.

When a complete scan finds no known material event, the report may use:

```text
NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES
```

That does not mean absolute safety.

When source coverage is insufficient, the report uses:

```text
INSUFFICIENT_SOURCE_COVERAGE
```

Unreachable sources must never be interpreted as "no risk".

### 5.4 NO_TRADE / WAIT

`NO_TRADE` is a valid decision, not a system failure. The report should state
the primary reason, secondary reasons, the unmet gates/thresholds, and what
changes could lift the current restriction.

## 6. NAV, History, and CashFlowResolution

The system uses cash-flow-adjusted unitized NAV. Deposits and withdrawals
change units and must never be booked as market gains or losses.

Cash flow statuses:

- `ASSUMED_NONE`: no cash flow disclosed by the user; defaults to no external
  cash flow;
- `CONFIRMED_NONE`: the user explicitly confirmed there was no external cash
  flow;
- `CONFIRMED_AMOUNT`: the user confirmed a deposit or withdrawal amount;
- `UNRESOLVED`: a cash flow definitely exists, but amount or direction is not
  yet resolved;
- `BASELINE_RESET`: a new verified accounting baseline starts from the given
  snapshot.

Resolutions are append-only and never rewrite old snapshots.

List unresolved flows:

```bash
python3 scripts/cash_flow_resolutions.py list-unresolved
python3 scripts/cash_flow_resolutions.py validate
```

Confirm no external cash flow:

```bash
python3 scripts/cash_flow_resolutions.py resolve-none \
  --snapshot-id <id> --rationale "User confirmed no external cash flow"
```

Confirm a deposit / withdrawal:

```bash
python3 scripts/cash_flow_resolutions.py resolve-amount \
  --snapshot-id <id> --type DEPOSIT --amount 1000 \
  --rationale "User confirmed deposit"
```

Start a new baseline:

```bash
python3 scripts/cash_flow_resolutions.py baseline-reset \
  --snapshot-id <id> --rationale "Start verified baseline here"
```

As long as any `UNRESOLVED` snapshot remains in the relevant history, NAV,
benchmark, and drawdown may stay `PROVISIONAL`.

## 7. Data Fetching

Conceptually the data flow is:

```text
fresh MetricObservation
→ provider cache / content-addressed history
→ structured provider
→ bounded external resolution tasks when necessary
```

Important boundaries:

- numeric and time-series metrics defer to structured providers and must not
  be casually replaced by ordinary web search;
- chain liveness uses only structured block/slot/RPC-style sources;
- security and regulatory events use a fixed source catalog;
- web/agent external retrieval handles only explicitly returned, bounded
  unresolved qualitative/event work;
- data already resolved by a fresh observation, cache, or structured provider
  is not fetched again.

### 7.1 Fetch Mode

- `AUTO`: prefer fresh observations and cache, then refresh missing/expired
  data;
- `CACHE_ONLY`: no network requests; missing data stays visibly missing;
- `REFRESH`: refresh mutable current data; completed historical artifacts are
  still reusable.

```bash
export CRYPTO_PORTFOLIO_FETCH_MODE=CACHE_ONLY
# or
export CRYPTO_PORTFOLIO_FETCH_MODE=REFRESH
```

An explicit run-level choice takes precedence over the environment variable.

### 7.2 Chain Liveness

Chain-native assets such as BTC, ETH, BNB, and SOL use structured operational
status evidence. Token/protocol assets such as AAVE and LINK are not checked
as independent chains.

Typical results:

- `HEALTHY`: canonical progress is fresh;
- `DEGRADED`: still progressing, but freshness/finality is abnormal;
- `HALTED`: credible independent sources jointly prove severe stagnation;
- `UNKNOWN`: not enough credible structured evidence.

DNS, TLS, timeouts, 403, 429, or a provider outage only mean evidence is
unavailable — never that the chain has stopped.

### 7.3 Optional API Keys

Some optional providers are configured through environment variables, e.g.:

```bash
export SOSOVALUE_API_KEY='...'
export COINGECKO_API_KEY='...'
export GITHUB_TOKEN='...'
export RATED_API_KEY='...'
export LUNARCRUSH_API_KEY='...'
```

Keys must never be written into repository config, JSONL, caches, logs, or
reports. A configured key also does not guarantee a valid subscription or
entitlement.

For the full details see
[Data Provider Policy](../references/data-providers.md).

## 8. Execution Plans and Volume Profile

The technical execution stage is entered only after the Python rebalance layer
approves an `INCREASE`.

The technical layer may reduce the immediately deployed amount, stage orders,
or return `WAIT`; it cannot increase the portfolio-layer approved amount,
change the strategic target, bypass the risk gate, or place orders
automatically.

The execution layer uses timestamped SpotPrice, completed OHLCV,
MA20/50/100/200, 30D/90D/180D returns, ATR14, realized volatility, relative
volume, drawdown, confirmed swing zones, and Volume Profile.

Daily candles prefer at least 200 completed candles, ideally about 240.
Volume Profile prefers completed `1H` / `4H` bars, uses
`(high + low + close) / 3` as the bar price, and outputs `POC`, `VAL`, `VAH`,
HVN, and LVN.

Volume Profile is a proxy for historical volume concentration; it is not the
holder's exact cost basis.

Current execution plans generate only `PULLBACK`; `BREAKOUT` returns `WAIT`,
and `MIXED` is rejected. `planned_amount_usd` is a recommended staged
deployment amount, not a fill.

## 9. Agent and Python Responsibility Boundaries

Principle:

> Results derivable deterministically from structured data belong to Python;
> the Agent handles only bounded semantic questions that cannot be derived
> deterministically, plus the final explanation.

| Responsibility | Owner |
|---|---|
| Screenshot field extraction | Agent |
| Metric plan | Python |
| Provider fetching and normalization | Python |
| Position P&L | Python |
| Technical indicators / Volume Profile | Python |
| Bounded semantic judgment and event interpretation | Agent |
| Scoring / regime / allocation / risk / rebalance | Python |
| High-impact review | Agent, when a Python predicate triggers |
| Final report prose | Agent |

`Agent` here means the model/session the user already selected in the current
host.

The repository does not choose or switch LLM models, does not modify
reasoning/thinking levels, and implements no LLM model fallback. High-impact
review still exists but is performed by the current Agent; it does not mean
switching to a special model.

For the detailed architecture see [How It Works](HOW_IT_WORKS.md).

## 10. Runtime Data and Privacy

Runtime data defaults to:

```text
~/.local/share/crypto-portfolio-manager/
├── portfolio/snapshots.jsonl
├── decisions/decisions.jsonl
├── decisions/status-events.jsonl
├── metrics/observations.jsonl
├── metrics/collection-events.jsonl
├── market-data/sha256/<ohlcv_hash>.json
├── volume-profiles/sha256/<profile_hash>.json
└── provider-cache/
```

You can set:

```bash
export CRYPTO_PORTFOLIO_DATA_DIR=/path/to/runtime-data
```

Real balances, quantities, cost basis, account identifiers, trade history, API
keys, private keys, and seed phrases must never be committed to Git.

Read-only check of current confidence / runtime status:

```bash
python3 scripts/confidence.py \
  --data-dir "$CRYPTO_PORTFOLIO_DATA_DIR" \
  --json
```

The repository supports only the current internal runtime data contract. When
a breaking change makes old generated state incompatible, back it up and
rebuild state:

```bash
mv "$CRYPTO_PORTFOLIO_DATA_DIR" \
   "${CRYPTO_PORTFOLIO_DATA_DIR}.backup"
```

Without enough history, only a baseline can be established; no reliable
historical performance may be claimed.

## 11. Multi-host Skill Installation and Usage

If the Skill already works in your current repository, skip this section.

One-shot install from the repository root:

```bash
./install.sh --target all
```

Or install for a single host:

```bash
./install.sh --target codex
./install.sh --target claude
./install.sh --target zcode
```

The install script uses symlinks pointing at
`.agents/skills/crypto-portfolio-manager/`; it never copies runtime code,
config, or references.

### Codex

Start Codex from the repository root or a subdirectory:

```bash
git clone https://github.com/iavl/crypto-portfolio-manager.git
cd crypto-portfolio-manager
codex
```

Invoke:

```text
$crypto-portfolio-manager
```

The Codex repository Skill reads the implementation directly from the current
Git working tree.

### Claude Code

Use a user-level symlink:

```bash
cd /path/to/crypto-portfolio-manager
mkdir -p "$HOME/.claude/skills"
ln -s "$PWD/.agents/skills/crypto-portfolio-manager" \
  "$HOME/.claude/skills/crypto-portfolio-manager"
claude .
```

Invoke:

```text
/crypto-portfolio-manager
```

### ZCode

Import the Skill via `Settings → Skills → Import`, preferring `Symlink` mode;
you can also link it to:

```text
~/.zcode/skills/crypto-portfolio-manager
```

Invoke:

```text
$crypto-portfolio-manager
```

Each host separately decides Skill triggering, model, reasoning/thinking
settings, web, shell, filesystem, and network permissions. The repository
never overrides these host settings.

## 12. What to Do When Something Goes Wrong

A normal portfolio review never requires the user to understand provider
internals. If the report shows many `FAILED`, `STALE`, `SKIPPED`, or
`CONFLICT` entries, first check:

```bash
python3 scripts/providers.py --status
```

Diagnose a specific provider further:

```bash
python3 scripts/providers.py --doctor coingecko
python3 scripts/providers.py --probe coingecko --asset ETH
```

Event source problems:

```bash
python3 scripts/events.py --plan --asset BTC --asset ETH
python3 scripts/events.py --smoke --asset BTC --asset ETH
```

Never manually mark missing evidence as successful because of DNS / TLS /
timeout / HTTP 401, 403, 429 / subscription failures.

For full diagnosis, TLS CA bundles, record/replay, contract, smoke, and
development test flows see
[Development and Provider Debugging](DEVELOPMENT_DEBUGGING.md).

Core principle:

```text
Uncertainty reduces actionability; it is never erased by guesswork.
```


## Offline End-to-End Replay Evaluation

`scripts/evaluate_strategy.py` is a research tool: it replays frozen review
records (with next-period realized-return labels) through the full
`scoring → market regime → strategic targets → staged rebalancing → costs →
returns` pipeline and outputs total return, annualized return, maximum
drawdown, volatility, an approximate Sharpe ratio (risk-free rate taken as 0),
turnover, costs, average stablecoin weight, per-regime dwell counts, rebalance
and staging action counts, plus same-period BTC and 70-30 BTC-ETH buy-and-hold
benchmarks.

- Decision code only sees a `FrozenReviewView` and structurally cannot see
  next-period return labels; regression tests monitor for any leakage.
- Replay is closed-loop: the next review starts from holdings evolved from the
  previous actions plus real return paths.
- `--policy` / `--candidate` compares two strategy files; `--split train|validation|holdout`
  splits chronologically (calibration may use only train/validation).
- The code never auto-enables a candidate strategy because it performed better
  on the holdout.
- A missing realized-return label for a held asset is a hard error
  (fail-closed).

Example:

```bash
python3 scripts/evaluate_strategy.py tests/fixtures/strategy_replay_basic.json \
    --fee-bps 10 --slippage-bps 5
python3 scripts/evaluate_strategy.py reviews.json --candidate candidate_policy.json
```
