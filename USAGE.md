# crypto-portfolio-manager Usage Guide

This guide covers the normal user workflow after the Skill is installed. For
installation, updates, and uninstalling, see the [README](README.md).

## What the Skill Does

The Skill reviews a spot-only crypto portfolio over an approximately 6–12
month horizon. It combines current evidence with deterministic portfolio
accounting, risk, allocation, benchmark, and rebalance calculations.

The default posture is conservative-balanced:

- BTC and ETH are the default core assets; selected large-cap assets may be
  satellites.
- Stablecoins and cash stay above the configured minimum.
- Portfolio drawdown risk is managed as a whole, with a default budget of
  approximately 20%.
- `NO_TRADE` is valid; new capital does not need to be fully deployed.
- The Skill proposes actions only and never places trades.

## Quick Start

1. Install and reload the Skill.
2. Upload a portfolio screenshot or provide structured holdings.
3. Invoke `$crypto-portfolio-manager`.
4. Request the review type you need.

Example first review:

```text
$crypto-portfolio-manager

这是我当前的交易所仓位截图。

请读取已有的 portfolio history 和 previous decisions，
获取当前市场数据，并做一次 SNAPSHOT_REVIEW。

如果没有足够好的风险收益机会，可以 NO_TRADE。
```

The review uses portfolio input, available history, current evidence, scoring,
market regime, allocation, risk checks, and rebalance analysis before producing
a validated Chinese report.

## Provide Portfolio Input

### Screenshot

Screenshots are the primary end-user input. Provide an exchange portfolio
view, asset balances, or a total portfolio value when available. The Agent
extracts holdings and validates them before analysis.

If a screenshot value is unclear, provide the value separately or expect the
Skill to ask for clarification. It must not silently guess material amounts.

### Structured JSON

You can provide a timestamped snapshot instead:

```json
{
  "timestamp": "2026-09-01T12:00:00Z",
  "positions": [
    {"symbol": "BTC", "value_usd": 10000},
    {"symbol": "ETH", "value_usd": 6000},
    {"symbol": "SOL", "value_usd": 2000},
    {"symbol": "USDT", "value_usd": 3000}
  ]
}
```

From the repository checkout, validate and normalize it with:

```bash
python3 scripts/portfolio_snapshot.py portfolio.json
```

The command validates and normalizes the snapshot only. It does not fetch live
market data or produce a complete investment decision.

## Staged execution data

After the portfolio/rebalance engine approves an `INCREASE` amount, the Skill
requests a timestamped `SpotPrice` and normalized completed `1D` OHLCV. Provide
at least 120 completed daily candles and preferably 365; include volume when
the source is consistent and reliable. `TechnicalSnapshot` computes
MA20/50/100/200, calendar-based 30D/90D/180D returns, ATR14, realized
volatility, relative volume, history drawdown, and confirmed swings before
selecting ATR-aware support zones.

`SpotPrice.observed_at` defines the decision time when `as_of` is omitted.
Historical replay rejects a bare float spot price and any spot observed after
`as_of`. `fetched_at` records retrieval time and may be later than a historical
`as_of`; market freshness is determined from the latest observed completed
candle, not from retrieval time. Duplicate UTC dates and large daily coverage
gaps produce low confidence or `WAIT`.

The technical execution layer is downstream of allocation: it cannot increase
the approved USD amount, can leave funds unallocated, and may return `WAIT`.
An `ExecutionPlan` is persisted only when its `INCREASE` amount matches one
approved `RebalanceAction`; its `execution_technical` evidence retains the
spot observation, technical summary, and OHLCV hash. Estimated quantities are
approximate (`amount_usd / reference_price`) and invalidation is a review
trigger, not an automatic stop order. `planned_amount_usd` is staged capacity,
not dollars already filled.

v1 generates pullback plans only. `BREAKOUT` returns `WAIT` until a real
breakout/retest planner exists, and `MIXED` is rejected.

Exact normalized OHLCV can be cached and replayed without network access from
`~/.local/share/crypto-portfolio-manager/market-data/sha256/<ohlcv_hash>.json`
(or the configured `CRYPTO_PORTFOLIO_DATA_DIR`).

Snapshot-level policy overrides may be supplied in the top-level `config`
object. Omit `config`, or an individual field, to use the canonical policy:

```json
{
  "timestamp": "2026-09-01T12:00:00Z",
  "positions": [
    {"symbol": "BTC", "value_usd": 8000},
    {"symbol": "USDC", "value_usd": 2000}
  ],
  "config": {"min_stablecoin_weight": 0.10}
}
```

Invalid values, overlapping asset groups, duplicate symbols, and conflicting
asset-type hints are rejected rather than guessed.

## Review Types

### SNAPSHOT_REVIEW

Use for a current holdings check or a routine update:

```text
$crypto-portfolio-manager

这是我的最新仓位截图。

做一次 SNAPSHOT_REVIEW。
读取历史仓位和上一轮决策，并结合当前市场情况判断是否需要调仓。
只有当风险收益比足够明显时才建议交易。
```

### New capital allocation

Use when adding cash. The system uses post-new-cash economic dollars and may
leave some or all of the cash in the stable sleeve:

```text
$crypto-portfolio-manager

我新增了 5000 USDT 可投资资金。

结合我的现有仓位、历史决策和当前市场环境，
判断应该投入多少，以及分别配置到哪些资产。

不要求把 5000 USDT 全部投入。
允许全部保留为 stablecoin。
```

### FULL_REVIEW

Use for a complete comparison with the prior portfolio decision:

```text
$crypto-portfolio-manager

做一次 FULL_REVIEW。

对比上一次完整复盘：
- Portfolio NAV
- Drawdown
- BTC benchmark
- previous target weights
- previous asset scores
- current asset scores
- market regime

然后给出新的 target allocation 和 rebalance recommendation。
```

Full reviews are designed around an approximately 14-day cadence. A material
event can trigger one sooner.

### EVENT_REVIEW

Use when a material security, fundamental, regulatory, governance, or market
event may change an investment thesis:

```text
$crypto-portfolio-manager

SOL 今天发生了重大安全 / 基本面事件。

做一次 EVENT_REVIEW，
检查这个事件是否破坏原投资 thesis，
并判断当前组合是否需要立即调整。
```

### Dry run / no persistence

Request a dry run when you want analysis without changing local history:

```text
$crypto-portfolio-manager

对这个组合做一次 dry run。

分析并输出建议，但不要保存 portfolio snapshot、
decision 或其他历史状态。
```

Dry-run and no-persistence requests still use validation and risk controls, but
do not append snapshots, decisions, or status events.

## How Current Data Is Used

The repository has provider interfaces in
`crypto_portfolio/providers/base.py`, but no live provider implementations.
The running Codex environment uses its available web/data capabilities for
current public information.

Depending on the review, the Skill may need:

- spot prices, daily history, volume, and volatility;
- BTC trend, dominance, drawdown, breadth, and relative strength;
- ETF/institutional flows and stablecoin liquidity;
- fundamentals, usage, fees, TVL, supply, emissions, and token unlocks;
- exploits, outages, governance, regulatory, and other material events.

The repository does not directly connect to Binance, OKX, CoinGecko,
DefiLlama, Token Terminal, Glassnode, or CryptoQuant. See
`references/data-sources.md` for source hierarchy, freshness, and
missing-data rules.

If critical information is unavailable, the Skill lowers confidence or
declines to make a strong actionable recommendation. Missing data is not
positive evidence.

## API Keys

No third-party API key is required by this repository because it does not yet
ship live external provider adapters. Current public information is obtained
through the running Codex environment's available web/data capabilities.

If a future provider needs credentials, supply them through environment
variables or a secret store. Never put keys in policy files, Skill files,
README examples, source files, or Git-tracked `.env` files. Provider-specific
variable names will be documented only when that provider exists.

## Local History

The Skill keeps runtime state outside the Git checkout:

```text
default:   ~/.local/share/crypto-portfolio-manager/
override:  $CRYPTO_PORTFOLIO_DATA_DIR
```

Current state files are:

```text
~/.local/share/crypto-portfolio-manager/
├── portfolio/snapshots.jsonl
├── decisions/
│   ├── decisions.jsonl
│   └── status-events.jsonl
└── market-data/sha256/<ohlcv_hash>.json
```

History supports comparison with previous holdings, cash-flow-aware NAV and
drawdown, previous targets/actions/theses, and full-review timing. Records are
append-only; previous rationales are not rewritten.

On the first review, there may be no prior snapshot, decision, or NAV history.
The Skill establishes a validated baseline and does not fabricate historical
performance. A later review can then compare the new snapshot with that
baseline.

Updating the Skill does not remove this runtime history. A dry run does not
append to it.

## Troubleshooting

### The Skill is not discovered

Check the installed root file:

```bash
ls "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/SKILL.md"
```

Then reload/restart Codex. The complete Skill directory is required, not just
`SKILL.md`.

### `$crypto-portfolio-manager` is unknown

Check that the installation directory is named `crypto-portfolio-manager` and
that the frontmatter contains:

```yaml
name: crypto-portfolio-manager
```

### Python or normalization fails

Check the interpreter:

```bash
python3 --version
```

It must be Python 3.11 or newer. Normal Skill use does not require `pip
install`; package installation is for development checks.

### Live market information is unavailable

The repository does not ship live provider adapters. Ensure the running Codex
environment has suitable web/data access. The Skill should lower confidence or
stop a strong entry recommendation when critical data is missing.

### History is not being saved

Check the configured directory and write permissions:

```bash
echo "$CRYPTO_PORTFOLIO_DATA_DIR"
ls ~/.local/share/crypto-portfolio-manager
```

If `CRYPTO_PORTFOLIO_DATA_DIR` is set, inspect that directory instead of the
default path.
