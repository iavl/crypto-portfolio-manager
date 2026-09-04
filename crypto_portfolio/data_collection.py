"""Compact, stderr-friendly data collection telemetry."""

from __future__ import annotations

import sys
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, TextIO

from .metrics_registry import REVIEW_TYPES, metric_definition
from .models.metrics_history import CollectionEvent, MetricObservation
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
    if weights is None:
        coverage = (
            sum(event.status == "SUCCESS" for event in applicable) / len(applicable)
            if applicable else 0.0
        )
    else:
        if not isinstance(weights, Mapping):
            raise ValueError("weights must be an object")
        total = successful = 0.0
        for event in applicable:
            definition = metric_definition(event.metric_key)
            raw_weight = weights.get(event.metric_key, weights.get(definition.factor, 0.0))
            if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
                raise ValueError("collection weights must be finite non-negative numbers")
            weight = float(raw_weight)
            if not math.isfinite(weight) or weight < 0:
                raise ValueError("collection weights must be finite non-negative numbers")
            total += weight
            if event.status == "SUCCESS":
                successful += weight
        coverage = successful / total if total else 0.0
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
    if critical_failures or coverage < 0.7:
        confidence = "LOW"
    elif coverage < 0.9:
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"
    return {
        "requested": len(values),
        "counts": {status: counts.get(status, 0) for status in _STATUS_ORDER},
        "critical_failures": critical_failures,
        "coverage": coverage,
        "evidence_coverage": coverage,
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
            f"Overall evidence coverage: {summary['coverage']:.0%}",
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
        result = collection_summary(self.events, weights=self.weights, review_type=self.review_type)
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
