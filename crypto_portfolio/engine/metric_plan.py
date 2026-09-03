"""Deterministic metric requests for a portfolio review."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from ..metrics_registry import METRIC_REGISTRY, MetricDefinition, metric_definition
from ..models.metrics_history import MetricObservation
from ..models.portfolio import PortfolioSnapshot
from ..models.time import parse_timestamp
from ..models.policy import Policy, resolve_policy


_REVIEW_TYPES = {"SNAPSHOT_REVIEW", "FULL_REVIEW", "EVENT_REVIEW"}
_GLOBAL_METRICS = (
    "market.btc_dominance",
    "market.total_crypto_market_cap",
    "market.stablecoin_supply",
    "market.breadth",
    "market.breadth_state",
    "market.flow_state",
    "flows.etf_net_1d",
    "flows.etf_net_7d",
    "flows.etf_net_30d",
    "sentiment.market_fear_greed",
)
_ASSET_METRICS = (
    "market.spot_price",
    "market.return_30d",
    "market.return_90d",
    "market.return_180d",
    "market.ma20",
    "market.ma50",
    "market.ma100",
    "market.ma200",
    "market.atr14",
    "market.realized_vol_30d",
    "market.realized_vol_90d",
    "market.relative_volume",
    "market.drawdown",
    "flows.exchange_netflow",
    "fundamentals.tvl",
    "fundamentals.fees_30d",
    "fundamentals.revenue_30d",
    "fundamentals.stablecoin_liquidity",
    "fundamentals.active_users",
    "fundamentals.developer_activity",
    "onchain.active_addresses",
    "onchain.transfer_volume",
    "onchain.blockspace_fees",
    "onchain.transaction_count",
    "valuation.market_cap",
    "valuation.fdv",
    "valuation.fdv_market_cap_ratio",
    "valuation.fee_revenue_multiple",
    "tokenomics.next_unlock_pct",
    "tokenomics.annualized_emissions",
    "tokenomics.supply_growth",
    "risk.security_event_status",
    "risk.chain_liveness_status",
    "risk.regulatory_event_status",
    "risk.governance_event_status",
)
_RELATIVE_METRICS = (
    "relative.return_vs_btc_30d",
    "relative.return_vs_btc_90d",
    "relative.return_vs_btc_180d",
)
_POSITIONING_METRICS = (
    "derivatives.funding_rate",
    "derivatives.funding_rate_24h_avg",
    "derivatives.funding_rate_7d_avg",
    "derivatives.funding_rate_percentile",
    "derivatives.open_interest_usd",
    "derivatives.open_interest_change_1d",
    "derivatives.open_interest_change_7d",
    "derivatives.open_interest_to_market_cap",
    "derivatives.long_short_account_ratio",
    "derivatives.top_trader_long_short_ratio",
    "derivatives.long_liquidations_24h_usd",
    "derivatives.short_liquidations_24h_usd",
    "derivatives.total_liquidations_24h_usd",
    "derivatives.long_liquidations_7d_usd",
    "derivatives.short_liquidations_7d_usd",
    "derivatives.futures_basis_annualized",
    "sentiment.social_bullish_share",
    "sentiment.social_mentions_24h",
    "sentiment.social_mentions_change_7d",
    "sentiment.social_sentiment_percentile",
    "sentiment.social_attention_percentile",
)
_BTC_CYCLE_METRICS = (
    "onchain.btc.mvrv",
    "onchain.btc.mvrv_zscore",
    "onchain.btc.realized_price",
    "onchain.btc.market_to_realized_price",
    "onchain.btc.sopr",
    "onchain.btc.lth_supply_pct",
    "onchain.btc.lth_net_position_change",
    "onchain.btc.sth_realized_price",
    "onchain.btc.lth_realized_price",
    "onchain.btc.nupl",
)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _review_type(value: Any) -> str:
    result = _text(value, "review_type").upper()
    if result not in _REVIEW_TYPES:
        raise ValueError(f"review_type must be one of {sorted(_REVIEW_TYPES)}")
    return result


def _unique_symbols(values: Iterable[Any], field: str) -> tuple[str, ...]:
    if isinstance(values, (set, frozenset)):
        values = sorted(values)
    result: list[str] = []
    for value in values:
        symbol = _text(value, field).upper()
        if symbol not in result:
            result.append(symbol)
    return tuple(result)


@dataclass(frozen=True)
class MetricRequest:
    """One validated request handed to a data-collection stage."""

    asset: str
    metric_key: str
    factor: str | None = None
    critical: bool | None = None
    freshness: str | int | float | None = None
    trend_enabled: bool | None = None
    can_reuse: bool = False
    cached_observation_id: str | None = None
    reason: str = ""
    definition: MetricDefinition | None = None

    def __post_init__(self) -> None:
        asset = _text(self.asset, "metric request asset").upper()
        definition = self.definition or metric_definition(self.metric_key)
        if not isinstance(definition, MetricDefinition):
            raise ValueError("metric request definition must be a MetricDefinition")
        if definition.key != metric_definition(self.metric_key).key:
            raise ValueError("metric request definition key must be registered")
        if self.factor is not None:
            if not isinstance(self.factor, str) or self.factor.strip().lower() != definition.factor:
                raise ValueError(f"metric request factor must be {definition.factor}")
        if self.critical is not None and not isinstance(self.critical, bool):
            raise ValueError("metric request critical must be boolean or null")
        if self.trend_enabled is not None and not isinstance(self.trend_enabled, bool):
            raise ValueError("metric request trend_enabled must be boolean or null")
        if self.freshness is not None:
            if isinstance(self.freshness, bool):
                raise ValueError("metric request freshness must be a day window or state")
            if isinstance(self.freshness, (int, float)):
                if not math.isfinite(float(self.freshness)) or self.freshness <= 0:
                    raise ValueError("metric request freshness must be positive")
            elif not isinstance(self.freshness, str) or (
                self.freshness.strip().upper() not in {"CURRENT", "STALE", "UNKNOWN"}
                and not (
                    self.freshness.strip().lower().endswith("d")
                    and self.freshness.strip()[:-1].isdigit()
                    and int(self.freshness.strip()[:-1]) > 0
                )
            ):
                raise ValueError("metric request freshness is unsupported")
        if not isinstance(self.can_reuse, bool):
            raise ValueError("metric request can_reuse must be boolean")
        cached_id = self.cached_observation_id
        if cached_id is not None:
            cached_id = _text(cached_id, "cached_observation_id")
        if cached_id is not None and not self.can_reuse:
            raise ValueError("cached_observation_id requires can_reuse")
        if not isinstance(self.reason, str):
            raise ValueError("metric request reason must be a string")
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "metric_key", definition.key)
        object.__setattr__(self, "definition", definition)
        object.__setattr__(self, "factor", definition.factor)
        object.__setattr__(self, "critical", definition.critical if self.critical is None else self.critical)
        object.__setattr__(self, "freshness", definition.freshness if self.freshness is None else self.freshness)
        object.__setattr__(self, "trend_enabled", definition.trend_enabled if self.trend_enabled is None else self.trend_enabled)
        object.__setattr__(self, "cached_observation_id", cached_id)
        object.__setattr__(self, "reason", self.reason.strip())

    @property
    def required(self) -> bool:
        return bool(self.critical)

    @property
    def key(self) -> str:
        return self.metric_key

    @property
    def freshness_window(self) -> str | int | float:
        return self.freshness

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MetricRequest":
        if not isinstance(value, Mapping):
            raise ValueError("metric request must be an object")
        data = dict(value)
        allowed = {
            "asset", "metric_key", "factor", "value_type", "unit", "critical", "freshness",
            "trend_enabled", "can_reuse", "cached_observation_id", "reason", "definition",
            "decision_role", "context_group",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"metric request contains unknown fields: {', '.join(sorted(unknown))}")
        definition = metric_definition(data["metric_key"])
        if "value_type" in data and data["value_type"] != definition.value_type:
            raise ValueError("metric request value_type does not match the registry")
        if "unit" in data and data["unit"] != definition.unit:
            raise ValueError("metric request unit does not match the registry")
        if "decision_role" in data and str(data["decision_role"]).strip().upper() != definition.decision_role:
            raise ValueError("metric request decision_role does not match the registry")
        if "context_group" in data and data["context_group"] != definition.context_group:
            raise ValueError("metric request context_group does not match the registry")
        data.pop("value_type", None)
        data.pop("unit", None)
        data.pop("definition", None)
        data.pop("decision_role", None)
        data.pop("context_group", None)
        return cls(**data)

    @property
    def freshness_requirement(self) -> str | int | float:
        return self.freshness

    @property
    def decision_role(self) -> str:
        return self.definition.decision_role

    @property
    def context_group(self) -> str | None:
        return self.definition.context_group

    def as_dict(self) -> dict[str, Any]:
        result = {
            "asset": self.asset,
            "metric_key": self.metric_key,
            "factor": self.factor,
            "value_type": self.definition.value_type,
            "unit": self.definition.unit,
            "decision_role": self.definition.decision_role,
            "context_group": self.definition.context_group,
            "critical": self.critical,
            "freshness": self.freshness,
            "trend_enabled": self.trend_enabled,
            "can_reuse": self.can_reuse,
            "cached_observation_id": self.cached_observation_id,
            "reason": self.reason,
        }
        return result


@dataclass(frozen=True)
class MetricCollectionPlan:
    """A complete, deterministic collection plan."""

    review_type: str
    requests: tuple[MetricRequest, ...]
    assets: tuple[str, ...] = ()
    discovery_required_assets: tuple[str, ...] = ()
    collector_model: str = "LUNA_MAX"

    def __post_init__(self) -> None:
        object.__setattr__(self, "review_type", _review_type(self.review_type))
        requests = tuple(
            item if isinstance(item, MetricRequest) else MetricRequest.from_mapping(item)
            for item in self.requests
        )
        identities = [(item.asset, item.metric_key) for item in requests]
        if len(identities) != len(set(identities)):
            raise ValueError("metric collection plan contains duplicate requests")
        assets = _unique_symbols(self.assets, "plan asset")
        requested_assets = _unique_symbols((item.asset for item in requests), "plan asset")
        if assets and any(asset not in assets for asset in requested_assets):
            raise ValueError("plan assets must include every requested asset")
        object.__setattr__(self, "requests", requests)
        object.__setattr__(self, "assets", assets or requested_assets)
        object.__setattr__(
            self,
            "discovery_required_assets",
            _unique_symbols(self.discovery_required_assets, "discovery asset"),
        )
        if str(self.collector_model).strip().upper() != "LUNA_MAX":
            raise ValueError("metric collection plans must use LUNA_MAX")
        object.__setattr__(self, "collector_model", "LUNA_MAX")

    @property
    def collection_requests(self) -> tuple[MetricRequest, ...]:
        return self.requests

    @property
    def metrics(self) -> tuple[MetricRequest, ...]:
        return self.requests

    @property
    def critical_requests(self) -> tuple[MetricRequest, ...]:
        return tuple(item for item in self.requests if item.critical)

    @property
    def critical_metric_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.metric_key for item in self.critical_requests))

    def for_asset(self, asset: str) -> tuple[MetricRequest, ...]:
        symbol = _text(asset, "asset").upper()
        return tuple(item for item in self.requests if item.asset == symbol)

    requests_for_asset = for_asset

    @property
    def metric_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.metric_key for item in self.requests))

    def __iter__(self):
        return iter(self.requests)

    def __len__(self) -> int:
        return len(self.requests)

    def as_dict(self) -> dict[str, Any]:
        return {
            "review_type": self.review_type,
            "assets": list(self.assets),
            "requests": [item.as_dict() for item in self.requests],
            "collector_model": self.collector_model,
            "critical_metric_keys": list(self.critical_metric_keys),
            "discovery_required_assets": list(self.discovery_required_assets),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MetricCollectionPlan":
        if not isinstance(value, Mapping):
            raise ValueError("metric collection plan must be an object")
        data = dict(value)
        allowed = {"review_type", "requests", "assets", "discovery_required_assets", "collector_model", "critical_metric_keys"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"metric collection plan contains unknown fields: {', '.join(sorted(unknown))}")
        model = cls(
            review_type=data["review_type"],
            requests=data.get("requests", ()),
            assets=data.get("assets", ()),
            discovery_required_assets=data.get("discovery_required_assets", ()),
            collector_model=data.get("collector_model", "LUNA_MAX"),
        )
        if "critical_metric_keys" in data and tuple(data["critical_metric_keys"]) != model.critical_metric_keys:
            raise ValueError("critical_metric_keys does not match the collection requests")
        return model


def _portfolio_symbols(portfolio: Any, policy: Policy) -> tuple[str, ...]:
    if isinstance(portfolio, PortfolioSnapshot):
        return tuple(position.symbol for position in portfolio.positions)
    if isinstance(portfolio, Mapping):
        raw_positions = portfolio.get("positions", ())
        if not isinstance(raw_positions, (list, tuple)):
            raise ValueError("portfolio.positions must be a list")
        result: list[str] = []
        for index, position in enumerate(raw_positions):
            if not isinstance(position, Mapping):
                raise ValueError(f"portfolio.positions[{index}] must be an object")
            result.append(_text(position.get("symbol"), f"portfolio.positions[{index}].symbol").upper())
        return _unique_symbols(result, "portfolio symbol")
    if portfolio is None:
        return ()
    if isinstance(portfolio, str):
        return (_text(portfolio, "portfolio symbol").upper(),)
    try:
        return _unique_symbols(portfolio, "portfolio symbol")
    except TypeError as exc:
        raise ValueError("portfolio must be a PortfolioSnapshot, mapping, or symbol sequence") from exc


def _watchlist_symbols(watchlist: Any) -> tuple[str, ...]:
    if watchlist is None:
        return ()
    if isinstance(watchlist, Mapping):
        watchlist = watchlist.get("symbols", watchlist.get("assets", tuple(watchlist)))
    if isinstance(watchlist, str):
        return (_text(watchlist, "watchlist symbol").upper(),)
    try:
        return _unique_symbols(watchlist, "watchlist symbol")
    except TypeError as exc:
        raise ValueError("watchlist must be a symbol sequence or mapping") from exc


def _cached_values(value: Any) -> tuple[MetricObservation, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        if "observations" in value:
            value = value["observations"]
        else:
            flattened: list[Any] = []
            for item in value.values():
                if isinstance(item, Mapping) and "observation_id" not in item:
                    flattened.extend(item.values())
                else:
                    flattened.append(item)
            value = flattened
    if isinstance(value, MetricObservation):
        value = (value,)
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise ValueError("cached observations must be a sequence of MetricObservation objects")
    result = []
    for item in value:
        result.append(item if isinstance(item, MetricObservation) else MetricObservation.from_mapping(item))
    return tuple(result)


def _fresh_enough(observation: MetricObservation, definition: MetricDefinition, as_of: str | datetime | None) -> bool:
    if observation.freshness != "CURRENT":
        return False
    if as_of is None:
        return True
    window = definition.freshness
    if not isinstance(window, str) or not window.lower().endswith("d"):
        return True
    days = int(window[:-1])
    age = (parse_timestamp(as_of.isoformat() if isinstance(as_of, datetime) else as_of) - parse_timestamp(observation.observed_at)).total_seconds()
    return 0 <= age <= days * 86400


def _latest_cached(
    observations: tuple[MetricObservation, ...],
    asset: str,
    metric_key: str,
    definition: MetricDefinition,
    as_of: str | datetime | None,
) -> MetricObservation | None:
    candidates = [
        item
        for item in observations
        if item.asset == asset and item.metric_key == metric_key and _fresh_enough(item, definition, as_of)
    ]
    return max(candidates, key=lambda item: (parse_timestamp(item.observed_at), item.observation_id)) if candidates else None


def build_metric_collection_plan(
    portfolio: PortfolioSnapshot | Mapping[str, Any] | Iterable[str] | None,
    watchlist: Iterable[str] | Mapping[str, Any] | None = None,
    review_type: str = "SNAPSHOT_REVIEW",
    policy: Policy | None = None,
    metric_registry: Mapping[str, MetricDefinition] | None = None,
    *,
    cached_observations: Iterable[MetricObservation | Mapping[str, Any]] | Mapping[str, Any] | None = None,
    history: Iterable[MetricObservation | Mapping[str, Any]] | Mapping[str, Any] | None = None,
    as_of: str | datetime | None = None,
) -> MetricCollectionPlan:
    """Build all applicable requests without asking a model to choose metrics."""
    resolved = policy or resolve_policy()
    registry = METRIC_REGISTRY if metric_registry is None else metric_registry
    if not isinstance(registry, Mapping) or not registry:
        raise ValueError("metric_registry must be a non-empty mapping")
    definitions: dict[str, MetricDefinition] = {}
    for raw_key, definition in registry.items():
        key = metric_definition(raw_key).key
        if not isinstance(definition, MetricDefinition):
            raise ValueError("metric_registry values must be MetricDefinition objects")
        if definition.key != key:
            raise ValueError(f"metric registry key {raw_key!r} does not match definition {definition.key!r}")
        definitions[key] = definition
    review = _review_type(review_type)
    if history is not None and cached_observations is not None:
        raise ValueError("provide only one of cached_observations or history")
    cached = _cached_values(cached_observations if cached_observations is not None else history)
    symbols = list(_portfolio_symbols(portfolio, resolved))
    for symbol in _watchlist_symbols(watchlist):
        if symbol not in symbols:
            symbols.append(symbol)
    if "BTC" not in symbols:
        symbols.insert(0, "BTC")

    requests: list[MetricRequest] = []

    def add(asset: str, key: str, reason: str) -> None:
        definition = definitions.get(key)
        if definition is None:
            return
        if not definition.applies_to(asset):
            return
        latest = _latest_cached(cached, asset, key, definition, as_of)
        requests.append(
            MetricRequest(
                asset=asset,
                metric_key=key,
                definition=definition,
                critical=definition.critical,
                can_reuse=latest is not None,
                cached_observation_id=latest.observation_id if latest else None,
                reason=reason,
            )
        )

    for key in _GLOBAL_METRICS:
        add("MARKET", key, "global market context")
    add("BTC", "market.btc_trend", "BTC trend regime input")
    add("BTC", "market.volatility_state", "BTC volatility regime input")

    discovery: list[str] = []
    for symbol in symbols:
        asset_type = resolved.classify(symbol)
        if asset_type in {"stablecoin", "cash"}:
            continue
        if asset_type == "other":
            discovery.append(symbol)
        for key in _ASSET_METRICS:
            add(symbol, key, "asset market, risk, and decision factors")
        for key in _POSITIONING_METRICS:
            add(symbol, key, "derivatives positioning and social context")
        if symbol == "BTC":
            for key in _BTC_CYCLE_METRICS:
                add(symbol, key, "BTC cycle and on-chain context")
        if symbol != "BTC":
            for key in _RELATIVE_METRICS:
                add(symbol, key, "BTC-relative performance")

    return MetricCollectionPlan(
        review_type=review,
        requests=tuple(requests),
        assets=tuple(["MARKET", *symbols]),
        discovery_required_assets=tuple(discovery),
    )


build_collection_plan = build_metric_collection_plan
MetricCollectionRequest = MetricRequest


def build_metric_collection_request(plan: MetricCollectionPlan | Mapping[str, Any]) -> dict[str, Any]:
    """Return the validated compact payload handed to the collector."""
    if isinstance(plan, Mapping) and plan.get("collector_model", "LUNA_MAX") != "LUNA_MAX":
        raise ValueError("metric collection requests must use LUNA_MAX")
    model = plan if isinstance(plan, MetricCollectionPlan) else MetricCollectionPlan.from_mapping(plan)
    return model.as_dict()


__all__ = [
    "MetricCollectionPlan",
    "MetricCollectionRequest",
    "MetricRequest",
    "build_collection_plan",
    "build_metric_collection_request",
    "build_metric_collection_plan",
]
