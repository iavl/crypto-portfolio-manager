"""Compatibility import surface for the canonical availability policy."""

from ..metric_availability import (
    MetricAvailabilityPolicy,
    REASON_CODES,
    REQUIREMENTS,
    is_skippable,
    metric_availability,
    skip_reason,
)

__all__ = [
    "MetricAvailabilityPolicy",
    "REASON_CODES",
    "REQUIREMENTS",
    "is_skippable",
    "metric_availability",
    "skip_reason",
]
