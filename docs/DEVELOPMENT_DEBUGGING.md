# Development and Provider Debugging

## Purpose

Provider diagnostics are deterministic Python infrastructure. They explain
configuration, transport, upstream contracts, normalization, fallback, and
cache state without changing portfolio policy, scoring, allocation, or risk
authority.

## Fast diagnostic workflow

```bash
python3 scripts/providers.py --status
python3 scripts/providers.py --doctor <provider>
python3 scripts/providers.py --probe <provider> --asset ETH
python3 scripts/providers.py --metric market.spot_price
python3 scripts/providers.py --contract <provider>
python3 scripts/providers.py --smoke ETH --metric market.spot_price
```

| Command | Answers | Network |
|---|---|---|
| `--status` | Is config, adapter, and credential readiness correct? | No |
| `--doctor` | Which readiness/transport/schema layer failed? | Yes for ready providers |
| `--probe` | Can the provider's minimal adapter request work? | Yes |
| `--metric` | Which deterministic provider chain is configured? | No |
| `--contract` | Does a minimal upstream contract still normalize? | Yes |
| `--smoke` | Does the real metric route work end to end? | Yes; uses `REFRESH` |

`--status` is an offline readiness check. `--probe`, `--doctor`, `--contract`,
and `--smoke` are explicit network operations. A tested failure exits `2`;
an unconfigured provider skipped by an `all` operation does not fail the
command. `scripts/run_with_debug.py` preserves its report-friendly exit `0`
while recording a child exit `2` as `FAILED`.

## Provider architecture

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

`HttpClient` owns verified TLS, bounded retries, response-size limits, safe
redaction, and transport classification. The router owns request budgets,
cache behavior, fallback, and the in-process provider circuit breaker.

## Decision tree

```text
NOT_READY
  -> config / adapter / credential

DNS / CONNECT_TIMEOUT / READ_TIMEOUT / TLS / PROXY
  -> local transport environment or transient network

HTTP_401 / HTTP_403_AUTH
  -> credential or authentication contract

HTTP_403_ACCESS_DENIED / HTTP_403_REGION_RESTRICTED / HTTP_403_WAF
  -> endpoint or provider access policy

HTTP_403_RATE_LIMIT / HTTP_429
  -> rate limit; bounded retry may apply

HTTP_5XX / CONNECTION_RESET
  -> transient upstream/network failure

PROVIDER_PLAN_RESTRICTED
  -> subscription or provider entitlement

INVALID_JSON / PROVIDER_SCHEMA_ERROR / PROVIDER_SCHEMA_CHANGED
  -> upstream contract or normalization regression

CACHE_MISS / CACHE_EXPIRED / CACHE_CORRUPT
  -> local cache state

CIRCUIT_OPEN / REQUEST_BUDGET_EXHAUSTED
  -> local safety gate; inspect the earlier root attempt
```

A plain 403 is `HTTP_403_UNKNOWN` unless safe response metadata identifies
authentication, rate limiting, region restriction, access denial, or WAF.
Raw HTML challenge pages and response bodies are never printed into reports.

## Error-code reference

Transport codes include `DNS_RESOLUTION_FAILED`, `CONNECT_TIMEOUT`,
`READ_TIMEOUT`, `CONNECTION_REFUSED`, `CONNECTION_RESET`, `TLS_*`, and
`PROXY_ERROR`. HTTP codes include `HTTP_400`, `HTTP_401`, the classified
`HTTP_403_*` family, `HTTP_404`, `HTTP_429`, and `HTTP_5XX`.

Provider codes include `PROVIDER_PLAN_RESTRICTED`,
`PROVIDER_INSUFFICIENT_HISTORY`, `PROVIDER_UNSUPPORTED`,
`PROVIDER_NOT_APPLICABLE`, `PROVIDER_SCHEMA_ERROR`, and
`PROVIDER_SCHEMA_CHANGED`. Cache and local safety codes are
`CACHE_MISS`, `CACHE_EXPIRED`, `CACHE_CORRUPT`, `CIRCUIT_OPEN`, and
`REQUEST_BUDGET_EXHAUSTED`.

## Configuration and credentials

`--status` separates configuration, adapter registration, credential
presence, and runtime readiness. Credentials are environment-only. Do not put
API keys in `config/data-providers.json`, request recordings, cache files, or
logs.

```bash
export COINGECKO_API_KEY='...'
python3 scripts/providers.py --status
python3 scripts/providers.py --doctor coingecko
```

Presence is not health: a provider can be configured and credentialed while
DNS, TLS, plan access, or upstream normalization is unavailable.

## Proxy and TLS troubleshooting

The client requires certificate and hostname verification. If the local trust
store is incomplete, provide a trusted bundle:

```bash
export CRYPTO_PORTFOLIO_CA_BUNDLE=/path/to/trusted-ca-bundle.pem
python3 scripts/providers.py --doctor binance
```

Do not use `verify=False`, an unverified SSL context, or `curl -k`. A TLS
failure is unavailable evidence, not proof that a chain or provider is down.

## Rate limits and retries

`HTTP_429` and bounded `HTTP_403_RATE_LIMIT` responses may retry only for
idempotent requests. Retry delays honor `Retry-After`; otherwise exponential
full jitter is used and capped. Permanent 4xx, plan, unsupported, and schema
errors fail quickly.

The router opens a per-provider in-process circuit after three retryable
failures for 60 seconds. An open circuit emits `CIRCUIT_OPEN` and allows the
configured fallback to run; it does not erase the original failure.

## Cache and fallback troubleshooting

Use `CACHE_ONLY` when proving that a workflow is offline. `REFRESH` bypasses
mutable response caches. Missing, stale, corrupt, or unsupported data remains
unavailable; it is never converted to zero. Provider attempts retain the
provider, dataset, asset, metric keys, request hash, network count, endpoint,
method, error code, retryability, status, and safe detail.

## Record / replay

Temporary public response recordings may be staged under the ignored path:

```bash
python3 scripts/providers.py --probe binance --record
python3 scripts/providers.py --replay .data/provider-recordings/<file>.json
```

Recordings contain only a version, provider, method, redacted endpoint,
timestamp, status, and redacted JSON response. Request headers, cookies,
authorization values, query credentials, and raw response bodies are not
serialized. Credential-bearing requests are skipped by the default recorder.
Curate sanitized public fixtures under `tests/fixtures/providers/` only when
they are safe and useful for parser regression.

Offline replay tests use `ReplayTransport` with the existing `HttpClient` and
provider adapter. A replay request must match the recorded method and redacted
endpoint; mismatches fail rather than silently returning the wrong payload.

## Offline, contract, and smoke tests

Normal unit tests must not access the internet:

```bash
python3 -m unittest discover -s tests -v
```

Live contract checks are explicit and minimal:

```bash
python3 scripts/providers.py --contract coingecko
python3 scripts/providers.py --contract all
```

Missing optional credentials are `SKIPPED`/`NOT_READY`; a supplied but
rejected credential, schema change, or normalization failure is a failure.
Smoke checks use the production router and `REFRESH`:

```bash
python3 scripts/providers.py --smoke ETH --metric market.spot_price
```

The result reports chain, attempts, selected provider, network count, cache
hits, fallback count, final status, and unresolved diagnostics without raw
market payloads.

## Debug report integration

Wrap a live or contract command when its result must enter a report:

```bash
python3 scripts/run_with_debug.py \
  --script scripts/providers.py -- \
  python3 scripts/providers.py --probe coingecko --asset ETH
```

The wrapper returns a report-ready JSON record. A non-zero child exit is
`FAILED`; timeout and launch errors are `TIMEOUT` and `LAUNCH_FAILED`. Logs
are bounded and redacted. The wrapper itself may return `0` so report
collection continues.

## CI provider checks

`.github/workflows/provider-contracts.yml` is separate from deterministic unit
CI and runs on a schedule or manually. Public checks run without secrets.
Credentialed checks run only when the corresponding GitHub secret is present;
missing credentials are reported as skipped. CI distinguishes transient
network, rate-limit, authentication, plan, schema, and normalization failures.

## Adding a provider

An addition is incomplete until it has:

```text
adapter, config, capabilities, route, readiness/doctor behavior,
offline fixture tests, minimal contract check, deterministic error mapping,
secret redaction, and documentation.
```

Do not add trading keys, order placement, leverage, or autonomous execution.

## Merge checklist

- [ ] deterministic unit tests remain offline;
- [ ] provider failure preserves a safe root diagnostic;
- [ ] 403 and timeout phases are classified correctly;
- [ ] TLS verification remains enabled;
- [ ] credentials are absent from fixtures, cache, logs, and reports;
- [ ] fallback and circuit behavior are covered;
- [ ] contract/smoke commands are explicit and documented;
- [ ] full tests, Ruff, and compile checks pass.
