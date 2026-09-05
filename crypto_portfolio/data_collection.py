"""Compact, stderr-friendly data collection telemetry."""

from __future__ import annotations

import sys
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, TextIO

from .metrics_registry import REVIEW_TYPES, metric_definition
from .models.metrics_history import CollectionEvent, MetricObservation
from .models.policy import Policy, resolve_policy
from .state.metrics import (
    append_collection_event,
    append_metric_observation,
    classify_metric_change,
)


_STATUS_ORDER = ("SUCCESS", "FAILED", "STALE", "CONFLICT", "NOT_APPLICABLE")


def _display(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def collection_summary(
    events: Iterable[CollectionEvent],
    *,
    weights: Mapping[str, float] | None = None,
    review_type: str | None = None,
    policy: Policy | None = None,
) -> dict[str, Any]:
    values = tuple(events)
    if any(not isinstance(event, CollectionEvent) for event in values):
        raise ValueError("events must contain CollectionEvent objects")
    if review_type is not None:
        review_type = review_type.strip().upper() if isinstance(review_type, str) else review_type
        if review_type not in REVIEW_TYPES:
            raise ValueError(f"review_type must be one of {list(REVIEW_TYPES)}")
    counts = Counter(event.status for event in values)
    scoring_events = [
        event for event in values
        if metric_definition(event.metric_key).decision_role == "SCORING_FACTOR"
    ]
    applicable = [event for event in scoring_events if event.status != "NOT_APPLICABLE"]
    resolved_policy = policy or resolve_policy()
    if isinstance(resolved_policy, Mapping):
        policy_weights = dict(resolved_policy.get("scoring_weights", {}))
        scoring_policy = resolved_policy.get("scoring", {})
    else:
        policy_weights = dict(resolved_policy.scoring_weights)
        scoring_policy = resolved_policy.scoring
    supplied_weights = policy_weights if weights is None else weights
    if not isinstance(supplied_weights, Mapping):
        raise ValueError("weights must be an object")
    factor_weights: dict[str, float] = {}
    metric_weights: dict[str, float] = {}
    for raw_key, raw_weight in supplied_weights.items():
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError("collection weights must be finite non-negative numbers")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("collection weights must be finite non-negative numbers")
        key = str(raw_key).strip().lower()
        if key in policy_weights:
            factor_weights[key] = weight
        else:
            definition = metric_definition(key)
            if definition.decision_role == "SCORING_FACTOR":
                metric_weights[key] = weight
    if metric_weights and not factor_weights:
        factor_weights = {
            factor: sum(weight for key, weight in metric_weights.items() if metric_definition(key).factor == factor)
            for factor in {metric_definition(key).factor for key in metric_weights}
        }
    if not factor_weights:
        factor_weights = policy_weights
    by_factor: dict[str, list[CollectionEvent]] = {}
    for event in applicable:
        by_factor.setdefault(metric_definition(event.metric_key).factor, []).append(event)
    factor_coverage = {
        factor: sum(event.status == "SUCCESS" for event in factor_events) / len(factor_events)
        for factor, factor_events in by_factor.items()
    }
    per_request_coverage = sum(event.status == "SUCCESS" for event in applicable) / len(applicable) if applicable else 0.0
    weighted_factors = {
        factor: coverage
        for factor, coverage in factor_coverage.items()
        if factor_weights.get(factor, 0.0) > 0
    }
    total_factor_weight = sum(factor_weights.get(factor, 0.0) for factor in weighted_factors)
    policy_weighted_coverage = (
        sum(factor_weights[factor] * weighted_factors[factor] for factor in weighted_factors) / total_factor_weight
        if total_factor_weight else 0.0
    )
    critical_failures = sum(
        event.status in {"FAILED", "STALE", "CONFLICT"}
        and (
            metric_definition(event.metric_key).is_critical_for(review_type)
            if review_type is not None
            else metric_definition(event.metric_key).critical
        )
        and metric_definition(event.metric_key).decision_role == "SCORING_FACTOR"
        for event in values
    )
    minimum = float(scoring_policy["minimum_investable_coverage"])
    medium = float(scoring_policy["medium_confidence_min_coverage"])
    high = float(scoring_policy["high_confidence_min_coverage"])
    if critical_failures or policy_weighted_coverage < minimum or policy_weighted_coverage < medium:
        confidence = "LOW"
    elif policy_weighted_coverage < high:
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"
    return {
        "requested": len(values),
        "counts": {status: counts.get(status, 0) for status in _STATUS_ORDER},
        "critical_failures": critical_failures,
        "coverage": policy_weighted_coverage,
        "evidence_coverage": policy_weighted_coverage,
        "per_request_coverage": per_request_coverage,
        "policy_weighted_coverage": policy_weighted_coverage,
        "factor_coverage": factor_coverage,
        "policy_factor_weights": {factor: factor_weights[factor] for factor in weighted_factors},
        "hard_critical_failure": bool(critical_failures),
        "confidence": confidence,
        "overlay_requested": len(values) - len(scoring_events),
        "review_type": review_type,
    }


def format_collection_event(
    event: CollectionEvent,
    observation: MetricObservation | None = None,
    previous: MetricObservation | None = None,
    *,
    review_type: str | None = None,
) -> str:
    if not isinstance(event, CollectionEvent):
        raise ValueError("event must be a CollectionEvent")
    current = observation.value if observation else None
    change = None
    if observation and previous and isinstance(current, (int, float)) and isinstance(previous.value, (int, float)) and previous.value != 0:
        change = (float(current) - float(previous.value)) / float(previous.value)
    summary = _display(current)
    if change is not None:
        summary = f"{summary} ({change:+.2%})"
    lines = [
        f"[DATA] {event.asset} {event.metric_key} {event.status} {summary}",
        f"       source: {event.source or (observation.source if observation else 'N/A')}",
        f"       observed_at: {event.observed_at or (observation.observed_at if observation else 'N/A')}",
        f"       fetched_at: {event.fetched_at or (observation.fetched_at if observation else event.timestamp)}",
    ]
    if event.refresh_provider:
        lines.append(f"       provider: {event.refresh_provider}")
    if event.refresh_endpoint:
        lines.append(f"       endpoint: {event.refresh_endpoint}")
    if event.refresh_error_code:
        lines.append(f"       error_code: {event.refresh_error_code}")
    if event.last_observation_at:
        lines.append(f"       last_observation: {event.last_observation_at} (STALE)")
    if observation is not None:
        lines.append(f"       Current: {_display(observation.value)}")
    if previous is not None:
        lines.append(f"       Previous: {_display(previous.value)}")
        lines.append(f"       Change: {f'{change:+.2%}' if change is not None else 'N/A'}")
        lines.append(
            f"       Trend: {classify_metric_change(event.metric_key, observation.value if observation else None, previous.value, stale=observation.freshness != 'CURRENT' if observation else False)}"
        )
    if event.reason:
        lines.append(f"       reason: {event.reason}")
    definition = metric_definition(event.metric_key)
    if definition.decision_role != "SCORING_FACTOR":
        effect = "context only; excluded from base scoring coverage"
    elif event.status == "SUCCESS":
        effect = "available for scoring/history"
    elif event.status == "NOT_APPLICABLE":
        effect = "excluded from applicable coverage"
    else:
        effect = "coverage/confidence reduced"
        definition = metric_definition(event.metric_key)
        hard_critical = definition.is_critical_for(review_type) if review_type is not None else definition.critical
        if hard_critical:
            effect += "; CRITICAL DATA FAILURE; high-conviction trade blocked"
        elif review_type is not None and definition.critical:
            effect += "; not hard-critical for this review"
    lines.append(f"       scoring_effect: {effect}")
    return "\n".join(lines)


def format_collection_summary(summary: Mapping[str, Any]) -> str:
    counts = summary["counts"]
    return "\n".join(
        (
            "Data Collection Summary",
            f"Requested metrics: {summary['requested']}",
            f"SUCCESS: {counts['SUCCESS']}  STALE: {counts['STALE']}  FAILED: {counts['FAILED']}",
            f"CONFLICT: {counts['CONFLICT']}  NOT_APPLICABLE: {counts['NOT_APPLICABLE']}",
            f"Critical failures: {summary['critical_failures']}",
            f"Per-request coverage: {summary.get('per_request_coverage', summary['coverage']):.0%}",
            f"Policy-weighted coverage: {summary.get('policy_weighted_coverage', summary['coverage']):.0%}",
            f"Decision confidence: {summary['confidence']}",
            f"Overlay context metrics: {summary.get('overlay_requested', 0)}",
        )
    )


def format_overlay_summary(overlays: Any) -> str:
    """Render compact positioning/cycle telemetry without raw source data."""
    from .models.market_overlays import MarketOverlays

    value = overlays if isinstance(overlays, MarketOverlays) else MarketOverlays.from_mapping(overlays)
    lines = ["Positioning & Cycle Context"]
    for symbol, facts in value.positioning_by_asset.items():
        lines.append(
            f"{symbol} Positioning: {facts.bias} / {facts.risk} "
            f"({facts.confidence} confidence; Social {facts.social_state})"
        )
    if value.btc_cycle is not None:
        cycle = value.btc_cycle
        lines.append(
            f"BTC Cycle: {cycle.market_cycle_state} / {cycle.cycle_risk} "
            f"({cycle.confidence} confidence; {cycle.halving_context})"
        )
    if value.effective_deployment_caps:
        lines.append("Effective deployment caps: " + ", ".join(
            f"{symbol} {factor:.0%}" for symbol, factor in value.effective_deployment_caps.items()
        ))
    lines.extend(value.warnings)
    return "\n".join(lines)


@dataclass
class CollectionReporter:
    """Persist and print collection results without polluting JSON stdout."""

    stream: TextIO | None = None
    observation_path: str | None = None
    event_path: str | None = None
    weights: Mapping[str, float] | None = None
    routing: Mapping[str, str] | None = None
    review_type: str | None = None
    policy: Policy | None = None

    def __post_init__(self) -> None:
        self.stream = self.stream or sys.stderr
        self.events: list[CollectionEvent] = []

    def record(
        self,
        event: CollectionEvent,
        observation: MetricObservation | None = None,
        previous: MetricObservation | None = None,
    ) -> None:
        if not isinstance(event, CollectionEvent):
            raise ValueError("event must be a CollectionEvent")
        if event.status == "SUCCESS":
            if not isinstance(observation, MetricObservation):
                raise ValueError("SUCCESS requires a MetricObservation")
            if observation.asset != event.asset or observation.metric_key != event.metric_key:
                raise ValueError("observation does not match collection event")
            if self.observation_path:
                append_metric_observation(observation, self.observation_path)
        if self.event_path:
            append_collection_event(event, self.event_path)
        self.events.append(event)
        print(format_collection_event(event, observation, previous, review_type=self.review_type), file=self.stream)

    def summary(self) -> dict[str, Any]:
        result = collection_summary(
            self.events,
            weights=self.weights,
            review_type=self.review_type,
            policy=self.policy,
        )
        if self.routing is not None:
            from .model_routing import routing_metadata

            result["routing_metadata"] = routing_metadata(self.routing)
        return result

    def record_result(self, result: Any, previous: MetricObservation | None = None) -> None:
        from .engine.metric_normalization import NormalizedMetricResult, normalize_metric_result

        normalized = result if isinstance(result, NormalizedMetricResult) else normalize_metric_result(result)
        self.record(normalized.event, normalized.observation, previous)

    def print_summary(self) -> dict[str, Any]:
        result = self.summary()
        print(format_collection_summary(result), file=self.stream)
        return result


DataCollectionLog = CollectionReporter


__all__ = [
    "CollectionReporter",
    "DataCollectionLog",
    "collection_summary",
    "format_collection_event",
    "format_collection_summary",
    "format_overlay_summary",
]
