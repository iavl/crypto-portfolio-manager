# plan.md

# Crypto Portfolio Manager — Refactor & Hardening Plan

## Goal

Refactor the repository from a mostly prompt-driven portfolio Skill into a maintainable portfolio-management system with:

* correct cash-flow-aware portfolio accounting;
* deterministic portfolio allocation and rebalance logic;
* a single source of truth for policy configuration;
* structured evidence and decision provenance;
* stronger domain models and validation;
* proper runtime-data isolation;
* regression tests and CI;
* clean architecture for future market-data providers, CLI, and read-only exchange adapters.

The repository must remain:

* spot-only;
* medium-term;
* conservative-balanced;
* BTC-benchmarked;
* non-custodial;
* non-trading by default;
* capable of returning `NO TRADE`.

Follow `AGENTS.md` strictly throughout the implementation.

Do not introduce automatic trading or exchange write permissions.

---

# 1. General Implementation Rules

Before modifying code:

1. Read:

    * `AGENTS.md`
    * `SKILL.md`
    * all files under `references/`
    * all files under `schemas/`
    * all existing Python source and tests.

2. Run the current test suite and record the baseline result:

```bash
python -m unittest discover -s tests -v
```

3. Do not perform unrelated refactors.

4. Preserve existing documented investment behavior unless this plan explicitly changes it.

5. Add regression tests before or together with every behavioral fix.

6. Do not commit:

    * real portfolio data;
    * API credentials;
    * exchange credentials;
    * `.DS_Store`;
    * `.idea/`;
    * generated cache files.

---

# 2. Target Architecture

Refactor toward the following structure:

```text
crypto-portfolio-manager/
├── AGENTS.md
├── SKILL.md
├── README.md
├── pyproject.toml
├── .gitignore
│
├── config/
│   └── policy.json
│
├── references/
│   ├── investment-policy.md
│   ├── scoring-model.md
│   ├── risk-model.md
│   ├── data-sources.md
│   ├── decision-rules.md
│   └── output-template.md
│
├── crypto_portfolio/
│   ├── __init__.py
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── policy.py
│   │   ├── portfolio.py
│   │   ├── evidence.py
│   │   └── decision.py
│   │
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── metrics.py
│   │   ├── ledger.py
│   │   ├── scoring.py
│   │   ├── regime.py
│   │   ├── allocation.py
│   │   ├── rebalance.py
│   │   ├── risk.py
│   │   └── benchmark.py
│   │
│   ├── state/
│   │   ├── __init__.py
│   │   ├── snapshots.py
│   │   └── decisions.py
│   │
│   └── providers/
│       ├── __init__.py
│       └── base.py
│
├── scripts/
│   └── portfolio_snapshot.py
│
├── schemas/
│   ├── portfolio.schema.json
│   ├── decision.schema.json
│   └── evidence.schema.json
│
├── examples/
│   └── fixtures/
│
├── tests/
│   ├── unit/
│   ├── regression/
│   └── fixtures/
│
└── .github/
    └── workflows/
        └── test.yml
```

Do not create abstractions that are not yet used.

The main architectural rule is:

```text
LLM / Agent
    → research and bounded judgment

Deterministic Python
    → accounting, scoring math, constraints,
       allocation, rebalance and validation
```

---

# 3. Phase 1 — Repository Hygiene

## 3.1 Add root `.gitignore`

Create:

```text
.gitignore
```

Include at minimum:

```gitignore
.DS_Store
.idea/
.vscode/

__pycache__/
*.py[cod]

.pytest_cache/
.mypy_cache/
.ruff_cache/

.venv/
venv/

.env
.env.*

.crypto-portfolio-manager/

data/portfolio/*
data/decisions/*
```

If `.gitkeep` is still required, preserve it explicitly.

## 3.2 Remove tracked IDE / OS artifacts

Remove tracked:

```text
.DS_Store
.idea/
```

Do not remove user-local files outside Git tracking.

## 3.3 Runtime financial data

Do not store real portfolio state inside the Git repository.

Introduce a documented default runtime directory such as:

```text
~/.local/share/crypto-portfolio-manager/
```

Allow future override through environment/configuration, but do not over-engineer this in the current task.

Repository examples must use fake data only.

### Acceptance criteria

* `.DS_Store` is no longer tracked.
* `.idea/` is no longer tracked.
* root `.gitignore` exists.
* README clearly states that real portfolio data must not be committed.

---

# 4. Phase 2 — Canonical Policy Configuration

The current repository duplicates policy defaults across documentation, schemas, and Python.

Create one canonical machine-readable policy file:

```text
config/policy.json
```

Prefer JSON in this phase to avoid introducing a YAML dependency.

Suggested shape:

```json
{
  "policy_version": 1,
  "investment_horizon_months": {
    "min": 6,
    "max": 12
  },
  "universe": {
    "core": ["BTC", "ETH"],
    "satellites": ["SOL", "BNB", "LINK", "AAVE"],
    "stable": ["USDT", "USDC", "DAI", "FDUSD", "TUSD", "USD", "CASH"]
  },
  "risk": {
    "min_stablecoin_weight": 0.10,
    "max_portfolio_drawdown": 0.20
  },
  "benchmarks": {
    "primary": {
      "BTC": 1.0
    },
    "secondary": {
      "BTC": 0.7,
      "ETH": 0.3
    }
  },
  "rebalance": {
    "hold_below_pp": 3,
    "watch_below_pp": 5,
    "high_priority_above_pp": 10
  },
  "scoring_weights": {
    "trend": 0.25,
    "valuation": 0.20,
    "fundamentals": 0.20,
    "onchain": 0.10,
    "capital_flows": 0.10,
    "relative_strength_btc": 0.10,
    "event_risk": 0.05
  }
}
```

Implement a policy loader in:

```text
crypto_portfolio/models/policy.py
```

Requirements:

* validate all fractions;
* validate benchmark weights sum to 1;
* validate scoring weights sum to 1;
* reject overlapping asset groups;
* normalize tickers to uppercase;
* reject unknown policy fields;
* expose one resolved policy object.

Do not keep an independent Python `DEFAULT_CONFIG` containing competing defaults.

Snapshot-specific overrides may still exist, but must be applied explicitly on top of canonical policy.

Every decision record should preserve:

```text
policy_version
```

and any overrides used.

### Acceptance criteria

* canonical defaults live in `config/policy.json`;
* Python code loads them instead of duplicating them;
* documentation references canonical policy rather than redefining machine values independently;
* tests verify invalid/overlapping policy values fail.

---

# 5. Phase 3 — Fix Asset Classification

Current classification allows explicit position `asset_type` to override the resolved policy.

Remove silent override behavior.

Canonical flow must become:

```text
policy
  ↓
resolved configuration
  ↓
classification
```

A position may optionally provide an `asset_type_hint`, but:

* matching hint → accepted;
* conflicting hint → validation error or explicit warning;
* missing hint → deterministic classification.

Do not allow the same symbol to be:

```text
core
```

in one subsystem and:

```text
satellite
```

in another.

Add duplicate-symbol handling.

Default behavior:

* duplicate symbols in a snapshot should fail validation unless an explicit aggregation function is invoked before normalization.

Do not silently aggregate duplicates without documenting the behavior.

### Required tests

* overlapping policy groups;
* conflicting `asset_type_hint`;
* duplicate symbols;
* lowercase symbols;
* whitespace normalization;
* unknown asset classified as `other`.

---

# 6. Phase 4 — Cash-Flow-Aware Portfolio Ledger

This is the highest-priority functional correction.

Raw account balance must not be used directly to calculate investment return or drawdown when deposits/withdrawals exist.

Create:

```text
crypto_portfolio/engine/ledger.py
```

Implement a simple unitized NAV model.

## 6.1 Required concepts

Support:

```text
PortfolioSnapshot
ExternalCashFlow
NAVState
```

At minimum track:

```text
timestamp
portfolio_value
external_cash_flow
units
nav_per_unit
```

## 6.2 Behavior

Initial portfolio:

```text
initial_units = initial_portfolio_value
initial_nav = 1.0
```

or another documented equivalent.

Deposit:

```text
new_units = deposit / current_nav
```

Withdrawal:

```text
removed_units = withdrawal / current_nav
```

Deposits and withdrawals must not change NAV.

Market P&L changes NAV.

## 6.3 Example regression case

Given:

```text
Day 1 portfolio value = 20,000
Day 2 deposit = 5,000
Day 2 portfolio value = 25,000
```

with no market movement:

```text
portfolio return = 0%
```

not:

```text
+25%
```

Add an equivalent withdrawal test.

## 6.4 Drawdown

Calculate portfolio drawdown from:

```text
NAV history
```

not raw account value history.

Expose:

```text
current_drawdown
max_drawdown
```

using existing negative fraction convention.

Example:

```text
-0.20 == -20%
```

## 6.5 Migration

Remove or deprecate the current `portfolio_peak_value`-based drawdown logic where external cash flows make it unreliable.

Do not silently reinterpret existing historical fields.

If compatibility is required:

* retain legacy input support temporarily;
* clearly mark it as legacy;
* only use it when no cash-flow-aware history exists.

### Required tests

* deposit does not create return;
* withdrawal does not create loss;
* positive market return;
* negative market return;
* repeated deposits;
* repeated withdrawals;
* deposit + market loss;
* withdrawal + market gain;
* NAV current drawdown;
* NAV max drawdown.

---

# 7. Phase 5 — Correct Portfolio Return and Benchmark Logic

Move metrics into:

```text
crypto_portfolio/engine/metrics.py
crypto_portfolio/engine/benchmark.py
```

Current portfolio-return behavior must not silently renormalize over missing held-asset returns.

For actual portfolio performance:

```text
held asset missing required return
→ explicit incomplete/error state
```

Do not use scoring-style missing-data renormalization.

## 7.1 Benchmark requirements

Primary:

```text
100% BTC
```

Secondary:

```text
70% BTC
30% ETH
```

Benchmark performance must use:

* same start/end period;
* equivalent cash-flow treatment;
* same valuation timestamps where possible.

If external cash is contributed to the portfolio, model an equivalent contribution to benchmark tracking.

Do not report:

```text
alpha
outperformance
```

when periods/cash flows are not comparable.

### Required tests

* missing held-asset return raises;
* 100% BTC benchmark;
* 70/30 benchmark;
* portfolio and benchmark with deposit;
* portfolio and benchmark with withdrawal;
* mismatched date period rejected.

---

# 8. Phase 6 — Domain Models and Validation

Create lightweight typed domain models without adding a heavy framework unless clearly necessary.

Standard-library dataclasses are acceptable.

Create:

```text
crypto_portfolio/models/portfolio.py
crypto_portfolio/models/evidence.py
crypto_portfolio/models/decision.py
```

Models should explicitly encode:

## Portfolio

```text
timestamp
base_currency
positions
external_cash_flow
total_value
policy_version
```

## Position

```text
symbol
quantity
value_usd
cost_basis_usd
resolved_asset_type
```

## Evidence

```text
id
asset
factor
source
observed_at
fetched_at
freshness
confidence
value / summary
```

## FactorScore

```text
factor
score
evidence_ids
```

## AssetAssessment

```text
symbol
factor_scores
weighted_score
confidence
```

## Decision

```text
market_regime
current_weights
target_weights
actions
risk_checks
policy_version
evidence references
```

Reject:

* NaN;
* Infinity;
* invalid negative values;
* invalid weights;
* invalid score ranges.

---

# 9. Phase 7 — Structured Evidence / Provenance

Do not keep only free-text:

```text
data_quality_notes
```

Add structured evidence support.

Create:

```text
schemas/evidence.schema.json
```

Decision records should be able to reference evidence IDs.

Example:

```json
{
  "id": "btc-etf-flow-2026-09-01",
  "asset": "BTC",
  "factor": "capital_flows",
  "source": "example-provider",
  "observed_at": "2026-09-01T00:00:00Z",
  "fetched_at": "2026-09-01T01:00:00Z",
  "freshness": "CURRENT",
  "confidence": "HIGH",
  "value": 123456789
}
```

Asset factor score:

```json
{
  "factor": "capital_flows",
  "score": 72,
  "evidence_ids": [
    "btc-etf-flow-2026-09-01"
  ]
}
```

Do not attempt to build actual web providers yet.

This phase is only about the canonical internal data model.

### Acceptance criteria

A historical decision can answer:

```text
Why did this factor receive this score?
Which evidence supported it?
How fresh was the evidence?
```

---

# 10. Phase 8 — Deterministic Scoring Engine

Move weighted score math into:

```text
crypto_portfolio/engine/scoring.py
```

Use canonical weights from policy.

Support:

```text
weighted_score
missing-factor renormalization
confidence adjustment metadata
```

Missing non-critical factors:

```text
remove factor
renormalize remaining weights
lower confidence
```

Do not fabricate missing values.

Return both:

```text
score
effective_weights
missing_factors
```

so the decision is auditable.

Example:

```json
{
  "score": 71.4,
  "effective_weights": {
    "trend": 0.2778,
    "valuation": 0.2222
  },
  "missing_factors": ["onchain"]
}
```

Do not automatically map score directly to BUY.

---

# 11. Phase 9 — Deterministic Market Regime Engine

Create:

```text
crypto_portfolio/engine/regime.py
```

Do not fully automate qualitative event interpretation.

Instead define structured regime inputs such as:

```text
btc_trend
volatility_state
portfolio_drawdown_band
flow_state
breadth_state
systemic_event_risk
```

Output:

```text
NORMAL
DEFENSIVE
CAPITAL_PRESERVATION
```

plus:

```text
confidence
reasons
```

Use rule confirmation across multiple independent dimensions.

A severe systemic event may override confirmation.

Avoid magic behavior hidden inside prompts.

Document all thresholds/constants in policy/config where reasonable.

---

# 12. Phase 10 — Deterministic Allocation Engine

This is the second highest-priority architectural task.

Create:

```text
crypto_portfolio/engine/allocation.py
```

The purpose is not to create a perfect optimizer.

The purpose is to make:

```text
same structured inputs
→ same target weights
```

## 12.1 Inputs

At minimum:

```text
resolved policy
market regime
asset assessments
confidence
asset class
current weights
volatility/risk tier where available
```

## 12.2 Constraints

Always enforce:

```text
target weights sum ≈ 1.0
stablecoin >= configured minimum
satellites <= regime satellite envelope
core remains majority of risky allocation in normal circumstances
weak/low-confidence satellite may receive 0%
```

## 12.3 Suggested v1 approach

Do not build mean-variance optimization.

Use deterministic bounded allocation rules.

Example concept:

### Step A — reserve stablecoin

Based on regime:

```text
NORMAL
stablecoin baseline = 10–20%

DEFENSIVE
stablecoin baseline = 20–40%

CAPITAL_PRESERVATION
stablecoin baseline = 40–80%+
```

Use policy minimum as hard floor.

### Step B — calculate risky allocation

```text
risky_budget = 1 - stablecoin_target
```

### Step C — assign core budget

Core should normally receive majority of risky allocation.

### Step D — satellite eligibility

A satellite receives non-zero allocation only if:

```text
score >= threshold
confidence != LOW
relative BTC case acceptable
no severe event override
```

### Step E — normalize within allowed envelopes

Apply risk/regime caps.

### Step F — final constraint gate

Validate all target weights.

Do not produce target weights that depend on arbitrary free-form LLM percentages.

## 12.4 Output

Return:

```text
target_weights
allocation_reasons
constraints_applied
```

---

# 13. Phase 11 — Risk Gate

Create:

```text
crypto_portfolio/engine/risk.py
```

The risk gate runs after allocation.

Validate:

* stablecoin floor;
* total target = 100%;
* satellite concentration;
* single-asset concentration;
* drawdown regime compatibility;
* low-confidence exposure;
* event override;
* aggregate high-beta exposure.

Do not attempt to claim:

```text
20% maximum loss guaranteed
```

The configured 20% remains a risk-budget objective only.

Return structured violations such as:

```text
ERROR
WARNING
INFO
```

Allocation must not bypass `ERROR` violations.

---

# 14. Phase 12 — Rebalance Engine

Create:

```text
crypto_portfolio/engine/rebalance.py
```

Use policy thresholds.

Default behavior:

```text
absolute deviation < 3 pp
→ HOLD

3–5 pp
→ WATCH

>5 pp
→ eligible for rebalance

>10 pp
→ high priority
```

Also handle small target positions using relative deviation where appropriate.

Inputs:

```text
current weights
target weights
portfolio value
new cash available
```

Prefer new cash for repairing underweights when practical.

Do not preserve a thesis-broken asset merely to avoid selling.

Output:

```text
symbol
action
current_weight
target_weight
amount_usd
priority
```

Valid actions:

```text
INCREASE
REDUCE
HOLD
EXIT
WAIT
NO_TRADE
```

Explicitly evaluate `NO_TRADE` before returning trade actions.

---

# 15. Phase 13 — Execution Plan Validation

Do not automatically generate market price zones in deterministic code.

The Agent may propose zones based on current market structure.

However, add deterministic validation for execution plans.

Validate:

```text
0 < allocation_fraction <= 1
sum(allocation_fraction) ≈ 1
price_low <= price_high
non-negative price
```

Do not accept execution zones with invalid tranche totals.

Add tests.

---

# 16. Phase 14 — State and History

Create:

```text
crypto_portfolio/state/snapshots.py
crypto_portfolio/state/decisions.py
```

Keep storage simple.

Use append-only JSONL where practical.

Runtime path should be outside Git.

Every snapshot record should preserve:

```text
timestamp
portfolio data
external cash flow
policy_version
```

Every decision record should preserve:

```text
timestamp
policy_version
market regime
asset assessments
evidence IDs
target weights
recommendations
status
```

Statuses:

```text
PENDING
CONFIRMED
NOT_EXECUTED
```

Never rewrite old recommendation rationale.

A later user-confirmed execution may append an updated status/event.

---

# 17. Phase 15 — Refactor Existing Script

Keep:

```text
scripts/portfolio_snapshot.py
```

as a thin compatibility/CLI wrapper.

Move business logic out of the script.

Desired structure:

```text
portfolio_snapshot.py
    ↓
load canonical policy
    ↓
domain model validation
    ↓
normalization engine
    ↓
JSON output
```

The script should not own core portfolio policy.

Do not duplicate classification or risk constants inside the script.

---

# 18. Phase 16 — Schemas

Update:

```text
schemas/portfolio.schema.json
schemas/decision.schema.json
```

Add:

```text
schemas/evidence.schema.json
```

Important changes:

## Portfolio schema

Add support for:

```text
policy_version
external_cash_flow
```

Avoid using raw `portfolio_peak_value` as the preferred modern drawdown mechanism.

## Decision schema

Add:

```text
policy_version
evidence
factor_scores / evidence references
constraints_applied
risk_checks
```

## Validation

Ensure schema and Python model conventions match exactly:

```text
weights      → fraction
returns      → fraction
drawdown     → negative fraction
risk budget  → positive fraction
scores       → 0–100
```

---

# 19. Phase 17 — Provider Abstraction Skeleton

Do not implement full market-data integrations yet.

Create only:

```text
crypto_portfolio/providers/base.py
```

Define minimal protocols/interfaces for future providers.

Possible conceptual interfaces:

```text
MarketDataProvider
FundamentalDataProvider
OnchainDataProvider
EventDataProvider
```

Do not add dependencies or network calls.

The goal is only to prevent future market-data code from coupling directly to the portfolio engine.

---

# 20. Phase 18 — Tests

Reorganize tests if helpful, but do not remove existing coverage.

Add tests for all new financial invariants.

## Mandatory test categories

### Policy

* default policy loads;
* invalid fractions;
* overlapping asset groups;
* benchmark sum != 1;
* scoring weight sum != 1;
* duplicate symbol.

### Portfolio

* classification;
* unknown asset;
* invalid quantity;
* invalid value;
* NaN;
* Infinity;
* duplicate position.

### Accounting

* deposits;
* withdrawals;
* NAV calculation;
* current drawdown;
* max drawdown;
* external cash-flow neutrality.

### Metrics

* simple return;
* volatility;
* moving averages;
* missing held-asset return failure.

### Scoring

* full factor set;
* missing factor renormalization;
* all factors missing;
* invalid score.

### Allocation

* targets sum to 100%;
* stablecoin floor;
* satellite cap;
* low-confidence satellite receives reduced/zero allocation;
* CAPITAL_PRESERVATION lowers risky exposure;
* deterministic output.

### Rebalance

* <3 pp HOLD;
* 3–5 pp WATCH;
* > 5 pp rebalance;
* > 10 pp high priority;
* new cash repairs underweight;
* thesis-break EXIT case;
* NO_TRADE case.

### Execution plan

* tranche fractions sum to 1;
* invalid zone ordering fails.

### Benchmark

* equivalent cash-flow treatment;
* portfolio-vs-BTC aligned dates;
* 70/30 calculation.

---

# 21. Phase 19 — Regression Fixtures

Create fake scenarios under:

```text
tests/fixtures/
```

At minimum:

```text
normal_market.json
defensive_market.json
capital_preservation.json
deposit_no_market_move.json
withdrawal_no_market_move.json
btc_overweight.json
satellite_concentration.json
stablecoin_below_floor.json
missing_factor_data.json
thesis_failure.json
```

Add deterministic regression tests asserting expected:

```text
regime
target allocation envelope
risk violations
rebalance action class
```

Do not assert unnecessarily exact percentages if the policy does not require them.

---

# 22. Phase 20 — CI and Project Tooling

Create:

```text
pyproject.toml
```

Keep dependencies minimal.

Configure at minimum:

* supported Python version;
* Ruff if added;
* basic package metadata.

Create:

```text
.github/workflows/test.yml
```

Run on:

```text
push
pull_request
```

Test at least:

```text
Python 3.11
Python 3.12
Python 3.13
```

CI should run:

```bash
python -m unittest discover -s tests -v
```

If Ruff is introduced:

```bash
ruff check .
```

Do not add unnecessary packaging/build complexity.

---

# 23. Phase 21 — Update SKILL.md

After the deterministic engine exists, simplify `SKILL.md`.

`SKILL.md` should describe orchestration, not reimplement portfolio mathematics in prose.

Preferred workflow:

```text
1. Extract / load portfolio
2. Load canonical policy
3. Validate snapshot
4. Obtain current market evidence
5. Produce factor assessments
6. Run deterministic scoring
7. Run regime engine
8. Run allocation engine
9. Run risk gate
10. Run rebalance engine
11. Agent constructs execution zones if needed
12. Validate execution plan
13. Produce Chinese user-facing report
14. Append decision history when enabled
```

Remove duplicated constants where code/config is canonical.

Keep qualitative rules in `references/`.

Do not make `SKILL.md` longer merely because new engine modules exist.

---

# 24. Phase 22 — Update README

README should clearly explain:

## What the project is

```text
AI-assisted portfolio research
+
deterministic portfolio risk/allocation engine
```

## What it is not

```text
not an automatic trading bot
not leverage
not short-term trading
```

## Architecture

Briefly describe:

```text
Agent
Evidence
Scoring
Regime
Allocation
Risk
Rebalance
History
```

## Privacy

Explicitly state that real portfolio data must be stored outside the Git repository.

## Local validation

Document:

```bash
python -m unittest discover -s tests -v
```

and any new script usage.

---

# 25. Do Not Implement Yet

The following are explicitly out of scope for this refactor:

* Binance API integration;
* OKX API integration;
* Bybit API integration;
* Coinbase API integration;
* API keys;
* trading permissions;
* automatic orders;
* real-time websocket feeds;
* database server;
* web frontend;
* mobile UI;
* ML price prediction;
* mean-variance optimizer;
* Monte Carlo portfolio optimization;
* complex tax accounting;
* staking execution;
* autonomous background monitoring.

Create clean extension points only where required.

---

# 26. Expected Final Behavior

After the refactor, the workflow should look like:

```text
Screenshot / holdings
        ↓
Portfolio normalization
        ↓
Cash-flow-aware ledger
        ↓
Current market evidence
        ↓
Agent factor judgments
        ↓
Deterministic weighted scoring
        ↓
Deterministic market regime
        ↓
Deterministic target allocation
        ↓
Portfolio risk gate
        ↓
Deterministic rebalance decision
        ↓
Agent execution-zone proposal
        ↓
Execution-plan validation
        ↓
Chinese user-facing recommendation
        ↓
Append-only history
```

The same structured input should produce materially consistent portfolio decisions.

The Agent may still differ slightly in qualitative research or factor judgments, but accounting, constraints, target construction, and rebalance behavior must be reproducible.

---

# 27. Definition of Done

Do not consider this task complete until:

* `.DS_Store` and `.idea/` are removed from tracking;
* `.gitignore` exists;
* canonical policy config exists;
* policy defaults are no longer independently duplicated in Python;
* asset classification is deterministic;
* conflicting asset type input is rejected;
* cash-flow-aware NAV exists;
* deposits do not appear as investment return;
* withdrawals do not appear as investment loss;
* drawdown is based on investment NAV rather than raw account balance;
* portfolio return no longer silently renormalizes missing held assets;
* benchmark comparisons use equivalent cash-flow treatment;
* structured evidence model exists;
* deterministic scoring engine exists;
* deterministic regime engine exists;
* deterministic allocation engine exists;
* deterministic risk gate exists;
* deterministic rebalance engine exists;
* execution tranche validation exists;
* historical state is append-oriented;
* runtime financial data is stored outside the Git repository;
* provider abstraction skeleton exists;
* all old tests still pass or are intentionally migrated;
* new unit/regression tests pass;
* CI exists and passes;
* README is updated;
* SKILL.md uses the deterministic engine instead of duplicating its logic.

---

# 28. Final Verification

Before finishing, run:

```bash
python -m unittest discover -s tests -v
```

and any configured lint command.

Then inspect:

```bash
git status
git diff --stat
git diff
```

Verify:

* no secrets;
* no real portfolio data;
* no IDE files;
* no unrelated changes;
* no accidental investment-policy changes.

Finally provide a concise implementation report using:

```text
## Implemented
## Important behavioral changes
## Architecture changes
## Tests added
## Test results
## Backward compatibility / migrations
## Remaining recommended work
```

Do not claim completion if any test is failing.
