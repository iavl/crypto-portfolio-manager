"""Current, on-demand event-scan result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import hashlib
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from .evidence import EventRiskAssessment
from .time import normalize_timestamp, parse_timestamp


_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_EVENT_STATES = {"CLEAR", "WATCH", "ELEVATED", "CRITICAL"}
_EVENT_RELEVANCE = {"RELEVANT", "IRRELEVANT", "UNKNOWN"}
_EVENT_SEVERITY = {"CLEAR", "WATCH", "ELEVATED", "CRITICAL"}
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
class EventItem:
    """Normalized event evidence accepted from the bounded semantic stage."""

    event_id: str
    category: str
    title: str
    summary: str | None = None
    published_at: str | None = None
    canonical_url: str | None = None
    source_ids: tuple[str, ...] = ()
    source_groups: tuple[str, ...] = ()
    affected_assets: tuple[str, ...] = ()
    relevance: str = "RELEVANT"
    severity: str = "WATCH"
    materiality_source: str = "STRUCTURED_EVENT"
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _text(self.event_id, "event_id"))
        object.__setattr__(self, "category", _text(self.category, "event category").lower())
        object.__setattr__(self, "title", _text(self.title, "event title"))
        if self.summary is not None:
            object.__setattr__(self, "summary", _text(self.summary, "event summary"))
        if self.published_at is not None:
            object.__setattr__(self, "published_at", normalize_timestamp(self.published_at, "published_at"))
        for field_name in ("source_ids", "source_groups", "affected_assets", "evidence_ids"):
            value = getattr(self, field_name)
            if isinstance(value, (str, bytes)):
                raise ValueError(f"event {field_name} must be a sequence")
            values = tuple(str(item).strip() for item in value)
            if any(not item for item in values) or len(values) != len(set(values)):
                raise ValueError(f"event {field_name} must contain unique non-empty values")
            if field_name == "affected_assets":
                values = tuple(item.upper() for item in values)
            object.__setattr__(self, field_name, values)
        relevance = _text(self.relevance, "event relevance").upper()
        severity = _text(self.severity, "event severity").upper()
        if relevance not in _EVENT_RELEVANCE or severity not in _EVENT_SEVERITY:
            raise ValueError("event relevance or severity is unsupported")
        object.__setattr__(self, "relevance", relevance)
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "materiality_source", _text(self.materiality_source, "materiality_source"))
        if self.canonical_url is not None:
            object.__setattr__(self, "canonical_url", _text(self.canonical_url, "canonical_url"))

    @property
    def stable_fingerprint(self) -> str:
        payload = "|".join((self.canonical_url or "", self.title.lower(), self.published_at or "", self.category, *self.affected_assets))
        return hashlib.sha256(payload.encode()).hexdigest()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EventItem":
        if not isinstance(value, Mapping):
            raise ValueError("event item must be an object")
        data = dict(value)
        fallback = data.get("stable_fingerprint") or data.get("canonical_url") or data.get("title") or data.get("published_at") or "event"
        data.setdefault("event_id", fallback)
        data.setdefault("title", data.get("event_id"))
        data.setdefault("category", "unknown")
        data.setdefault("source_ids", (data.get("source_id"),) if data.get("source_id") else ())
        data.setdefault("affected_assets", tuple(data.get("affected_assets", ())))
        data.setdefault("evidence_ids", tuple(data.get("evidence_ids", ())))
        return cls(**{key: data[key] for key in {
            "event_id", "category", "title", "summary", "published_at", "canonical_url",
            "source_ids", "source_groups", "affected_assets", "relevance", "severity",
            "materiality_source", "evidence_ids",
        } if key in data})

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "stable_fingerprint": self.stable_fingerprint,
            "category": self.category,
            "title": self.title,
            "summary": self.summary,
            "published_at": self.published_at,
            "canonical_url": self.canonical_url,
            "source_ids": list(self.source_ids),
            "source_groups": list(self.source_groups),
            "affected_assets": list(self.affected_assets),
            "relevance": self.relevance,
            "severity": self.severity,
            "materiality_source": self.materiality_source,
            "evidence_ids": list(self.evidence_ids),
        }


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
    state: str | None = None
    confidence_score: float | None = None
    source_coverage: Mapping[str, Any] | None = None
    source_quality: Mapping[str, Any] | None = None
    source_redundancy: Mapping[str, Any] | None = None
    signal_consistency: Mapping[str, Any] | None = None
    conflict_ids: tuple[str, ...] = ()
    deduplicated_event_count: int | None = None
    evidence_ids: tuple[str, ...] = ()

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
        events = tuple(item.as_dict() if isinstance(item, EventItem) else item for item in self.material_events)
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
        state = self.state
        if state is None:
            severities = {
                str(item.get("severity", "WATCH")).strip().upper()
                for item in events
                if isinstance(item, Mapping)
            }
            if "CRITICAL" in severities:
                state = "CRITICAL"
            elif "ELEVATED" in severities:
                state = "ELEVATED"
            elif events or self.coverage < 1.0:
                state = "WATCH"
            else:
                state = "CLEAR"
        state = _text(state, "event scan state").upper()
        if state not in _EVENT_STATES:
            raise ValueError("event scan state is unsupported")
        object.__setattr__(self, "state", state)
        score = self.confidence_score
        if score is None:
            score = self.coverage if self.coverage < 1.0 else {"CLEAR": 1.0, "WATCH": 0.5, "ELEVATED": 0.75, "CRITICAL": 1.0}[state]
        score = float(score)
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("event scan confidence_score must be a finite fraction")
        object.__setattr__(self, "confidence_score", score)
        for field_name in ("source_coverage", "source_quality", "source_redundancy", "signal_consistency"):
            value = getattr(self, field_name)
            if value is not None:
                if not isinstance(value, Mapping):
                    raise ValueError(f"event scan {field_name} must be an object or null")
                object.__setattr__(self, field_name, dict(value))
        for field_name in ("conflict_ids", "evidence_ids"):
            value = getattr(self, field_name)
            if isinstance(value, (str, bytes)):
                raise ValueError(f"event scan {field_name} must be a sequence")
            values = tuple(_text(item, field_name) for item in value)
            if len(values) != len(set(values)):
                raise ValueError(f"event scan {field_name} must be unique")
            object.__setattr__(self, field_name, values)
        count = len(events) if self.deduplicated_event_count is None else self.deduplicated_event_count
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("deduplicated_event_count must be a non-negative integer")
        object.__setattr__(self, "deduplicated_event_count", count)

    @property
    def status(self) -> str:
        if self.state in {"ELEVATED", "CRITICAL"} or self.material_events:
            return "MATERIAL_EVENT_FOUND"
        if self.state != "CLEAR" or self.coverage < 1.0:
            return "INSUFFICIENT_SOURCE_COVERAGE"
        return "NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES"

    @property
    def event_items(self) -> tuple[EventItem, ...]:
        """Return normalized typed items without breaking legacy mapping consumers."""
        result = []
        for item in self.material_events:
            if isinstance(item, EventItem):
                result.append(item)
                continue
            if isinstance(item, Mapping):
                value = dict(item)
                value.setdefault("category", self.category)
                value.setdefault("source_ids", value.get("source_ids", (value.get("source_id"),) if value.get("source_id") else ()))
                result.append(EventItem.from_mapping(value))
        return tuple(result)

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
                "state": self.state,
                "confidence_score": self.confidence_score,
                "source_coverage": self.source_coverage,
                "source_quality": self.source_quality,
                "source_redundancy": self.source_redundancy,
                "signal_consistency": self.signal_consistency,
                "conflict_ids": list(self.conflict_ids),
                "deduplicated_event_count": self.deduplicated_event_count,
                "evidence_ids": list(self.evidence_ids),
            },
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EventScanResult":
        if not isinstance(value, Mapping):
            raise ValueError("event scan result must be an object")
        allowed = {
            "asset", "category", "scan_as_of", "lookback_days", "sources_checked",
            "material_events", "coverage", "confidence", "status",
            "state", "confidence_score", "source_coverage", "source_quality", "source_redundancy",
            "signal_consistency", "conflict_ids", "deduplicated_event_count", "evidence_ids",
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
            state=value.get("state"),
            confidence_score=value.get("confidence_score"),
            source_coverage=value.get("source_coverage"),
            source_quality=value.get("source_quality"),
            source_redundancy=value.get("source_redundancy"),
            signal_consistency=value.get("signal_consistency"),
            conflict_ids=tuple(value.get("conflict_ids", ())),
            deduplicated_event_count=value.get("deduplicated_event_count"),
            evidence_ids=tuple(value.get("evidence_ids", ())),
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
            "state": self.state,
            "confidence_score": self.confidence_score,
            "source_coverage": dict(self.source_coverage) if self.source_coverage is not None else None,
            "source_quality": dict(self.source_quality) if self.source_quality is not None else None,
            "source_redundancy": dict(self.source_redundancy) if self.source_redundancy is not None else None,
            "signal_consistency": dict(self.signal_consistency) if self.signal_consistency is not None else None,
            "conflict_ids": list(self.conflict_ids),
            "deduplicated_event_count": self.deduplicated_event_count,
            "evidence_ids": list(self.evidence_ids),
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
    state: str | None = None,
    confidence_score: float | None = None,
    source_coverage: Mapping[str, Any] | None = None,
    source_quality: Mapping[str, Any] | None = None,
    source_redundancy: Mapping[str, Any] | None = None,
    signal_consistency: Mapping[str, Any] | None = None,
    conflict_ids: Iterable[str] = (),
    deduplicated_event_count: int | None = None,
    evidence_ids: Iterable[str] = (),
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
        state=state,
        confidence_score=confidence_score,
        source_coverage=source_coverage,
        source_quality=source_quality,
        source_redundancy=source_redundancy,
        signal_consistency=signal_consistency,
        conflict_ids=tuple(conflict_ids),
        deduplicated_event_count=deduplicated_event_count,
        evidence_ids=tuple(evidence_ids),
    )


def event_scan_observation(
    scan: EventScanResult | Mapping[str, Any],
    metric_key: str,
    *,
    fetched_at: str | datetime | None = None,
) -> dict[str, Any]:
    model = scan if isinstance(scan, EventScanResult) else EventScanResult.from_mapping(scan)
    return model.to_observation(metric_key, fetched_at=fetched_at)


__all__ = [
    "EventRiskAssessment",
    "EventItem",
    "EventScanResult",
    "build_event_scan_result",
    "event_scan_observation",
]
