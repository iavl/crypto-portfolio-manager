"""On-demand event source planning and result synthesis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from ..metrics_registry import REVIEW_TYPES, metric_definition
from ..models.events import EventScanResult, event_scan_observation
from ..models.policy import Policy, resolve_policy
from ..models.time import normalize_timestamp, parse_timestamp
from ..providers.base import FetchMode
from .sources import EVENT_CATEGORIES, EVENT_SOURCE_CATALOG, EventSource


_EVENT_METRICS = {
    "security": "risk.security_event_status",
    "governance": "risk.governance_event_status",
    "regulatory": "risk.regulatory_event_status",
}
_DEFAULT_LOOKBACKS = {
    "SNAPSHOT_REVIEW": {"security": 30, "governance": 30, "regulatory": 30},
    "FULL_REVIEW": {"security": 90, "governance": 90, "regulatory": 90},
    "EVENT_REVIEW": {"security": 30, "governance": 30, "regulatory": 30},
}
_DEFAULT_COVERAGE = {"medium_minimum": 0.5, "high_minimum": 1.0}
_MATERIAL_VALUES = {"MATERIAL", "MATERIAL_EVENT", "CRITICAL", "HIGH", "SEVERE", "TRUE", "YES"}
_MAX_ITEM_TEXT = 2_000


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _timestamp(value: Any, field: str) -> str:
    try:
        return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, field)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a timezone-aware RFC3339 timestamp") from exc


def _json_finite(value: Any, field: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite JSON") from exc


def _review_type(value: str) -> str:
    result = _text(value, "review_type").upper()
    if result not in REVIEW_TYPES:
        raise ValueError(f"review_type must be one of {list(REVIEW_TYPES)}")
    return result


def _category(value: str) -> str:
    result = _text(value, "event category").lower()
    if result not in EVENT_CATEGORIES:
        raise ValueError(f"event category must be one of {list(EVENT_CATEGORIES)}")
    return result


@dataclass(frozen=True)
class EventSourceScanRequest:
    asset: str
    category: str
    source_id: str
    source_name: str
    source_url: str
    authority: str
    lookback_start: str
    as_of: str
    tier: int = 1
    required_for_full_coverage: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset", _text(self.asset, "event scan asset").upper())
        object.__setattr__(self, "category", _category(self.category))
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id").lower())
        object.__setattr__(self, "source_name", _text(self.source_name, "source_name"))
        url = _text(self.source_url, "source_url")
        if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc:
            raise ValueError("source_url must use http or https")
        object.__setattr__(self, "source_url", url)
        object.__setattr__(self, "authority", _text(self.authority, "authority"))
        start = _timestamp(self.lookback_start, "lookback_start")
        end = _timestamp(self.as_of, "as_of")
        if parse_timestamp(start) > parse_timestamp(end):
            raise ValueError("lookback_start must not be after as_of")
        object.__setattr__(self, "lookback_start", start)
        object.__setattr__(self, "as_of", end)
        if isinstance(self.tier, bool) or not isinstance(self.tier, int) or self.tier not in {1, 2, 3}:
            raise ValueError("tier must be 1, 2, or 3")
        if not isinstance(self.required_for_full_coverage, bool):
            raise ValueError("required_for_full_coverage must be boolean")

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "category": self.category,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "authority": self.authority,
            "lookback_start": self.lookback_start,
            "as_of": self.as_of,
            "tier": self.tier,
            "required_for_full_coverage": self.required_for_full_coverage,
        }


def _normalize_item(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("event scan item must be an object")
    allowed = {"title", "published_at", "canonical_url", "summary", "materiality", "affected_assets"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("event scan item contains unknown fields: " + ", ".join(sorted(unknown)))
    result: dict[str, Any] = {}
    for field in ("title", "summary"):
        if value.get(field) is not None:
            text = _text(value[field], field)
            if len(text) > _MAX_ITEM_TEXT:
                raise ValueError(f"event scan {field} exceeds size limit")
            result[field] = text
    if value.get("published_at") is not None:
        result["published_at"] = _timestamp(value["published_at"], "published_at")
    if value.get("canonical_url") is not None:
        url = _text(value["canonical_url"], "canonical_url")
        if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc:
            raise ValueError("canonical_url must use http or https")
        result["canonical_url"] = url
    materiality = value.get("materiality", False)
    if isinstance(materiality, str):
        materiality = materiality.strip().upper()
    elif not isinstance(materiality, bool):
        raise ValueError("event scan materiality must be boolean or a classification string")
    result["materiality"] = materiality
    affected = value.get("affected_assets", ())
    if isinstance(affected, str) or not isinstance(affected, (list, tuple)):
        raise ValueError("event scan affected_assets must be a sequence")
    result["affected_assets"] = list(dict.fromkeys(_text(item, "affected asset").upper() for item in affected))
    _json_finite(result, "event scan item")
    return result


@dataclass(frozen=True)
class EventSourceScanResponse:
    source_id: str
    reachable: bool
    checked_at: str
    items: tuple[Mapping[str, Any], ...] = ()
    error: str | None = None
    conflict: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id").lower())
        if not isinstance(self.reachable, bool):
            raise ValueError("reachable must be boolean")
        object.__setattr__(self, "checked_at", _timestamp(self.checked_at, "checked_at"))
        if isinstance(self.items, (str, bytes)) or not isinstance(self.items, (tuple, list)):
            raise ValueError("event scan response items must be a sequence")
        items = tuple(_normalize_item(item) for item in self.items)
        object.__setattr__(self, "items", items)
        if self.error is not None:
            error = _text(self.error, "error")
            if len(error) > _MAX_ITEM_TEXT:
                raise ValueError("event scan error exceeds size limit")
            object.__setattr__(self, "error", error)
        if not isinstance(self.conflict, bool):
            raise ValueError("conflict must be boolean")
        if not self.reachable and self.error is None:
            raise ValueError("unreachable event source requires an error")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EventSourceScanResponse":
        if not isinstance(value, Mapping):
            raise ValueError("event scan response must be an object")
        allowed = {"source_id", "reachable", "checked_at", "items", "error", "conflict"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("event scan response contains unknown fields: " + ", ".join(sorted(unknown)))
        required = {"source_id", "reachable", "checked_at", "items", "error"}
        missing = required - set(value)
        if missing:
            raise ValueError("event scan response is missing fields: " + ", ".join(sorted(missing)))
        return cls(
            source_id=value["source_id"],
            reachable=value["reachable"],
            checked_at=value["checked_at"],
            items=tuple(value.get("items", ())),
            error=value.get("error"),
            conflict=value.get("conflict", False),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "reachable": self.reachable,
            "checked_at": self.checked_at,
            "items": [dict(item) for item in self.items],
            "error": self.error,
            "conflict": self.conflict,
        }


def _coerce_responses(value: Any) -> tuple[EventSourceScanResponse, ...]:
    if isinstance(value, Mapping):
        if "source_id" in value:
            value = (value,)
        else:
            value = tuple(value.values())
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValueError("event scan responses must be a sequence or source mapping")
    return tuple(item if isinstance(item, EventSourceScanResponse) else EventSourceScanResponse.from_mapping(item) for item in value)


class EventScanner:
    """Build allowlisted source requests and synthesize current scan results."""

    def __init__(self, policy: Policy | Mapping[str, Any] | None = None, *, sources: Iterable[EventSource] = EVENT_SOURCE_CATALOG) -> None:
        self.policy = policy or resolve_policy()
        self.sources = tuple(sources)
        if not self.sources:
            raise ValueError("event scanner requires a non-empty source catalog")
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError("event source catalog contains duplicate IDs")

    def lookback_days(self, category: str, *, review_type: str = "SNAPSHOT_REVIEW") -> int:
        category = _category(category)
        review = _review_type(review_type)
        configured = self.policy.events if isinstance(self.policy, Policy) else self.policy.get("events", {})
        configured = configured or {}
        try:
            value = configured["lookback_days"][review][category]
        except (KeyError, TypeError):
            value = _DEFAULT_LOOKBACKS[review][category]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("event lookback_days must be a positive integer")
        return value

    def _coverage_thresholds(self) -> tuple[float, float]:
        configured = self.policy.events if isinstance(self.policy, Policy) else self.policy.get("events", {})
        configured = configured or {}
        coverage = configured.get("coverage", {}) if isinstance(configured, Mapping) else {}
        medium = coverage.get("medium_minimum", _DEFAULT_COVERAGE["medium_minimum"])
        high = coverage.get("high_minimum", _DEFAULT_COVERAGE["high_minimum"])
        try:
            medium, high = float(medium), float(high)
        except (TypeError, ValueError) as exc:
            raise ValueError("event coverage thresholds must be finite fractions") from exc
        if not math.isfinite(medium) or not math.isfinite(high) or not 0 < medium <= high <= 1:
            raise ValueError("event coverage thresholds must be ordered fractions")
        return medium, high

    def build_requests(
        self,
        asset: str,
        category: str,
        as_of: str | datetime,
        *,
        lookback_days: int | None = None,
        review_type: str = "SNAPSHOT_REVIEW",
    ) -> tuple[EventSourceScanRequest, ...]:
        category = _category(category)
        review = _review_type(review_type)
        end = _timestamp(as_of, "as_of")
        days = self.lookback_days(category, review_type=review) if lookback_days is None else lookback_days
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise ValueError("lookback_days must be a positive integer")
        start = normalize_timestamp((parse_timestamp(end) - timedelta(days=days)).isoformat(), "lookback_start")
        return tuple(
            EventSourceScanRequest(
                asset=asset,
                category=category,
                source_id=source.id,
                source_name=source.source_name,
                source_url=source.url,
                authority=source.authority,
                lookback_start=start,
                as_of=end,
                tier=source.tier,
                required_for_full_coverage=source.required_for_full_coverage,
            )
            for source in self.sources
            if source.category == category and source.applies_to(asset)
        )

    @staticmethod
    def _material(item: Mapping[str, Any], asset: str, start: datetime, end: datetime) -> bool:
        materiality = item.get("materiality", False)
        if not (materiality is True or isinstance(materiality, str) and materiality.upper() in _MATERIAL_VALUES):
            return False
        published = item.get("published_at")
        if published is not None:
            timestamp = parse_timestamp(published)
            if timestamp < start or timestamp > end:
                return False
        affected = set(item.get("affected_assets", ()))
        return not affected or asset.upper() == "MARKET" or asset.upper() in affected or "MARKET" in affected

    def build_result(
        self,
        asset: str,
        category: str,
        as_of: str | datetime,
        responses: Iterable[EventSourceScanResponse | Mapping[str, Any]] | Mapping[str, Any],
        *,
        lookback_days: int | None = None,
        review_type: str = "SNAPSHOT_REVIEW",
    ) -> EventScanResult:
        category = _category(category)
        end = _timestamp(as_of, "scan_as_of")
        requests = self.build_requests(asset, category, end, lookback_days=lookback_days, review_type=review_type)
        by_id = {request.source_id: request for request in requests}
        coerced = _coerce_responses(responses)
        if not coerced:
            raise ValueError("event scan requires at least one source response")
        if len({item.source_id for item in coerced}) != len(coerced):
            raise ValueError("event scan responses contain duplicate source IDs")
        unknown = {item.source_id for item in coerced} - set(by_id)
        if unknown:
            raise ValueError("event scan response contains an unknown source ID")
        start = parse_timestamp(requests[0].lookback_start) if requests else parse_timestamp(end)
        end_time = parse_timestamp(end)
        required = [source for source in self.sources if source.category == category and source.applies_to(asset) and source.required_for_full_coverage]
        responses_by_id = {item.source_id: item for item in coerced}
        checked = tuple(request.source_id for request in requests if request.source_id in responses_by_id)
        if not checked:
            raise ValueError("event scan has no recognized source responses")
        reachable_required = sum(
            bool(responses_by_id.get(source.id) and responses_by_id[source.id].reachable)
            for source in required
        )
        coverage = reachable_required / len(required) if required else 0.0
        material_events: list[dict[str, Any]] = []
        conflict = False
        for source_id in checked:
            response = responses_by_id[source_id]
            conflict = conflict or response.conflict
            for item in response.items if response.reachable else ():
                if self._material(item, asset, start, end_time):
                    material_events.append({"source_id": source_id, **dict(item)})
        by_url: dict[str, set[Any]] = {}
        for item in material_events:
            if item.get("canonical_url"):
                by_url.setdefault(item["canonical_url"], set()).add(str(item.get("materiality")).upper())
        conflict = conflict or any(len(values) > 1 for values in by_url.values())
        medium, high = self._coverage_thresholds()
        confidence = "LOW" if conflict or coverage < medium else "HIGH" if coverage >= high else "MEDIUM"
        return EventScanResult(
            asset=asset,
            category=category,
            scan_as_of=end,
            lookback_days=lookback_days or self.lookback_days(category, review_type=review_type),
            sources_checked=checked,
            material_events=tuple(material_events),
            coverage=coverage,
            confidence=confidence,
        )

    def scan(
        self,
        asset: str,
        category: str,
        as_of: str | datetime,
        *,
        responses: Iterable[EventSourceScanResponse | Mapping[str, Any]] | Mapping[str, Any] | None = None,
        source_fetcher: Callable[[EventSourceScanRequest], EventSourceScanResponse | Mapping[str, Any]] | None = None,
        lookback_days: int | None = None,
        review_type: str = "SNAPSHOT_REVIEW",
        fetch_mode: FetchMode | str = FetchMode.AUTO,
    ) -> EventScanResult:
        mode = FetchMode.parse(fetch_mode)
        requests = self.build_requests(asset, category, as_of, lookback_days=lookback_days, review_type=review_type)
        if responses is None:
            if mode == FetchMode.CACHE_ONLY:
                raise ValueError("CACHE_ONLY has no cached event scan result")
            if source_fetcher is None:
                raise ValueError("event scan responses or a source_fetcher are required")
            responses = tuple(source_fetcher(request) for request in requests)
        return self.build_result(asset, category, as_of, responses, lookback_days=lookback_days, review_type=review_type)

    def scan_shared_regulatory(
        self,
        assets: Iterable[str],
        as_of: str | datetime,
        *,
        responses: Iterable[EventSourceScanResponse | Mapping[str, Any]] | Mapping[str, Any] | None = None,
        source_fetcher: Callable[[EventSourceScanRequest], EventSourceScanResponse | Mapping[str, Any]] | None = None,
        lookback_days: int | None = None,
        review_type: str = "SNAPSHOT_REVIEW",
        fetch_mode: FetchMode | str = FetchMode.AUTO,
    ) -> dict[str, EventScanResult]:
        symbols = tuple(dict.fromkeys(_text(asset, "asset").upper() for asset in assets))
        if not symbols:
            return {}
        market = self.scan(
            "MARKET", "regulatory", as_of,
            responses=responses,
            source_fetcher=source_fetcher,
            lookback_days=lookback_days,
            review_type=review_type,
            fetch_mode=fetch_mode,
        )
        result = {}
        for asset in symbols:
            events = tuple(
                item for item in market.material_events
                if not item.get("affected_assets")
                or asset in item.get("affected_assets", ())
                or "MARKET" in item.get("affected_assets", ())
            )
            result[asset] = EventScanResult(
                asset=asset,
                category="regulatory",
                scan_as_of=market.scan_as_of,
                lookback_days=market.lookback_days,
                sources_checked=market.sources_checked,
                material_events=events,
                coverage=market.coverage,
                confidence=market.confidence,
            )
        return result

    def observation(self, scan: EventScanResult, metric_key: str, *, fetched_at: str | datetime | None = None) -> dict[str, Any]:
        return event_scan_observation(scan, metric_key, fetched_at=fetched_at)


def event_metric_category(metric_key: str) -> str | None:
    key = metric_definition(metric_key).key
    for category, candidate in _EVENT_METRICS.items():
        if key == candidate:
            return category
    return None


__all__ = [
    "EventScanner",
    "EventSourceScanRequest",
    "EventSourceScanResponse",
    "event_metric_category",
]
