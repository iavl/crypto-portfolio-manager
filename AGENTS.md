# AGENTS.md

This repository implements a conservative-balanced, medium-term crypto
portfolio management system and Agent Skill. It analyzes allocation, market
conditions, risk, rebalancing, staged entries/exits, history, and benchmarks
over an approximately 6–12 month horizon. It is not a short-term trading bot.

## Non-negotiable investment policy

The default strategy is:

- spot-only, medium-term, conservative-balanced;
- BTC is the primary benchmark; BTC and ETH are default core holdings;
- selected large-cap assets may be satellites;
- stablecoin/cash stays at or above the configured minimum;
- the default portfolio drawdown risk budget is approximately 20%;
- risk is managed at portfolio level, not independently per asset;
- capital preservation overrides outperformance during severe regimes;
- `NO TRADE` is a valid recommendation and new capital need not be fully
  deployed;
- existing holdings receive no entitlement, sunk cost does not control a
  forward-looking decision, chasing price is exceptional, and turnover is
  limited.

Do not silently weaken or reinterpret these rules, change the horizon, add
meme/small-cap speculation, or optimize return without the drawdown and stable
cash constraints.

## Safety boundary

The project may analyze portfolios, assess risk, calculate targets, recommend
rebalances, create staged plans, analyze history, and compare benchmarks. It
must not automatically execute real-money trades.

Do not introduce exchange trading keys, order placement, futures, perpetuals,
leverage, margin, leveraged tokens, liquidation logic, or autonomous trading.
Read-only integrations require explicit request. Any future execution must be
isolated from analysis, require explicit confirmation, and receive a separate
security review.

## Architecture

Keep responsibilities separated:

```text
SKILL.md       agent workflow/orchestration
references/    human-readable investment and decision policy
config/        canonical machine-readable policy
providers/     normalized market/factual data
models/        typed domain contracts
engine/        deterministic calculations
state/         history, cash flows, decisions, ledger
cli/           thin optional interface
```

Do not put deterministic mathematics in prompts or subjective interpretation
in accounting functions. Keep the core reusable by the Skill, CLI, API, and
tests.

Before delegating work to an LLM, determine whether the result can be derived
deterministically from structured data. If yes, Python MUST produce it; if no,
the LLM may perform bounded semantic judgment. LLMs must never recompute or
override deterministic financial results already produced by Python.

Deterministic financial calculations belong to Python. LLM must never silently
override deterministic engine outputs. Luna-family usage supports exactly one
target: `LUNA_MAX`. Model changes must not alter portfolio risk authority. Do
not store private chain-of-thought.

Luna-family usage has exactly one supported target: `LUNA_MAX`. Do not store
chain-of-thought, private scratchpads, or hidden reasoning in runtime state.
Model routing must not alter the portfolio allocation, risk, rebalance, or
execution authority held by Python.

LLM/Agent work may research evidence, interpret market structure, assess
fundamentals/governance/security, assign bounded factor judgments, explain
decisions, and identify uncertainty. Code must perform valuation, weights,
cash-flow-adjusted NAV/returns/drawdown, benchmark math, weighted scoring and
renormalization, risk/target/rebalance constraints, stable floors, tranche
sums, schema validation, freshness checks, and history calculations.

## Canonical policy and classification

Configurable values have one machine-readable source, preferably
`config/policy.json` or a typed equivalent. When changing policy: update the
canonical config, dependent documentation, tests, and schemas as needed. Do
not leave conflicting defaults elsewhere.

Classification follows:

```text
canonical policy -> resolved configuration -> asset classification
```

Snapshot `asset_type` values are validated hints only. Reject conflicts; do
not let one asset be core in one subsystem and satellite in another.

## Accounting and benchmarks

Portfolio accounting is high-integrity logic. Never treat raw balance changes
as investment performance. Deposits and withdrawals use a cash-flow-adjusted
method such as unitized NAV. In this v1 convention, a flow attached to a
snapshot occurs immediately before that snapshot valuation:

```text
pre_flow_value = snapshot_value - flow
pre_flow_nav   = pre_flow_value / existing_units
units_added    = flow / pre_flow_nav
new_units      = existing_units + units_added
new_nav        = snapshot_value / new_units
```

The initial snapshot must have zero external flow. Require positive finite
pre-flow value, units, and NAV. Preserve timestamped flow events or remove the
timestamped API; never expose an ignored timestamp.

Portfolio and benchmark evaluation periods and flow timing must match. The
primary benchmark is 100% BTC buy-and-hold. The secondary benchmark is 70/30
BTC/ETH buy-and-hold, with each external contribution/withdrawal allocated
70/30 at its event. Do not silently use a daily-rebalanced methodology.

Held-asset return data must be complete; never renormalize remaining portfolio
weights when a held return is missing. Portfolio weighted-return functions
require weights to sum to 1 unless a separately named partial-exposure API is
explicitly provided.

Use decimal fractions for weights/returns, negative fractions for drawdown,
positive magnitude for risk budget, scores from 0–100, and non-negative USD
values. Do not mix percentage points and fractions without conversion.

## Scoring, regimes, and allocation

The score covers trend, valuation, fundamentals, on-chain activity, capital
flows, BTC-relative strength, and event/risk adjustment. It is an input to
portfolio construction, never a direct `score > X -> buy` signal. Apply
confidence, regime, valuation/entry conditions, risk tier, concentration,
BTC opportunity cost, drawdown capacity, stable constraints, and rebalance
thresholds.

For non-critical missing factors, remove the factor, renormalize configured
weights, and reduce confidence. Never fabricate data. Critical missing price,
trend history, portfolio valuation, or unresolved material security status
blocks high-conviction entries. Unknown factor keys are errors. Confidence
must reflect weighted data coverage; poor coverage cannot be raised by a
user-supplied base confidence.

Altcoins must prove their BTC-relative risk/reward case. Missing critical
BTC-relative comparison is `HOLD_ONLY`: preserve existing exposure where
appropriate, but do not add risk or force an exit solely for temporary missing
data. A broken thesis, severe event, or materially negative BTC-relative case
is ineligible.

Target allocation is deterministic and portfolio-level:

```text
regime -> score -> confidence -> risk tier -> volatility/correlation
       -> risk budget -> target -> rebalance threshold -> action
```

Always validate target sum, stable floor, regime stable target, satellite
envelope, and single-asset concentration. A high-scoring asset cannot bypass
caps. Satellite size must increase monotonically with score strength and
confidence, and decrease with risk tier and worsening regime; minimum-score
or low-confidence satellites receive no new risk.

Risk regimes are `NORMAL`, `DEFENSIVE`, and `CAPITAL_PRESERVATION`. Do not
switch on one noisy indicator; use trend, volatility/drawdown, liquidity/flows,
breadth/relative strength, and material events. Severe events may override
confirmation. Drawdown floors from `references/risk-model.md` are mandatory:
with `D` as the positive risk budget, `<= -0.60D` is at least defensive,
`<= -0.80D` is capital preservation, and `< -D` is a breach. Worsening
drawdown/regime must never produce a less defensive result.

Stablecoins and cash are one sleeve. Allocation preserves existing safe
composition, scales it to the sleeve target, and uses the configured/default
settlement asset only when no stable asset exists. Never create
stablecoin-to-stablecoin trades merely to select a symbol. The risk gate and
allocation engine both require stable exposure of at least
`max(global_floor, regime_target)`. Treat a configured `core_risky_min` as a
hard constraint unless its name and documentation explicitly change.

## Rebalancing and execution plans

Use post-new-cash economic dollars, not pre-deposit percentages:

```text
current_amount = current_weight * existing_value
post_total     = existing_value + new_cash
target_amount  = target_weight * post_total
```

Undeployed cash belongs to the stable sleeve. Use thresholds of normally HOLD
below 3 percentage points, WATCH from 3–5, eligible above 5, and high priority
above 10. Prefer new cash before forced selling when the thesis remains sound;
do not preserve a failed thesis. Do not trade merely to make weights exact.

Every executable `INCREASE`, `REDUCE`, or `EXIT` has a strictly positive
`amount_usd`. `HOLD`, `WAIT`, and `NO_TRADE` have zero executable amount.
Reconcile new cash, executable sales, purchases, and residual stable change;
do not create or destroy unexplained dollars. Execution plans use structural
zones and explicit tranche fractions; no false precision or mechanical
percentage ladders. Tranche fractions must sum to 1.

## History, evidence, and persistence

History is append-only. Never rewrite prior rationales. Recommended decision
statuses are `PENDING`, `CONFIRMED`, and `NOT_EXECUTED`; execution requires
explicit confirmation or a trusted later read-only snapshot. Status changes
are append-only events, not edits to old JSONL lines.

Before a new decision, load available snapshots and decisions, calculate
cash-flow-aware NAV/drawdown, inspect prior targets/actions/thesis/scores and
execution status, then fetch current evidence. Review types are
`SNAPSHOT_REVIEW`, `FULL_REVIEW`, and `EVENT_REVIEW`; a full review is due when
at least 14 days have passed since the last full review, while a material event
may trigger an event review immediately. History informs but does not override
current evidence.

Evidence judgments must retain ID, asset, factor, source, observed/fetched
timestamps, freshness, confidence, and enough value/summary to explain the
score. Persist complete Evidence records in decisions; derive `evidence_ids`.
Every factor evidence ID must exist in that decision and match its asset and
factor. Dangling or duplicate references are invalid.

Persist `snapshot_id`, `decision_id`, `based_on_snapshot_id` when applicable,
policy version, and a deterministic canonical policy SHA-256 hash. Persist the
resolved policy (or another exact historical-policy artifact) so a decision is
reproducible after config files change. Do not trust a persisted state digest
or policy version without validating it against the resolved policy.

Persisted timestamps are timezone-aware RFC3339 normalized to UTC and compared
as datetimes, never raw strings. Date-only compatibility input must be
explicitly normalized or rejected; ambiguous timestamps must not be persisted.

Runtime portfolio data belongs outside Git, by default
`~/.local/share/crypto-portfolio-manager/` or an explicitly configured local
directory. Repository `data/` contains only fake fixtures, tests, or
`.gitkeep`. Never commit quantities, balances, cost basis, account IDs,
history, credentials, private keys, seeds, tokens, or cookies.

## Models, schemas, providers, and state

Typed models are the source of validation; generated or clearly aligned JSON
Schemas must match them. When changing a schema, update the model, validation,
fixtures, tests, and user documentation as needed. Keep input, persistent
record, and normalized-output contracts distinct. Persisting a mapping means
parsing it through the canonical model first; invalid records never reach
JSONL. JSONL writes should remain small, append-only, locked, flushed, fsynced,
and able to report incomplete lines.

Provider interfaces normalize data before it reaches the engine. The engine
must not depend on whether data came from Binance, Coinbase, CoinGecko, or
another approved source; preserve provenance. Exchange integrations start with
read-only balances, positions, and history, using adapters and canonical
models. Never leak exchange-specific representations into the engine.

Staking is secondary. Do not hold an unattractive asset solely for APY. Assess
inflation, validator/slashing, contracts, custody, lockups/delays, liquid
staking depeg, liquidity, and counterparty risk.

## Engineering discipline

Before editing, inspect implementation, callers, tests, policy/reference docs,
and the current invariant. Fix shared root causes, not only named call sites.
Implement the smallest coherent change. Do not redesign unrelated modules,
add services, trading execution, UI, leverage, or speculative indicators.
Prefer small typed deterministic pure functions, standard library, existing
helpers, and dependency-light code. Avoid hidden global state, magic values,
duplicate policy, premature microservices, and heavyweight dependencies.

Preserve formats and public behavior when practical. Breaking changes must
identify affected schemas/history/CLI/Skill behavior and provide migration or
version handling. Invalid financial inputs generally fail clearly: NaN,
Infinity, negative quantities/values, unknown policy fields, overlapping asset
groups, invalid percentages, and duplicate symbols without explicit semantics.

The CLI is a thin orchestrator over models/engine/providers/state. `SKILL.md`
orchestrates workflow and points to canonical references; it is not a second
portfolio engine. Keep README installation/overview, references methodology,
config policy, and this file coding constraints concise and non-duplicative.

## Tests and definition of done

Every behavioral change needs a regression test. Maintain coverage for default,
custom, invalid and overlapping policy; classification; stable floors;
drawdown; cash-flow-adjusted performance; scoring/coverage/unknown factors;
return completeness; benchmark alignment/methodology; target/tranche sums;
asset hints and `thesis_broken`; NaN/Infinity/negative/zero values; state
validation; policy hashes; evidence references; schemas; and history context.
Prefer standard-library deterministic invariant loops; do not add Hypothesis
without clear need.

Run the complete suite before finishing:

```bash
python -m unittest discover -s tests -v
```

Also run configured checks such as:

```bash
ruff check .
python -m compileall crypto_portfolio scripts
```

Before finalizing, confirm implementation scope, tests, financial invariants,
configuration/schema consistency, no real data/secrets, no unrelated changes,
and matching user documentation. For high-impact financial changes report
Before / After / Why / Risk / Tests. Prioritize cash-flow-aware ledger,
deterministic allocation, canonical policy, evidence, models, CI/regressions,
providers, read-only integrations, interfaces, then secondary analytics.

When a simpler deterministic implementation is sufficient, choose the one
that is easiest to verify, reproduce, explain, and maintain.
