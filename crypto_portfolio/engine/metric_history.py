"""Deterministic current-vs-previous metric comparisons and fact builders."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Type

from ..facts.models import FACT_TYPES, FactBase
from ..metric_availability import contributes_to_scoring_coverage
from ..metrics_registry import METRIC_REGISTRY, metric_definition
from ..models.metrics_history import MetricObservation
from ..models.time import parse_timestamp
from ..state.metrics import classify_metric_change


def _observations(value: Any) -> tuple[MetricObservation, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        value = (value,) if "metric_key" in value else value.get("observations", value.values())
    if isinstance(value, MetricObservation):
        return (value,)
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise ValueError("observations must be a sequence of MetricObservation objects")
    return tuple(item if isinstance(item, MetricObservation) else MetricObservation.from_mapping(item) for item in value)


def _number_change(current: Any, previous: Any) -> tuple[float | None, float | None]:
    def numeric(item: Any) -> bool:
        return isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item))

    if not numeric(current) or not numeric(previous):
        return None, None
    absolute = float(current) - float(previous)
    percentage = None if float(previous) == 0 else absolute / float(previous)
    return absolute, percentage


def compare_metric_observations(
    current: MetricObservation | Mapping[str, Any],
    previous: MetricObservation | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current = current if isinstance(current, MetricObservation) else MetricObservation.from_mapping(current)
    if previous is not None:
        previous = previous if isinstance(previous, MetricObservation) else MetricObservation.from_mapping(previous)
        if (current.asset, current.metric_key) != (previous.asset, previous.metric_key):
            raise ValueError("current and previous observations must identify the same metric")
    absolute, percentage = _number_change(current.value, previous.value if previous else None)
    elapsed_seconds = (
        (parse_timestamp(current.observed_at) - parse_timestamp(previous.observed_at)).total_seconds()
        if previous
        else None
    )
    if elapsed_seconds is not None and elapsed_seconds < 0:
        raise ValueError("current observation must not precede previous observation")
    trend = (
        classify_metric_change(
            current.metric_key,
            current.value,
            previous.value,
            stale=current.freshness != "CURRENT",
        )
        if previous
        else "INSUFFICIENT_HISTORY"
    )
    return {
        "asset": current.asset,
        "metric_key": current.metric_key,
        "current": current.value,
        "previous": previous.value if previous else None,
        "current_value": current.value,
        "previous_value": previous.value if previous else None,
        "absolute_change": absolute,
        "percentage_change": percentage,
        "change_pct": percentage,
        "elapsed_seconds": elapsed_seconds,
        "elapsed_days": elapsed_seconds / 86400 if elapsed_seconds is not None else None,
        "trend": trend,
        "multi_review_trend": trend,
        "freshness": current.freshness,
        "source_ids": [item for item in (current.observation_id, previous.observation_id if previous else None) if item],
        "data_quality_flags": [] if current.freshness == "CURRENT" else ["STALE_OR_UNKNOWN_CURRENT"],
    }


def _fact_type(factor: str | None, fact_type: Type[FactBase] | None) -> Type[FactBase]:
    if fact_type is not None:
        if not isinstance(fact_type, type) or not issubclass(fact_type, FactBase):
            raise ValueError("fact_type must be a FactBase subclass")
        return fact_type
    if factor is None:
        return FactBase
    try:
        return FACT_TYPES[factor.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported fact factor: {factor}") from exc


def build_factor_facts(
    observations: Iterable[MetricObservation | Mapping[str, Any]],
    *,
    symbol: str | None = None,
    factor: str | None = None,
    previous_observations: Iterable[MetricObservation | Mapping[str, Any]] | None = None,
    fact_type: Type[FactBase] | None = None,
    required_metric_keys: Iterable[str] | None = None,
) -> FactBase:
    """Build compact facts; all numeric changes stay in Python."""
    values = _observations(observations)
    previous_values = _observations(previous_observations)
    normalized_symbol = symbol.strip().upper() if symbol is not None else None
    if normalized_symbol is not None:
        values = tuple(item for item in values if item.asset == normalized_symbol)
        previous_values = tuple(item for item in previous_values if item.asset == normalized_symbol)
    factor_name = factor.strip().lower() if factor is not None else None
    if factor is not None:
        values = tuple(item for item in values if item.factor.lower() == factor_name)
        previous_values = tuple(item for item in previous_values if item.factor.lower() == factor_name)
        if factor_name not in FACT_TYPES:
            raise ValueError(f"overlay factor {factor_name} must use its dedicated overlay engine")
    values = tuple(
        item
        for item in values
        if metric_definition(item.metric_key).decision_role == "SCORING_FACTOR"
        or (factor_name == "event_risk" and metric_definition(item.metric_key).decision_role == "EVENT_RISK")
    )
    previous_values = tuple(
        item
        for item in previous_values
        if metric_definition(item.metric_key).decision_role == "SCORING_FACTOR"
        or (factor_name == "event_risk" and metric_definition(item.metric_key).decision_role == "EVENT_RISK")
    )
    if values and normalized_symbol is None:
        symbols = {item.asset for item in values}
        if len(symbols) != 1:
            raise ValueError("symbol is required when observations contain multiple assets")
        normalized_symbol = next(iter(symbols))
    if normalized_symbol is None:
        normalized_symbol = "UNKNOWN"
    grouped: dict[str, list[MetricObservation]] = defaultdict(list)
    for item in values:
        grouped[item.metric_key].append(item)
    previous_grouped: dict[str, list[MetricObservation]] = defaultdict(list)
    for item in previous_values:
        previous_grouped[item.metric_key].append(item)
    current_map: dict[str, Any] = {}
    previous_map: dict[str, Any] = {}
    changes: dict[str, Any] = {}
    trends: dict[str, str] = {}
    source_ids: list[str] = []
    flags: list[str] = []
    latest_values: list[MetricObservation] = []
    for key, series in sorted(grouped.items()):
        series = sorted(series, key=lambda item: (parse_timestamp(item.observed_at), item.observation_id))
        current = series[-1]
        prior_candidates = list(series[:-1]) + previous_grouped.get(key, [])
        prior_candidates = [
            item for item in prior_candidates
            if parse_timestamp(item.observed_at) < parse_timestamp(current.observed_at)
        ]
        prior = max(prior_candidates, key=lambda item: (parse_timestamp(item.observed_at), item.observation_id)) if prior_candidates else None
        current_map[key] = current.value
        previous_map[key] = prior.value if prior else None
        comparison = compare_metric_observations(current, prior)
        pair_trends = [
            classify_metric_change(
                key,
                right.value,
                left.value,
                stale=right.freshness != "CURRENT",
            )
            for left, right in zip(series, series[1:])
        ]
        if len(pair_trends) > 1:
            if all(item == "IMPROVING" for item in pair_trends):
                comparison["multi_review_trend"] = "IMPROVING"
            elif all(item == "DETERIORATING" for item in pair_trends):
                comparison["multi_review_trend"] = "DETERIORATING"
            else:
                comparison["multi_review_trend"] = "MIXED"
        changes[key] = comparison
        trends[key] = comparison["trend"]
        latest_values.append(current)
        source_ids.append(current.observation_id)
        if prior:
            source_ids.append(prior.observation_id)
        else:
            flags.append(f"NO_PREVIOUS:{key}")
        flags.extend(comparison["data_quality_flags"])
    supplied_required_keys = None if required_metric_keys is None else tuple(required_metric_keys)
    required_keys = tuple(supplied_required_keys or ())
    if supplied_required_keys is not None:
        required_keys = tuple(dict.fromkeys(metric_definition(key).key for key in required_keys))
    latest_by_key = {item.metric_key: item for item in latest_values}
    required_values = [latest_by_key[key] for key in required_keys if key in latest_by_key]
    if supplied_required_keys is not None and required_keys:
        coverage = sum(item.freshness == "CURRENT" for item in required_values) / len(required_keys)
        if len(required_values) != len(required_keys):
            freshness = "UNKNOWN"
        elif any(item.freshness == "STALE" for item in required_values):
            freshness = "STALE"
        elif any(item.freshness == "UNKNOWN" for item in required_values):
            freshness = "UNKNOWN"
        else:
            freshness = "CURRENT"
        flags.extend(f"MISSING_REQUIRED:{key}" for key in required_keys if key not in current_map)
    elif supplied_required_keys is not None:
        coverage = 0.0
        freshness = "UNKNOWN"
    elif latest_values:
        coverage = sum(item.freshness == "CURRENT" for item in latest_values) / len(latest_values)
        freshness = "STALE" if any(item.freshness == "STALE" for item in latest_values) else "UNKNOWN" if any(item.freshness == "UNKNOWN" for item in latest_values) else "CURRENT"
    else:
        coverage = 0.0
        freshness = "UNKNOWN"
        flags.append("NO_OBSERVATIONS")
    selected_type = _fact_type(factor, fact_type)
    return selected_type(
        symbol=normalized_symbol,
        current=current_map,
        previous=previous_map,
        changes=changes,
        trends=trends,
        coverage=coverage,
        freshness=freshness,
        source_ids=tuple(dict.fromkeys(source_ids)),
        data_quality_flags=tuple(dict.fromkeys(flags)),
    )


def build_facts_for_asset(
    observations: Iterable[MetricObservation | Mapping[str, Any]],
    symbol: str,
    *,
    previous_observations: Iterable[MetricObservation | Mapping[str, Any]] | None = None,
) -> dict[str, FactBase]:
    values = _observations(observations)
    previous = _observations(previous_observations)
    factors = sorted({
        item.factor
        for item in values
        if item.asset == symbol.strip().upper()
        and metric_definition(item.metric_key).decision_role in {"SCORING_FACTOR", "EVENT_RISK"}
    })
    return {
        factor: build_factor_facts(
            values,
            symbol=symbol,
            factor=factor,
            previous_observations=previous,
            required_metric_keys=(
                tuple(
                    key for key, definition in METRIC_REGISTRY.items()
                    if definition.factor == factor
                    and contributes_to_scoring_coverage(symbol.strip().upper(), key)
                )
                if any(
                    definition.factor == factor and definition.decision_role == "SCORING_FACTOR"
                    for definition in METRIC_REGISTRY.values()
                )
                else None
            ),
        )
        for factor in factors
    }


__all__ = [
    "build_factor_facts",
    "build_facts_for_asset",
    "compare_metric_observations",
]
