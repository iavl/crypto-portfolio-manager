# AGENTS.md

This repository implements a conservative-balanced, medium-term crypto
portfolio management system and Agent Skill. It analyzes allocation, market
conditions, risk, rebalancing, staged entries/exits, history, and benchmarks
over an approximately 3–6 month active-allocation horizon. It is not a short-term trading bot.

## Non-negotiable investment policy

The default strategy is:

- spot-only, medium-term, conservative-balanced;
- BTC is the primary benchmark; BTC and ETH are default core holdings;
- selected large-cap assets may be satellites;
- stablecoin/cash stays at or above the configured minimum;
- the default portfolio drawdown risk budget is approximately 15%;
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
.agents/skills/crypto-portfolio-manager/SKILL.md
               repository-scoped agent workflow/orchestration
references/    human-readable investment and decision policy
config/        canonical machine-readable policy
providers/     normalized market/factual data
models/        typed domain contracts
engine/        deterministic calculations
state/         history, cash flows, decisions, ledger
cli/           thin optional interface
```

The Git repository is the single source of truth. The Repository Skill contains
orchestration instructions only and must use config, references, schemas,
Python packages, scripts, tests, and other resources directly from the same
repository working tree. Do not introduce a copied Skill payload or maintain
duplicate runtime code under a Skill installation directory. Repository-
relative paths are resolved from Git `REPO_ROOT`, not from the physical
directory containing `SKILL.md`.

Do not put deterministic mathematics in prompts or subjective interpretation
in accounting functions. Keep the core reusable by the Skill, CLI, API, and
tests.

Before delegating work to an LLM, determine whether the result can be derived
deterministically from structured data. If yes, Python MUST produce it; if no,
the LLM may perform bounded semantic judgment. LLMs must never recompute or
override deterministic financial results already produced by Python.

Deterministic financial calculations belong to Python. LLM must never silently
override deterministic engine outputs. Model selection and reasoning settings
belong entirely to the current Agent Skills host; this repository must inherit
them and must not switch them. Do not introduce model-specific
routing/configuration without an explicit future product requirement. Do not
store private chain-of-thought, private scratchpads, or hidden reasoning in
runtime state.

LLM/Agent work may research evidence, interpret market structure, assess
fundamentals/governance/security, assign bounded factor judgments, explain
decisions, and identify uncertainty. Code must perform valuation, weights,
cash-flow-adjusted NAV/returns/drawdown, benchmark math, reliability-aware
weighted scoring, risk/target/rebalance constraints, stable floors, tranche
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
method such as unitized NAV. In this convention, a flow attached to a
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

The base score covers exactly trend, valuation, fundamentals, on-chain
activity, capital flows, and BTC-relative strength. Event/security risk is a
separate evidence-backed gate; positioning and BTC-cycle context are overlays.
The score is an input to portfolio construction, never a direct
`score > X -> buy` signal. Apply confidence, regime, valuation/entry
conditions, risk tier, concentration, BTC opportunity cost, drawdown capacity,
stable constraints, and rebalance thresholds.

The current policy adds ETH-specific monetary, staking, L2/DA, realized-valuation, and
normalized ETF-flow evidence without changing the six top-level factor weights.
ETH core classification is not an allocation entitlement and must not restore
the old `core_min_score` score floor. The configurable 70/30 BTC/ETH core-sleeve
anchor is a prior, not a floor. Missing ETH/BTC evidence blocks new ETH
increases; low confidence cannot create a high-conviction ETH increase; ETH/BTC
opportunity cost changes core sizing but not the base score.

Ethereum L2 activity is bullish ETH evidence only when Ethereum settlement or
DA value capture is demonstrated. Monetary, staking, supply, ratio, and
allocation arithmetic is Python-owned. Do not scrape unstable dashboards for
deterministic structural-risk values; structural risk remains non-scoring.

In the current scoring model, `MISSING` factors remain at their configured weight and shrink their
raw score toward neutral 50 according to deterministic reliability; do not
renormalize missing factors. `NOT_APPLICABLE` is defined by a zero-weight
profile factor. Only the latest policy and data contracts are supported.
Never fabricate data. Critical missing price,
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
below 2 percentage points, WATCH from 2–4, eligible above 4, and high priority
above 8. Prefer new cash before forced selling when the thesis remains sound;
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

Persist `snapshot_id`, `decision_id`, and `based_on_snapshot_id` when applicable,
plus a deterministic canonical policy SHA-256 hash and the exact resolved policy.
This preserves reproducibility after config files change; always validate the
hash against the resolved policy.

Persisted timestamps are timezone-aware RFC3339 normalized to UTC and compared
as datetimes, never raw strings. Date-only input must be rejected at domain boundaries; ambiguous timestamps must not be persisted.

If the user does not disclose an external cash flow, normalize the snapshot to
`ASSUMED_NONE / 0 / NONE` and treat valuation change as market performance.
Explicit unresolved flow details remain `UNRESOLVED / PROVISIONAL`; explicit
resolutions and baseline resets are append-only.

Runtime portfolio data belongs outside Git, by default
`~/.local/share/crypto-portfolio-manager/` or an explicitly configured local
directory. Repository `data/` contains only fake fixtures, tests, or
`.gitkeep`. Never commit quantities, balances, cost basis, account IDs,
history, credentials, private keys, seeds, tokens, or cookies.

## Models, schemas, providers, and state

Provider reliability is fail-closed and deterministic: preserve structured
transport/upstream/cache diagnostics through fallback, never convert a
provider failure into zero or normal data, and keep live network checks
separate from the offline unit suite. TLS verification must remain enabled;
credentials, cookies, raw headers, and sensitive response bodies must never
enter fixtures, caches, logs, or reports.

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

A metric without a downstream scoring, risk, regime, allocation, cycle,
positioning, or execution consumer does not belong in the registry. Do not
retain provider integrations solely for report enrichment.

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

### Maintainability first

Treat maintainability as a required engineering property, not an optional
cleanup goal. New code should be easy for a future maintainer or agent to
understand, verify, test, and change without reconstructing hidden assumptions.

Prefer:

- clear responsibility boundaries and single-purpose modules/functions;
- descriptive names that reflect portfolio-domain meaning;
- explicit typed inputs/outputs and narrow interfaces;
- existing shared abstractions when they are already the canonical path;
- small reusable helpers when they remove real duplication;
- deterministic control flow over clever or implicit behavior;
- comments that explain non-obvious financial invariants or design reasons, not
  comments that merely restate the code;
- localized changes with reviewable diffs;
- regression tests that document intended behavior.

Avoid:

- copy/paste implementations of policy or accounting logic;
- parallel sources of truth;
- one-off special cases when a general existing invariant should be fixed;
- large functions that mix validation, calculation, persistence, I/O, and LLM
  orchestration;
- deeply nested conditionals when clearer decomposition is practical;
- hidden coupling through globals, environment state, mutable singletons, or
  undocumented side effects;
- speculative abstractions created for hypothetical future requirements;
- broad refactors bundled with unrelated behavioral changes.

When touching an existing subsystem, follow its established architecture and
conventions unless there is a concrete maintainability, correctness, or safety
reason to change them. Prefer incremental improvement over architectural churn.

### Clarify material uncertainty before coding

If an implementation choice is materially ambiguous and the repository does not
provide a clear authoritative answer, ask the user before making the change.
Do not silently choose a consequential interpretation merely to keep the task
moving.

Ask for confirmation before coding when uncertainty could materially affect any
of the following:

- portfolio policy, risk constraints, or investment semantics;
- accounting or benchmark methodology;
- public APIs, CLI behavior, Skill behavior, or provider contracts;
- persistent state, schemas, history compatibility, or migrations;
- canonical configuration ownership or source-of-truth placement;
- architecture or responsibility boundaries between modules;
- dependency additions, removals, or major upgrades;
- security/privacy boundaries or any path toward real-money execution;
- destructive changes, data loss, or backward-incompatible behavior;
- a design trade-off where multiple plausible choices have materially different
  maintenance or correctness consequences.

Do not interrupt the user for low-risk implementation details when the codebase
already establishes a clear convention. In those cases, choose the most
conservative option that is easiest to verify, test, explain, and maintain.

If a task is partially specified but a safe, reversible, low-risk portion is
unambiguous, that portion may be implemented while the materially uncertain part
is surfaced for confirmation. Never guess through uncertainty that could change
financial behavior or persistent data semantics.

Keep only current contracts; do not add backward-compatibility branches or
legacy replay. Breaking internal changes may require regenerating local state;
never silently upgrade or rewrite old records.
Breaking changes must identify affected schemas/history/CLI/Skill behavior. Invalid financial inputs generally fail clearly: NaN,
Infinity, negative quantities/values, unknown policy fields, overlapping asset
groups, invalid percentages, and duplicate symbols without explicit semantics.

The CLI is a thin orchestrator over models/engine/providers/state. `SKILL.md`
orchestrates workflow and points to canonical references; it is not a second
portfolio engine. Keep README installation/overview, references methodology,
config policy, and this file coding constraints concise and non-duplicative.

## Current-contract-only development

This repository intentionally supports only the current internal contract.
Do not add:

- backward-compatible schema readers;
- internal version dispatch;
- deprecated field aliases;
- legacy field adapters;
- migration shims or migrations;
- dual old/new serialization.

When an internal contract changes, update all producers, consumers, schemas,
tests, fixtures, and docs in the same change. Old generated runtime state may
be regenerated instead of migrated, and application code must not silently
delete user state.

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
that is easiest to verify, reproduce, explain, test, and maintain. Prefer
boring, explicit, well-factored code over cleverness. If two approaches are
otherwise equivalent, choose the one with fewer hidden assumptions, clearer
ownership, and lower long-term maintenance cost.


## Git commits and attribution

- Preserve the repository user's existing Git author and committer identity.
- Do not modify `git config user.name` or `git config user.email`.
- Do not use `--author` to change commit authorship to Codex.
- Every commit created by Codex MUST include the following Git trailer:

  `Co-authored-by: Codex <codex@openai.com>`

- Add the trailer exactly once, separated from the commit body by a blank line.
- If the commit already contains this Codex co-author trailer, do not add a duplicate.
- When amending a commit created by Codex, preserve the trailer.

## Commit message language

- Write every Git commit subject and body in English, regardless of the
  language used in conversation or UI. Keep the existing conventional-commit
  style (`fix:`, `feat:`, `refactor:`, `chore:`).
