"""Pure Python derivations for metrics with explicit input dependencies."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from ..models.metrics_history import MetricObservation
from ..models.time import parse_timestamp
from .metric_plan import MetricRequest


def _finite_number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        suffix = f" >= {minimum}" if minimum is not None else " finite"
        raise ValueError(f"{field} must be{suffix}")
    return result


def calculate_net_supply_growth(current_supply: float, prior_supply: float) -> float:
    """Return signed supply growth without substituting a zero denominator."""
    current = _finite_number(current_supply, "current_supply", minimum=0.0)
    prior = _finite_number(prior_supply, "prior_supply", minimum=0.0)
    if prior <= 0:
        raise ValueError("prior_supply must be > 0")
    return current / prior - 1.0


def calculate_burn_to_issuance(burn: float, issuance: float) -> float | None:
    """Return burn/issuance, or None when issuance is zero."""
    burned = _finite_number(burn, "burn", minimum=0.0)
    issued = _finite_number(issuance, "issuance", minimum=0.0)
    return None if issued == 0 else burned / issued


def derive_cumulative_burn_delta(
    asset: str,
    current: MetricObservation | Mapping[str, Any],
    prior: MetricObservation | Mapping[str, Any],
    *,
    days: int,
    fetched_at: str,
    as_of: str | datetime | None = None,
    tolerance_days: int = 7,
) -> Mapping[str, Any] | None:
    """Derive a window only from aligned, same-source cumulative counters."""
    if days not in {30, 365}:
        raise ValueError("cumulative burn delta supports only 30d or 365d")
    if isinstance(current, MetricObservation):
        current_value, current_at, source, metadata = current.value, current.observed_at, current.source, dict(current.metadata or {})
    else:
        current_value, current_at, source, metadata = current.get("value"), current.get("observed_at"), current.get("source"), dict(current.get("metadata") or {})
    if isinstance(prior, MetricObservation):
        prior_value, prior_at, prior_source, prior_metadata = prior.value, prior.observed_at, prior.source, dict(prior.metadata or {})
    else:
        prior_value, prior_at, prior_source, prior_metadata = prior.get("value"), prior.get("observed_at"), prior.get("source"), dict(prior.get("metadata") or {})
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) for value in (current_value, prior_value)):
        return None
    if not all(isinstance(value, str) for value in (current_at, prior_at, source, prior_source)) or source != prior_source:
        return None
    if not metadata.get("methodology") or metadata.get("methodology") != prior_metadata.get("methodology"):
        return None
    try:
        current_time = parse_timestamp(current_at)
        prior_time = parse_timestamp(prior_at)
        if as_of is not None and current_time > parse_timestamp(as_of.isoformat() if isinstance(as_of, datetime) else as_of):
            return None
    except ValueError:
        return None
    target = current_time - timedelta(days=days)
    if prior_time > target or target - prior_time > timedelta(days=tolerance_days):
        return None
    delta = float(current_value) - float(prior_value)
    if delta < 0:
        return None
    return {
        "asset": asset.strip().upper(),
        "metric_key": f"eth.monetary.burn_{days}d_eth",
        "value": delta,
        "unit": "ETH",
        "period": f"{days}d",
        "observed_at": current_at,
        "fetched_at": fetched_at,
        "source": "python-derived",
        "confidence": "MEDIUM",
        "summary": f"Derived from same-source cumulative burn counters separated by {days} days.",
        "metadata": {
            "source_mode": "DERIVED",
            "calculation": "current cumulative burn - aligned prior cumulative burn",
            "window": f"{days}d",
            "current_observation_at": current_at,
            "prior_observation_at": prior_at,
            "current_source": source,
            "prior_source": prior_source,
        },
    }


def derive_active_effective_stake_change(
    asset: str,
    current: MetricObservation | Mapping[str, Any],
    prior: MetricObservation | Mapping[str, Any],
    *,
    days: int,
    fetched_at: str,
    as_of: str | datetime | None = None,
    tolerance_days: int = 7,
) -> Mapping[str, Any] | None:
    """Derive a date-aligned active-effective stake change snapshot."""
    if days not in {30, 90}:
        raise ValueError("active effective stake change supports only 30d or 90d")
    current_value = current.value if isinstance(current, MetricObservation) else current.get("value")
    prior_value = prior.value if isinstance(prior, MetricObservation) else prior.get("value")
    current_at = current.observed_at if isinstance(current, MetricObservation) else current.get("observed_at")
    prior_at = prior.observed_at if isinstance(prior, MetricObservation) else prior.get("observed_at")
    current_source = current.source if isinstance(current, MetricObservation) else current.get("source")
    prior_source = prior.source if isinstance(prior, MetricObservation) else prior.get("source")
    current_metadata = dict(current.metadata or {}) if isinstance(current, MetricObservation) else dict(current.get("metadata") or {})
    prior_metadata = dict(prior.metadata or {}) if isinstance(prior, MetricObservation) else dict(prior.get("metadata") or {})
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) >= 0 for value in (current_value, prior_value)):
        return None
    if not all(isinstance(value, str) for value in (current_at, prior_at, current_source, prior_source)) or current_source != prior_source:
        return None
    if not current_metadata.get("methodology") or current_metadata.get("methodology") != prior_metadata.get("methodology"):
        return None
    try:
        current_time = parse_timestamp(current_at)
        prior_time = parse_timestamp(prior_at)
        if as_of is not None and current_time > parse_timestamp(as_of.isoformat() if isinstance(as_of, datetime) else as_of):
            return None
    except ValueError:
        return None
    target = current_time - timedelta(days=days)
    if prior_time > target or target - prior_time > timedelta(days=tolerance_days):
        return None
    return {
        "asset": asset.strip().upper(),
        "metric_key": f"eth.staking.active_effective_stake_change_{days}d",
        "value": float(current_value) - float(prior_value),
        "unit": "ETH",
        "period": f"{days}d",
        "observed_at": current_at,
        "fetched_at": fetched_at,
        "source": "python-derived",
        "confidence": "MEDIUM",
        "summary": f"Derived from same-source active effective stake snapshots separated by {days} days.",
        "metadata": {
            "source_mode": "DERIVED",
            "calculation": "current active effective stake - aligned prior active effective stake",
            "window": f"{days}d",
            "current_observation_at": current_at,
            "prior_observation_at": prior_at,
            "methodology": current_metadata["methodology"],
            "input_observation_ids": [
                item for item in (
                    current.observation_id if isinstance(current, MetricObservation) else current.get("observation_id"),
                    prior.observation_id if isinstance(prior, MetricObservation) else prior.get("observation_id"),
                ) if item
            ],
            "inputs": ["eth.staking.active_effective_stake_eth", "historical_active_effective_stake_eth"],
            "source_methodologies": [current_metadata["methodology"]],
        },
    }


def calculate_active_effective_stake_pct(active_effective_stake: float, total_supply: float) -> float:
    staked = _finite_number(active_effective_stake, "active_effective_stake", minimum=0.0)
    total = _finite_number(total_supply, "total_supply", minimum=0.0)
    if total <= 0:
        raise ValueError("total_supply must be > 0")
    result = staked / total
    if result > 1:
        raise ValueError("active_effective_stake must not exceed total_supply")
    return result


def calculate_active_stake_change_to_supply(
    active_stake_now: float,
    active_stake_prior: float,
    current_supply: float,
) -> float:
    change = _finite_number(active_stake_now, "active_stake_now", minimum=0.0) - _finite_number(
        active_stake_prior, "active_stake_prior", minimum=0.0
    )
    supply = _finite_number(current_supply, "current_supply", minimum=0.0)
    if supply <= 0:
        raise ValueError("current_supply must be > 0")
    return change / supply


def calculate_exchange_flow_to_market_cap(netflow: float, market_cap: float) -> float:
    flow = _finite_number(netflow, "netflow")
    cap = _finite_number(market_cap, "market_cap", minimum=0.0)
    if cap <= 0:
        raise ValueError("market_cap must be > 0")
    return flow / cap


def calculate_eth_etf_flow_to_aum(netflow: float, aum: float) -> float:
    flow = _finite_number(netflow, "netflow")
    assets = _finite_number(aum, "aum", minimum=0.0)
    if assets <= 0:
        raise ValueError("aum must be > 0")
    return flow / assets


def _fresh_input(
    value: MetricObservation | Mapping[str, Any] | None,
    *,
    asset: str,
    metric_key: str,
    as_of: str | datetime | None,
) -> tuple[float, str, str | None, Mapping[str, Any]] | None:
    if value is None:
        return None
    if isinstance(value, MetricObservation):
        raw_value = value.value
        observed_at = value.observed_at
        observation_id = value.observation_id
        source = value.source
        metadata = dict(value.metadata or {})
        freshness = value.freshness
    elif isinstance(value, Mapping):
        raw_value = value.get("value")
        observed_at = value.get("observed_at")
        observation_id = value.get("observation_id")
        source = value.get("source")
        metadata = dict(value.get("metadata") or {})
        freshness = str(value.get("freshness", "CURRENT")).upper()
    else:
        return None
    if (
        isinstance(raw_value, bool)
        or not isinstance(raw_value, (int, float))
        or not math.isfinite(float(raw_value))
        or not isinstance(observed_at, str)
        or not isinstance(source, str)
        or freshness != "CURRENT"
    ):
        return None
    try:
        observed = parse_timestamp(observed_at)
    except ValueError:
        return None
    if as_of is not None:
        cutoff = parse_timestamp(as_of.isoformat() if isinstance(as_of, datetime) else as_of)
        if observed > cutoff:
            return None
        definition = metric_definition(metric_key)
        if definition.freshness_days is not None and (cutoff - observed).total_seconds() > definition.freshness_days * 86400:
            return None
    if asset.strip().upper() != str(value.asset if isinstance(value, MetricObservation) else value.get("asset", asset)).strip().upper():
        return None
    return float(raw_value), observed_at, observation_id, {"source": source, **metadata}


def derive_open_interest_to_market_cap(
    asset: str,
    open_interest: MetricObservation | Mapping[str, Any],
    market_cap: MetricObservation | Mapping[str, Any],
    *,
    fetched_at: str,
    as_of: str | datetime | None = None,
) -> Mapping[str, Any] | None:
    """Derive OI/market-cap only from fresh, same-asset USD observations."""
    asset = asset.strip().upper()
    oi = _fresh_input(open_interest, asset=asset, metric_key="derivatives.open_interest_usd", as_of=as_of)
    cap = _fresh_input(market_cap, asset=asset, metric_key="valuation.market_cap", as_of=as_of)
    if oi is None or cap is None or oi[0] < 0 or cap[0] <= 0:
        return None
    observed_at = min(oi[1], cap[1], key=parse_timestamp)
    confidence = "HIGH" if isinstance(open_interest, MetricObservation) and isinstance(market_cap, MetricObservation) and {open_interest.confidence, market_cap.confidence} == {"HIGH"} else "MEDIUM"
    input_ids = [item for item in (oi[2], cap[2]) if item]
    return {
        "asset": asset,
        "metric_key": "derivatives.open_interest_to_market_cap",
        "value": oi[0] / cap[0],
        "unit": "ratio",
        "period": "current",
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "source": "python-derived",
        "confidence": confidence,
        "summary": "Derived from same-asset USD open interest divided by market cap.",
        "metadata": {
            "source_mode": "DERIVED",
            "calculation": "derivatives.open_interest_usd / valuation.market_cap",
            "input_observation_ids": input_ids,
            "input_sources": [oi[3], cap[3]],
        },
    }


def derive_fdv_market_cap_ratio(
    asset: str,
    fdv: MetricObservation | Mapping[str, Any] | None,
    market_cap: MetricObservation | Mapping[str, Any] | None,
    *,
    fetched_at: str,
    as_of: str | datetime | None = None,
) -> Mapping[str, Any] | None:
    """Derive FDV/market cap only from fresh, same-asset observations."""
    asset = asset.strip().upper()
    fully_diluted_value = _fresh_input(fdv, asset=asset, metric_key="valuation.fdv", as_of=as_of)
    cap = _fresh_input(market_cap, asset=asset, metric_key="valuation.market_cap", as_of=as_of)
    if fully_diluted_value is None or cap is None or fully_diluted_value[0] <= 0 or cap[0] <= 0:
        return None
    observed_at = min(fully_diluted_value[1], cap[1], key=parse_timestamp)
    confidence = (
        "HIGH"
        if isinstance(fdv, MetricObservation)
        and isinstance(market_cap, MetricObservation)
        and {fdv.confidence, market_cap.confidence} == {"HIGH"}
        else "MEDIUM"
    )
    input_ids = [item for item in (fully_diluted_value[2], cap[2]) if item]
    return {
        "asset": asset,
        "metric_key": "valuation.fdv_market_cap_ratio",
        "value": fully_diluted_value[0] / cap[0],
        "unit": "ratio",
        "period": "current",
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "source": "python-derived",
        "confidence": confidence,
        "summary": "Derived from same-asset FDV divided by market cap.",
        "metadata": {
            "source_mode": "DERIVED",
            "calculation": "valuation.fdv / valuation.market_cap",
            "input_observation_ids": input_ids,
            "input_sources": [fully_diluted_value[3], cap[3]],
        },
    }


def derive_btc_price_to_realized_price(
    asset: str,
    spot_price: MetricObservation | Mapping[str, Any],
    realized_price: MetricObservation | Mapping[str, Any],
    *,
    fetched_at: str,
    as_of: str | datetime | None = None,
) -> Mapping[str, Any] | None:
    """Derive BTC spot price divided by realized price from fresh primitives."""
    asset = asset.strip().upper()
    spot = _fresh_input(spot_price, asset=asset, metric_key="market.spot_price", as_of=as_of)
    realized = _fresh_input(
        realized_price,
        asset=asset,
        metric_key="btc_valuation.realized_price",
        as_of=as_of,
    )
    if spot is None or realized is None or spot[0] <= 0 or realized[0] <= 0:
        return None
    observed_at = min(spot[1], realized[1], key=parse_timestamp)
    input_ids = [item for item in (spot[2], realized[2]) if item]
    return {
        "asset": asset,
        "metric_key": "btc_valuation.price_to_realized_price",
        "value": spot[0] / realized[0],
        "unit": "ratio",
        "period": "current",
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "source": "python-derived",
        "confidence": "HIGH",
        "summary": "Derived from BTC spot price divided by realized price.",
        "metadata": {
            "source_mode": "DERIVED",
            "calculation": "market.spot_price / btc_valuation.realized_price",
            "input_observation_ids": input_ids,
        },
    }


def _derived_ratio_observation(
    asset: str,
    metric_key: str,
    value: float | None,
    inputs: Iterable[tuple[float, str | None, str | None, Mapping[str, Any]]],
    *,
    fetched_at: str,
    unit: str = "fraction",
    period: str = "current",
    calculation: str,
) -> Mapping[str, Any] | None:
    if value is None or not math.isfinite(float(value)):
        return None
    values = tuple(inputs)
    observed_at = min((item[1] for item in values), key=parse_timestamp)
    return {
        "asset": asset,
        "metric_key": metric_key,
        "value": float(value),
        "unit": unit,
        "period": period,
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "source": "python-derived",
        "confidence": "MEDIUM",
        "summary": f"Derived from {calculation}.",
        "metadata": {
            "source_mode": "DERIVED",
            "calculation": calculation,
            "input_observation_ids": [item[2] for item in values if item[2]],
            "input_sources": [item[3] for item in values],
        },
    }


def _derived_text_observation(
    asset: str,
    metric_key: str,
    value: str,
    input_value: tuple[float, str, str | None, Mapping[str, Any]],
    *,
    fetched_at: str,
    calculation: str,
) -> Mapping[str, Any]:
    return {
        "asset": asset,
        "metric_key": metric_key,
        "value": value,
        "period": "current",
        "observed_at": input_value[1],
        "fetched_at": fetched_at,
        "source": "python-derived",
        "confidence": "MEDIUM",
        "summary": f"Derived from {calculation}.",
        "metadata": {
            "source_mode": "DERIVED",
            "calculation": calculation,
            "input_observation_ids": [input_value[2]] if input_value[2] else [],
            "input_sources": [input_value[3]],
        },
    }


def _breadth_state(value: float) -> str:
    if value >= 0.6:
        return "HEALTHY"
    if value <= 0.4:
        return "WEAK"
    return "NEUTRAL"


def derive_eth_price_to_realized_price(
    asset: str,
    spot_price: MetricObservation | Mapping[str, Any],
    realized_price: MetricObservation | Mapping[str, Any],
    *,
    fetched_at: str,
    as_of: str | datetime | None = None,
) -> Mapping[str, Any] | None:
    asset = asset.strip().upper()
    spot = _fresh_input(spot_price, asset=asset, metric_key="market.spot_price", as_of=as_of)
    realized = _fresh_input(realized_price, asset=asset, metric_key="eth_valuation.realized_price", as_of=as_of)
    if spot is None or realized is None or spot[0] <= 0 or realized[0] <= 0:
        return None
    return _derived_ratio_observation(
        asset,
        "eth_valuation.price_to_realized_price",
        spot[0] / realized[0],
        (spot, realized),
        fetched_at=fetched_at,
        unit="ratio",
        calculation="market.spot_price / eth_valuation.realized_price",
    )


def derive_eth_active_effective_stake_pct(
    asset: str,
    active_effective_stake: MetricObservation | Mapping[str, Any],
    total_supply: MetricObservation | Mapping[str, Any],
    *,
    fetched_at: str,
    as_of: str | datetime | None = None,
) -> Mapping[str, Any] | None:
    asset = asset.strip().upper()
    staked = _fresh_input(active_effective_stake, asset=asset, metric_key="eth.staking.active_effective_stake_eth", as_of=as_of)
    supply = _fresh_input(total_supply, asset=asset, metric_key="eth.monetary.current_supply_eth", as_of=as_of)
    if staked is None or supply is None:
        return None
    try:
        value = calculate_active_effective_stake_pct(staked[0], supply[0])
    except ValueError:
        return None
    result = _derived_ratio_observation(
        asset,
        "eth.staking.active_effective_stake_pct",
        value,
        (staked, supply),
        fetched_at=fetched_at,
        calculation="eth.staking.active_effective_stake_eth / eth.monetary.current_supply_eth",
    )
    if result is not None:
        result["metadata"] = {
            **dict(result.get("metadata") or {}),
            "inputs": ["eth.staking.active_effective_stake_eth", "eth.monetary.current_supply_eth"],
            "formula": "active_effective_stake_eth / current_supply_eth",
            "source_methodologies": [
                method for method in (
                    dict(staked[3]).get("methodology"),
                    dict(supply[3]).get("methodology"),
                ) if method
            ],
        }
    return result


def derive_eth_exchange_flow_to_market_cap(
    asset: str,
    netflow: MetricObservation | Mapping[str, Any],
    market_cap: MetricObservation | Mapping[str, Any],
    *,
    fetched_at: str,
    as_of: str | datetime | None = None,
) -> Mapping[str, Any] | None:
    asset = asset.strip().upper()
    flow = _fresh_input(netflow, asset=asset, metric_key="flows.exchange_netflow", as_of=as_of)
    cap = _fresh_input(market_cap, asset=asset, metric_key="valuation.market_cap", as_of=as_of)
    if flow is None or cap is None:
        return None
    try:
        value = calculate_exchange_flow_to_market_cap(flow[0], cap[0])
    except ValueError:
        return None
    return _derived_ratio_observation(
        asset,
        "flows.eth_exchange_netflow_to_market_cap",
        value,
        (flow, cap),
        fetched_at=fetched_at,
        calculation="flows.exchange_netflow / valuation.market_cap",
    )


def derive_eth_active_stake_change_to_supply(
    asset: str,
    staked_change: MetricObservation | Mapping[str, Any],
    current_supply: MetricObservation | Mapping[str, Any],
    *,
    fetched_at: str,
    as_of: str | datetime | None = None,
) -> Mapping[str, Any] | None:
    asset = asset.strip().upper()
    change = _fresh_input(staked_change, asset=asset, metric_key="eth.staking.active_effective_stake_change_30d", as_of=as_of)
    supply = _fresh_input(current_supply, asset=asset, metric_key="eth.monetary.current_supply_eth", as_of=as_of)
    if change is None or supply is None:
        return None
    try:
        value = _finite_number(change[0], "active_effective_stake_change_30d") / _finite_number(
            supply[0], "current_supply", minimum=0.0
        )
    except ValueError:
        return None
    if supply[0] <= 0:
        return None
    return _derived_ratio_observation(
        asset,
        "flows.eth_active_stake_change_to_supply_30d",
        value,
        (change, supply),
        fetched_at=fetched_at,
        calculation="eth.staking.active_effective_stake_change_30d / eth.monetary.current_supply_eth",
    )


def derive_metric_observations(
    requests: Iterable[MetricRequest],
    reusable: Mapping[tuple[str, str], MetricObservation],
    routed: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    fetched_at: str,
    as_of: str | datetime | None = None,
    historical_observations: Iterable[MetricObservation] = (),
) -> tuple[dict[tuple[str, str], Mapping[str, Any]], dict[tuple[str, str], str]]:
    """Resolve the currently supported derived graph without provider calls."""
    values: dict[tuple[str, str], Mapping[str, Any]] = {}
    unresolved: dict[tuple[str, str], str] = {}
    history_by_identity: dict[tuple[str, str], list[MetricObservation]] = {}
    for observation in historical_observations:
        if isinstance(observation, MetricObservation):
            history_by_identity.setdefault((observation.asset, observation.metric_key), []).append(observation)

    def aligned_prior(
        current: MetricObservation | Mapping[str, Any],
        candidates: Iterable[MetricObservation],
        days: int,
    ) -> MetricObservation | None:
        current_at = current.observed_at if isinstance(current, MetricObservation) else current.get("observed_at")
        if not isinstance(current_at, str):
            return None
        try:
            target = parse_timestamp(current_at) - timedelta(days=days)
        except ValueError:
            return None
        usable = []
        for item in candidates:
            try:
                observed = parse_timestamp(item.observed_at)
            except ValueError:
                continue
            if observed <= target and target - observed <= timedelta(days=7):
                usable.append(item)
        return max(usable, key=lambda item: (parse_timestamp(item.observed_at), item.observation_id), default=None)

    for request in requests:
        if request.metric_key not in {
            "market.breadth_state",
            "derivatives.open_interest_to_market_cap",
            "valuation.fdv_market_cap_ratio",
            "btc_valuation.price_to_realized_price",
            "eth_valuation.price_to_realized_price",
            "eth.staking.active_effective_stake_pct",
            "eth.staking.active_effective_stake_change_30d",
            "eth.staking.active_effective_stake_change_90d",
            "flows.eth_exchange_netflow_to_market_cap",
            "flows.eth_active_stake_change_to_supply_30d",
            "flows.eth_etf_net_to_aum_7d",
            "flows.eth_etf_net_to_aum_30d",
            "eth.monetary.burn_to_issuance_30d",
            "eth.monetary.burn_to_issuance_365d",
            "eth.monetary.burn_30d_eth",
            "eth.monetary.burn_365d_eth",
        }:
            continue
        identity = (request.asset, request.metric_key)
        if request.metric_key == "market.breadth_state":
            raw = reusable.get((request.asset, "market.breadth")) or routed.get((request.asset, "market.breadth"))
            breadth = _fresh_input(raw, asset=request.asset, metric_key="market.breadth", as_of=as_of) if raw is not None else None
            if breadth is None:
                unresolved[identity] = "DERIVED_INPUT_UNAVAILABLE: missing dependencies: market.breadth"
            else:
                values[identity] = _derived_text_observation(
                    request.asset,
                    request.metric_key,
                    _breadth_state(breadth[0]),
                    breadth,
                    fetched_at=fetched_at,
                    calculation="breadth fraction thresholds [0.4, 0.6]",
                )
            continue
        if request.metric_key in {"eth.monetary.burn_30d_eth", "eth.monetary.burn_365d_eth"}:
            if (request.asset, request.metric_key) in reusable or (request.asset, request.metric_key) in routed:
                continue
            cumulative_identity = (request.asset, "eth.monetary.cumulative_burn_eth")
            current = reusable.get(cumulative_identity) or routed.get(cumulative_identity)
            candidates = [
                item for item in history_by_identity.get(cumulative_identity, ())
                if current is None or item.observation_id != getattr(current, "observation_id", None)
            ]
            if current is not None and candidates:
                days = 30 if request.metric_key.endswith("30d_eth") else 365
                derived = derive_cumulative_burn_delta(
                    request.asset,
                    current,
                    max(candidates, key=lambda item: (parse_timestamp(item.observed_at), item.observation_id)),
                    days=days,
                    fetched_at=fetched_at,
                    as_of=as_of,
                )
                if derived is not None:
                    values[(request.asset, request.metric_key)] = derived
                    continue
            unresolved[(request.asset, request.metric_key)] = "DERIVED_INPUT_UNAVAILABLE: aligned cumulative burn snapshots are required"
            continue
        if request.metric_key in {
            "eth.staking.active_effective_stake_change_30d",
            "eth.staking.active_effective_stake_change_90d",
        }:
            stake_identity = (request.asset, "eth.staking.active_effective_stake_eth")
            current = reusable.get(stake_identity) or routed.get(stake_identity)
            candidates = [
                item for item in history_by_identity.get(stake_identity, ())
                if current is None or item.observation_id != getattr(current, "observation_id", None)
            ]
            days = 30 if request.metric_key.endswith("30d") else 90
            prior = aligned_prior(current, candidates, days) if current is not None else None
            derived = derive_active_effective_stake_change(
                request.asset,
                current,
                prior,
                days=days,
                fetched_at=fetched_at,
                as_of=as_of,
            ) if current is not None and prior is not None else None
            if derived is None:
                unresolved[identity] = "DERIVED_INPUT_UNAVAILABLE: aligned active effective stake snapshots are required"
            else:
                values[identity] = derived
            continue
        cap_identity = (request.asset, "valuation.market_cap")
        cap = reusable.get(cap_identity) or routed.get(cap_identity)
        if request.metric_key == "btc_valuation.price_to_realized_price":
            spot_identity = (request.asset, "market.spot_price")
            realized_identity = (request.asset, "btc_valuation.realized_price")
            spot = reusable.get(spot_identity) or routed.get(spot_identity)
            realized = reusable.get(realized_identity) or routed.get(realized_identity)
            derived = derive_btc_price_to_realized_price(
                request.asset,
                spot,
                realized,
                fetched_at=fetched_at,
                as_of=as_of,
            ) if spot is not None and realized is not None else None
            dependencies = (("market.spot_price", spot), ("btc_valuation.realized_price", realized))
        elif request.metric_key == "eth_valuation.price_to_realized_price":
            spot = reusable.get((request.asset, "market.spot_price")) or routed.get((request.asset, "market.spot_price"))
            realized = reusable.get((request.asset, "eth_valuation.realized_price")) or routed.get((request.asset, "eth_valuation.realized_price"))
            derived = derive_eth_price_to_realized_price(
                request.asset,
                spot,
                realized,
                fetched_at=fetched_at,
                as_of=as_of,
            ) if spot is not None and realized is not None else None
            dependencies = (("market.spot_price", spot), ("eth_valuation.realized_price", realized))
        elif request.metric_key == "eth.staking.active_effective_stake_pct":
            staked = reusable.get((request.asset, "eth.staking.active_effective_stake_eth")) or routed.get((request.asset, "eth.staking.active_effective_stake_eth"))
            supply = reusable.get((request.asset, "eth.monetary.current_supply_eth")) or routed.get((request.asset, "eth.monetary.current_supply_eth"))
            derived = derive_eth_active_effective_stake_pct(
                request.asset,
                staked,
                supply,
                fetched_at=fetched_at,
                as_of=as_of,
            ) if staked is not None and supply is not None else None
            dependencies = (("eth.staking.active_effective_stake_eth", staked), ("eth.monetary.current_supply_eth", supply))
        elif request.metric_key == "flows.eth_exchange_netflow_to_market_cap":
            flow = reusable.get((request.asset, "flows.exchange_netflow")) or routed.get((request.asset, "flows.exchange_netflow"))
            derived = derive_eth_exchange_flow_to_market_cap(
                request.asset,
                flow,
                cap,
                fetched_at=fetched_at,
                as_of=as_of,
            ) if flow is not None and cap is not None else None
            dependencies = (("flows.exchange_netflow", flow), ("valuation.market_cap", cap))
        elif request.metric_key == "flows.eth_active_stake_change_to_supply_30d":
            change = reusable.get((request.asset, "eth.staking.active_effective_stake_change_30d")) or routed.get((request.asset, "eth.staking.active_effective_stake_change_30d"))
            supply = reusable.get((request.asset, "eth.monetary.current_supply_eth")) or routed.get((request.asset, "eth.monetary.current_supply_eth"))
            derived = derive_eth_active_stake_change_to_supply(
                request.asset,
                change,
                supply,
                fetched_at=fetched_at,
                as_of=as_of,
            ) if change is not None and supply is not None else None
            dependencies = (("eth.staking.active_effective_stake_change_30d", change), ("eth.monetary.current_supply_eth", supply))
        elif request.metric_key in {"flows.eth_etf_net_to_aum_7d", "flows.eth_etf_net_to_aum_30d"}:
            days = request.metric_key.rsplit("_", 1)[-1]
            raw = reusable.get((request.asset, f"flows.etf_net_{days}")) or routed.get((request.asset, f"flows.etf_net_{days}"))
            aum = reusable.get((request.asset, "flows.eth_etf_aum_usd")) or routed.get((request.asset, "flows.eth_etf_aum_usd"))
            raw_input = _fresh_input(raw, asset=request.asset, metric_key=f"flows.etf_net_{days}", as_of=as_of) if raw is not None else None
            aum_input = _fresh_input(aum, asset=request.asset, metric_key="flows.eth_etf_aum_usd", as_of=as_of) if aum is not None else None
            value = None
            if raw_input is not None and aum_input is not None:
                try:
                    value = calculate_eth_etf_flow_to_aum(raw_input[0], aum_input[0])
                except ValueError:
                    value = None
            derived = _derived_ratio_observation(
                request.asset,
                request.metric_key,
                value,
                (raw_input, aum_input) if raw_input is not None and aum_input is not None else (),
                fetched_at=fetched_at,
                calculation=f"flows.etf_net_{days} / flows.eth_etf_aum_usd",
            ) if raw_input is not None and aum_input is not None else None
            dependencies = ((f"flows.etf_net_{days}", raw), ("flows.eth_etf_aum_usd", aum))
        elif request.metric_key in {"eth.monetary.burn_to_issuance_30d", "eth.monetary.burn_to_issuance_365d"}:
            days = request.metric_key.rsplit("_", 1)[-1].removesuffix("d")
            burn_key = f"eth.monetary.burn_{days}d_eth"
            issuance_key = f"eth.monetary.issuance_{days}d_eth"
            burn = reusable.get((request.asset, burn_key)) or routed.get((request.asset, burn_key))
            issuance = reusable.get((request.asset, issuance_key)) or routed.get((request.asset, issuance_key))
            burn_input = _fresh_input(burn, asset=request.asset, metric_key=burn_key, as_of=as_of) if burn is not None else None
            issuance_input = _fresh_input(issuance, asset=request.asset, metric_key=issuance_key, as_of=as_of) if issuance is not None else None
            value = None
            burn_period = burn.period if isinstance(burn, MetricObservation) else burn.get("period") if isinstance(burn, Mapping) else None
            issuance_period = issuance.period if isinstance(issuance, MetricObservation) else issuance.get("period") if isinstance(issuance, Mapping) else None
            if burn_input is not None and issuance_input is not None and burn_period == issuance_period:
                value = calculate_burn_to_issuance(burn_input[0], issuance_input[0])
            derived = _derived_ratio_observation(
                request.asset,
                request.metric_key,
                value,
                (burn_input, issuance_input) if burn_input is not None and issuance_input is not None else (),
                fetched_at=fetched_at,
                unit="ratio",
                calculation=f"{burn_key} / {issuance_key}",
            ) if burn_input is not None and issuance_input is not None else None
            dependencies = ((burn_key, burn), (issuance_key, issuance))
        elif request.metric_key == "derivatives.open_interest_to_market_cap":
            oi_identity = (request.asset, "derivatives.open_interest_usd")
            oi = reusable.get(oi_identity) or routed.get(oi_identity)
            derived = derive_open_interest_to_market_cap(
                request.asset,
                oi,
                cap,
                fetched_at=fetched_at,
                as_of=as_of,
            ) if oi is not None and cap is not None else None
            dependencies = (("derivatives.open_interest_usd", oi), ("valuation.market_cap", cap))
        else:
            fdv_identity = (request.asset, "valuation.fdv")
            fdv = reusable.get(fdv_identity) or routed.get(fdv_identity)
            derived = derive_fdv_market_cap_ratio(
                request.asset,
                fdv,
                cap,
                fetched_at=fetched_at,
                as_of=as_of,
            ) if fdv is not None and cap is not None else None
            dependencies = (("valuation.fdv", fdv), ("valuation.market_cap", cap))
        if derived is None:
            missing = [key for key, value in dependencies if value is None]
            detail = "missing dependencies: " + ", ".join(missing) if missing else "dependencies are stale, future-dated, or invalid"
            unresolved[identity] = f"DERIVED_INPUT_UNAVAILABLE: {detail}"
        else:
            values[identity] = derived
    return values, unresolved


__all__ = [
    "calculate_burn_to_issuance",
    "calculate_eth_etf_flow_to_aum",
    "calculate_exchange_flow_to_market_cap",
    "calculate_net_supply_growth",
    "calculate_active_effective_stake_pct",
    "calculate_active_stake_change_to_supply",
    "derive_metric_observations",
    "derive_btc_price_to_realized_price",
    "derive_eth_exchange_flow_to_market_cap",
    "derive_eth_price_to_realized_price",
    "derive_eth_active_effective_stake_pct",
    "derive_eth_active_stake_change_to_supply",
    "derive_cumulative_burn_delta",
    "derive_active_effective_stake_change",
    "derive_fdv_market_cap_ratio",
    "derive_open_interest_to_market_cap",
]
