"""Deterministic provider priority and bundle classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from ..metrics_registry import normalize_metric_key
from ..models.time import normalize_timestamp, parse_timestamp
from .base import ProviderRequest


BASIS_METHODOLOGY = "delivery_mark_index_act365_v1"


def current_delivery_basis(metadata: Mapping[str, Any] | None, as_of: str | datetime) -> bool:
    """Old perpetual observations and expired contracts are history only."""
    metadata = metadata or {}
    if metadata.get("methodology") != BASIS_METHODOLOGY:
        return False
    try:
        cutoff = parse_timestamp(as_of.isoformat() if isinstance(as_of, datetime) else as_of)
        return parse_timestamp(metadata.get("delivery_at")) > cutoff
    except ValueError:
        return False


DEFAULT_TTL_SECONDS = {
    "spot": 600,
    "funding": 3600,
    "open_interest": 3600,
    "ratios": 3600,
    "basis": 3600,
    "liquidations": 3600,
    "etf": 86400,
    "sentiment": 43200,
    "protocol": 21600,
    "onchain": 86400,
    "default": 3600,
}
PROVIDER_ROUTES = {
    "market": ("binance", "bybit"),
    "derivatives": ("binance", "bybit"),
    "basis": ("binance",),
    "fundamentals": ("defillama",),
    "etf": ("sosovalue",),
    "liquidations": (),
    "sentiment.market": ("alternative_me",),
    "sentiment.social": ("lunarcrush",),
    "btc_cycle": ("coinmetrics_community", "coinmetrics_pro"),
}


def provider_chain(metric_key: str) -> tuple[str, ...]:
    key = normalize_metric_key(metric_key)
    if key in {
        "market.btc_dominance",
        "market.total_crypto_market_cap",
        "market.stablecoin_supply",
        "market.breadth",
        "market.breadth_state",
        "market.flow_state",
    }:
        return ()
    if key == "market.spot_price" or key.startswith("market."):
        return ("binance", "bybit")
    if key.startswith("flows.etf_"):
        return ("sosovalue",)
    if "liquidations" in key:
        return ()
    if key == "derivatives.futures_basis_annualized":
        return PROVIDER_ROUTES["basis"]
    if key.startswith("derivatives."):
        return ("binance", "bybit")
    if key == "sentiment.market_fear_greed":
        return ("alternative_me",)
    if key.startswith("sentiment.social_"):
        return ("lunarcrush",)
    if key.startswith(("fundamentals.", "valuation.", "tokenomics.")):
        return ("defillama",)
    if key.startswith("onchain.btc."):
        return ("coinmetrics_community", "coinmetrics_pro")
    # Exchange netflow requires on-chain attribution and event metrics require
    # current source scans; neither is fabricated from market endpoints.
    return ()


resolve_provider_chain = provider_chain
provider_priority = provider_chain
get_provider_chain = provider_chain


def dataset_for_metric(metric_key: str) -> str:
    key = normalize_metric_key(metric_key)
    if key == "market.spot_price":
        return "spot"
    if key.startswith("market."):
        return "ohlcv"
    if key.startswith("derivatives.funding_rate"):
        return "funding"
    if key.startswith("derivatives.open_interest"):
        return "open_interest"
    if key.startswith(("derivatives.long_short", "derivatives.top_trader")):
        return "ratios"
    if key.startswith("derivatives.futures_basis"):
        return "basis"
    if key.startswith("derivatives.") and "liquidations" in key:
        return "liquidations"
    if key.startswith("flows.etf_"):
        return "etf"
    if key.startswith("sentiment."):
        return "sentiment"
    if key.startswith(("fundamentals.", "valuation.", "tokenomics.")):
        return "protocol"
    if key.startswith("onchain.btc."):
        return "onchain"
    return "web"


def cache_ttl_seconds(dataset: str, configured: dict[str, Any] | None = None) -> int:
    values = configured or DEFAULT_TTL_SECONDS
    value = values.get(dataset, values.get("default", DEFAULT_TTL_SECONDS["default"]))
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("cache TTL must be a positive integer")
    return value


def metric_is_mutable(metric_key: str) -> bool:
    key = normalize_metric_key(metric_key)
    if key == "market.spot_price":
        return True
    # Candle-derived values are immutable at their source timestamp; current
    # scalar/provider aggregates are TTL governed.
    return dataset_for_metric(key) != "ohlcv"


def _as_of(value: str | datetime | None, now: datetime) -> datetime:
    if value is None:
        return now
    return parse_timestamp(value.isoformat() if isinstance(value, datetime) else value)


def _parameters(dataset: str, asset: str, *, as_of: str | datetime | None, now: datetime, history_days: int) -> dict[str, Any]:
    end = _as_of(as_of, now)
    start_days = 365 if dataset == "ohlcv" else max(7, min(history_days, 90))
    start = end - timedelta(days=start_days)
    result: dict[str, Any] = {
        "symbol": asset,
        "market": "spot" if dataset == "ohlcv" or dataset == "spot" else "perpetual",
        "quote_currency": "USDT",
        "as_of": None if dataset == "basis" and as_of is None else normalize_timestamp(end.isoformat(), "as_of"),
    }
    if dataset == "ohlcv":
        result.update({"timeframe": "1D", "interval": "1d"})
    if dataset in {"ohlcv", "funding", "open_interest", "ratios", "basis", "liquidations", "etf"}:
        result.update({
            "start": normalize_timestamp(start.isoformat(), "start"),
            "end": normalize_timestamp(end.isoformat(), "end"),
        })
    return result


def build_provider_requests(
    requests: Iterable[Any],
    *,
    as_of: str | datetime | None = None,
    now: str | datetime | None = None,
    history_days: int = 365,
    ttl_seconds: dict[str, Any] | None = None,
) -> tuple[ProviderRequest, ...]:
    """Group metric requests by primary provider and fetchable dataset."""
    current = parse_timestamp(now.isoformat() if isinstance(now, datetime) else now) if now is not None else datetime.now(timezone.utc)
    groups: dict[tuple[str, str, str, str], list[Any]] = {}
    for item in requests:
        key = normalize_metric_key(item.metric_key)
        chain = provider_chain(key)
        if not chain:
            continue
        dataset = dataset_for_metric(key)
        group_key = (chain[0], dataset, item.asset.strip().upper(), str(item.parameters.get("timeframe", "1D")).upper() if hasattr(item, "parameters") else "1D")
        groups.setdefault(group_key, []).append(item)
    result = []
    for (provider, dataset, asset, timeframe), items in groups.items():
        parameters = _parameters(dataset, asset, as_of=as_of, now=current, history_days=history_days)
        parameters["timeframe"] = timeframe if dataset == "ohlcv" else parameters.get("timeframe")
        if hasattr(items[0], "parameters"):
            parameters.update({key: value for key, value in items[0].parameters.items() if key not in {"api_key", "api_secret", "authorization", "token"}})
        if dataset == "basis":
            parameters.update({
                "market": "delivery",
                "methodology": BASIS_METHODOLOGY,
            })
        keys = tuple(item.metric_key for item in items)
        mutable = any(metric_is_mutable(key) for key in keys) and dataset != "ohlcv"
        result.append(
            ProviderRequest(
                provider=provider,
                dataset=dataset,
                asset=asset,
                parameters=parameters,
                metric_keys=keys,
                mutable=mutable,
                freshness_seconds=cache_ttl_seconds(dataset, ttl_seconds),
            )
        )
    return tuple(sorted(result, key=lambda item: (item.provider, item.dataset, item.asset, item.metric_keys)))


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "PROVIDER_ROUTES",
    "build_provider_requests",
    "cache_ttl_seconds",
    "dataset_for_metric",
    "metric_is_mutable",
    "provider_chain",
    "provider_priority",
    "get_provider_chain",
    "resolve_provider_chain",
]
