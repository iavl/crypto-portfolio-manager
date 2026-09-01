# crypto-portfolio-manager

`crypto-portfolio-manager` is a Codex Skill for conservative-balanced,
spot-only crypto portfolio research over an approximately 6–12 month horizon.
It combines current market evidence with deterministic accounting, risk,
allocation, benchmark, and rebalance calculations.

Quick reference:

```text
Install:  ~/.codex/skills/crypto-portfolio-manager/
Invoke:   $crypto-portfolio-manager
History:  ~/.local/share/crypto-portfolio-manager/
Runtime:  Python >= 3.11 for the included scripts
Trading:  advisory only; no automatic execution
```

## Features

- Validates and classifies structured holdings from the canonical
  `config/policy.json` policy.
- Separates current Agent research and bounded judgments from deterministic
  Python accounting and portfolio mathematics.
- Uses unitized NAV so deposits and withdrawals are not mistaken for returns.
- Produces reproducible scoring, regime, target-allocation, risk-gate, and
  rebalance results.
- Preserves evidence, factor scores, and append-only decision history.
- Compares aligned portfolio periods with 100% BTC and 70/30 BTC/ETH
  buy-and-hold benchmarks.
- Treats stablecoins and cash as one sleeve and permits `NO_TRADE`.

## Safety / What It Is Not

This Skill provides analysis and proposed execution zones. It never places
orders and does not request exchange trading or withdrawal permissions.

It is not a short-term trading bot, leveraged or margin system,
futures/perpetuals system, or custodial exchange integration. Never provide
private keys, seed phrases, or trading credentials.

## Requirements

- Codex with Agent Skills support.
- Python 3.11 or newer for the included normalization script and development
  checks.
- Git for GitHub/manual installation and development.
- Network/web access in the running Codex environment for live portfolio
  research.

Normal Skill use does not require installing Python packages. The repository
has no runtime Python dependencies; `jsonschema` and `ruff` are development
dependencies only.

## Install

### Option A — Install with Codex skill-installer

In a Codex session, invoke the built-in `$skill-installer` and ask it to
install this GitHub repository as a root-level Skill:

```text
$skill-installer

Install https://github.com/iavl/crypto-portfolio-manager as
crypto-portfolio-manager. The Skill is at the repository root; use path `.`
and name it `crypto-portfolio-manager`.
```

The current installer helper represents that request with the repository URL,
root path, and explicit name: `--url`, `--path .`, and `--name
crypto-portfolio-manager`. A root repository needs the explicit name because
`.` is not itself a valid Skill directory name. If the installer is
unavailable or cannot fetch the repository, use the manual method below.

The installed Skill should contain:

```text
${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/SKILL.md
```

With the default `CODEX_HOME`, that is:

```text
~/.codex/skills/crypto-portfolio-manager/SKILL.md
```

Reload or restart Codex after installation if the Skill is not immediately
available.

### Option B — Manual installation from GitHub

This installs the complete directory, including the references and Python
modules used by the Skill:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"

git clone \
  https://github.com/iavl/crypto-portfolio-manager.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager"
```

The resulting layout should include:

```text
~/.codex/
└── skills/
    └── crypto-portfolio-manager/
        ├── SKILL.md
        ├── config/
        ├── references/
        ├── crypto_portfolio/
        └── scripts/
```

Reload or restart Codex after cloning. Do not copy only `SKILL.md`.

### Development installation

For active repository development, keep the checkout separate from the
installed Skill and install only the development checks:

```bash
git clone https://github.com/iavl/crypto-portfolio-manager.git
cd crypto-portfolio-manager

python3 -m pip install -e ".[dev]"
```

An optional development symlink workflow is not documented because it has not
been verified for reliable discovery by the current Codex environment.

## Verify Installation

First verify the file on disk:

```bash
test -f "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/SKILL.md" \
  && echo "crypto-portfolio-manager installed"
```

Then verify discovery in Codex with an explicit invocation:

```text
$crypto-portfolio-manager explain what portfolio reviews you support.
```

Codex may select the Skill automatically when a request clearly matches its
description. Use `$crypto-portfolio-manager` when explicit invocation is
important. If it is not recognized, reload/restart Codex and check the
directory name and YAML frontmatter name.

## Quick Start

1. Install the Skill and reload Codex.
2. Upload a portfolio screenshot or provide structured holdings.
3. Invoke `$crypto-portfolio-manager`.
4. Ask for a review and allow the Skill to load local history when available.

For example:

```text
$crypto-portfolio-manager

这是我当前的交易所仓位截图。

请读取已有的 portfolio history 和 previous decisions，
获取当前市场数据，并做一次 SNAPSHOT_REVIEW。

如果没有足够好的风险收益机会，可以 NO_TRADE。
```

Conceptually, the review flows from portfolio input and historical state
through current evidence, scoring, regime, allocation, risk, rebalance, and a
validated Chinese report. The first review may establish the history baseline
without claiming performance before sufficient history exists.

## Common Usage Examples

### Portfolio snapshot review

```text
$crypto-portfolio-manager

这是我的最新仓位截图。

做一次 SNAPSHOT_REVIEW。
读取历史仓位和上一轮决策，并结合当前市场情况判断是否需要调仓。
只有当风险收益比足够明显时才建议交易。
```

### New capital allocation

```text
$crypto-portfolio-manager

我新增了 5000 USDT 可投资资金。

结合我的现有仓位、历史决策和当前市场环境，
判断应该投入多少，以及分别配置到哪些资产。

不要求把 5000 USDT 全部投入。
允许全部保留为 stablecoin。
```

### Full portfolio review

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

Full reviews are designed around an approximately 14-day cadence; material
events can trigger an earlier event review.

### Event-driven review

```text
$crypto-portfolio-manager

SOL 今天发生了重大安全 / 基本面事件。

做一次 EVENT_REVIEW，
检查这个事件是否破坏原投资 thesis，
并判断当前组合是否需要立即调整。
```

### Dry run / do not save

```text
$crypto-portfolio-manager

对这个组合做一次 dry run。

分析并输出建议，但不要保存 portfolio snapshot、
decision 或其他历史状态。
```

An explicit dry-run or no-persistence request prevents appending runtime
state; it does not bypass validation or risk controls.

## How It Works

The repository keeps responsibilities separate:

```text
Codex evidence and bounded judgments
        ↓
canonical policy → typed models → ledger/metrics → scoring → regime
        ↓
allocation → risk gate → rebalance → execution-plan validation
        ↓
Chinese report and optional append-only history
```

`config/policy.json` is the canonical machine-readable policy. The Python
models and engine perform validation and mathematics; the Skill supplies
current evidence, bounded qualitative judgments, explanations, and structural
execution zones.

## External Market Data

The repository currently provides provider interfaces in
`crypto_portfolio/providers/base.py`, but no live provider implementations.
The running Codex environment is expected to use its available web/data
capabilities to obtain current public information.

Depending on the review, that information may include:

- spot prices, daily history, volume, and volatility;
- BTC trend, dominance, drawdown, breadth, and relative strength;
- ETF/institutional flows and stablecoin liquidity;
- fundamentals, usage, fees, TVL, supply, emissions, and token unlocks;
- exploits, outages, governance, regulatory, and other material events.

The repository does not directly connect to Binance, OKX, CoinGecko,
DefiLlama, Token Terminal, Glassnode, or CryptoQuant. See
`references/data-sources.md` for source hierarchy, freshness, and missing-data
rules. When critical information is unavailable, the Skill lowers confidence
or refuses a strong actionable recommendation.

## API Keys

No third-party API key is required by the repository itself because it does
not yet ship live external provider adapters. Codex uses its available web/data
capabilities for current public information.

If a future provider adapter needs credentials, supply them through environment
variables or a secret store. Never put credentials in `config/policy.json`,
`SKILL.md`, README examples, source files, or Git-tracked `.env` files. Do not
invent provider-specific variable names until that provider exists.

## Local Portfolio History

Runtime state is intentionally outside the Git checkout:

```text
default:   ~/.local/share/crypto-portfolio-manager/
override:  $CRYPTO_PORTFOLIO_DATA_DIR
```

The current state stores are:

```text
~/.local/share/crypto-portfolio-manager/
├── portfolio/snapshots.jsonl
└── decisions/
    ├── decisions.jsonl
    └── status-events.jsonl
```

Historical context is derived from these append-only records. It is used to
compare holdings, calculate cash-flow-aware NAV and drawdown, review previous
targets/actions/theses, reduce strategy drift, and determine when a full
review is due. Updating the Skill does not delete this data.

On a first review, previous snapshots, decisions, and NAV history may not
exist. The Skill creates an initial validated snapshot, performs the current
review, and establishes a baseline; it does not fabricate historical
performance.

## Structured Snapshot Input

Screenshots are the primary end-user input, but a normalized JSON snapshot is
also supported:

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

Validate and normalize it with:

```bash
python3 scripts/portfolio_snapshot.py portfolio.json
```

This command validates/normalizes the snapshot only. It does not fetch live
market data or produce a complete investment decision. Unclear screenshot
values and invalid JSON fields must be resolved rather than silently guessed.

## Updating

For a manual Git installation, update the Skill without touching the separate
runtime history directory:

```bash
git -C \
  "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager" \
  pull --ff-only
```

Reload or restart Codex if necessary. The current installer helper is
install-only and refuses to overwrite an existing destination; it does not
provide a documented update-in-place command. Use the manual Git method when
you want simple ongoing updates.

## Uninstalling

Remove the Skill installation only:

```bash
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager"
```

This does not remove portfolio history under
`~/.local/share/crypto-portfolio-manager/`. If you intentionally want to
delete that financial history too, use this separate destructive command:

```bash
rm -rf ~/.local/share/crypto-portfolio-manager
```

## Development

### Install development dependencies

```bash
python3 -m pip install -e ".[dev]"
```

### Run tests

```bash
python3 -m unittest discover -s tests -v
```

### Lint

```bash
ruff check .
```

### Compile check

```bash
python3 -m compileall crypto_portfolio scripts
```

### Snapshot normalization

```bash
python3 scripts/portfolio_snapshot.py path/to/snapshot.json
```

The CI workflow runs the same unittest, Ruff, and compile checks on Python
3.11, 3.12, and 3.13.

## Troubleshooting

### Skill is not discovered

Check the installed root file:

```bash
ls "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/SKILL.md"
```

Then reload/restart Codex. The complete Skill directory must be present.

### `$crypto-portfolio-manager` is unknown

Check that the directory is named `crypto-portfolio-manager` and that its
`SKILL.md` frontmatter contains:

```yaml
name: crypto-portfolio-manager
```

### Python command fails

Check the interpreter:

```bash
python3 --version
```

It must be Python 3.11 or newer. Normal Skill invocation does not require
`pip install`; package installation is for development checks.

### Live market information is unavailable

The repository does not ship live provider adapters. Ensure the running Codex
environment has suitable web/data access. Missing critical data intentionally
lowers confidence or blocks strong actionable recommendations.

### History is not being saved

Check the override and default directory:

```bash
echo "$CRYPTO_PORTFOLIO_DATA_DIR"
ls ~/.local/share/crypto-portfolio-manager
```

Verify that the selected directory exists or is creatable and that the
process has write permission.

## Privacy and Security

Never commit real portfolio balances, asset quantities, cost basis,
transaction history, exchange account identifiers, API keys, API secrets,
wallet private keys, or seed phrases. Keep real runtime state outside Git.

This repository never places orders and must not request exchange trading or
withdrawal permissions, private keys, or seed phrases. Treat uploaded
portfolio information and locally stored history as sensitive financial data.
