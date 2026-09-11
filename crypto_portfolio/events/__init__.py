"""On-demand event scanning contracts."""

from .scanner import EVENT_SCAN_SAFETY_INSTRUCTIONS, EventScanner, EventSourceScanRequest, EventSourceScanResponse, event_metric_category
from .sources import EVENT_CATEGORIES, EVENT_SOURCE_CATALOG, EventSource, source_catalog
from .transports import (
    EventCandidate,
    EventTransportCache,
    EventTransportResult,
    EventTransportSpec,
    StructuredEventTransport,
    structured_event_source_fetcher,
)
from .classifier import (
    EVENT_CLASSIFIER_ERROR_CODES,
    EventClassificationError,
    EventMaterialityClassifier,
    cheap_screen_candidate,
    cheap_screen_response,
    validate_classified_response,
)
from .resolver import (
    EXCHANGE_SCHEMA_VERSION,
    EVENT_RESOLUTION_ERROR_CODES,
    EventResolutionDiagnostic,
    EventResolver,
    build_exchange_document,
    parse_exchange_document,
    validate_exchange_responses,
)

__all__ = [
    "EVENT_CATEGORIES",
    "EVENT_SOURCE_CATALOG",
    "EventScanner",
    "EVENT_SCAN_SAFETY_INSTRUCTIONS",
    "EventSource",
    "EventSourceScanRequest",
    "EventSourceScanResponse",
    "event_metric_category",
    "source_catalog",
    "EventCandidate",
    "EventTransportCache",
    "EventTransportResult",
    "EventTransportSpec",
    "StructuredEventTransport",
    "structured_event_source_fetcher",
    "EVENT_CLASSIFIER_ERROR_CODES",
    "EventClassificationError",
    "EventMaterialityClassifier",
    "cheap_screen_candidate",
    "cheap_screen_response",
    "validate_classified_response",
    "EXCHANGE_SCHEMA_VERSION",
    "EVENT_RESOLUTION_ERROR_CODES",
    "EventResolutionDiagnostic",
    "EventResolver",
    "build_exchange_document",
    "parse_exchange_document",
    "validate_exchange_responses",
]
