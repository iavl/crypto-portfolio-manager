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
remains `FAILED`, with no observation/value and `NO_PROVIDER_ROUTE` in the
diagnostic. This differs from `PROVIDER_UNSUPPORTED` (adapter capability) and
transport/schema failures. Existing allowed Web fallback behavior is unchanged;
`CACHE_ONLY` performs no network work. Missing liquidation evidence remains
missing coverage, not a zero-liquidation claim.

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
