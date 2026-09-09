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
]
