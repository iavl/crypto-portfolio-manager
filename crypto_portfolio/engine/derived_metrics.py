"""Pure Python derivations for metrics with explicit input dependencies."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from ..models.metrics_history import MetricObservation
from ..models.time import parse_timestamp
from .metric_plan import MetricRequest


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


def derive_metric_observations(
    requests: Iterable[MetricRequest],
    reusable: Mapping[tuple[str, str], MetricObservation],
    routed: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    fetched_at: str,
    as_of: str | datetime | None = None,
) -> tuple[dict[tuple[str, str], Mapping[str, Any]], dict[tuple[str, str], str]]:
    """Resolve the currently supported derived graph without provider calls."""
    values: dict[tuple[str, str], Mapping[str, Any]] = {}
    unresolved: dict[tuple[str, str], str] = {}
    for request in requests:
        if request.metric_key not in {
            "derivatives.open_interest_to_market_cap",
            "valuation.fdv_market_cap_ratio",
            "btc_valuation.price_to_realized_price",
        }:
            continue
        identity = (request.asset, request.metric_key)
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
    "derive_metric_observations",
    "derive_btc_price_to_realized_price",
    "derive_fdv_market_cap_ratio",
    "derive_open_interest_to_market_cap",
]
