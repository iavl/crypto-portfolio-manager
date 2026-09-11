"""Bounded semantic classification for structured event candidates."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from ..providers.http import redact_log
from .scanner import EventSourceScanRequest, EventSourceScanResponse


EVENT_CLASSIFIER_ERROR_CODES = (
    "EVENT_CLASSIFIER_UNAVAILABLE",
    "EVENT_CLASSIFIER_AUTH",
    "EVENT_CLASSIFIER_RATE_LIMIT",
    "EVENT_CLASSIFIER_TIMEOUT",
    "EVENT_CLASSIFIER_INVALID_RESPONSE",
    "EVENT_CLASSIFICATION_SCHEMA_ERROR",
)
_CANDIDATE = "CANDIDATE"
_CLASSIFIED_MATERIALITY = {
    "CLEAR", "WATCH", "ELEVATED", "CRITICAL", "MATERIAL", "MATERIAL_EVENT",
    "HIGH", "SEVERE", "TRUE", "YES", "FALSE", "NO", "NOT_MATERIAL",
    "IRRELEVANT",
}
_DIRECTIONS = {"POSITIVE", "NEGATIVE", "MIXED", "NEUTRAL", "UNCERTAIN"}
_MAGNITUDES = {"LOW", "MEDIUM", "HIGH"}
_IMPLEMENTATION_STATES = {"DISCUSSION", "PROPOSED", "VOTING", "PASSED", "EXECUTED", "REJECTED", "UNKNOWN"}
_MATERIAL_SCREENING_TERMS = {
    "emission", "inflation", "supply", "fee switch", "revenue", "treasury", "solvency",
    "collateral", "admin", "upgrade authority", "emergency", "exploit", "security",
    "staking", "tokenholder", "value capture", "parameter change", "governance attack",
}
_ROUTINE_SCREENING_TERMS = {"routine", "minor", "maintenance", "typo", "documentation", "parameter adjustment"}


class EventMaterialityClassifier(Protocol):
    """Classify all candidates in one normalized source response."""

    mode: str
    backend: str

    def classify(
        self,
        *,
        request: EventSourceScanRequest,
        response: EventSourceScanResponse,
    ) -> EventSourceScanResponse:
        ...


class EventClassificationError(ValueError):
    """A fail-closed classifier failure with a stable diagnostic code."""

    def __init__(self, code: str, reason: str) -> None:
        if code not in EVENT_CLASSIFIER_ERROR_CODES:
            raise ValueError(f"unsupported event classifier error code: {code}")
        self.code = code
        self.reason = redact_log(reason)
        super().__init__(f"{code}: {self.reason}")


def _candidate(item: Mapping[str, Any]) -> bool:
    return item.get("is_material") is None or str(item.get("materiality", "")).strip().upper() == _CANDIDATE


def _identity(item: Mapping[str, Any]) -> tuple[str, ...]:
    for field in ("external_id", "canonical_url"):
        value = item.get(field)
        if value:
            return (field, str(value))
    values = tuple(str(item.get(field, "")) for field in ("title", "published_at"))
    if any(values):
        return ("title_published", *values)
    raise EventClassificationError(
        "EVENT_CLASSIFICATION_SCHEMA_ERROR",
        "candidate has no stable identity",
    )


def _classifier_error(code: str, reason: str) -> EventClassificationError:
    return EventClassificationError(code, reason)


def cheap_screen_candidate(item: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Resolve clearly routine candidates without semantic classification."""
    text = " ".join(str(item.get(field, "")) for field in ("title", "summary")).strip().lower()
    if not text or not any(term in text for term in _ROUTINE_SCREENING_TERMS):
        return None
    if any(term in text for term in _MATERIAL_SCREENING_TERMS):
        return None
    return {
        **dict(item),
        "materiality": False,
        "is_material": False,
        "severity": "CLEAR",
        "impact_direction": "NEUTRAL",
        "magnitude": "LOW",
        "implementation_status": "DISCUSSION",
        "confidence": 1.0,
        "relevance": "RELEVANT",
    }


def cheap_screen_response(response: EventSourceScanResponse) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
    screened: list[Mapping[str, Any]] = []
    unresolved: list[Mapping[str, Any]] = []
    for item in response.items:
        normalized = cheap_screen_candidate(item)
        (screened if normalized is not None else unresolved).append(normalized or item)
    return tuple(screened), tuple(unresolved)


def validate_classified_response(
    request: EventSourceScanRequest,
    original: EventSourceScanResponse,
    classified: EventSourceScanResponse | Mapping[str, Any],
) -> EventSourceScanResponse:
    """Validate a classifier response without trusting classifier-supplied identity."""
    if not isinstance(classified, EventSourceScanResponse):
        try:
            classified = EventSourceScanResponse.from_mapping(classified)
        except (TypeError, ValueError) as exc:
            raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", str(exc)) from exc
    if classified.source_id != request.source_id:
        raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "classifier changed source_id")
    if classified.checked_at != original.checked_at:
        raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "classifier changed checked_at")
    if (classified.reachable, classified.complete_for_source) != (
        original.reachable,
        original.complete_for_source,
    ):
        raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "classifier changed source reachability")
    if len(classified.items) != len(original.items):
        raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "classifier changed candidate count")

    original_by_identity = {_identity(item): item for item in original.items}
    classified_by_identity = {_identity(item): item for item in classified.items}
    if len(original_by_identity) != len(original.items) or len(classified_by_identity) != len(classified.items):
        raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "classifier duplicated candidate identity")
    if set(original_by_identity) != set(classified_by_identity):
        raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "classifier changed candidate identity")

    for identity, source_item in original_by_identity.items():
        item = classified_by_identity[identity]
        for field in ("external_id", "canonical_url", "published_at"):
            if source_item.get(field) != item.get(field):
                raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", f"classifier changed {field}")
        if item.get("source_group") not in (None, request.source_group):
            raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "classifier changed source_group")
        materiality = item.get("materiality")
        if _candidate(item):
            raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "candidate remains unclassified")
        if not isinstance(item.get("is_material"), bool):
            raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "classifier returned invalid is_material")
        if isinstance(materiality, str) and materiality.upper() not in _CLASSIFIED_MATERIALITY:
            raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "classifier returned invalid materiality")
        direction = str(item.get("impact_direction", "UNCERTAIN")).upper()
        magnitude = str(item.get("magnitude", "MEDIUM")).upper()
        implementation = str(item.get("implementation_status", "UNKNOWN")).upper()
        if direction not in _DIRECTIONS or magnitude not in _MAGNITUDES or implementation not in _IMPLEMENTATION_STATES:
            raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "classifier returned invalid event classification fields")
        confidence = item.get("confidence", 0.5)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise _classifier_error("EVENT_CLASSIFICATION_SCHEMA_ERROR", "classifier returned invalid confidence")

    return classified


__all__ = [
    "EVENT_CLASSIFIER_ERROR_CODES",
    "EventClassificationError",
    "EventMaterialityClassifier",
    "cheap_screen_candidate",
    "cheap_screen_response",
    "validate_classified_response",
]
