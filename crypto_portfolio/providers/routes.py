"""Deterministic provider priority and bundle classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition, normalize_metric_key
from ..models.time import normalize_timestamp, parse_timestamp
from .base import ProviderRequest


BASIS_METHODOLOGY = "delivery_mark_index_act365"


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
    "macro": 86400,
    "github": 86400,
    "chain_liveness": 300,
    "ethereum_protocol": 21600,
    "ethereum_staking": 21600,
    "ethereum_l2": 21600,
    "ethereum_da": 21600,
    "ethereum_valuation": 86400,
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
    "chain_liveness": ("chain_liveness",),
    "fred": ("fred",),
}


def provider_chain(metric_key: str, asset: str | None = None) -> tuple[str, ...]:
    key = normalize_metric_key(metric_key)
    symbol = asset.strip().upper() if isinstance(asset, str) and asset.strip() else None
    if key in {"market.btc_dominance", "market.total_crypto_market_cap"}:
        return ("coingecko",)
    if key == "market.breadth":
        return ("coingecko",)
    if key == "market.stablecoin_supply":
        return ("defillama",)
    if key in {"market.breadth_state", "market.flow_state"}:
        return ()
    if key == "risk.chain_liveness_status":
        return PROVIDER_ROUTES["chain_liveness"]
    if key == "market.spot_price" or key.startswith("market."):
        return ("binance", "bybit")
    if key == "derivatives.open_interest_to_market_cap":
        return ()
    if key == "valuation.market_cap":
        return ("coingecko", "coinmetrics_community", "coinmetrics_pro")
    if key == "valuation.fdv":
        return ("coingecko",)
    if key == "valuation.fdv_market_cap_ratio":
        return ()
    if key == "eth_valuation.price_to_realized_price":
        return ()
    if key.startswith("eth_valuation."):
        return ("coinmetrics_community", "coinmetrics_pro")
    if key.startswith("eth.l2.rent_paid") or key.startswith("eth.da."):
        return ("growthepie",)
    if key.startswith("eth.l2."):
        return ("l2beat",)
    if key.startswith("eth.blobs."):
        return ("blobscan",)
    if key.startswith("eth.monetary."):
        return ("ethereum_protocol", "coinmetrics_community", "coinmetrics_pro")
    if key.startswith("eth.staking."):
        return ("coinmetrics_community", "coinmetrics_pro")
    if key.startswith("eth.structural."):
        return ()
    if key == "btc_valuation.price_to_realized_price":
        return ()
    if key.startswith("btc_valuation."):
        return ("coinmetrics_community", "coinmetrics_pro") if symbol in {None, "BTC"} else ()
    if key == "valuation.fee_revenue_multiple":
        return ("defillama",)
    if key.startswith(("flows.etf_", "flows.btc_etf_")):
        return ("sosovalue",)
    if key.startswith("flows.eth_etf_"):
        return ("sosovalue",)
    if key.startswith(("flows.eth_exchange_", "flows.eth_staking_")):
        return ()
    if "liquidations" in key:
        return ()
    if key == "derivatives.futures_basis_annualized":
        return PROVIDER_ROUTES["basis"]
    if key.startswith("derivatives."):
        return ("binance", "bybit")
    if key == "flows.exchange_netflow":
        return ("coinmetrics_community", "coinmetrics_pro")
    if key == "sentiment.market_fear_greed":
        return ("alternative_me",)
    if key.startswith("sentiment.social_"):
        return ("lunarcrush",)
    if key == "fundamentals.developer_activity":
        return ("github",)
    if key == "fundamentals.stablecoin_liquidity":
        return ("defillama",) if symbol in {"ETH", "SOL", "BNB"} else ()
    if key == "fundamentals.active_users" and symbol == "AAVE":
        return ()
    if key in {"tokenomics.annualized_emissions", "tokenomics.supply_growth"}:
        return ("coinmetrics_community", "coinmetrics_pro") if symbol in {None, "BTC", "ETH"} else ()
    if key in {
        "onchain.active_addresses", "onchain.transfer_volume",
        "onchain.blockspace_fees", "onchain.transaction_count",
    }:
        return ("coinmetrics_community", "coinmetrics_pro") if symbol in {None, "BTC", "ETH"} else ()
    if key.startswith(("fundamentals.", "valuation.", "tokenomics.")):
        return ("defillama",)
    if key.startswith("onchain.btc."):
        return ("coinmetrics_community", "coinmetrics_pro") if symbol in {None, "BTC"} else ()
    if key.startswith("btc_network."):
        return ("coinmetrics_community", "coinmetrics_pro") if symbol in {None, "BTC"} else ()
    if key.startswith("macro."):
        return PROVIDER_ROUTES["fred"] if symbol in {None, "BTC"} else ()
    # Exchange netflow requires on-chain attribution and event metrics require
    # current source scans; neither is fabricated from market endpoints.
    return ()


def dataset_for_metric(metric_key: str) -> str:
    key = normalize_metric_key(metric_key)
    if key in {"market.btc_dominance", "market.total_crypto_market_cap"}:
        return "market_global"
    if key == "market.breadth":
        return "market_breadth"
    if key in {"market.breadth_state", "market.flow_state"}:
        return "derived"
    if key == "market.stablecoin_supply" or key == "fundamentals.stablecoin_liquidity":
        return "stablecoin"
    if key == "market.spot_price":
        return "spot"
    if key == "risk.chain_liveness_status":
        return "chain_liveness"
    if key.startswith("market."):
        return "ohlcv"
    if key.startswith("derivatives.funding_rate"):
        return "funding"
    if key == "derivatives.open_interest_to_market_cap":
        return "derived"
    if key in {"valuation.market_cap", "valuation.fdv"}:
        return "valuation"
    if key == "valuation.fdv_market_cap_ratio":
        return "derived"
    if key in {
        "eth_valuation.price_to_realized_price",
        "flows.eth_exchange_netflow_to_market_cap",
        "flows.eth_staking_netflow_to_supply_30d",
    }:
        return "derived"
    if key.startswith("eth_valuation."):
        return "ethereum_valuation"
    if key.startswith(("eth.l2.rent_paid", "eth.da.")):
        return "ethereum_l2"
    if key.startswith("eth.blobs."):
        return "ethereum_da"
    if key.startswith("eth.l2."):
        return "ethereum_l2"
    if key.startswith("eth.monetary."):
        return "ethereum_protocol"
    if key.startswith("eth.staking."):
        return "ethereum_staking"
    if key.startswith("eth.structural."):
        return "web"
    if key == "valuation.fee_revenue_multiple":
        return "protocol"
    if key.startswith("derivatives.open_interest"):
        return "open_interest"
    if key.startswith(("derivatives.long_short", "derivatives.top_trader")):
        return "ratios"
    if key.startswith("derivatives.futures_basis"):
        return "basis"
    if key.startswith("derivatives.") and "liquidations" in key:
        return "liquidations"
    if key.startswith(("flows.etf_", "flows.btc_etf_", "flows.eth_etf_")):
        return "etf"
    if key == "flows.exchange_netflow" or key.startswith(("onchain.", "btc_valuation.", "btc_network.")) or key in {
        "tokenomics.annualized_emissions", "tokenomics.supply_growth",
    }:
        return "onchain"
    if key == "fundamentals.developer_activity":
        return "github"
    if key.startswith("sentiment."):
        return "sentiment"
    if key.startswith(("fundamentals.", "valuation.", "tokenomics.")):
        return "protocol"
    if key.startswith("onchain.btc."):
        return "onchain"
    if key.startswith("btc_network."):
        return "onchain"
    if key.startswith("macro."):
        return "macro"
    return "web"


def cache_ttl_seconds(dataset: str, configured: dict[str, Any] | None = None) -> int:
    values = configured or DEFAULT_TTL_SECONDS
    value = values.get(dataset, values.get("default", DEFAULT_TTL_SECONDS["default"]))
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("cache TTL must be a positive integer")
    return value


def metric_reuse_ttl_seconds(
    metric_key: str,
    configured: Mapping[str, Any] | None = None,
) -> int | float | None:
    """Return the shortest configured window for mutable structured data."""
    definition = metric_definition(metric_key)
    registry_ttl = (
        definition.freshness_days * 86400
        if definition.freshness_days is not None
        else None
    )
    dataset = dataset_for_metric(definition.key)
    provider_ttl = cache_ttl_seconds(dataset, dict(configured or {}))
    if registry_ttl is None:
        return provider_ttl
    return min(registry_ttl, provider_ttl)


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
    start_days = (
        450 if dataset == "macro"
        else history_days if dataset in {
            "ohlcv", "onchain", "ethereum_protocol", "ethereum_staking",
            "ethereum_l2", "ethereum_da", "ethereum_valuation",
        }
        else max(7, min(history_days, 90))
    )
    start = end - timedelta(days=start_days)
    result: dict[str, Any] = {
        "symbol": asset,
        "market": "chain" if dataset == "chain_liveness" else "spot" if dataset in {"ohlcv", "spot", "valuation"} else "perpetual",
        "quote_currency": "USD" if dataset == "valuation" else "USDT",
        "as_of": (
            None
            if dataset in {"basis", "chain_liveness", "valuation"} and as_of is None
            else normalize_timestamp(end.isoformat(), "as_of")
        ),
    }
    if dataset == "ohlcv":
        result.update({"timeframe": "1D", "interval": "1d"})
    if dataset in {
        "ohlcv", "funding", "open_interest", "ratios", "basis", "liquidations", "etf", "onchain",
        "github", "macro", "ethereum_protocol", "ethereum_staking", "ethereum_l2", "ethereum_da",
        "ethereum_valuation",
    } or (
        dataset == "valuation" and as_of is not None
    ):
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
    history_days: int = 240,
    ttl_seconds: dict[str, Any] | None = None,
) -> tuple[ProviderRequest, ...]:
    """Group metric requests by primary provider and fetchable dataset."""
    current = parse_timestamp(now.isoformat() if isinstance(now, datetime) else now) if now is not None else datetime.now(timezone.utc)
    groups: dict[tuple[str, str, str, str], list[Any]] = {}
    for item in requests:
        key = normalize_metric_key(item.metric_key)
        definition = getattr(item, "definition", None)
        if definition is not None and not definition.applies_to(item.asset):
            continue
        chain = provider_chain(key, item.asset)
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
        mutable = (
            any(metric_is_mutable(key) for key in keys)
            and dataset != "ohlcv"
            and not (dataset == "valuation" and as_of is not None)
        )
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
    "metric_reuse_ttl_seconds",
    "provider_chain",
]
