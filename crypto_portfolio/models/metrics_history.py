"""Canonical historical metric and collection-event records."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..metrics_registry import metric_definition, normalize_metric_key, validate_metric_value
from .time import normalize_timestamp, parse_timestamp


_FRESHNESS = {"CURRENT", "STALE", "UNKNOWN"}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_STATUSES = {"SUCCESS", "FAILED", "STALE", "CONFLICT", "NOT_APPLICABLE"}
_REVIEW_TYPES = {"SNAPSHOT_REVIEW", "FULL_REVIEW", "EVENT_REVIEW"}
_PRIVATE_REASONING_FIELDS = {"chain_of_thought", "scratchpad", "private_reasoning", "hidden_reasoning"}
_SECRET_FIELDS = {"api_key", "apikey", "api_secret", "authorization", "cookie", "password", "secret", "token"}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, field: str) -> str | None:
    return None if value is None else _text(value, field)


def _hash(value: Any, field: str) -> str:
    result = _text(value, field).lower()
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return result


def _json_value(value: Any, field: str) -> Any:
    if isinstance(value, bool):
        raise ValueError(f"{field} must not be boolean")
    if isinstance(value, (int, float)) and not math.isfinite(float(value)):
        raise ValueError(f"{field} must be finite")
    return value


def _contains_credential_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if (
                normalized in _SECRET_FIELDS
                or "api_key" in normalized
                or normalized.endswith(("_secret", "_token"))
                or "authorization" in normalized
                or _contains_credential_key(item)
            ):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_credential_key(item) for item in value)
    return False


def stable_observation_id(
    asset: str,
    metric_key: str,
    observed_at: str,
    source: str,
    value: Any,
    period: str | None = None,
) -> str:
    """Return the stable identity for one source observation."""
    key = normalize_metric_key(metric_key)
    metric_definition(key)
    value = _json_value(value, "observation.value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = float(value)
    normalized_period = None if period is None else _text(period, "period")
    payload = {
        "asset": _text(asset, "asset").upper(),
        "metric_key": key,
        "observed_at": normalize_timestamp(observed_at, "observed_at"),
        "source": _text(source, "source"),
        "value": value,
        "period": normalized_period,
    }
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("observation identity value must be JSON serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


observation_id_for = stable_observation_id


@dataclass(frozen=True)
class MetricObservation:
    observation_id: str
    asset: str
    metric_key: str
    factor: str
    value: float | int | str | None
    unit: str | None
    period: str | None
    observed_at: str
    fetched_at: str
    source: str
    freshness: str
    confidence: str
    decision_id: str | None = None
    review_type: str | None = None
    metadata: Mapping[str, Any] | None = None
    supersedes_observation_id: str | None = None
    revision_reason: str | None = None
    summary: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _text(self.observation_id, "observation_id"))
        object.__setattr__(self, "asset", _text(self.asset, "asset").upper())
        key = normalize_metric_key(self.metric_key)
        definition = metric_definition(key)
        object.__setattr__(self, "metric_key", key)
        factor = _text(self.factor, "factor").lower()
        if factor != definition.factor:
            raise ValueError(f"metric {key} must use factor {definition.factor}")
        object.__setattr__(self, "factor", factor)
        value = _json_value(self.value, "observation.value")
        if value is not None:
            if definition.expected_type == "number" and not isinstance(value, (int, float)):
                raise ValueError(f"metric {key} value must be numeric")
            if definition.expected_type == "string" and not isinstance(value, str):
                raise ValueError(f"metric {key} value must be a string")
            validate_metric_value(key, value)
        object.__setattr__(self, "value", value)
        unit = _optional_text(self.unit, "unit")
        if unit is not None and definition.unit is not None and unit.upper() != definition.unit.upper():
            raise ValueError(f"metric {key} must use unit {definition.unit}")
        if unit is not None and definition.unit is not None:
            unit = definition.unit
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "period", _optional_text(self.period, "period"))
        object.__setattr__(self, "observed_at", normalize_timestamp(self.observed_at, "observed_at"))
        object.__setattr__(self, "fetched_at", normalize_timestamp(self.fetched_at, "fetched_at"))
        if parse_timestamp(self.fetched_at) < parse_timestamp(self.observed_at):
            raise ValueError("fetched_at must be at or after observed_at")
        object.__setattr__(self, "source", _text(self.source, "source"))
        freshness = _text(self.freshness, "freshness").upper()
        if freshness not in _FRESHNESS:
            raise ValueError(f"freshness must be one of {sorted(_FRESHNESS)}")
        object.__setattr__(self, "freshness", freshness)
        confidence = _text(self.confidence, "confidence").upper()
        if confidence not in _CONFIDENCE:
            raise ValueError(f"confidence must be one of {sorted(_CONFIDENCE)}")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "decision_id", _optional_text(self.decision_id, "decision_id"))
        review_type = _optional_text(self.review_type, "review_type")
        if review_type is not None:
            review_type = review_type.upper()
            if review_type not in _REVIEW_TYPES:
                raise ValueError(f"review_type must be one of {sorted(_REVIEW_TYPES)}")
        object.__setattr__(self, "review_type", review_type)
        object.__setattr__(self, "summary", _optional_text(self.summary, "summary"))
        if self.metadata is not None:
            if not isinstance(self.metadata, Mapping):
                raise ValueError("metadata must be an object or null")
            metadata = dict(self.metadata)
            if any(str(key).strip().lower() in _PRIVATE_REASONING_FIELDS for key in metadata):
                raise ValueError("metadata must not contain private reasoning")
            if _contains_credential_key(metadata):
                raise ValueError("metadata must not contain credentials")
            try:
                json.dumps(metadata, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("metadata must be JSON serializable and finite") from exc
            object.__setattr__(self, "metadata", metadata)
        supersedes = _optional_text(self.supersedes_observation_id, "supersedes_observation_id")
        reason = _optional_text(self.revision_reason, "revision_reason")
        if reason is not None and supersedes is None:
            raise ValueError("revision_reason requires supersedes_observation_id")
        if supersedes is not None and reason is None:
            raise ValueError("supersedes_observation_id requires revision_reason")
        object.__setattr__(self, "supersedes_observation_id", supersedes)
        object.__setattr__(self, "revision_reason", reason)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MetricObservation":
        if not isinstance(value, Mapping):
            raise ValueError("metric observation must be an object")
        allowed = {
            "observation_id", "asset", "metric_key", "factor", "value", "unit", "period",
            "observed_at", "fetched_at", "source", "freshness", "confidence", "decision_id",
            "review_type", "summary", "metadata", "supersedes_observation_id", "revision_reason",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"metric observation contains unknown fields: {', '.join(sorted(unknown))}")
        required = {
            "observation_id", "asset", "metric_key", "factor", "value",
            "observed_at", "fetched_at", "source", "freshness", "confidence",
        }
        missing = required - set(value)
        if missing:
            raise ValueError(f"metric observation is missing fields: {', '.join(sorted(missing))}")
        data = {field: value.get(field) for field in allowed if field in value}
        data.setdefault("unit", None)
        data.setdefault("period", None)
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "observation_id": self.observation_id,
            "asset": self.asset,
            "metric_key": self.metric_key,
            "factor": self.factor,
            "value": self.value,
            "unit": self.unit,
            "period": self.period,
            "observed_at": self.observed_at,
            "fetched_at": self.fetched_at,
            "source": self.source,
            "freshness": self.freshness,
            "confidence": self.confidence,
        }
        for field in ("decision_id", "review_type", "summary", "metadata", "supersedes_observation_id", "revision_reason"):
            value = getattr(self, field)
            if value is not None:
                result[field] = dict(value) if field == "metadata" else value
        return result

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def to_evidence(self):
        """Project the observation into an immutable Decision Evidence record."""
        from .evidence import Evidence

        metadata = dict(self.metadata or {})
        metadata.update({
            "observation_id": self.observation_id,
            "metric_key": self.metric_key,
            "unit": self.unit,
            "period": self.period,
            "decision_role": metric_definition(self.metric_key).decision_role,
            "context_group": metric_definition(self.metric_key).context_group,
        })
        if self.supersedes_observation_id is not None:
            metadata.update({
                "supersedes_observation_id": self.supersedes_observation_id,
                "revision_reason": self.revision_reason,
            })
        return Evidence(
            id=self.observation_id,
            asset=self.asset,
            factor=self.factor,
            source=self.source,
            observed_at=self.observed_at,
            fetched_at=self.fetched_at,
            freshness=self.freshness,
            confidence=self.confidence,
            value=self.value,
            summary=self.summary,
            metadata=metadata,
        )

    as_evidence = to_evidence


@dataclass(frozen=True)
class CollectionEvent:
    event_id: str
    timestamp: str
    asset: str
    metric_key: str
    status: str
    reason: str | None = None
    source: str | None = None
    observed_at: str | None = None
    decision_id: str | None = None
    fetched_at: str | None = None
    last_observation_id: str | None = None
    last_observation_at: str | None = None
    refresh_provider: str | None = None
    refresh_endpoint: str | None = None
    refresh_error_code: str | None = None
    refresh_error_detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _text(self.event_id, "event_id"))
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp, "timestamp"))
        object.__setattr__(self, "asset", _text(self.asset, "asset").upper())
        object.__setattr__(self, "metric_key", normalize_metric_key(self.metric_key))
        metric_definition(self.metric_key)
        status = _text(self.status, "status").upper()
        if status not in _STATUSES:
            raise ValueError(f"status must be one of {sorted(_STATUSES)}")
        object.__setattr__(self, "status", status)
        reason = _optional_text(self.reason, "reason")
        if status != "SUCCESS" and reason is None:
            raise ValueError(f"reason is required for {status}")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "source", _optional_text(self.source, "source"))
        if self.observed_at is not None:
            object.__setattr__(self, "observed_at", normalize_timestamp(self.observed_at, "observed_at"))
        object.__setattr__(self, "decision_id", _optional_text(self.decision_id, "decision_id"))
        if self.fetched_at is not None:
            object.__setattr__(self, "fetched_at", normalize_timestamp(self.fetched_at, "fetched_at"))
        for field in (
            "last_observation_id", "refresh_provider", "refresh_endpoint", "refresh_error_code", "refresh_error_detail",
        ):
            value = _optional_text(getattr(self, field), field)
            if field == "refresh_error_code" and value is not None:
                value = value.upper()
            object.__setattr__(self, field, value)
        if self.last_observation_at is not None:
            object.__setattr__(self, "last_observation_at", normalize_timestamp(self.last_observation_at, "last_observation_at"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CollectionEvent":
        if not isinstance(value, Mapping):
            raise ValueError("collection event must be an object")
        allowed = {
            "event_id", "timestamp", "asset", "metric_key", "status", "reason", "source",
            "observed_at", "decision_id", "fetched_at", "last_observation_id", "last_observation_at",
            "refresh_provider", "refresh_endpoint", "refresh_error_code", "refresh_error_detail",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"collection event contains unknown fields: {', '.join(sorted(unknown))}")
        required = {"event_id", "timestamp", "asset", "metric_key", "status"}
        missing = required - set(value)
        if missing:
            raise ValueError(f"collection event is missing fields: {', '.join(sorted(missing))}")
        return cls(**{field: value[field] for field in value if field in allowed})

    def as_dict(self) -> dict[str, Any]:
        result = {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "asset": self.asset,
            "metric_key": self.metric_key,
            "status": self.status,
            "reason": self.reason,
            "source": self.source,
            "observed_at": self.observed_at,
            "decision_id": self.decision_id,
            "fetched_at": self.fetched_at,
            "last_observation_id": self.last_observation_id,
            "last_observation_at": self.last_observation_at,
            "refresh_provider": self.refresh_provider,
            "refresh_endpoint": self.refresh_endpoint,
            "refresh_error_code": self.refresh_error_code,
            "refresh_error_detail": self.refresh_error_detail,
        }
        return result

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


__all__ = [
    "CollectionEvent",
    "MetricObservation",
    "observation_id_for",
    "stable_observation_id",
]
