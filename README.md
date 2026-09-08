[English (default)](README.md) · [简体中文](README.zh-CN.md)

The repository supports only the current internal contract. Generated local state from older breaking revisions may need to be regenerated. Confidence is Python-owned and layered as Data -> Regime -> Decision. Timestamps require a timezone; execution requires a timestamped SpotPrice.

# crypto-portfolio-manager

`crypto-portfolio-manager` is a Codex Skill for conservative-balanced,
spot-only crypto portfolio research over an approximately 3–6 month horizon.
It combines current market evidence with deterministic accounting, risk,
allocation, benchmark, and rebalance calculations.

Detailed review workflows, input examples, history behavior, and
troubleshooting are in the [Usage Guide](docs/USAGE.md).

For investment philosophy and portfolio strategy, see
[Investment Strategy](references/investment-strategy.md).

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

```bash
git clone https://github.com/iavl/crypto-portfolio-manager.git
cd crypto-portfolio-manager
./install.sh
```

Installed to:

```text
${CODEX_HOME:-$HOME/.codex}/skills/crypto-portfolio-manager/
```

The installer refuses to overwrite an existing directory or symlink. See the
[Usage Guide](docs/USAGE.md#安装管理) for verification, updates, and
uninstallation. Reload or restart Codex if the Skill is not immediately
available.

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
Only the current runtime data contract is supported; incompatible generated
state must be regenerated manually after a breaking change.
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
Provider or API problems? See [Development and Provider Debugging](docs/DEVELOPMENT_DEBUGGING.md).
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
