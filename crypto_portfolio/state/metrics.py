"""Append-only metric observations and collection-event history."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from ..models.metrics_history import CollectionEvent, MetricObservation
from ..models.time import normalize_timestamp, parse_timestamp
from ._jsonl import append_record, read_records
from .snapshots import runtime_data_dir


def default_metrics_dir() -> Path:
    return runtime_data_dir() / "metrics"


def default_observations_path() -> Path:
    return default_metrics_dir() / "observations.jsonl"


def default_collection_events_path() -> Path:
    return default_metrics_dir() / "collection-events.jsonl"


def _observation(value: MetricObservation | Mapping[str, Any]) -> MetricObservation:
    return value if isinstance(value, MetricObservation) else MetricObservation.from_mapping(value)


def _event(value: CollectionEvent | Mapping[str, Any]) -> CollectionEvent:
    return value if isinstance(value, CollectionEvent) else CollectionEvent.from_mapping(value)


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return left == right


def _same_identity(left: MetricObservation, right: MetricObservation) -> bool:
    return (
        left.asset == right.asset
        and left.metric_key == right.metric_key
        and left.observed_at == right.observed_at
        and left.source == right.source
        and left.period == right.period
        and _same_value(left.value, right.value)
    )


def append_metric_observation(
    observation: MetricObservation | Mapping[str, Any],
    path: str | Path | None = None,
) -> Path:
    model = _observation(observation)
    destination = Path(path or default_observations_path())
    # Legacy records may no longer parse; they cannot collide with the new
    # observation's dedup identity, so read tolerantly and continue.
    invalid: list[str] = []
    existing = read_metric_observations(destination, invalid=invalid)
    for item in existing:
        if item.observation_id == model.observation_id:
            if _same_identity(item, model):
                return destination
            raise ValueError(f"duplicate observation_id {model.observation_id} has different content")
    same_point = [
        item for item in existing
        if (
            item.asset == model.asset
            and item.metric_key == model.metric_key
            and item.observed_at == model.observed_at
            and item.source == model.source
            and item.period == model.period
        )
    ]
    is_revision = model.supersedes_observation_id is not None
    if any(_same_value(item.value, model.value) for item in same_point) and not is_revision:
        return destination
    if same_point:
        if model.supersedes_observation_id not in {item.observation_id for item in same_point}:
            raise ValueError("revised observation must supersede the prior same-point observation")
    if model.supersedes_observation_id is not None and not any(
        item.observation_id == model.supersedes_observation_id for item in existing
    ):
        raise ValueError("supersedes_observation_id does not reference an existing observation")
    return append_record(destination, model.as_dict())


def append_collection_event(
    event: CollectionEvent | Mapping[str, Any],
    path: str | Path | None = None,
) -> Path:
    model = _event(event)
    destination = Path(path or default_collection_events_path())
    existing = read_records(destination)
    for item in existing:
        if item.get("event_id") == model.event_id:
            if item == model.as_dict():
                return destination
            raise ValueError(f"duplicate event_id {model.event_id} has different content")
    return append_record(destination, model.as_dict())


def read_metric_observations(
    path: str | Path | None = None,
    *,
    asset: str | None = None,
    metric_key: str | None = None,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    invalid: list[str] | None = None,
) -> list[MetricObservation]:
    """Read canonical observations.

    Persistence is strictly validated, but *historical* files are append-only
    and may contain records written before registry/schema changes. History
    must inform, not block, a new review, so records that no longer parse are
    skipped and reported through ``invalid`` (a collector for human-readable
    messages) instead of failing the whole read. Callers that require strict
    reads simply omit ``invalid`` and keep the raising behavior.
    """
    records: list[MetricObservation] = []
    for item in read_records(path or default_observations_path()):
        try:
            records.append(MetricObservation.from_mapping(item))
        except ValueError as exc:
            if invalid is None:
                raise
            invalid.append(str(exc))
    if asset is not None and (not isinstance(asset, str) or not asset.strip()):
        raise ValueError("asset must be a non-empty string or null")
    normalized_asset = asset.strip().upper() if asset is not None else None
    normalized_key = None if metric_key is None else metric_definition(metric_key).key
    start_time = None if start is None else parse_timestamp(normalize_timestamp(start.isoformat() if isinstance(start, datetime) else start, "start"))
    end_time = None if end is None else parse_timestamp(normalize_timestamp(end.isoformat() if isinstance(end, datetime) else end, "end"))
    result = [
        item for item in records
        if (normalized_asset is None or item.asset == normalized_asset)
        and (normalized_key is None or item.metric_key == normalized_key)
        and (start_time is None or parse_timestamp(item.observed_at) >= start_time)
        and (end_time is None or parse_timestamp(item.observed_at) <= end_time)
    ]
    return result


def read_collection_events(
    path: str | Path | None = None,
    *,
    invalid: list[str] | None = None,
) -> list[CollectionEvent]:
    """Read collection events, skipping legacy records that no longer parse.

    See :func:`read_metric_observations` for the historical-file rationale.
    """
    records: list[CollectionEvent] = []
    for item in read_records(path or default_collection_events_path()):
        try:
            records.append(CollectionEvent.from_mapping(item))
        except ValueError as exc:
            if invalid is None:
                raise
            invalid.append(str(exc))
    return records


default_metric_observation_path = default_observations_path
default_collection_event_path = default_collection_events_path
append_observation = append_metric_observation
read_observations = read_metric_observations


def _series(
    asset: str,
    metric_key: str,
    *,
    path: str | Path | None = None,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    invalid: list[str] | None = None,
) -> list[MetricObservation]:
    values = read_metric_observations(
        path, asset=asset, metric_key=metric_key, start=start, end=end, invalid=invalid
    )
    return sorted(enumerate(values), key=lambda item: (parse_timestamp(item[1].observed_at), item[0]))


def metric_series(
    asset: str,
    metric_key: str,
    *,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    path: str | Path | None = None,
    invalid: list[str] | None = None,
) -> list[MetricObservation]:
    return [
        item
        for _, item in _series(
            asset, metric_key, path=path, start=start, end=end, invalid=invalid
        )
    ]


def latest_metric(
    asset: str,
    metric_key: str,
    *,
    path: str | Path | None = None,
    invalid: list[str] | None = None,
) -> MetricObservation | None:
    values = metric_series(asset, metric_key, path=path, invalid=invalid)
    return values[-1] if values else None


def observation_is_fresh(
    observation: MetricObservation,
    *,
    as_of: str | datetime | None = None,
) -> bool:
    """Check registry freshness without trusting JSONL file order."""
    if not isinstance(observation, MetricObservation) or observation.freshness != "CURRENT":
        return False
    cutoff = parse_timestamp(
        normalize_timestamp(
            as_of.isoformat() if isinstance(as_of, datetime) else as_of,
            "as_of",
        )
    ) if as_of is not None else datetime.now().astimezone()
    observed = parse_timestamp(observation.observed_at)
    age = (cutoff - observed).total_seconds()
    if age < 0:
        return False
    definition = metric_definition(observation.metric_key)
    days = definition.freshness_days
    return days is None or age <= days * 86400


def latest_usable_observation(
    asset: str,
    metric_key: str,
    *,
    as_of: str | datetime | None = None,
    path: str | Path | None = None,
    observations: Iterable[MetricObservation | Mapping[str, Any]] | None = None,
    invalid: list[str] | None = None,
) -> MetricObservation | None:
    """Return the newest compatible observation, not merely the newest line."""
    normalized_asset = asset.strip().upper()
    normalized_key = metric_definition(metric_key).key
    if observations is None:
        values = metric_series(normalized_asset, normalized_key, path=path, invalid=invalid)
    else:
        source: Any = observations
        if isinstance(source, Mapping):
            source = source.get("observations", source.values())
        if isinstance(source, MetricObservation):
            source = (source,)
        values = [
            item if isinstance(item, MetricObservation) else MetricObservation.from_mapping(item)
            for item in source
        ]
    candidates = [
        item for item in values
        if item.asset == normalized_asset
        and item.metric_key == normalized_key
        and observation_is_fresh(item, as_of=as_of)
    ]
    return max(candidates, key=lambda item: (parse_timestamp(item.observed_at), item.observation_id), default=None)


def previous_metric(
    asset: str,
    metric_key: str,
    *,
    path: str | Path | None = None,
    invalid: list[str] | None = None,
) -> MetricObservation | None:
    values = metric_series(asset, metric_key, path=path, invalid=invalid)
    if not values:
        return None
    latest_time = values[-1].observed_at
    latest_source = values[-1].source
    source_transition_guard = metric_definition(metric_key).key.startswith("flows.etf_")
    for item in reversed(values[:-1]):
        if item.observed_at != latest_time and (not source_transition_guard or item.source == latest_source):
            return item
    return None


def _numeric_change(latest: Any, previous: Any) -> tuple[float | None, float | None]:
    if not isinstance(latest, (int, float)) or isinstance(latest, bool):
        return None, None
    if not isinstance(previous, (int, float)) or isinstance(previous, bool):
        return None, None
    absolute = float(latest) - float(previous)
    percentage = None if previous == 0 else absolute / float(previous)
    return absolute, percentage


def classify_metric_change(metric_key: str, latest: Any, previous: Any, *, stale: bool = False) -> str:
    definition = metric_definition(metric_key)
    if latest is None or previous is None or not definition.trend_comparison_enabled:
        return "INSUFFICIENT_HISTORY"
    if stale:
        return "CONFLICTING"
    if isinstance(latest, (int, float)) and not isinstance(latest, bool) and isinstance(previous, (int, float)) and not isinstance(previous, bool):
        if math.isclose(float(latest), float(previous), rel_tol=1e-12, abs_tol=1e-12):
            return "STABLE"
        rising = float(latest) > float(previous)
        if definition.direction == "HIGHER_IS_BETTER":
            return "IMPROVING" if rising else "DETERIORATING"
        if definition.direction == "LOWER_IS_BETTER":
            return "DETERIORATING" if rising else "IMPROVING"
        return "CONFLICTING"
    return "STABLE" if latest == previous else "CONFLICTING"


def trend_summary(
    asset: str,
    metric_key: str,
    *,
    path: str | Path | None = None,
    limit: int | None = None,
    invalid: list[str] | None = None,
) -> dict[str, Any]:
    values = metric_series(asset, metric_key, path=path, invalid=invalid)
    if values and metric_definition(metric_key).key.startswith("flows.etf_"):
        source = values[-1].source
        compatible: list[MetricObservation] = []
        for item in reversed(values):
            if item.source != source:
                break
            compatible.append(item)
        values = list(reversed(compatible))
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer or null")
        values = values[-limit:]
    result: dict[str, Any] = {
        "asset": asset.strip().upper(),
        "metric_key": metric_definition(metric_key).key,
        "observation_count": len(values),
        "values": [item.value for item in values],
        "observation_ids": [item.observation_id for item in values],
        "trend": "INSUFFICIENT_HISTORY",
        "consecutive_improvements": 0,
        "consecutive_deteriorations": 0,
        "slope_per_day": None,
    }
    if len(values) < 2:
        return result
    pair_trends = [
        classify_metric_change(metric_key, right.value, left.value, stale=right.freshness != "CURRENT")
        for left, right in zip(values, values[1:])
    ]
    result["trend"] = pair_trends[-1] if pair_trends else "INSUFFICIENT_HISTORY"
    for trend_name, key in (("IMPROVING", "consecutive_improvements"), ("DETERIORATING", "consecutive_deteriorations")):
        count = 0
        for item in reversed(pair_trends):
            if item != trend_name:
                break
            count += 1
        result[key] = count
    first, last = values[0], values[-1]
    if isinstance(first.value, (int, float)) and not isinstance(first.value, bool) and isinstance(last.value, (int, float)) and not isinstance(last.value, bool):
        elapsed = (parse_timestamp(last.observed_at) - parse_timestamp(first.observed_at)).total_seconds() / 86400
        if elapsed > 0:
            result["slope_per_day"] = (float(last.value) - float(first.value)) / elapsed
    return result


def compare_latest_metric(
    asset: str,
    metric_key: str,
    *,
    path: str | Path | None = None,
    invalid: list[str] | None = None,
) -> dict[str, Any]:
    latest = latest_metric(asset, metric_key, path=path, invalid=invalid)
    previous = previous_metric(asset, metric_key, path=path, invalid=invalid)
    canonical_key = metric_definition(metric_key).key
    result: dict[str, Any] = {
        "asset": asset.strip().upper(),
        "metric_key": canonical_key,
        "latest": latest.as_dict() if latest else None,
        "previous": previous.as_dict() if previous else None,
        "latest_value": latest.value if latest else None,
        "previous_value": previous.value if previous else None,
        "latest_observation_id": latest.observation_id if latest else None,
        "previous_observation_id": previous.observation_id if previous else None,
        "absolute_change": None,
        "percentage_change": None,
        "change_pct": None,
        "elapsed_seconds": None,
        "elapsed_days": None,
        "trend": "INSUFFICIENT_HISTORY",
        "stale": bool(latest and latest.freshness != "CURRENT"),
    }
    result["recent_trend"] = trend_summary(asset, canonical_key, path=path, limit=3, invalid=invalid)
    if latest is None or previous is None:
        return result
    absolute, percentage = _numeric_change(latest.value, previous.value)
    result["absolute_change"] = absolute
    result["percentage_change"] = percentage
    result["change_pct"] = percentage
    elapsed = (parse_timestamp(latest.observed_at) - parse_timestamp(previous.observed_at)).total_seconds()
    result["elapsed_seconds"] = elapsed
    result["elapsed_days"] = elapsed / 86400
    result["trend"] = classify_metric_change(canonical_key, latest.value, previous.value, stale=latest.freshness != "CURRENT")
    return result


def metric_history_context(
    asset: str,
    metric_keys: Iterable[str],
    *,
    path: str | Path | None = None,
    invalid: list[str] | None = None,
) -> dict[str, Any]:
    normalized_asset = asset.strip().upper()
    return {
        metric_definition(key).key: compare_latest_metric(
            normalized_asset, key, path=path, invalid=invalid
        )
        for key in metric_keys
    }


__all__ = [
    "append_collection_event",
    "append_metric_observation",
    "append_observation",
    "classify_metric_change",
    "compare_latest_metric",
    "default_collection_events_path",
    "default_metrics_dir",
    "default_observations_path",
    "default_metric_observation_path",
    "default_collection_event_path",
    "latest_metric",
    "latest_usable_observation",
    "metric_history_context",
    "metric_series",
    "observation_is_fresh",
    "previous_metric",
    "read_collection_events",
    "read_metric_observations",
    "read_observations",
    "trend_summary",
]
