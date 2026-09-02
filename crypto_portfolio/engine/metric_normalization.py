"""Validation and normalization at the metric-collection boundary."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ..metrics_registry import MetricDefinition, metric_definition, validate_metric_value
from .metric_plan import MetricCollectionPlan
from ..models.metrics_history import CollectionEvent, MetricObservation, stable_observation_id
from ..models.time import normalize_timestamp, parse_timestamp
from ..state.metrics import append_collection_event, append_metric_observation


_STATUSES = {"SUCCESS", "FAILED", "STALE", "CONFLICT", "NOT_APPLICABLE"}
_FRESHNESS = {"CURRENT", "STALE", "UNKNOWN"}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_UNIT_ALIASES = {
    "$": "USD",
    "US DOLLAR": "USD",
    "USD": "USD",
    "%": "fraction",
    "PERCENT": "fraction",
    "PERCENTAGE": "fraction",
    "FRACTION": "fraction",
    "RATIO": "ratio",
    "COUNT": "count",
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _timestamp(value: Any, field: str, *, fallback: str | None = None) -> str:
    if value is None:
        if fallback is None:
            raise ValueError(f"{field} is required")
        value = fallback
    return normalize_timestamp(value, field)


def _now(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    raw = value.isoformat() if isinstance(value, datetime) else value
    return normalize_timestamp(raw, "now")


def _status(value: Any) -> str:
    result = _text(value, "status").upper()
    if result not in _STATUSES:
        raise ValueError(f"status must be one of {sorted(_STATUSES)}")
    return result


def _confidence(value: Any) -> str:
    result = _text(value, "confidence").upper()
    if result not in _CONFIDENCE:
        raise ValueError(f"confidence must be one of {sorted(_CONFIDENCE)}")
    return result


def _unit_and_value(
    value: Any,
    supplied_unit: Any,
    definition: MetricDefinition,
) -> tuple[Any, str | None]:
    if isinstance(value, bool):
        raise ValueError("metric value must not be boolean")
    expected = definition.unit
    if expected is None:
        if supplied_unit is None:
            return value, None
        unit = _text(supplied_unit, "unit").upper()
        return value, unit
    unit = expected
    if supplied_unit is not None:
        raw_unit = _text(supplied_unit, "unit").upper()
        canonical = _UNIT_ALIASES.get(raw_unit, raw_unit)
        expected_canonical = _UNIT_ALIASES.get(expected.upper(), expected.upper())
        if canonical != expected_canonical:
            raise ValueError(f"metric {definition.key} requires unit {expected}")
        if raw_unit in {"%", "PERCENT", "PERCENTAGE"}:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError("percentage metric value must be numeric")
            value = float(value) / 100.0
    if value is not None:
        if definition.expected_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"metric {definition.key} value must be numeric")
            if not math.isfinite(float(value)):
                raise ValueError(f"metric {definition.key} value must be finite")
        elif not isinstance(value, str):
            raise ValueError(f"metric {definition.key} value must be a string")
    return value, unit


def _freshness_from_age(
    definition: MetricDefinition,
    observed_at: str,
    as_of: str | datetime | None,
) -> str:
    if as_of is None:
        return "CURRENT"
    window = definition.freshness
    if not isinstance(window, str) or not window.lower().endswith("d"):
        return "CURRENT"
    age = (parse_timestamp(_timestamp(as_of, "as_of")) - parse_timestamp(observed_at)).total_seconds()
    if age < 0:
        raise ValueError("observed_at must not be after as_of")
    return "CURRENT" if age <= int(window[:-1]) * 86400 else "STALE"


def normalize_metric_observation(
    value: MetricObservation | Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    as_of: str | datetime | None = None,
    decision_id: str | None = None,
    review_type: str | None = None,
) -> MetricObservation:
    """Turn one successful collector response into a canonical observation."""
    if isinstance(value, MetricObservation):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("metric observation must be an object")
    allowed = {
        "observation_id", "asset", "metric_key", "status", "value", "unit", "period",
        "observed_at", "fetched_at", "source", "freshness", "confidence", "decision_id",
        "review_type", "summary", "metadata", "supersedes_observation_id", "revision_reason",
        "timestamp", "event_id",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"metric result contains unknown fields: {', '.join(sorted(unknown))}")
    status = _status(value.get("status", "SUCCESS"))
    if status != "SUCCESS":
        raise ValueError("only SUCCESS results can become MetricObservation")
    asset = _text(value.get("asset"), "asset").upper()
    definition = metric_definition(value.get("metric_key"))
    if not definition.applies_to(asset):
        raise ValueError(f"metric {definition.key} is not applicable to {asset}")
    observed_at = _timestamp(value.get("observed_at"), "observed_at")
    now_value = _now(now)
    fetched_at = _timestamp(value.get("fetched_at"), "fetched_at", fallback=now_value or observed_at)
    observation_value, unit = _unit_and_value(value.get("value"), value.get("unit"), definition)
    if observation_value is None:
        raise ValueError("successful metric observations require a value")
    validate_metric_value(definition.key, observation_value)
    if as_of is not None and parse_timestamp(observed_at) > parse_timestamp(_timestamp(as_of, "as_of")):
        raise ValueError("observed_at must not be after as_of")
    freshness = value.get("freshness")
    freshness = _freshness_from_age(definition, observed_at, as_of) if freshness is None else _text(freshness, "freshness").upper()
    if freshness not in _FRESHNESS:
        raise ValueError(f"freshness must be one of {sorted(_FRESHNESS)}")
    confidence = _confidence(value.get("confidence", "LOW"))
    observation_id = value.get("observation_id") or stable_observation_id(
        asset,
        definition.key,
        observed_at,
        _text(value.get("source"), "source"),
        observation_value,
        value.get("period"),
    )
    return MetricObservation(
        observation_id=observation_id,
        asset=asset,
        metric_key=definition.key,
        factor=definition.factor,
        value=observation_value,
        unit=unit,
        period=value.get("period"),
        observed_at=observed_at,
        fetched_at=fetched_at,
        source=value["source"],
        freshness=freshness,
        confidence=confidence,
        decision_id=value.get("decision_id", decision_id),
        review_type=value.get("review_type", review_type),
        summary=value.get("summary"),
        metadata=value.get("metadata"),
        supersedes_observation_id=value.get("supersedes_observation_id"),
        revision_reason=value.get("revision_reason"),
    )


def _event_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"collection:{hashlib.sha256(encoded).hexdigest()}"


def normalize_collection_event(
    value: CollectionEvent | Mapping[str, Any],
    *,
    now: str | datetime | None = None,
) -> CollectionEvent:
    if isinstance(value, CollectionEvent):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("collection event must be an object")
    allowed = {
        "event_id", "timestamp", "asset", "metric_key", "status", "reason", "source",
        "observed_at", "decision_id", "fetched_at",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"collection event contains unknown fields: {', '.join(sorted(unknown))}")
    status = _status(value.get("status"))
    asset = _text(value.get("asset"), "asset").upper()
    key = metric_definition(value.get("metric_key")).key
    timestamp = _timestamp(
        value.get("timestamp") or value.get("fetched_at") or value.get("observed_at"),
        "timestamp",
        fallback=_now(now),
    )
    reason = value.get("reason")
    if status != "SUCCESS" and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError(f"reason is required for {status}")
    event_payload = {
        "timestamp": timestamp,
        "asset": asset,
        "metric_key": key,
        "status": status,
        "reason": reason,
        "source": value.get("source"),
        "observed_at": value.get("observed_at"),
        "fetched_at": value.get("fetched_at"),
        "decision_id": value.get("decision_id"),
    }
    return CollectionEvent(
        event_id=value.get("event_id") or _event_id(event_payload),
        timestamp=timestamp,
        asset=asset,
        metric_key=key,
        status=status,
        reason=reason,
        source=value.get("source"),
        observed_at=value.get("observed_at"),
        decision_id=value.get("decision_id"),
        fetched_at=value.get("fetched_at"),
    )


@dataclass(frozen=True)
class NormalizedMetricResult:
    """Canonical result for one requested metric."""

    status: str
    observation: MetricObservation | None
    event: CollectionEvent

    def __post_init__(self) -> None:
        status = _status(self.status)
        if status == "SUCCESS" and self.observation is None:
            raise ValueError("SUCCESS requires an observation")
        if status != "SUCCESS" and self.observation is not None:
            raise ValueError(f"{status} cannot carry an observation")
        if self.event.status != status:
            raise ValueError("result status and event status must match")
        object.__setattr__(self, "status", status)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "observation": self.observation.as_dict() if self.observation else None,
            "event": self.event.as_dict(),
        }


def normalize_metric_result(
    value: Mapping[str, Any] | MetricObservation | CollectionEvent | NormalizedMetricResult,
    *,
    now: str | datetime | None = None,
    as_of: str | datetime | None = None,
    decision_id: str | None = None,
    review_type: str | None = None,
) -> NormalizedMetricResult:
    if isinstance(value, NormalizedMetricResult):
        return value
    if isinstance(value, MetricObservation):
        raw = value.as_dict()
    elif isinstance(value, CollectionEvent):
        raw = value.as_dict()
    elif isinstance(value, Mapping):
        raw = value
    else:
        raise ValueError("metric result must be a mapping, MetricObservation, or CollectionEvent")
    status = _status(raw.get("status", "SUCCESS"))
    if status == "SUCCESS":
        observation = normalize_metric_observation(
            raw,
            now=now,
            as_of=as_of,
            decision_id=decision_id,
            review_type=review_type,
        )
        event = normalize_collection_event(
            {
                "event_id": raw.get("event_id"),
                "timestamp": raw.get("timestamp") or observation.fetched_at,
                "asset": observation.asset,
                "metric_key": observation.metric_key,
                "status": status,
                "source": observation.source,
                "observed_at": observation.observed_at,
                "fetched_at": observation.fetched_at,
                "decision_id": observation.decision_id,
            },
            now=now,
        )
        return NormalizedMetricResult(status, observation, event)
    event = normalize_collection_event(raw, now=now)
    return NormalizedMetricResult(status, None, event)


def persist_metric_result(
    result: NormalizedMetricResult | Mapping[str, Any],
    *,
    observation_path: str | Path | None = None,
    event_path: str | Path | None = None,
) -> NormalizedMetricResult:
    normalized = result if isinstance(result, NormalizedMetricResult) else normalize_metric_result(result)
    if normalized.observation is not None:
        append_metric_observation(normalized.observation, observation_path)
    append_collection_event(normalized.event, event_path)
    return normalized


def normalize_collection_results(
    plan: MetricCollectionPlan,
    results: list[Mapping[str, Any] | MetricObservation | CollectionEvent] | tuple[Any, ...],
    *,
    now: str | datetime | None = None,
    as_of: str | datetime | None = None,
    decision_id: str | None = None,
    review_type: str | None = None,
) -> tuple[NormalizedMetricResult, ...]:
    """Require one result for every planned request; omissions fail closed."""
    if not isinstance(plan, MetricCollectionPlan):
        raise ValueError("plan must be a MetricCollectionPlan")
    if isinstance(results, (str, bytes)) or not isinstance(results, (list, tuple)):
        raise ValueError("results must be a list or tuple")
    normalized = tuple(
        normalize_metric_result(
            item,
            now=now,
            as_of=as_of,
            decision_id=decision_id,
            review_type=review_type,
        )
        for item in results
    )
    expected = {(item.asset, item.metric_key) for item in plan.requests}
    actual = {(item.event.asset, item.event.metric_key) for item in normalized}
    if len(actual) != len(normalized):
        raise ValueError("collection results contain duplicate asset/metric keys")
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        message = []
        if missing:
            message.append("missing: " + ", ".join(f"{asset}:{key}" for asset, key in sorted(missing)))
        if extra:
            message.append("unexpected: " + ", ".join(f"{asset}:{key}" for asset, key in sorted(extra)))
        raise ValueError("collection results do not match plan (" + "; ".join(message) + ")")
    by_identity = {(item.event.asset, item.event.metric_key): item for item in normalized}
    return tuple(by_identity[identity] for identity in ((item.asset, item.metric_key) for item in plan.requests))


def persist_collection_results(
    plan: MetricCollectionPlan,
    results: list[Mapping[str, Any] | MetricObservation | CollectionEvent] | tuple[Any, ...],
    *,
    observation_path: str | Path | None = None,
    event_path: str | Path | None = None,
    now: str | datetime | None = None,
    as_of: str | datetime | None = None,
    decision_id: str | None = None,
    review_type: str | None = None,
) -> tuple[NormalizedMetricResult, ...]:
    normalized = normalize_collection_results(
        plan,
        results,
        now=now,
        as_of=as_of,
        decision_id=decision_id,
        review_type=review_type,
    )
    for result in normalized:
        persist_metric_result(
            result,
            observation_path=observation_path,
            event_path=event_path,
        )
    return normalized


def validate_metric_observation(value: MetricObservation | Mapping[str, Any]) -> bool:
    normalize_metric_observation(value)
    return True


normalize_observation = normalize_metric_observation
normalise_metric_observation = normalize_metric_observation
normalize_result = normalize_metric_result
normalize_metric = normalize_metric_observation


__all__ = [
    "NormalizedMetricResult",
    "normalize_collection_event",
    "normalize_metric_observation",
    "normalize_metric",
    "normalize_metric_result",
    "normalize_collection_results",
    "normalize_observation",
    "normalise_metric_observation",
    "normalize_result",
    "persist_metric_result",
    "persist_collection_results",
    "validate_metric_observation",
]
