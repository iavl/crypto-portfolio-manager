[English (default)](README.md) · [简体中文](README.zh-CN.md)

Only current contracts are supported: policy v3, scoring v2, routing v2, and execution plans v2. Old versions are rejected; no legacy replay or automatic migration is provided. Local data must be upgraded from verifiable facts or removed. Timestamps require a timezone; execution requires a timestamped SpotPrice.

# crypto-portfolio-manager

`crypto-portfolio-manager` is a Codex Skill for conservative-balanced,
spot-only crypto portfolio research over an approximately 6–12 month horizon.
It combines current market evidence with deterministic accounting, risk,
allocation, benchmark, and rebalance calculations.

Detailed review workflows, input examples, history behavior, and
troubleshooting are in the [Usage Guide](docs/USAGE.md).

For architecture and implementation details, see [How It Works](docs/HOW_IT_WORKS.md).

Model/reasoning profiles use safe defaults and runtime-aware fallback; see the
[routing reference](references/model-routing.md).

## At a Glance

```text
Invoke:   $crypto-portfolio-manager
Install:  ${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/
History:  ~/.local/share/crypto-portfolio-manager/
Trading:  advisory only; no automatic execution
```

## Features

- Validates holdings against the canonical `config/policy.json` policy.
- Performs cash-flow-aware NAV, risk, allocation, benchmark, and rebalance
  calculations.
- Uses a Python-first pipeline: registry-driven metric plans, normalized
  observations, deterministic Facts, and compact immutable review/report
  packets.
- Preserves evidence, factor scores, and append-only decision history.
- Persists decision-relevant `MetricObservation` history and collection
  failures for compact current-vs-previous trend comparisons.
- Acquires data on demand through free structured public APIs first, with
  freshness-aware local provider caching; no background service is required.
- When configured, uses SoSoValue's documented v2 U.S. BTC/ETH historical
  inflow chart for structured ETF-flow context; liquidation data is not
  attributed to it.
  Annualized basis uses Binance's nearest trading USDT delivery contract and
  exact-symbol mark/index prices; unsupported assets remain unavailable.
- Adds derivatives/social positioning and BTC cycle context as non-scoring
  overlays that can conservatively cap immediate deployment.
- Benchmarks aligned periods against 100% BTC and 70/30 BTC/ETH buy-and-hold.
- Treats stablecoins and cash as one sleeve and permits `NO_TRADE`.
- Imports structured fields from Binance wallet screenshots and deterministically
  calculates per-position cost basis, unrealized P&L, return, and coverage.
- Stages an approved rebalance amount from timestamped spot data and completed
  OHLCV with calendar coverage checks, deterministic ATR-aware zones, confirmed
  swings, Volume Profile POC/value area/HVN context, tranches, and `WAIT`
  handling.
- Configurable model/reasoning profiles use safe defaults and runtime-aware
  fallback; Luna stages remain `LUNA_MAX` only.

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
- Network/web access in the running Codex environment for live research or
  on-demand public-provider refreshes.

Normal Skill use does not require installing Python packages. The repository
has no runtime Python dependencies; `jsonschema` and `ruff` are development
dependencies only. Logical stage routing is configured in
`config/model-routing.json`.

## Install

### Local checkout with `install.sh`

Clone the repository outside the Codex skills directory, then run the
installer from the checkout:

```bash
git clone https://github.com/iavl/crypto-portfolio-manager.git
cd crypto-portfolio-manager
./install.sh
```

The script uses its own directory as the source, honors
`${CODEX_HOME:-$HOME/.codex}`, and copies the complete runtime Skill payload
to:

```text
${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/
```

It excludes Git metadata, tests, development environments, caches, and
repository `data/`; portfolio history remains under the separate runtime data
directory. The installer refuses to overwrite an existing directory or
symlink. Reload or restart Codex if the Skill is not immediately available.

### Codex skill-installer

If no local checkout is needed, invoke `$skill-installer` in a Codex session
and provide:

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

The helper also refuses to overwrite an existing destination. If it is
unavailable, use the local checkout method above.

## Verify Installation

```bash
test -d "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager" \
  && test -f "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/SKILL.md" \
  && echo "crypto-portfolio-manager installed"
```

Then verify discovery in Codex:

```text
$crypto-portfolio-manager explain what portfolio reviews you support.
```

Codex may also select the Skill automatically when a request matches its
description. Use the explicit invocation when discovery must be guaranteed.

## Usage

Start with the [Usage Guide](docs/USAGE.md) for screenshot/JSON input, review types,
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

Set `CRYPTO_PORTFOLIO_DATA_DIR` to use another directory. Free public
providers work without API keys; optional provider keys are environment-only.
Never commit real balances, quantities, cost basis, transaction
history, account identifiers, credentials, private keys, or seed phrases.
Content-addressed public OHLCV replay data is stored under
`market-data/sha256/<ohlcv_hash>.json` in that same runtime directory.
Metric observations and collection events are stored under `metrics/`; cached
Volume Profile results are stored under
`volume-profiles/sha256/<profile_hash>.json`. Volume Profile is a historical
traded-volume concentration proxy, not exact holder cost basis.
Provider responses and series manifests are cached under `provider-cache/`;
see [Data Providers](references/data-providers.md) for modes and cleanup.
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

`install.sh` is intentionally install-only and does not overwrite the
installed copy. Pull the source checkout, remove the exact old Skill
directory, and run the installer again:

```bash
git -C /path/to/crypto-portfolio-manager \
  pull --ff-only
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager"
/path/to/crypto-portfolio-manager/install.sh
```

Check the removal path before running it. This only removes the installed
Skill copy; it does not remove portfolio history.

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
