"""Compact, stderr-friendly data collection telemetry."""

from __future__ import annotations

import sys
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, TextIO

from .metrics_registry import metric_definition
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
) -> dict[str, Any]:
    values = tuple(events)
    if any(not isinstance(event, CollectionEvent) for event in values):
        raise ValueError("events must contain CollectionEvent objects")
    counts = Counter(event.status for event in values)
    applicable = [event for event in values if event.status != "NOT_APPLICABLE"]
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
        and metric_definition(event.metric_key).critical
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
    }


def format_collection_event(
    event: CollectionEvent,
    observation: MetricObservation | None = None,
    previous: MetricObservation | None = None,
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
    if event.status == "SUCCESS":
        effect = "available for scoring/history"
    elif event.status == "NOT_APPLICABLE":
        effect = "excluded from applicable coverage"
    else:
        effect = "coverage/confidence reduced"
        if metric_definition(event.metric_key).critical:
            effect += "; CRITICAL DATA FAILURE; high-conviction trade blocked"
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
        )
    )


@dataclass
class CollectionReporter:
    """Persist and print collection results without polluting JSON stdout."""

    stream: TextIO | None = None
    observation_path: str | None = None
    event_path: str | None = None
    weights: Mapping[str, float] | None = None

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
        print(format_collection_event(event, observation, previous), file=self.stream)

    def summary(self) -> dict[str, Any]:
        return collection_summary(self.events, weights=self.weights)

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
]
