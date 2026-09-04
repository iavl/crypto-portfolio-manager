"""Current, on-demand event-scan result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from .time import normalize_timestamp, parse_timestamp


_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_EVENT_METRICS = {
    "security": "risk.security_event_status",
    "governance": "risk.governance_event_status",
    "regulatory": "risk.regulatory_event_status",
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class EventScanResult:
    """A current scan, independent of the timestamps of source items found."""

    asset: str
    category: str
    scan_as_of: str
    lookback_days: int
    sources_checked: tuple[str, ...]
    material_events: tuple[Any, ...] = ()
    coverage: float = 0.0
    confidence: str = "LOW"

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset", _text(self.asset, "event scan asset").upper())
        object.__setattr__(self, "category", _text(self.category, "event scan category").lower())
        object.__setattr__(self, "scan_as_of", normalize_timestamp(self.scan_as_of, "scan_as_of"))
        if isinstance(self.lookback_days, bool) or not isinstance(self.lookback_days, int) or self.lookback_days < 1:
            raise ValueError("event scan lookback_days must be a positive integer")
        if isinstance(self.sources_checked, str):
            raise ValueError("event scan sources_checked must be a sequence")
        sources = tuple(_text(item, "event scan source") for item in self.sources_checked)
        if not sources or len(sources) != len(set(sources)):
            raise ValueError("event scan sources_checked must be non-empty and unique")
        object.__setattr__(self, "sources_checked", sources)
        if isinstance(self.material_events, (str, bytes)):
            raise ValueError("event scan material_events must be a sequence")
        events = tuple(self.material_events)
        try:
            json.dumps(events, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("event scan material_events must be finite JSON") from exc
        object.__setattr__(self, "material_events", events)
        try:
            coverage = float(self.coverage)
        except (TypeError, ValueError) as exc:
            raise ValueError("event scan coverage must be a finite fraction") from exc
        if not math.isfinite(coverage) or not 0 <= coverage <= 1:
            raise ValueError("event scan coverage must be a finite fraction")
        object.__setattr__(self, "coverage", coverage)
        confidence = _text(self.confidence, "event scan confidence").upper()
        if confidence not in _CONFIDENCE:
            raise ValueError("event scan confidence must be HIGH, MEDIUM, or LOW")
        object.__setattr__(self, "confidence", confidence)

    @property
    def status(self) -> str:
        if self.material_events:
            return "MATERIAL_EVENT_FOUND"
        if self.coverage < 1.0:
            return "INSUFFICIENT_SOURCE_COVERAGE"
        return "NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES"

    def to_observation(
        self,
        metric_key: str,
        *,
        fetched_at: str | datetime | None = None,
        source: str = "event-scan",
    ) -> dict[str, Any]:
        definition = metric_definition(metric_key)
        if not definition.key.startswith("risk."):
            raise ValueError("event scans can only produce risk event metrics")
        expected_metric = _EVENT_METRICS.get(self.category)
        if expected_metric is not None and definition.key != expected_metric:
            raise ValueError(f"{self.category} event scan must produce {expected_metric}")
        fetched = fetched_at.isoformat() if isinstance(fetched_at, datetime) else fetched_at
        fetched = normalize_timestamp(fetched, "fetched_at") if fetched is not None else _now()
        if parse_timestamp(fetched) < parse_timestamp(self.scan_as_of):
            raise ValueError("fetched_at must be at or after scan_as_of")
        return {
            "asset": self.asset,
            "metric_key": definition.key,
            "value": self.status,
            "observed_at": self.scan_as_of,
            "fetched_at": fetched,
            "source": _text(source, "event scan source"),
            "confidence": self.confidence,
            "summary": self.status,
            "metadata": {
                "category": self.category,
                "scan_as_of": self.scan_as_of,
                "lookback_days": self.lookback_days,
                "sources_checked": list(self.sources_checked),
                "material_events": list(self.material_events),
                "coverage": self.coverage,
                "event_status": self.status,
            },
        }

    as_observation = to_observation

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EventScanResult":
        if not isinstance(value, Mapping):
            raise ValueError("event scan result must be an object")
        allowed = {
            "asset", "category", "scan_as_of", "lookback_days", "sources_checked",
            "material_events", "coverage", "confidence", "status",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("event scan result contains unknown fields: " + ", ".join(sorted(unknown)))
        result = cls(
            asset=value["asset"],
            category=value["category"],
            scan_as_of=value["scan_as_of"],
            lookback_days=value["lookback_days"],
            sources_checked=tuple(value["sources_checked"]),
            material_events=tuple(value.get("material_events", ())),
            coverage=value.get("coverage", 0.0),
            confidence=value.get("confidence", "LOW"),
        )
        if "status" in value and value["status"] != result.status:
            raise ValueError("event scan status does not match its contents")
        return result

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "category": self.category,
            "scan_as_of": self.scan_as_of,
            "lookback_days": self.lookback_days,
            "sources_checked": list(self.sources_checked),
            "material_events": list(self.material_events),
            "coverage": self.coverage,
            "confidence": self.confidence,
            "status": self.status,
        }


def build_event_scan_result(
    asset: str,
    category: str,
    *,
    scan_as_of: str,
    lookback_days: int,
    sources_checked: Iterable[str],
    material_events: Iterable[Any] = (),
    coverage: float = 0.0,
    confidence: str = "LOW",
) -> EventScanResult:
    return EventScanResult(
        asset=asset,
        category=category,
        scan_as_of=scan_as_of,
        lookback_days=lookback_days,
        sources_checked=tuple(sources_checked),
        material_events=tuple(material_events),
        coverage=coverage,
        confidence=confidence,
    )


def event_scan_observation(
    scan: EventScanResult | Mapping[str, Any],
    metric_key: str,
    *,
    fetched_at: str | datetime | None = None,
) -> dict[str, Any]:
    model = scan if isinstance(scan, EventScanResult) else EventScanResult.from_mapping(scan)
    return model.to_observation(metric_key, fetched_at=fetched_at)


normalize_event_scan = EventScanResult.from_mapping


__all__ = ["EventScanResult", "build_event_scan_result", "event_scan_observation", "normalize_event_scan"]
