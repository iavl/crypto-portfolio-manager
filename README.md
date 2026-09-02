# crypto-portfolio-manager

`crypto-portfolio-manager` is a Codex Skill for conservative-balanced,
spot-only crypto portfolio research over an approximately 6–12 month horizon.
It combines current market evidence with deterministic accounting, risk,
allocation, benchmark, and rebalance calculations.

Detailed review workflows, input examples, history behavior, and
troubleshooting are in the [Usage Guide](USAGE.md).

## At a Glance

```text
Invoke:   $crypto-portfolio-manager
Install:  ~/.codex/skills/crypto-portfolio-manager/
History:  ~/.local/share/crypto-portfolio-manager/
Trading:  advisory only; no automatic execution
```

## Features

- Validates holdings against the canonical `config/policy.json` policy.
- Performs cash-flow-aware NAV, risk, allocation, benchmark, and rebalance
  calculations.
- Preserves evidence, factor scores, and append-only decision history.
- Persists decision-relevant `MetricObservation` history and collection
  failures for compact current-vs-previous trend comparisons.
- Benchmarks aligned periods against 100% BTC and 70/30 BTC/ETH buy-and-hold.
- Treats stablecoins and cash as one sleeve and permits `NO_TRADE`.
- Imports structured fields from Binance wallet screenshots and deterministically
  calculates per-position cost basis, unrealized P&L, return, and coverage.
- Stages an approved rebalance amount from timestamped spot data and completed
  OHLCV with calendar coverage checks, deterministic ATR-aware zones, confirmed
  swings, Volume Profile POC/value area/HVN context, tranches, and `WAIT`
  handling.

## Safety / What It Is Not

This Skill provides analysis and proposed execution zones. It never places
orders and does not request exchange trading or withdrawal permissions.

It is not a short-term trading bot, leveraged or margin system,
futures/perpetuals system, or custodial exchange integration. Never provide
private keys, seed phrases, or trading credentials.

Portfolio allocation decides total USD exposure. The technical execution layer
only decides how to stage that already-approved amount; every plan is bound to
the matching rebalance approval, may stage less, and does not place orders.
`planned_amount_usd` means staged recommendation capacity, not filled orders.

## Requirements

- Codex with Agent Skills support.
- Python 3.11 or newer for the included scripts and development checks.
- Git for GitHub/manual installation and development.
- Network/web access in the running Codex environment for live research.

Normal Skill use does not require installing Python packages. The repository
has no runtime Python dependencies; `jsonschema` and `ruff` are development
dependencies only.

## Install

### Codex skill-installer

In a Codex session, invoke `$skill-installer` and provide:

```text
Install https://github.com/iavl/crypto-portfolio-manager as
crypto-portfolio-manager. The Skill is at the repository root; use path `.`
and name it `crypto-portfolio-manager`.
```

The current installer helper uses the corresponding `--url`, `--path .`, and
`--name crypto-portfolio-manager` arguments. The installed file should be:

```text
${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/SKILL.md
```

Reload or restart Codex if it is not immediately available. If the installer
is unavailable, use the manual method.

### Manual installation from GitHub

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"

git clone \
  https://github.com/iavl/crypto-portfolio-manager.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager"
```

Clone the complete directory; the Skill also uses `config/`, `references/`,
`crypto_portfolio/`, `schemas/`, and `scripts/`. Reload or restart Codex after
cloning.

## Verify Installation

```bash
test -f "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/SKILL.md" \
  && echo "crypto-portfolio-manager installed"
```

Then verify discovery in Codex:

```text
$crypto-portfolio-manager explain what portfolio reviews you support.
```

Codex may also select the Skill automatically when a request matches its
description. Use the explicit invocation when discovery must be guaranteed.

## Usage

Start with the [Usage Guide](USAGE.md) for screenshot/JSON input, review types,
copyable prompts, dry runs, external data, and local history.

For the standard Binance workflow, set the wallet overview display currency to
USD, capture the asset/quantity/price-cost/floating-P&L columns, and upload the
screenshot. The Agent extracts visible fields; Python calculates all derived
P&L values. Rows showing `--` remain unknown, and a partial screenshot is
reported as partial rather than treated as the full portfolio.

## Runtime Data and Privacy

Runtime history is outside the Git checkout by default:

```text
~/.local/share/crypto-portfolio-manager/
```

Set `CRYPTO_PORTFOLIO_DATA_DIR` to use another directory. No third-party API
key is required by the repository itself because it has no live provider
adapters. Never commit real balances, quantities, cost basis, transaction
history, account identifiers, credentials, private keys, or seed phrases.
Content-addressed public OHLCV replay data is stored under
`market-data/sha256/<ohlcv_hash>.json` in that same runtime directory.
Metric observations and collection events are stored under `metrics/`; cached
Volume Profile results are stored under
`volume-profiles/sha256/<profile_hash>.json`. Volume Profile is a historical
traded-volume concentration proxy, not exact holder cost basis.
Position P&L is unrealized performance for the remaining position only; this
feature does not claim realized P&L, fees, tax lots, or lifetime return.

## Development

```bash
git clone https://github.com/iavl/crypto-portfolio-manager.git
cd crypto-portfolio-manager
python3 -m pip install -e ".[dev]"
```

Run the checks used by CI:

```bash
python3 -m unittest discover -s tests -v
ruff check .
python3 -m compileall crypto_portfolio scripts
```

Normalize a structured snapshot:

```bash
python3 scripts/portfolio_snapshot.py path/to/snapshot.json
```

## Updating

For a manual Git installation:

```bash
git -C \
  "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager" \
  pull --ff-only
```

The current installer helper is install-only and refuses to overwrite an
existing destination. Use the manual Git method for simple ongoing updates.

## Uninstalling

Remove the Skill installation only:

```bash
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager"
```

This does not delete portfolio history. Removing the separate default history
directory is optional and destructive:

```bash
rm -rf ~/.local/share/crypto-portfolio-manager
```
