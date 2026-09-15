
# crypto-portfolio-manager

`crypto-portfolio-manager` is an Agent Skill for studying cryptocurrency
portfolios in a conservative-balanced, spot-only way over an approximately
3–6 month active-allocation horizon. It combines current market evidence with
deterministic accounting, risk, allocation, benchmark, and rebalancing
calculations, and is usable from Codex, Claude Code, and ZCode hosts that
support Agent Skills.

For the detailed review workflow, input examples, history behavior, and
troubleshooting, see the [Usage Guide](docs/USAGE.md).

For the investment philosophy and portfolio strategy, see
[Investment Strategy](references/investment-strategy.md).

For architecture and implementation details, see
[How It Works](docs/HOW_IT_WORKS.md).

Unfamiliar with the terminology? Start with the [Glossary](docs/GLOSSARY.md).

Model selection is controlled by the host (Codex, ZCode, Claude Code, etc.);
the Skill uses the model and reasoning settings of the user's current session
and performs no repository-level model switching.

## Overview

```text
Invoke:   $crypto-portfolio-manager
Skill:    .agents/skills/crypto-portfolio-manager/SKILL.md
History:  ~/.local/share/crypto-portfolio-manager/
Trading:  advice only; never executed automatically
```

## Features

- Validate holdings against the canonical policy in `config/policy.json`.
- Perform cash-flow-adjusted NAV, risk, allocation, benchmark, and rebalancing
  calculations.
- Follow a Python-first flow: registry-driven metric plans, normalized
  observations, deterministic Facts, and compact immutable review/report
  packets.
- Preserve evidence, factor scores, and append-only decision history.
- Persist decision-relevant `MetricObservation` history and collection failure
  records for compact current-versus-previous trend comparison.
- Fetch data on demand through free structured public APIs with
  freshness-aware local provider caching; no background services required.
- When configured, use SoSoValue's documented US BTC/ETH ETF aggregate history
  as structured ETF flow context; liquidation data is never attributed to that
  source. Annualized basis uses Binance's most recent traded USDT delivery
  contracts with exact mark/index prices; unsupported assets stay unavailable.
- Add non-scoring overlays such as derivatives/social positioning and BTC
  cycle context; these overlays can conservatively cap immediate deployment.
- Compare against 100% BTC and 70/30 BTC/ETH buy-and-hold benchmarks over
  matching evaluation periods.
- Provide a look-ahead-free end-to-end replay evaluation tool
  (`scripts/evaluate_strategy.py`): frozen review records → scoring → market
  regime → strategic targets → staged rebalancing → costs → realized returns,
  comparable against benchmarks and candidate strategy parameters.
- Treat stablecoins and cash as one money basket and allow `NO_TRADE`.
- Import structured fields from Binance wallet screenshots and
  deterministically compute each position's cost basis, unrealized P&L,
  return, and data coverage.
- Build staged execution plans for approved rebalance amounts from timestamped
  spot data and complete OHLCV, with calendar coverage checks, deterministic
  ATR-aware zones, confirmed swing points, Volume Profile POC/value-area/HVN
  context, tranches, and `WAIT` handling.
- Python owns deterministic financial calculations; the Agent owns bounded
  semantic research, interpretation, and report prose.

## Safety Boundary / What It Is Not

The Skill produces analysis and suggested execution ranges. It does not place
orders and never requests exchange trading or withdrawal permissions.

It is not a short-term trading bot, nor a leverage or margin system, a
futures/perpetuals system, or a custodial exchange integration. Never provide
private keys, seed phrases, or trading credentials.

Portfolio allocation decides total dollar exposure. The technical execution
layer only decides how already-approved amounts are staged; every plan is
bound to its rebalance approval, may be partially executed, and never places
orders. `planned_amount_usd` is a staged recommendation, not a filled order.

## Requirements

- A Codex, Claude Code, or ZCode host that supports Agent Skills.
- Python 3.11 or later, for the included scripts and development checks.
- Git, for cloning and development.
- The environment running the Agent needs network/web access for live
  research or on-demand refresh of public provider data.

Normal Skill usage does not require installing Python packages. The repository
has no runtime Python dependencies; `jsonschema` and `ruff` are
development-only dependencies.

## Skill Installation and Usage

```bash
git clone https://github.com/iavl/crypto-portfolio-manager.git
cd crypto-portfolio-manager
./install.sh --target all
```

The repository's single source of truth is:

```text
.agents/skills/crypto-portfolio-manager/SKILL.md
```

Codex discovers the Skill automatically when started from the repository root
or a subdirectory; Claude Code and ZCode need their own Skill directory or an
Import action. All hosts should use symlinks pointing at the directory above
to avoid stale copies. For the full commands, legacy migration, and runtime
data boundaries, see the
[multi-host installation guide](docs/USAGE.md#11-multi-host-skill-installation-and-usage).

`install.sh` creates user-level symlinks for Codex, Claude Code, and ZCode by
default; use `--target codex|claude|zcode` to install for a single host. The
script never copies the payload and never overwrites unknown existing paths;
a legacy Codex symlink at the current repository root is repaired in place.

The repository Skill needs no separate Python installation; if you want to run
development checks, run:

```bash
python3 -m pip install -e ".[dev]"
```

## Usage

For screenshot/JSON input, review types, copyable prompts, dry runs, external
data, and local history, start from the [Usage Guide](docs/USAGE.md).

For the standard Binance flow, set the wallet overview display currency to
USD, capture the asset/quantity/price-cost/floating P&L columns in a
screenshot, and upload it. The Agent extracts the visible fields; Python
computes all derived P&L values. Rows shown as `--` stay unknown; a partial
screenshot is reported as incomplete rather than treated as the full
portfolio.

## Runtime Data and Privacy

By default, history lives outside the Git checkout:

```text
~/.local/share/crypto-portfolio-manager/
```

Set `CRYPTO_PORTFOLIO_DATA_DIR` to use another directory. Free public
providers need no API key; keys for optional providers are supplied only
through environment variables. The repository supports only the current
runtime data contract; incompatible generated state after a breaking change
must be regenerated manually. Never commit real balances, quantities, cost
basis, trade history, account identifiers, credentials, private keys, or seed
phrases.

Content-addressed public OHLCV replay data is stored under
`market-data/sha256/<ohlcv_hash>.json` in the same runtime directory. Metric
observations and collection events live under `metrics/`; cached Volume
Profile results live under `volume-profiles/sha256/<profile_hash>.json`.
Volume Profile is a proxy for historical volume concentration, not an exact
holder cost basis.

Provider responses and recording manifests are cached under
`provider-cache/`; for schemas and cleanup see
[Data Providers](references/data-providers.md). Position P&L is the
unrealized performance of the remaining position; this feature makes no claim
about realized P&L, fees, tax lots, or lifetime returns.
Having provider or API trouble? See the
[Development and Provider Debugging Guide](docs/DEVELOPMENT_DEBUGGING.md).

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

Offline end-to-end replay evaluation (research only; no live market data, no
parameter auto-tuning):

```bash
python3 scripts/evaluate_strategy.py tests/fixtures/strategy_replay_basic.json --fee-bps 10
```
