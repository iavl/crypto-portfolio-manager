# Development and Provider Debugging

## Purpose

Provider diagnostics is deterministic infrastructure executed by Python. It
explains configuration, transport, upstream contracts, normalization,
fallback, and cache state; it never changes portfolio strategy, scoring,
allocation, risk, or execution permissions.

## Quick Troubleshooting Flow

```bash
python3 scripts/providers.py --status
python3 scripts/providers.py --doctor <provider>
python3 scripts/providers.py --probe <provider> --asset ETH
python3 scripts/providers.py --metric market.spot_price
python3 scripts/providers.py --contract <provider>
python3 scripts/providers.py --smoke ETH --metric market.spot_price
```

| Command | Question it answers | Network? |
|---|---|---|
| `--status` | Are config, adapters, and credentials ready? | No |
| `--doctor` | Which readiness/transport/schema layer failed? | Ready providers go online |
| `--probe` | Does the provider's minimal adapter request work? | Yes |
| `--metric` | Which deterministic provider chain is configured for the metric? | No |
| `--contract` | Does the minimal upstream contract still normalize? | Yes |
| `--smoke` | Does real metric routing work end to end? | Yes; uses `REFRESH` |

`--status` is an offline readiness check. `--probe`, `--doctor`, `--contract`,
and `--smoke` are explicit online operations. Failures that were actually
tested return exit code `2`; providers skipped within an `all` operation do
not fail the command. `scripts/run_with_debug.py` keeps exit code `0` suited
to the reporting flow while recording a child exit code of `2` as `FAILED`.

## OHLCV Freshness and Cash-Flow Checks

The latest completed daily candle uses one unified definition:
`expected = as_of UTC date - 1 day`, `lag_days = expected - actual`, with
thresholds taken only from the policy's
`execution.maximum_daily_candle_lag_days`. The technical snapshot's
`ohlcv_metadata` reports `expected_latest_completed_date`,
`actual_latest_completed_date` (`latest_completed_candle_date`),
`observation_lag_days`, `maximum_daily_candle_lag_days`,
`daily_candle_status` (`CURRENT`/`STALE`), `latest_candle_timestamp`, and
`ohlcv_hash`. OHLCV caches check tail integrity: when cached content is
missing the latest completed daily candle it is refreshed rather than reused
just because the fetch time was recent.

Unresolved historical cash flows block NAV/benchmark/drawdown finality.
Read-only check commands:

```bash
python3 scripts/cash_flow_resolutions.py list-unresolved
python3 scripts/cash_flow_resolutions.py validate
```

`list-unresolved` prints snapshot ids and timestamps still in `UNRESOLVED`;
for the explicit resolution commands see section 6 of `docs/USAGE.md`.

## growthepie L2 TVS Targeted Verification

To verify `ETH:eth.l2.tvs_usd`, use the current growthepie bulk TVL contract:

```bash
python3 scripts/providers.py --metric eth.l2.tvs_usd --asset ETH

python3 scripts/providers.py \
  --doctor growthepie \
  --asset ETH

python3 scripts/providers.py \
  --probe growthepie \
  --asset ETH

python3 scripts/providers.py \
  --contract growthepie \
  --asset ETH

python3 scripts/providers.py \
  --smoke ETH \
  --metric eth.l2.tvs_usd
```

A healthy result must satisfy: growthepie needs no credential; TVS uses
`master.json + export/tvl.json`; the normalized result has
`source=growthepie`; `observed_at` is a completed UTC date; the chain count
is non-zero; growthepie is the current L2 TVS/activity route. Missing chain
data must never be treated as zero, and DeFiLlama protocol TVL is not a
substitute for this L2 TVS metric.

## Provider Architecture

```text
metric request
    -> provider route
    -> ProviderRouter
    -> cache (AUTO/CACHE_ONLY) or HttpClient (REFRESH)
    -> normalized observation
    -> ProviderAttempt telemetry
    -> deterministic fallback
    -> unresolved evidence when all providers fail
```

`HttpClient` owns verified TLS, bounded retries, response size limits, safe
redaction, and transport classification. The router owns request budgets,
cache behavior, fallback, and the in-process provider circuit breaker.

## Event-Only Acquisition

Event metrics bypass the ordinary provider router. They use the fixed source
catalog and a structured transport to fetch bounded candidates first, then
apply a materiality judgment by the host or an explicitly configured
classifier; unclassified candidates stay `CLASSIFICATION_PENDING` and are
never treated as `CLEAR`.

```bash
python3 scripts/events.py --plan --asset BTC --asset ETH
python3 scripts/events.py --fetch --asset BTC --asset ETH --output pending-events.json
python3 scripts/events.py --resolve classified-events.json --asset BTC --asset ETH
python3 scripts/events.py --smoke --asset BTC --asset ETH
```

`--fetch` only fetches candidates; it never scores or produces portfolio
decisions. The host-assisted flow should keep `pending_responses`, fill in a
classification per candidate under `responses`, then run `--resolve`; the
program rejects unknown fields and changes to source/timestamp/URL/candidate
identity. `--smoke` shows only event statuses, coverage, confidence, candidate
counts, and sanitized diagnostics.

The current structured source mapping is: Ethereum Foundation security uses
the Blog RSS, Aave security uses the Aave Risk Discourse JSON, and ESMA/MiCA
uses the ESMA RSS. Governance proposals are no longer collected
automatically; supply material governance information manually via
`ManualAssetContext`. GitHub commits use the request's
`lookback_start`/`as_of` as `since`/`until`; Discourse follows only
same-origin `more_topics_url` and stays incomplete at the bounded pagination
limit. `NO_STRUCTURED_TRANSPORT` means a fixed source has no supported
structured endpoint configured, distinct from `DNS_RESOLUTION_FAILED`,
`TLS_*`, `HTTP_*`, and `PROVIDER_TRANSPORT_ERROR`.

Event stage statuses and error boundaries:

```text
FETCHED -> CLASSIFICATION_PENDING -> CLASSIFIED -> SUCCESS
FETCH_FAILED | CLASSIFICATION_FAILED | INSUFFICIENT_SOURCE_COVERAGE | CONFLICT
```

`CACHE_ONLY` reads only the event transport cache; a cache miss stays
unreachable. It never issues HTTP or calls an external classifier. When
semantic classification is needed, the Agent in the current host processes
`pending_responses` and hands structured results back via `--resolve`.
Missing or failed classification stays pending and never yields `CLEAR`.
Without a `GITHUB_TOKEN`, the GitHub source's smoke output must clearly say
it is running in unauthenticated mode.

## Decision Tree

```text
NOT_READY
  -> configuration / adapter / credential

DNS / CONNECT_TIMEOUT / READ_TIMEOUT / TLS / PROXY
  -> local transport environment or transient network failure

HTTP_401 / HTTP_403_AUTH
  -> credential or authentication contract

HTTP_403_ACCESS_DENIED / HTTP_403_REGION_RESTRICTED / HTTP_403_WAF
  -> endpoint or provider access policy

HTTP_403_RATE_LIMIT / HTTP_429
  -> rate limit; bounded retries may apply

HTTP_5XX / CONNECTION_RESET
  -> transient upstream/network failure

HTTP_402 / ENTITLEMENT_REQUIRED / PROVIDER_PLAN_RESTRICTED
  -> subscription or provider entitlement missing; only explicitly
     optional/premium metrics may be SKIPPED

RATED_SUBSCRIPTION_INACTIVE
  -> Rated returned 401 Subscription is not active; affected staking metrics
     are optional SKIPPED, provider attempts keep diagnostics

RATE_LIMITED / HTTP_429
  -> provider rate limit; kept retryable, never filled with zero or treated
     as success

UNAVAILABLE_BY_METHODOLOGY
  -> inputs cannot support the required computation methodology; not a
     transport failure

INVALID_JSON / PROVIDER_SCHEMA_ERROR / PROVIDER_SCHEMA_CHANGED
  -> upstream contract or normalization regression

CACHE_MISS / CACHE_EXPIRED / CACHE_CORRUPT
  -> local cache state

CIRCUIT_OPEN / REQUEST_BUDGET_EXHAUSTED
  -> local safety gates; inspect earlier root-cause attempts
```

An ordinary 403 is classified into a specific code only when safe response
metadata clearly indicates authentication, rate limiting, region restriction,
access denial, or WAF; otherwise it is `HTTP_403_UNKNOWN`. Raw HTML challenge
pages and response bodies are never written into reports.

## Error Code Reference

Transport error codes include `DNS_RESOLUTION_FAILED`, `CONNECT_TIMEOUT`,
`READ_TIMEOUT`, `CONNECTION_REFUSED`, `CONNECTION_RESET`, `TLS_*`, and
`PROXY_ERROR`. HTTP error codes include `HTTP_400`, `HTTP_401`, `HTTP_402`,
the classified `HTTP_403_*` family, `HTTP_404`, `HTTP_429`, and `HTTP_5XX`.

Provider error codes include `PROVIDER_PLAN_RESTRICTED`,
`RATED_SUBSCRIPTION_INACTIVE`, `ENTITLEMENT_REQUIRED`, `RATE_LIMITED`,
`UNAVAILABLE_BY_METHODOLOGY`, `PROVIDER_INSUFFICIENT_HISTORY`,
`PROVIDER_UNSUPPORTED`, `PROVIDER_NOT_APPLICABLE`, `PROVIDER_SCHEMA_ERROR`,
`PROVIDER_SCHEMA_CHANGED`, `SOURCE_METHOD_MISMATCH`,
`DERIVED_INPUT_UNAVAILABLE`, and `QUERY_BUDGET_EXCEEDED`. Cache and local
safety error codes include `CACHE_MISS`, `CACHE_EXPIRED`, `CACHE_CORRUPT`,
`CIRCUIT_OPEN`, and `REQUEST_BUDGET_EXHAUSTED`.

## Configuration and Credentials

`--status` separately shows configuration, adapter registration, credential
presence, and runtime readiness. Credentials are supplied only through
environment variables. Never write API keys into `config/data-providers.json`,
request recordings, cache files, or logs.

```bash
export COINGECKO_API_KEY='...'
python3 scripts/providers.py --status
python3 scripts/providers.py --doctor coingecko
```

A credential being present does not mean the provider is healthy: DNS, TLS,
plan permissions, or upstream normalization may still be unavailable.

## Why is Decision Confidence LOW?

Decision Confidence is action-scoped. Inspect the persisted component breakdown
before changing thresholds:

```bash
python3 scripts/confidence.py --json
python3 scripts/confidence.py --explain --json
```

The JSON contains `scope`, component scores, hard `caps`, one-time
`soft_penalties`, `why_low`, and the bounded conditions that can increase
confidence. A low-confidence watchlist asset with zero exposure should not
appear in a HOLD scope. Conversely, unknown target security or chain liveness
must remain an action blocker even if other components are HIGH.

## Proxy and TLS Troubleshooting

The client requires certificate and hostname verification. If your local
trust store is incomplete, supply a trusted CA bundle:

```bash
export CRYPTO_PORTFOLIO_CA_BUNDLE=/path/to/trusted-ca-bundle.pem
python3 scripts/providers.py --doctor binance
```

When Python/OpenSSL has no default CA path, the client prefers the installed
`certifi` CA bundle; if the environment has no `certifi`, it falls back to
macOS's `/etc/ssl/cert.pem` (when present). You can also point to a trusted
bundle explicitly with `CRYPTO_PORTFOLIO_CA_BUNDLE`.

Never use `verify=False`, unverified SSL contexts, or `curl -k`. A TLS
failure means evidence is unavailable — not that a chain or provider has
stopped.

## Rate Limits and Retries

`HTTP_429` and limited `HTTP_403_RATE_LIMIT` responses are retried only for
idempotent requests. Retry delays honor `Retry-After`; otherwise bounded
exponential full jitter is used. Permanent 4xx, plan, unsupported, and schema
errors fail fast.

After three retryable failures for the same provider, the router opens that
provider's in-process circuit breaker for 60 seconds. While open, it emits
`CIRCUIT_OPEN` and proceeds with configured fallback, never erasing the
original failure cause.

## Cache and Fallback Debugging

Use `CACHE_ONLY` to prove the flow works offline. `REFRESH` bypasses the
mutable-response cache. Missing, expired, corrupt, or unsupported data stays
unavailable and is never converted to zero. Provider attempts keep provider,
dataset, asset, metric keys, request hash, network request count, endpoint,
method, error code, retryability, status, and safe detail.

## Record / Replay

Temporary recordings of public responses may live in Git-ignored paths:

```bash
python3 scripts/providers.py --probe binance --record
python3 scripts/providers.py --replay .data/provider-recordings/<file>.json
```

A recording contains only version, provider, method, sanitized endpoint,
timestamp, status, and the sanitized JSON response. Request headers, cookies,
authorization values, query credentials, and raw response bodies are never
serialized. The default recorder skips credentialed requests. Only place
sanitized public fixtures into `tests/fixtures/providers/` when they are safe
and genuinely help parser regression tests.

Offline replay tests use the existing `HttpClient` and provider adapters with
a `ReplayTransport`. Replay requests must match the recording's method and
sanitized endpoint; mismatches fail rather than silently returning a wrong
payload.

## Offline, Contract, and Smoke Tests

Ordinary unit tests must never touch the internet:

```bash
python3 -m unittest discover -s tests -v
```

Live contract checks must be run explicitly and keep requests minimal:

```bash
python3 scripts/providers.py --contract coingecko
python3 scripts/providers.py --contract all
```

Missing optional credentials are marked `SKIPPED`/`NOT_READY`; a supplied-but-
rejected credential, schema change, or normalization failure is a failure.
Smoke checks use the production router and `REFRESH`:

```bash
python3 scripts/providers.py --smoke ETH --metric market.spot_price
```

Results report the chain, attempts, the selected provider, network request
count, cache hits, fallbacks, final status, and unresolved diagnostics; raw
market payloads are never printed.

## Debug Report Integration

To include live or contract command results in a report, use the wrapper:

```bash
python3 scripts/run_with_debug.py \
  --script scripts/providers.py -- \
  python3 scripts/providers.py --probe coingecko --asset ETH
```

The wrapper returns a JSON record suitable for the report. A non-zero child
exit is recorded as `FAILED`; timeouts and launch exceptions are recorded as
`TIMEOUT` and `LAUNCH_FAILED` respectively. Logs are length-bounded and
sanitized. The wrapper itself may return `0` so report collection continues.

## CI Provider Checks

`.github/workflows/provider-contracts.yml` is separate from the
deterministic unit-test CI and is triggered by schedule or manually. Public
checks need no secrets. Credentialed checks run only when the corresponding
GitHub secret exists; a missing credential is reported as skipped. CI
distinguishes transient network, rate limit, authentication, plan, schema,
and normalization failures.

## Adding a Provider

A new provider is complete only when it includes all of:

```text
adapter, config, capabilities, route, readiness/doctor behavior,
offline fixture tests, a minimal contract check, deterministic error mapping,
secret redaction, and documentation.
```

Never add trading keys, order placement, leverage, or autonomous execution
capabilities.

Read-only contract probes added/fixed by this effort:

```bash
python3 scripts/providers.py --probe coinmetrics_community --asset BTC
python3 scripts/providers.py --probe coinmetrics_community --asset ETH
python3 scripts/providers.py --probe coinmetrics_community --asset BNB
python3 scripts/providers.py --probe bgeometrics --asset BTC
python3 scripts/providers.py --probe lunarcrush --asset BTC
python3 scripts/providers.py --probe rated --asset ETH
python3 scripts/providers.py --probe ethereum_beacon --asset ETH
python3 scripts/providers.py --probe etherscan --asset ETH
```

Coin Metrics output shows the real 1D catalog per asset; LunarCrush uses an
API v4 Bearer credential, shares one time-series request across the same
asset's social metrics, and records 402/429/circuit as
`ENTITLEMENT_REQUIRED`/`RATE_LIMITED`/`CIRCUIT_OPEN` respectively. A Rated
401 body `{"detail":"Subscription is not active."}` is recorded as
`RATED_SUBSCRIPTION_INACTIVE`, and the corresponding optional staking metric
normalizes to `SKIPPED`; provider attempts still keep diagnostics. The
ordinary metric plan does not request LunarCrush by default; an explicit
`--probe` still keeps real error diagnostics. The Ethereum protocol probe
reads only one latest block. Missing numeric history stays
`PROVIDER_INSUFFICIENT_HISTORY` and never generates web requests.

BGeometrics needs no key; LunarCrush needs `LUNARCRUSH_API_KEY`; Rated needs
`RATED_API_KEY`; Beacon uses `ETH_BEACON_API_URL`, and the Ethereum protocol
uses `ETHEREUM_RPC_URL` or the default PublicNode address. When these
optional settings are missing, `--status`/`--doctor` show a not-ready state
and never turn a provider failure into a zero value.

The report debug drawer presents four categories:
`decision_blocking_failures`, `required_scoring_failures`,
`optional_data_unavailable`, and `provider_operational_failures`. Only the
first two enter decision semantics; optional/context gaps stay out of the
applicable scoring denominator.

## Pre-Merge Checklist

- [ ] Deterministic unit tests stay offline;
- [ ] provider failures keep safe root-cause diagnostics;
- [ ] 403 and timeout stages classify correctly;
- [ ] TLS verification stays enabled;
- [ ] no credentials in fixtures, caches, logs, or reports;
- [ ] fallback and circuit behavior has test coverage;
- [ ] contract/smoke commands are explicit and documented;
- [ ] full tests, Ruff, and compile checks pass.
