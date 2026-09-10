"""Orchestrate structured event transport and bounded classification."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ..models.time import normalize_timestamp
from ..providers.http import redact_log, redact_url
from .classifier import (
    EventClassificationError,
    EventMaterialityClassifier,
    cheap_screen_response,
    validate_classified_response,
)
from .scanner import EventSourceScanRequest, EventSourceScanResponse
from .sources import source_catalog


EXCHANGE_SCHEMA_VERSION = 1
EVENT_RESOLUTION_ERROR_CODES = (
    "EVENT_TRANSPORT_FAILED",
    "EVENT_SOURCE_UNREACHABLE",
    "EVENT_SOURCE_INCOMPLETE",
    "EVENT_CLASSIFICATION_REQUIRED",
    "EVENT_CLASSIFIER_UNAVAILABLE",
    "EVENT_CLASSIFIER_AUTH",
    "EVENT_CLASSIFIER_RATE_LIMIT",
    "EVENT_CLASSIFIER_TIMEOUT",
    "EVENT_CLASSIFIER_INVALID_RESPONSE",
    "EVENT_CLASSIFICATION_SCHEMA_ERROR",
    "EVENT_INSUFFICIENT_SOURCE_COVERAGE",
    "EVENT_SOURCE_CONFLICT",
)
_ALLOWED_EXCHANGE_FIELDS = {
    "schema_version", "generated_at", "requests", "pending_requests", "responses", "pending_responses",
    "classification_mode", "classifier_backend", "classifier_model",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _candidate_count(response: EventSourceScanResponse) -> int:
    return sum(str(item.get("materiality", "")).strip().upper() == "CANDIDATE" for item in response.items)


@dataclass(frozen=True)
class EventResolutionDiagnostic:
    asset: str
    category: str
    source_id: str
    source_group: str
    transport_kind: str
    endpoint: str
    reachable: bool
    complete_for_source: bool
    candidate_count: int
    classified_count: int
    classification_mode: str
    classifier_backend: str | None = None
    classifier_model: str | None = None
    coverage_ratio: float = 0.0
    confidence: str | None = None
    event_state: str | None = None
    status: str = "FETCH_FAILED"
    error_code: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "category": self.category,
            "source_id": self.source_id,
            "source_group": self.source_group,
            "transport_kind": self.transport_kind,
            "endpoint": redact_url(self.endpoint),
            "reachable": self.reachable,
            "complete_for_source": self.complete_for_source,
            "candidate_count": self.candidate_count,
            "classified_count": self.classified_count,
            "classification_mode": self.classification_mode,
            "classifier_backend": self.classifier_backend,
            "classifier_model": self.classifier_model,
            "coverage_ratio": self.coverage_ratio,
            "confidence": self.confidence,
            "event_state": self.event_state,
            "status": self.status,
            "error_code": self.error_code,
            "reason": self.reason,
        }


class EventResolver:
    """Resolve one request through transport, then optional classification."""

    def __init__(
        self,
        *,
        transport: Any,
        classifier: EventMaterialityClassifier | None = None,
    ) -> None:
        if transport is None:
            raise ValueError("event resolver requires a transport")
        self.transport = transport
        self.classifier = classifier
        self._diagnostics: list[EventResolutionDiagnostic] = []

    @property
    def diagnostics(self) -> tuple[EventResolutionDiagnostic, ...]:
        return tuple(self._diagnostics)

    def reset_diagnostics(self) -> None:
        self._diagnostics.clear()

    def _fetch(self, request: EventSourceScanRequest) -> EventSourceScanResponse:
        fetch = getattr(self.transport, "fetch", None)
        raw = fetch(request) if callable(fetch) else self.transport(request) if callable(self.transport) else None
        if raw is None:
            raise ValueError("event transport is not callable")
        return raw if isinstance(raw, EventSourceScanResponse) else EventSourceScanResponse.from_mapping(raw)

    def _record(
        self,
        request: EventSourceScanRequest,
        response: EventSourceScanResponse,
        *,
        status: str,
        error_code: str | None = None,
        reason: str | None = None,
    ) -> None:
        candidates = _candidate_count(response)
        classifier = self.classifier
        self._diagnostics.append(EventResolutionDiagnostic(
            asset=request.asset,
            category=request.category,
            source_id=request.source_id,
            source_group=request.source_group or request.authority.lower(),
            transport_kind=request.transport_kind or "WEB",
            endpoint=request.source_url,
            reachable=response.reachable,
            complete_for_source=bool(response.complete_for_source),
            candidate_count=candidates,
            classified_count=len(response.items) - candidates,
            classification_mode=getattr(classifier, "mode", "host"),
            classifier_backend=getattr(classifier, "backend", None),
            classifier_model=getattr(classifier, "model", None),
            coverage_ratio=1.0 if response.reachable and response.complete_for_source else 0.0,
            confidence="HIGH" if response.reachable and response.complete_for_source and not candidates else "LOW",
            status=status,
            error_code=error_code,
            reason=redact_log(reason) if reason else response.error,
        ))

    def resolve(self, request: EventSourceScanRequest) -> EventSourceScanResponse:
        try:
            response = self._fetch(request)
        except Exception as exc:
            reason = redact_log(str(exc)) or "event transport failed"
            response = EventSourceScanResponse(
                source_id=request.source_id,
                reachable=False,
                checked_at=request.as_of,
                items=(),
                error=f"EVENT_TRANSPORT_FAILED: {reason}",
                complete_for_source=False,
            )
            self._record(request, response, status="FETCH_FAILED", error_code="EVENT_TRANSPORT_FAILED", reason=reason)
            return response

        candidates = _candidate_count(response)
        if not response.reachable or not response.complete_for_source:
            self._record(
                request,
                response,
                status="FETCH_FAILED",
                error_code="EVENT_SOURCE_UNREACHABLE" if not response.reachable else "EVENT_SOURCE_INCOMPLETE",
                reason=response.error,
            )
            return response
        if response.conflict:
            conflicted = replace(response, error=response.error or "EVENT_SOURCE_CONFLICT")
            self._record(
                request,
                conflicted,
                status="CONFLICT",
                error_code="EVENT_SOURCE_CONFLICT",
                reason=conflicted.error,
            )
            return conflicted
        if not candidates:
            self._record(request, response, status="FETCHED")
            return response
        screened, unresolved = cheap_screen_response(response)
        if not unresolved:
            screened_response = replace(response, items=screened)
            self._record(request, screened_response, status="SCREENED")
            return screened_response
        if self.classifier is None:
            pending = replace(response, error="EVENT_CLASSIFICATION_REQUIRED")
            self._record(
                request,
                pending,
                status="CLASSIFICATION_PENDING",
                error_code="EVENT_CLASSIFICATION_REQUIRED",
                reason="structured candidates require bounded semantic classification",
            )
            return pending
        try:
            classification_response = replace(response, items=unresolved)
            classified = self.classifier.classify(request=request, response=classification_response)
            classified = validate_classified_response(request, classification_response, classified)
            classified_by_identity = {
                (item.get("external_id"), item.get("canonical_url"), item.get("published_at")): item
                for item in classified.items
            }
            merged_items = []
            for item in response.items:
                identity = (item.get("external_id"), item.get("canonical_url"), item.get("published_at"))
                if identity in classified_by_identity:
                    merged_items.append(classified_by_identity[identity])
                else:
                    merged_items.append(next(
                        screened_item for screened_item in screened
                        if (screened_item.get("external_id"), screened_item.get("canonical_url"), screened_item.get("published_at")) == identity
                    ))
            classified = replace(classified, items=tuple(merged_items))
            classified = validate_classified_response(request, response, classified)
        except EventClassificationError as exc:
            failed = replace(response, error=str(exc))
            self._record(request, failed, status="CLASSIFICATION_FAILED", error_code=exc.code, reason=exc.reason)
            return failed
        except Exception as exc:
            failed = replace(response, error=f"EVENT_CLASSIFICATION_SCHEMA_ERROR: {redact_log(str(exc))}")
            self._record(
                request,
                failed,
                status="CLASSIFICATION_FAILED",
                error_code="EVENT_CLASSIFICATION_SCHEMA_ERROR",
                reason=str(exc),
            )
            return failed
        self._record(request, classified, status="CLASSIFIED")
        return classified

    __call__ = resolve


def build_exchange_document(
    requests: Iterable[EventSourceScanRequest],
    responses: Iterable[EventSourceScanResponse],
    *,
    pending_responses: Iterable[EventSourceScanResponse] | None = None,
    generated_at: str | None = None,
    classification_mode: str = "host",
    classifier_backend: str | None = None,
    classifier_model: str | None = None,
) -> dict[str, Any]:
    """Build the stable host-assisted pending/classified exchange document."""
    requests = tuple(requests)
    responses = tuple(responses)
    pending = tuple(responses if pending_responses is None else pending_responses)
    return {
        "schema_version": EXCHANGE_SCHEMA_VERSION,
        "generated_at": normalize_timestamp(generated_at or _now(), "generated_at"),
        "requests": [request.as_dict() for request in requests],
        "pending_requests": [request.as_dict() for request in requests],
        "responses": [response.as_dict() for response in responses],
        "pending_responses": [response.as_dict() for response in pending],
        "classification_mode": classification_mode,
        "classifier_backend": classifier_backend,
        "classifier_model": classifier_model,
    }


def parse_exchange_document(value: Mapping[str, Any]) -> tuple[
    tuple[EventSourceScanRequest, ...],
    tuple[EventSourceScanResponse, ...],
    tuple[EventSourceScanResponse, ...],
]:
    """Parse and validate a host exchange document before scanner use."""
    if not isinstance(value, Mapping):
        raise ValueError("event exchange document must be an object")
    unknown = set(value) - _ALLOWED_EXCHANGE_FIELDS
    if unknown:
        raise ValueError("event exchange document contains unknown fields: " + ", ".join(sorted(unknown)))
    if value.get("schema_version") != EXCHANGE_SCHEMA_VERSION:
        raise ValueError("event exchange document has unsupported schema_version")
    normalize_timestamp(value.get("generated_at"), "generated_at")
    request_values = value.get("requests")
    pending_request_values = value.get("pending_requests")
    response_values = value.get("responses")
    pending_values = value.get("pending_responses")
    if (
        not isinstance(request_values, list)
        or not isinstance(pending_request_values, list)
        or not isinstance(response_values, list)
        or not isinstance(pending_values, list)
    ):
        raise ValueError("event exchange document requires requests, pending_requests, responses, and pending_responses arrays")
    requests = tuple(EventSourceScanRequest.from_mapping(item) for item in request_values)
    pending_requests = tuple(EventSourceScanRequest.from_mapping(item) for item in pending_request_values)
    responses = tuple(EventSourceScanResponse.from_mapping(item) for item in response_values)
    pending = tuple(EventSourceScanResponse.from_mapping(item) for item in pending_values)
    for label, items in (("requests", requests), ("pending_requests", pending_requests), ("responses", responses), ("pending_responses", pending)):
        ids = [item.source_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"event exchange document contains duplicate {label} source IDs")
    request_ids = {item.source_id for item in requests}
    if {item.source_id for item in pending_requests} != request_ids:
        raise ValueError("event exchange document request IDs changed")
    if any(current.as_dict() != original.as_dict() for current, original in zip(
        sorted(requests, key=lambda item: item.source_id),
        sorted(pending_requests, key=lambda item: item.source_id),
    )):
        raise ValueError("event exchange document request fields changed")
    if {item.source_id for item in responses} != request_ids or {item.source_id for item in pending} != request_ids:
        raise ValueError("event exchange document source IDs do not match requests")
    for request in requests:
        source = next((item for item in source_catalog(request.category, request.asset) if item.id == request.source_id), None)
        if (
            source is None
            or source.url != request.source_url
            or source.transport_candidates != request.source_urls
            or source.transport_kind != request.transport_kind
            or source.source_group != request.source_group
            or source.authority != request.authority
            or source.source_name != request.source_name
        ):
            raise ValueError(f"event exchange request is not from the fixed source catalog: {request.source_id}")
    return requests, responses, pending


def validate_exchange_responses(
    requests: Iterable[EventSourceScanRequest],
    responses: Iterable[EventSourceScanResponse],
    pending_responses: Iterable[EventSourceScanResponse],
) -> tuple[EventSourceScanResponse, ...]:
    """Validate classified responses against the original transport candidates."""
    requests_by_id = {request.source_id: request for request in requests}
    responses_by_id = {response.source_id: response for response in responses}
    pending_by_id = {response.source_id: response for response in pending_responses}
    if set(responses_by_id) != set(requests_by_id) or set(pending_by_id) != set(requests_by_id):
        raise ValueError("event exchange response source IDs do not match requests")
    result = []
    for source_id, request in requests_by_id.items():
        original = pending_by_id[source_id]
        current = responses_by_id[source_id]
        if _candidate_count(original):
            current = validate_classified_response(request, original, current)
        elif current.as_dict() != original.as_dict():
            raise ValueError(f"response for {source_id} changed an empty or unreachable source")
        result.append(current)
    return tuple(result)


__all__ = [
    "EXCHANGE_SCHEMA_VERSION",
    "EVENT_RESOLUTION_ERROR_CODES",
    "EventResolutionDiagnostic",
    "EventResolver",
    "build_exchange_document",
    "parse_exchange_document",
    "validate_exchange_responses",
]
