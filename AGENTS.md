# AGENTS.md

## Purpose

This repository implements a conservative-balanced, medium-term crypto portfolio management system and Agent Skill.

The system is intended to help analyze portfolio allocation, market conditions, risk, and rebalancing decisions over an approximately 6–12 month investment horizon.

This repository is **not** a short-term trading bot and must not evolve into one unless explicitly requested.

All AI agents modifying this repository must follow the architecture, investment-policy boundaries, data-integrity rules, and engineering constraints defined in this file.

---

# 1. Core Principles

The following principles are non-negotiable unless the repository owner explicitly requests a policy change.

## 1.1 Portfolio philosophy

The default strategy is:

* spot-only;
* medium-term, approximately 6–12 months;
* conservative-balanced;
* BTC is the primary benchmark;
* BTC and ETH are the default core holdings;
* selected large-cap assets may be used as satellites;
* stablecoin/cash must normally remain at or above the configured minimum;
* default portfolio drawdown risk budget is approximately 20%;
* risk is managed at the **portfolio level**, not independently per asset;
* capital preservation overrides outperformance objectives during severe risk regimes;
* `NO TRADE` is a valid and important recommendation;
* new capital does not need to be fully deployed;
* existing holdings receive no special treatment merely because they are already owned;
* sunk cost must not override forward-looking portfolio decisions;
* chasing price is exceptional rather than normal;
* excessive turnover must be avoided.

Do not silently weaken or reinterpret these principles.

---

# 2. Safety Boundary

This project currently provides:

* portfolio analysis;
* portfolio risk assessment;
* target allocation calculation;
* rebalance recommendations;
* staged entry/exit plans;
* historical portfolio analysis;
* benchmark comparison.

It must **not** automatically execute real-money trades.

Do not introduce:

* exchange API keys with trading permissions;
* automatic order placement;
* futures;
* perpetual contracts;
* leverage;
* margin borrowing;
* leveraged tokens;
* automatic liquidation logic;
* autonomous discretionary trading.

Read-only exchange integrations may be added when explicitly requested.

If execution functionality is ever introduced, it must be isolated from the analysis engine and require explicit user confirmation and a separate security review.

---

# 3. Architecture Direction

The long-term architecture should separate the following responsibilities.

```text
SKILL.md
    ↓
Agent workflow and orchestration

references/
    ↓
Human-readable investment and decision policy

config/
    ↓
Machine-readable policy configuration

providers/
    ↓
Market and factual data acquisition

models/
    ↓
Typed domain models and data contracts

engine/
    ↓
Deterministic portfolio calculations

state/
    ↓
Portfolio history, cash flows, decisions and ledger

cli/
    ↓
Optional user interface and automation layer
```

AI agents must preserve this separation.

Do not move deterministic portfolio mathematics into prompts when it can be represented as code.

Do not move subjective market interpretation into low-level accounting functions.

---

# 4. LLM vs Deterministic Code

A central architectural rule is:

> LLMs may research, interpret evidence, and assign bounded judgments. Deterministic code must perform accounting, validation, constraints, and portfolio mathematics.

## LLM / Agent responsibilities

Appropriate uses include:

* interpreting market structure;
* reviewing current news;
* assessing qualitative fundamentals;
* evaluating governance/security developments;
* identifying relevant evidence;
* assigning factor scores according to documented rubrics;
* explaining recommendations;
* identifying uncertainty.

## Deterministic code responsibilities

These should be implemented in code whenever possible:

* portfolio valuation;
* position weights;
* cash-flow adjustments;
* NAV calculation;
* portfolio returns;
* portfolio drawdown;
* benchmark returns;
* weighted scoring;
* score renormalization;
* risk-budget constraints;
* target allocation constraints;
* rebalance thresholds;
* stablecoin minimum enforcement;
* allocation sum validation;
* tranche sum validation;
* schema validation;
* data freshness checks;
* history calculations.

Do not replace deterministic functions with free-form Agent reasoning.

---

# 5. Single Source of Truth

Avoid duplicating configurable policy values across multiple files.

Values such as:

* core assets;
* satellite assets;
* stablecoin symbols;
* stablecoin minimum;
* drawdown budget;
* benchmark composition;
* rebalance thresholds;
* scoring weights;
* regime limits;

should eventually have one machine-readable source of truth.

Preferred direction:

```text
config/policy.yaml
```

or an equivalent typed configuration model.

Human-readable documents may explain the values, but should not become independent competing sources.

When changing a policy parameter:

1. change the canonical configuration;
2. update dependent documentation;
3. update tests;
4. verify schemas if affected.

Never leave conflicting defaults in different parts of the repository.

---

# 6. Portfolio Accounting Rules

Portfolio accounting is financially sensitive and must be treated as high-integrity logic.

## 6.1 External cash flows

Do not calculate investment performance from raw account balance changes.

Deposits and withdrawals must not be counted as investment profit or loss.

For example:

```text
Portfolio value: $20,000
Deposit:         $5,000
New value:       $25,000
```

must **not** be interpreted as a +25% return.

Performance and drawdown calculations should use a cash-flow-adjusted method such as:

* unitized NAV;
* time-weighted return;
* another explicitly documented equivalent.

Any implementation involving historical portfolio returns must account for external cash flows.

---

## 6.2 Benchmark consistency

BTC and other benchmark performance must be calculated over exactly the same evaluation period as the portfolio.

When portfolio cash flows exist, benchmark calculations must use an equivalent cash-flow treatment.

Do not claim alpha or benchmark outperformance when:

* evaluation dates differ;
* portfolio cash flows are ignored;
* benchmark exposure is inconsistent;
* portfolio valuation methodology changed.

---

## 6.3 Missing return data

Missing return data for an actually held asset must not silently trigger weight renormalization.

This is invalid:

```text
BTC weight = 50%
ETH weight = 50%

BTC return available
ETH return missing

→ treat BTC as 100%
```

For portfolio performance calculations, missing required asset returns should normally fail explicitly or mark the result incomplete.

Missing-factor renormalization is appropriate for the **scoring model**, not for portfolio accounting.

---

# 7. Asset Classification

Asset classification must be deterministic.

Preferred hierarchy:

```text
canonical policy
    ↓
resolved configuration
    ↓
asset classification
```

Avoid allowing individual portfolio snapshots to silently override canonical classification.

If an input provides an `asset_type` that conflicts with resolved policy:

* reject it;
* or emit an explicit validation error/warning.

Do not allow the same asset to simultaneously be considered `core` in one subsystem and `satellite` in another.

---

# 8. Scoring Model

The default asset score is 0–100 and currently considers:

* Trend & price structure
* Valuation & historical position
* Fundamentals
* On-chain activity
* Capital flows
* Relative strength vs BTC
* Event / risk adjustment

The score is an input to portfolio construction, not a direct trading signal.

Never implement:

```text
score > X → automatically buy
```

without applying:

1. confidence;
2. market regime;
3. valuation/entry conditions;
4. asset risk tier;
5. existing portfolio concentration;
6. BTC-relative opportunity cost;
7. portfolio drawdown capacity;
8. stablecoin constraints;
9. rebalance thresholds.

A high score must not bypass portfolio-level risk limits.

---

# 9. Missing-Data Behavior

Never fabricate unavailable market data.

For non-critical scoring factors:

1. remove the unavailable factor;
2. renormalize remaining scoring weights;
3. reduce confidence.

For critical data, do not produce a high-conviction trade recommendation.

Critical data includes at minimum:

* current market price;
* recent trend/price history;
* portfolio valuation;
* unresolved material security-event status.

Do not invent substitute metrics solely to preserve the configured scoring formula.

---

# 10. Target Allocation

Target allocation should increasingly become deterministic.

Do not add more free-form allocation language to `SKILL.md` when the same logic can be represented in code.

Preferred future pipeline:

```text
market regime
    ↓
asset score
    ↓
confidence adjustment
    ↓
risk tier
    ↓
volatility / correlation adjustment
    ↓
portfolio risk budget
    ↓
target allocation
    ↓
rebalance threshold
    ↓
recommended action
```

Target weights must always satisfy portfolio constraints.

At minimum validate:

```text
sum(target weights) ≈ 100%
stablecoin weight >= configured minimum
satellite exposure <= regime allowance
single-asset concentration acceptable
```

A single high-scoring asset must never dominate the portfolio merely because its score is high.

---

# 11. Risk Regimes

The supported portfolio regimes are:

```text
NORMAL
DEFENSIVE
CAPITAL_PRESERVATION
```

Regime selection is portfolio-level.

Do not change regimes based only on one noisy market indicator.

Use multiple independent dimensions where practical:

* BTC trend/structure;
* volatility;
* portfolio drawdown;
* liquidity/flows;
* breadth;
* relative strength;
* material events.

A severe systemic or asset-specific event may override the normal confirmation requirement.

---

# 12. Rebalancing

The system is intentionally designed to avoid overtrading.

Default interpretation:

```text
< 3 percentage points deviation
→ normally HOLD

3–5 percentage points
→ WATCH

> 5 percentage points
→ eligible for rebalance

> 10 percentage points
→ high priority unless deliberately documented
```

Do not create small trades merely to make target weights mathematically exact.

Transaction friction and uncertainty should be considered.

Prefer using new capital to repair underweights when doing so avoids unnecessary selling, unless an existing asset thesis has failed.

---

# 13. Entry and Exit Plans

Do not invent false price precision.

Prefer execution **zones** over exact single-price targets.

Execution plans may use:

* support/resistance;
* moving averages;
* ATR;
* realized volatility;
* prior swing levels;
* market structure.

Do not mechanically generate:

```text
-5%
-10%
-15%
```

unless those percentages correspond to actual market structure.

Staged buying or selling should have explicit allocation fractions.

Validate:

```text
sum(tranche allocation_fraction) == 1
```

within an appropriate numerical tolerance.

---

# 14. History and State

Portfolio history must be append-oriented.

Never rewrite previous investment rationales to make them appear correct in hindsight.

Recommended statuses include:

```text
PENDING
CONFIRMED
NOT_EXECUTED
```

A recommendation must not be marked as executed unless:

* the user explicitly confirms execution;
* or a later trusted read-only portfolio snapshot unambiguously establishes it.

Every historical record should preserve enough information to reconstruct:

* portfolio state;
* applicable policy version;
* market regime;
* factor scores;
* confidence;
* evidence;
* recommendation;
* execution status.

---

# 15. Runtime Data Privacy

This GitHub repository may be public.

Real portfolio state must not be committed.

Potentially sensitive runtime data includes:

* real asset quantities;
* portfolio USD value;
* cost basis;
* exchange balances;
* trading history;
* account identifiers;
* exchange API credentials;
* personal financial history.

Prefer runtime storage outside the repository, for example:

```text
~/.local/share/crypto-portfolio-manager/
```

or another explicitly configured local data directory.

Repository `data/` directories should contain only:

* fixtures;
* fake examples;
* test data;
* `.gitkeep` files.

Never commit real user financial data.

---

# 16. Secrets

Never commit:

```text
API keys
API secrets
exchange credentials
private keys
wallet seed phrases
access tokens
session cookies
```

Use environment variables or secure secret stores when integrations require credentials.

Read-only credentials are preferred whenever possible.

Do not request trading permissions if read-only permissions satisfy the feature.

---

# 17. Data Providers

Market data access should eventually be implemented through provider interfaces.

Do not tightly couple portfolio logic to one specific provider.

Preferred structure:

```text
providers/
    base.py
    market.py
    onchain.py
    flows.py
    fundamentals.py
    events.py
```

Provider output should normalize data before it reaches the decision engine.

The engine should not care whether a price came from:

* Binance;
* Coinbase;
* CoinGecko;
* another approved provider.

Source provenance must be preserved.

---

# 18. Evidence and Provenance

Important investment judgments should be traceable to structured evidence.

Preferred future model:

```json
{
  "id": "evidence-001",
  "asset": "BTC",
  "factor": "capital_flows",
  "source": "provider-name",
  "observed_at": "...",
  "fetched_at": "...",
  "freshness": "CURRENT",
  "confidence": "HIGH"
}
```

Factor scores should reference the evidence supporting them.

Do not save only a final number such as:

```text
SOL score = 74
```

without preserving enough context to explain why.

---

# 19. Schema and Model Integrity

Avoid manually maintaining multiple independent validation implementations.

Preferred long-term approach:

```text
typed Python models
    ↓
generated / validated JSON Schema
```

or another clear single-source model system.

Whenever changing a schema:

1. update domain model;
2. update validation;
3. update fixtures;
4. update tests;
5. update documentation if user-facing behavior changes.

Schema changes should be backward-compatible when practical.

If a breaking schema change is necessary, increment a schema/version field and document migration behavior.

---

# 20. Testing Requirements

All behavioral changes require tests.

Before finishing any code change, run the complete test suite.

Current baseline command:

```bash
python -m unittest discover -s tests -v
```

Future project tooling may replace this command, but the complete suite must still run.

At minimum maintain tests for:

* default configuration;
* custom configuration;
* invalid configuration;
* asset classification;
* duplicate symbols;
* stablecoin floor;
* portfolio drawdown;
* cash-flow-adjusted performance;
* weighted scoring;
* missing-factor renormalization;
* portfolio return completeness;
* benchmark calculations;
* target weight sum;
* tranche allocation sum;
* invalid `asset_type`;
* NaN;
* Infinity;
* negative values;
* zero-value edge cases;
* schema validation.

Do not remove a test merely because a code change causes it to fail.

If intended behavior changes, update both the implementation and test with a clear rationale.

---

# 21. Regression Tests

Investment decision systems are vulnerable to behavioral drift.

When a bug or important edge case is found:

1. create a regression test reproducing it;
2. verify the old implementation fails;
3. implement the fix;
4. verify the new implementation passes.

Important portfolio scenarios should eventually be maintained as fixtures.

Examples:

```text
NORMAL bull market
DEFENSIVE market
CAPITAL_PRESERVATION event
large BTC overweight
satellite concentration
new cash deposit
cash withdrawal
asset thesis failure
stablecoin below floor
missing market data
```

---

# 22. Code Quality

Prefer:

* small deterministic functions;
* explicit types;
* domain-specific names;
* clear invariants;
* dependency-light implementations;
* pure functions where practical.

Avoid:

* large functions mixing data retrieval and portfolio calculation;
* hidden global mutable state;
* magic constants;
* duplicated policy values;
* unnecessary abstractions;
* premature microservices;
* heavyweight dependencies without clear benefit.

Do not refactor unrelated areas during a narrowly scoped task.

---

# 23. Repository Hygiene

Do not commit editor or OS-specific files.

The repository should ignore at minimum:

```text
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
```

Remove accidentally tracked editor configuration unless explicitly intended to be shared.

Do not create noisy formatting-only commits alongside behavioral changes unless necessary.

---

# 24. Dependency Policy

Prefer the Python standard library when it is sufficient.

Before adding a dependency, determine:

* why it is necessary;
* whether the feature can be implemented safely without it;
* maintenance health;
* license;
* transitive dependency impact;
* security implications.

Do not introduce large financial, ML, dataframe, or web frameworks for trivial functionality.

Dependencies must be pinned or constrained through the project's chosen package-management strategy.

---

# 25. CLI Development

The long-term design may expose a CLI such as:

```bash
crypto-portfolio review portfolio.json
```

CLI code must remain a thin orchestration layer.

Do not implement portfolio policy directly inside CLI handlers.

Preferred direction:

```text
CLI
 ↓
application/service layer
 ↓
engine/models/providers/state
```

The same core engine should eventually be reusable from:

* Codex Skill;
* CLI;
* API;
* tests.

---

# 26. Exchange Integrations

When exchange support is implemented:

Prefer:

```text
read-only balance API
read-only positions
read-only transaction history
```

before considering any write capability.

Every exchange should have an adapter.

Do not leak exchange-specific representations into the portfolio engine.

Normalize:

```text
exchange response
    ↓
canonical portfolio model
```

Do not store API secrets in portfolio snapshots.

---

# 27. Staking

Staking is secondary to asset allocation.

Do not recommend holding an unattractive asset solely because staking yield is high.

Any staking analysis must consider:

* nominal APY;
* token inflation;
* validator risk;
* slashing;
* smart-contract risk;
* custody;
* lockup;
* unstaking delay;
* liquid staking token depeg;
* liquidity;
* counterparty risk.

Staking yield must be evaluated on a risk-adjusted basis.

---

# 28. Documentation

Keep documentation concise and non-duplicative.

Responsibilities:

```text
README.md
→ installation, overview, basic usage

SKILL.md
→ Agent workflow and operating instructions

references/
→ investment methodology

AGENTS.md
→ development constraints for coding agents

config/
→ canonical policy configuration
```

Do not copy entire sections between these files unnecessarily.

Prefer linking to the canonical document.

---

# 29. SKILL.md Discipline

`SKILL.md` should orchestrate behavior.

Do not continuously expand it with every new implementation detail.

Move detailed content into:

* reference documents;
* configuration;
* deterministic code.

`SKILL.md` should explain:

```text
what to do
in what order
which policy to consult
which tools/code to use
what output is expected
```

It should not become the implementation of the portfolio engine.

---

# 30. Change Discipline

Before editing code:

1. inspect the relevant implementation;
2. inspect related tests;
3. inspect relevant policy/reference documents;
4. identify the current invariant;
5. determine whether the requested change is policy, architecture, bug fix, or feature.

Do not immediately modify files based only on the user request without understanding existing behavior.

---

# 31. Scope Discipline

Implement the smallest coherent change that solves the requested problem.

Do not opportunistically:

* redesign unrelated modules;
* rename large directory trees;
* add unrelated features;
* change investment policy;
* add external services;
* introduce trading execution.

If an adjacent issue is discovered, document it separately unless it blocks correctness.

---

# 32. Backward Compatibility

Preserve existing data formats and public behavior when practical.

Before introducing a breaking change, identify:

* affected schemas;
* stored historical data;
* tests;
* CLI interfaces;
* Skill behavior;
* migration requirements.

Do not silently change the semantic meaning of existing fields.

For example, never change:

```text
portfolio_drawdown
```

from a negative fraction to a positive fraction without an explicit migration/version change.

---

# 33. Error Handling

Invalid financial data must fail clearly.

Do not silently coerce clearly invalid values.

Examples that should generally fail:

```text
NaN
Infinity
negative asset quantity
negative asset value
unknown policy field
overlapping asset groups
invalid percentage
duplicate position symbol without explicit aggregation semantics
```

Errors should describe the offending field and expected invariant.

---

# 34. Numerical Conventions

Unless explicitly documented otherwise:

```text
weights      → decimal fraction, e.g. 0.25
returns      → decimal fraction, e.g. 0.10
drawdowns    → negative decimal fraction, e.g. -0.20
risk budget  → positive magnitude, e.g. 0.20
scores       → 0–100
USD values   → non-negative numeric values
```

Maintain consistent sign conventions throughout the codebase.

Do not mix percentage points with decimal fractions without explicit conversion.

---

# 35. Decision Explainability

Every material recommendation should remain explainable.

A future result should be reconstructable as:

```text
market evidence
    ↓
factor scores
    ↓
confidence
    ↓
market regime
    ↓
allocation engine
    ↓
risk gate
    ↓
rebalance rule
    ↓
recommendation
```

Avoid opaque formulas that cannot be traced back to documented policy.

Do not introduce ML models whose behavior cannot be explained or tested unless explicitly requested.

---

# 36. Financial-Model Changes

Changes to any of the following are high-impact:

* scoring weights;
* drawdown calculation;
* benchmark methodology;
* market regime rules;
* target allocation mapping;
* stablecoin floor;
* asset eligibility;
* risk caps;
* rebalance thresholds;
* transaction accounting.

For these changes:

1. explicitly identify the previous behavior;
2. explain the new behavior;
3. add/update tests;
4. consider historical compatibility;
5. update policy/reference documentation.

Do not treat them as ordinary refactors.

---

# 37. What AI Agents Must Not Do

Unless explicitly instructed, do not:

* change the investment horizon;
* allow leverage;
* remove the stablecoin floor;
* increase the default drawdown budget;
* convert the strategy into short-term trading;
* recommend meme/small-cap speculation;
* add automated trading;
* add hidden network calls;
* hard-code API credentials;
* commit real portfolio data;
* silently reinterpret risk metrics;
* fabricate market data;
* remove risk gates to make recommendations more aggressive;
* make the model trade simply because the user supplied new capital;
* assume an asset must remain held because it is below cost basis;
* optimize for maximum return without respecting drawdown constraints.

---

# 38. Definition of Done

A coding task is not complete until all applicable items are satisfied.

Before finalizing:

* implementation matches the requested scope;
* relevant tests were added or updated;
* full test suite passes;
* financial invariants remain valid;
* configuration has no duplicated conflicting source;
* schemas/models remain consistent;
* no real portfolio data was introduced;
* no secrets were introduced;
* no unrelated files were modified unnecessarily;
* user-facing documentation was updated when behavior changed;
* important design decisions are clearly explained.

For high-impact portfolio logic changes, include a concise explanation of:

```text
Before
After
Why
Risk
Tests
```

---

# 39. Preferred Development Priorities

Unless the repository owner requests otherwise, prioritize architectural work in roughly this order:

1. cash-flow-aware portfolio ledger;
2. deterministic target-allocation engine;
3. canonical policy configuration;
4. structured evidence/provenance;
5. stronger schema/domain models;
6. CI and regression tests;
7. provider abstraction;
8. read-only exchange integrations;
9. CLI/API interfaces;
10. additional analytics.

Do not prioritize additional indicators over core accounting correctness.

---

# 40. Guiding Rule

When uncertain between a more sophisticated implementation and a simpler deterministic one, prefer:

> the implementation that is easier to verify, reproduce, explain, and maintain.

Correct portfolio accounting and predictable risk behavior are more important than apparent analytical sophistication.
