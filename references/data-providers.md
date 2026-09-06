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
| Funding/OI/ratios | Binance, Bybit | None | Venue and methodology stay in provenance. |
| Annualized futures basis | Binance | None | Nearest trading USDT delivery contract; exact-symbol mark/index basis, ACT/365. Bybit has no implemented basis adapter. |
| Protocol TVL/fees/revenue | DeFiLlama | None | Asset-to-protocol identifiers are explicit. |
| Market Fear & Greed | Alternative.me | None | Market-wide context, not per-asset sentiment. |
| Chain liveness | Structured Bitcoin block APIs, EVM JSON-RPC, and Solana JSON-RPC | None | Current canonical progress only; transport failure is not a halt. |
| BTC/ETH network, supply, cycle, and exchange attribution | Coin Metrics Community, optional authenticated tier | Optional environment key | Asset-specific catalog availability is checked; unsupported USD transfer/fee or asset combinations remain required failures or premium skips. |
| Developer activity | Fixed canonical ETH/AAVE GitHub repository allowlist | Optional `GITHUB_TOKEN` | Public REST commits only; bounded trailing 30-day default-branch counts, no repository discovery or HTML scraping. |
| ETF flows | SoSoValue API v1 when configured; Web only for unresolved non-provider work | `SOSOVALUE_API_KEY` | U.S. BTC/ETH ETF summary history is bundled into 1D/7D/30D values; current access and limits are controlled by SoSoValue. |
| Historical liquidations | No configured structured provider; optional and skipped when unavailable | None | SoSoValue's current official API documents ETF data, not liquidation history. Historical CoinGlass points remain audit-only; realtime snapshots are not substituted. |
| Social | No configured adapter; optional and skipped by default | Optional environment key | No scraping, search-count substitution, or invented sentiment. |
| Exchange netflow | Coin Metrics Community when its exchange-attribution catalog supports the asset, then optional authenticated tier | Optional environment key | Uses official exchange-attributed inflow/outflow inputs; otherwise premium evidence is skipped. |
| Security/governance/regulatory events | Deterministic source catalog plus on-demand scan | None | A scan timestamp, lookback, primary-source coverage, and material results are retained. |

The repository config is `config/data-providers.json`. User-local overrides
are read from `~/.config/crypto-portfolio-manager/data-providers.json` or the
path in `CRYPTO_PORTFOLIO_PROVIDER_CONFIG`. API keys are referenced only by
environment-variable name and are never written to config, cache, history, or
logs.

### Delivery basis and unavailable metrics

Binance's public `GET /fapi/v1/exchangeInfo` identifies the nearest unexpired,
trading `CURRENT_QUARTER` or `NEXT_QUARTER` USDT delivery contract. Its exact
symbol is passed to `GET /fapi/v1/premiumIndex`; the returned symbol must match.
Python calculates:

```text
annualized_basis = (markPrice / indexPrice - 1) * 365 * 86400 / seconds_to_expiry
```

Remaining time is measured from the quote's observation time to `deliveryDate`.
The value is a signed fraction (0.10 means 10% annualized), using the delivery
mark price rather than the last traded futures price. It is contextual evidence,
not an executable yield. Metadata retains both prices, the exact contract,
delivery date, observation time, remaining seconds, venue, and methodology.
The official [mark-price endpoint](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price)
and [exchange information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)
define these public inputs.

Live collection uses no historical `as_of`. Historical requests only reuse
previously verified compatible observations/caches: today's contract catalogue
cannot prove which contract was nearest and trading in the past. No suitable
contract, invalid prices, expired contracts, future quotes, or quotes older than
the registry's one-day freshness window remain unavailable. Perpetual premiums,
funding rates, and zero are never substitutes.

Delivery requests carry a methodology marker in their cache identity. Old
Binance perpetual-basis observations remain readable history, but cannot satisfy
current acquisition; expired delivery observations cannot either.

Liquidations still have no configured structured route. Their collection status
is `SKIPPED`, with no observation/value and an explicit optional-provider
reason. This differs from a required metric's `FAILED` status and from
`PROVIDER_UNSUPPORTED` diagnostics. No Web fallback or zero-liquidation claim
is created.

Availability semantics are deliberate: `NOT_APPLICABLE` means the asset/metric
pair has no meaningful interpretation; optional/premium `SKIPPED` means the
evidence is meaningful but no eligible provider is configured; `FAILED` means
the system expected applicable evidence and its route failed.

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
use bounded TTLs; chain-liveness responses use the 300-second TTL. Historical
series are reusable and revisions are not silently overwritten.

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
python3 scripts/providers.py --probe chain_liveness --asset BTC
```

Chain-liveness probes can target `BTC`, `ETH`, `SOL`, or `BNB`. The default
structured sources are Blockstream Esplora plus mempool.space for BTC,
zero-key EVM RPC endpoints for ETH, official BNB Chain dataseeds for BNB, and
the official Solana mainnet RPC plus one independent public fallback for SOL.
Optional `CRYPTO_PORTFOLIO_*_LIVENESS_URL`/`*_RPC_URL` overrides are local-only;
URLs are redacted in diagnostics and credentials are never persisted.

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
