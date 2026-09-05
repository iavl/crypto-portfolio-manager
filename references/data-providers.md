# Data Provider and Cache Policy

Acquisition is on demand. A review first checks fresh normalized
`MetricObservation` history, then the local provider cache, then free public
structured APIs. Only unresolved work is handed to the Web/LLM fallback. The
repository has no daemon, scheduler, collector, database, queue, or exchange
execution service.

## Fetch modes

- `AUTO` (default) reuses fresh observations and provider cache entries, then
  fetches missing or stale mutable data.
- `CACHE_ONLY` never makes a network request. Missing or stale data remains
  visible as `FAILED`/`STALE`.
- `REFRESH` refreshes mutable current data, while completed historical series
  remain reusable.

Set `CRYPTO_PORTFOLIO_FETCH_MODE` or pass a run-level mode. The run-level mode
wins. Runtime state defaults to `~/.local/share/crypto-portfolio-manager/` and
can be changed with `CRYPTO_PORTFOLIO_DATA_DIR`.

## Provider priority

| Category | Default priority | Authentication | Caveat |
|---|---|---|---|
| Spot/OHLCV | Binance, Bybit | None | Only approved public USDT mappings are used. |
| Funding/OI/ratios/basis | Binance, Bybit | None | Venue and methodology stay in provenance. |
| Protocol TVL/fees/revenue | DeFiLlama | None | Asset-to-protocol identifiers are explicit. |
| Market Fear & Greed | Alternative.me | None | Market-wide context, not per-asset sentiment. |
| BTC cycle/on-chain | Coin Metrics Community, optional authenticated tier | Optional environment key | Catalog availability is checked; unsupported metrics stay unknown. |
| ETF flows | SoSoValue API v1 when configured; Web only for unresolved non-provider work | `SOSOVALUE_API_KEY` | U.S. BTC/ETH ETF summary history is bundled into 1D/7D/30D values; current access and limits are controlled by SoSoValue. |
| Historical liquidations | No configured structured provider; Web only when explicitly allowed | None | SoSoValue's current official API documents ETF data, not liquidation history. Historical CoinGlass points remain audit-only; realtime snapshots are not substituted. |
| Social/netflow | Web; optional provider extension point | Optional environment key | Not fabricated from exchange trading data. |
| Security/governance/regulatory events | Deterministic source catalog plus on-demand scan | None | A scan timestamp, lookback, primary-source coverage, and material results are retained. |

The repository config is `config/data-providers.json`. User-local overrides
are read from `~/.config/crypto-portfolio-manager/data-providers.json` or the
path in `CRYPTO_PORTFOLIO_PROVIDER_CONFIG`. API keys are referenced only by
environment-variable name and are never written to config, cache, history, or
logs.

## Cache layers

`metrics/observations.jsonl` is the decision-history canonical record. The
provider cache under `provider-cache/` is acquisition infrastructure only:

```text
provider-cache/
├── responses/<provider>/sha256/<request-hash>.json
└── series/<provider>/<series-key-hash>/manifest.json
```

Completed normalized OHLCV remains content-addressed in
`market-data/sha256/<ohlcv-hash>.json`; the series manifest only points to the
existing immutable object and records its available range. Current incomplete
candles are not persisted as completed decision evidence. Mutable responses
use bounded TTLs; historical series are reusable and revisions are not
silently overwritten.

Inspect the local state without network access:

```bash
python3 scripts/providers.py --status
python3 scripts/providers.py --list
python3 scripts/provider_cache.py --stats
```

`--status` is an offline readiness table showing resolved configuration,
registered adapter, credential presence, and runtime readiness. `--list` shows
capabilities only for adapters registered in this process; it is not a network
probe. SoSoValue is registered only when `SOSOVALUE_API_KEY` is present.

Runtime readiness is intentionally separate from endpoint health. Use the
explicit probe for network, authentication, plan entitlement, schema, and
history diagnostics:

```bash
python3 scripts/providers.py --probe binance
python3 scripts/providers.py --probe defillama
python3 scripts/providers.py --probe alternative_me
python3 scripts/providers.py --probe sosovalue
```

SoSoValue's current official documentation is at
[`sosovalue-1.gitbook.io/sosovalue-api-doc`](https://sosovalue-1.gitbook.io/sosovalue-api-doc).
The active ETF route is `GET /openapi/v1/etfs/summary-history` on
`https://openapi.sosovalue.com`, authenticated with `x-soso-api-key`. The
documented v1 history window is one month; settled flow rows are normalized by
their U.S. trading date. The shared HTTP client also supports explicit
idempotent JSON POST calls for read-only endpoints, but the current SoSoValue
ETF contract is GET.

The Python client uses verified TLS. `CRYPTO_PORTFOLIO_CA_BUNDLE` overrides
`SSL_CERT_FILE`/`SSL_CERT_DIR`, and no normal configuration disables
certificate or hostname verification. Probe failures retain a safe endpoint
and stable error code; credentials and response bodies are not printed.

Use `--prune-expired` only as an explicit manual cleanup. It removes expired
mutable response entries and preserves immutable history.
