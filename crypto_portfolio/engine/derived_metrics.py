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
        if request.metric_key != "derivatives.open_interest_to_market_cap":
            continue
        identity = (request.asset, request.metric_key)
        oi_identity = (request.asset, "derivatives.open_interest_usd")
        cap_identity = (request.asset, "valuation.market_cap")
        oi = reusable.get(oi_identity) or routed.get(oi_identity)
        cap = reusable.get(cap_identity) or routed.get(cap_identity)
        derived = derive_open_interest_to_market_cap(
            request.asset,
            oi,
            cap,
            fetched_at=fetched_at,
            as_of=as_of,
        ) if oi is not None and cap is not None else None
        if derived is None:
            missing = [
                key for key, value in (("derivatives.open_interest_usd", oi), ("valuation.market_cap", cap))
                if value is None
            ]
            detail = "missing dependencies: " + ", ".join(missing) if missing else "dependencies are stale, future-dated, or invalid"
            unresolved[identity] = f"DERIVED_INPUT_UNAVAILABLE: {detail}"
        else:
            values[identity] = derived
    return values, unresolved


__all__ = [
    "derive_metric_observations",
    "derive_open_interest_to_market_cap",
]
