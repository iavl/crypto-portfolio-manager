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
| ETF/liquidations/social/netflow | Web; optional provider extension point | Optional environment key | No vendor-heavy adapter is required; not fabricated from exchange trading data. |
| Security/governance/regulatory events | Current source scan | None | A scan timestamp, lookback, source coverage, and material results are retained. |

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

Use `--prune-expired` only as an explicit manual cleanup. It removes expired
mutable response entries and preserves immutable history.
