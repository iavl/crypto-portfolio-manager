"""Bounded structured transports for allowlisted event sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from ..models.time import normalize_timestamp, parse_timestamp
from ..providers.base import ProviderResponseError
from ..providers.http import HttpClient, redact_log, redact_secrets
from ..state.market_data import _atomic_write
from ..state.snapshots import runtime_data_dir
from .scanner import EventSourceScanRequest, EventSourceScanResponse


TRANSPORT_KINDS = (
    "GITHUB_RELEASES",
    "GITHUB_SECURITY_ADVISORIES",
    "GITHUB_COMMITS",
    "RSS_ATOM",
    "DISCOURSE_JSON",
    "RPC_LOGS",
    "WEB",
)
MAX_CANDIDATES = 100
MAX_GITHUB_PAGES = 3
GITHUB_PAGE_SIZE = 100
MAX_RPC_BLOCK_RANGE = 500
BNB_GOVERNOR_ADDRESS = "0x0000000000000000000000000000000000002004"
BNB_RPC_ENDPOINT = "https://bsc-dataseed.bnbchain.org"
BNB_RPC_ENDPOINTS = (BNB_RPC_ENDPOINT, "https://bsc-dataseed-public.bnbchain.org")
RPC_EVENT_TOPICS = (
    "0x95f03e437e6d5037418f12bef80fc7e0a5f27754c078a1d5d3f62d39bac44e50",
    "0x712ae1383f79ac853f8d882153778e0260ef8f03b504e2866e0593e04d2b291f",
)
_MAX_EXCERPT = 2_000


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _timestamp(value: Any, field: str) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number > 100_000_000_000:
            number /= 1000
        return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), field)
    return normalize_timestamp(value, field)


def _bounded(value: Any, limit: int = _MAX_EXCERPT) -> str:
    text = str(redact_secrets(value or "")).strip()
    return text[:limit]


def _url(value: Any, field: str = "canonical_url") -> str:
    result = _text(value, field)
    parts = urlsplit(result)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"{field} must use http or https")
    return result


@dataclass(frozen=True)
class EventTransportSpec:
    source_id: str
    kind: str
    endpoint: str
    asset: str
    category: str
    source_group: str
    lookback_start: str
    as_of: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id").lower())
        kind = _text(self.kind, "transport kind").upper()
        if kind not in TRANSPORT_KINDS:
            raise ValueError(f"transport kind must be one of {TRANSPORT_KINDS}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "endpoint", _url(self.endpoint, "endpoint"))
        object.__setattr__(self, "asset", _text(self.asset, "asset").upper())
        object.__setattr__(self, "category", _text(self.category, "category").lower())
        object.__setattr__(self, "source_group", _text(self.source_group, "source_group").lower())
        start = _timestamp(self.lookback_start, "lookback_start")
        end = _timestamp(self.as_of, "as_of")
        if parse_timestamp(start) > parse_timestamp(end):
            raise ValueError("lookback_start must not be after as_of")
        object.__setattr__(self, "lookback_start", start)
        object.__setattr__(self, "as_of", end)

    @classmethod
    def from_request(cls, request: EventSourceScanRequest, endpoint: str) -> "EventTransportSpec":
        inferred_kind = infer_transport_kind(endpoint, request.source_id)
        if (
            inferred_kind == "WEB"
            and getattr(request, "transport_kind", None)
            and tuple(getattr(request, "source_urls", ())) == (request.source_url,)
        ):
            inferred_kind = request.transport_kind
        return cls(
            request.source_id,
            inferred_kind,
            endpoint,
            request.asset,
            request.category,
            getattr(request, "source_group", None) or request.authority,
            request.lookback_start,
            request.as_of,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "endpoint": self.endpoint,
            "asset": self.asset,
            "category": self.category,
            "source_group": self.source_group,
            "lookback_start": self.lookback_start,
            "as_of": self.as_of,
        }


@dataclass(frozen=True)
class EventCandidate:
    external_id: str
    title: str
    published_at: str
    canonical_url: str
    excerpt: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "external_id", _text(self.external_id, "external_id"))
        object.__setattr__(self, "title", _text(self.title, "title"))
        object.__setattr__(self, "published_at", _timestamp(self.published_at, "published_at"))
        object.__setattr__(self, "canonical_url", _url(self.canonical_url))
        object.__setattr__(self, "excerpt", _bounded(self.excerpt))

    def as_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "title": self.title,
            "published_at": self.published_at,
            "canonical_url": self.canonical_url,
            "excerpt": self.excerpt,
        }

    def as_scan_item(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "title": self.title,
            "published_at": self.published_at,
            "canonical_url": self.canonical_url,
            "summary": self.excerpt or None,
            "materiality": "CANDIDATE",
            "relevance": "UNKNOWN",
        }


@dataclass(frozen=True)
class EventTransportResult:
    source_id: str
    transport_kind: str
    endpoint: str
    reachable: bool
    complete_for_source: bool
    checked_at: str
    candidates: tuple[EventCandidate, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id").lower())
        kind = _text(self.transport_kind, "transport_kind").upper()
        if kind not in TRANSPORT_KINDS:
            raise ValueError(f"transport_kind must be one of {TRANSPORT_KINDS}")
        object.__setattr__(self, "transport_kind", kind)
        object.__setattr__(self, "endpoint", _url(self.endpoint, "endpoint"))
        if not isinstance(self.reachable, bool) or not isinstance(self.complete_for_source, bool):
            raise ValueError("transport reachability flags must be boolean")
        if self.complete_for_source and not self.reachable:
            raise ValueError("an unreachable transport cannot be complete")
        object.__setattr__(self, "checked_at", _timestamp(self.checked_at, "checked_at"))
        candidates = tuple(self.candidates)
        if any(not isinstance(item, EventCandidate) for item in candidates):
            raise ValueError("transport candidates must be EventCandidate objects")
        if len(candidates) > MAX_CANDIDATES:
            raise ValueError("transport returned too many candidates")
        object.__setattr__(self, "candidates", candidates)
        if self.error is not None:
            object.__setattr__(self, "error", _bounded(self.error))
        if not self.reachable and not self.error:
            raise ValueError("unreachable transport requires an error")

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "transport_kind": self.transport_kind,
            "endpoint": self.endpoint,
            "reachable": self.reachable,
            "complete_for_source": self.complete_for_source,
            "checked_at": self.checked_at,
            "candidates": [item.as_dict() for item in self.candidates],
            "error": self.error,
        }

    @property
    def kind(self) -> str:
        return self.transport_kind

    @property
    def items(self) -> tuple[EventCandidate, ...]:
        return self.candidates

    def as_response(self) -> EventSourceScanResponse:
        return EventSourceScanResponse(
            source_id=self.source_id,
            reachable=self.reachable,
            checked_at=self.checked_at,
            items=tuple(item.as_scan_item() for item in self.candidates),
            error=self.error,
            complete_for_source=self.complete_for_source,
        )


def infer_transport_kind(endpoint: str, source_id: str = "") -> str:
    path = urlsplit(endpoint).path.lower()
    source = source_id.lower()
    if source == "bnb-governor-rpc" or path.endswith("/rpc"):
        return "RPC_LOGS"
    if urlsplit(endpoint).netloc in {"api.github.com", "github.com"}:
        if "/security-advisories" in path:
            return "GITHUB_SECURITY_ADVISORIES"
        if "/releases" in path:
            return "GITHUB_RELEASES"
        if "/commits" in path or source in {"bitcoin-bips", "ethereum-eips", "ethereum-all-core-devs", "bnb-beps"}:
            return "GITHUB_COMMITS"
    if "discourse" in urlsplit(endpoint).netloc or path.endswith(".json"):
        return "DISCOURSE_JSON"
    if path.endswith((".xml", ".rss", "/feed", "/feed.xml")) or "rss" in path or "atom" in path:
        return "RSS_ATOM"
    return "WEB"


def _github_endpoint(endpoint: str, kind: str) -> str:
    parts = urlsplit(endpoint)
    if parts.netloc != "github.com":
        return endpoint
    pieces = [item for item in parts.path.split("/") if item]
    if len(pieces) < 2:
        return endpoint
    repository = "/".join(pieces[:2])
    suffix = {
        "GITHUB_RELEASES": "releases",
        "GITHUB_SECURITY_ADVISORIES": "security-advisories",
        "GITHUB_COMMITS": "commits",
    }[kind]
    return f"https://api.github.com/repos/{repository}/{suffix}"


def _date_from(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return normalize_timestamp(value, "event published_at")
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return normalize_timestamp(parsed.isoformat(), "event published_at")
        except (TypeError, ValueError, OverflowError):
            return None


def _in_lookback(candidate: EventCandidate, spec: EventTransportSpec) -> bool:
    return parse_timestamp(spec.lookback_start) <= parse_timestamp(candidate.published_at) <= parse_timestamp(spec.as_of)


def _dedup(candidates: Iterable[EventCandidate], spec: EventTransportSpec) -> tuple[EventCandidate, ...]:
    result: dict[str, EventCandidate] = {}
    for candidate in candidates:
        if _in_lookback(candidate, spec):
            result.setdefault(candidate.external_id, candidate)
    return tuple(sorted(result.values(), key=lambda item: (item.published_at, item.external_id)))[:MAX_CANDIDATES]


def _github_candidates(payloads: Iterable[Any], spec: EventTransportSpec) -> tuple[EventCandidate, ...]:
    candidates: list[EventCandidate] = []
    for payload in payloads:
        if not isinstance(payload, list):
            raise ProviderResponseError("GitHub event response must be an array")
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            if spec.kind == "GITHUB_RELEASES":
                if item.get("draft") or item.get("prerelease"):
                    continue
                published = _date_from(item.get("published_at") or item.get("created_at"))
                title = item.get("name") or item.get("tag_name")
                url = item.get("html_url")
                external_id = item.get("id") or item.get("tag_name")
                excerpt = item.get("body")
            elif spec.kind == "GITHUB_SECURITY_ADVISORIES":
                published = _date_from(item.get("published_at") or item.get("updated_at"))
                title = item.get("summary") or item.get("ghsa_id")
                url = item.get("html_url") or item.get("url")
                external_id = item.get("ghsa_id") or item.get("id")
                excerpt = item.get("description")
            else:
                commit = item.get("commit")
                message = commit.get("message") if isinstance(commit, Mapping) else None
                author = commit.get("author") if isinstance(commit, Mapping) else None
                published = _date_from(author.get("date") if isinstance(author, Mapping) else None)
                title = message
                url = item.get("html_url")
                external_id = item.get("sha")
                excerpt = message
            if published is None or title is None or url is None or external_id is None:
                continue
            if not all(str(value).strip() for value in (title, url, external_id)):
                continue
            candidates.append(EventCandidate(str(external_id), str(title), published, str(url), _bounded(excerpt)))
    return _dedup(candidates, spec)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _xml_text(element: ET.Element, names: set[str]) -> str | None:
    for child in element.iter():
        if _local_name(child.tag) in names and child.text:
            return child.text.strip()
    return None


def _xml_link(element: ET.Element) -> str | None:
    for child in element.iter():
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text:
            return child.text.strip()
    return None


def _rss_candidates(text: str, spec: EventTransportSpec) -> tuple[EventCandidate, ...]:
    if len(text.encode("utf-8")) > 5_000_000 or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ProviderResponseError("RSS/Atom document exceeds the safe XML contract")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ProviderResponseError("RSS/Atom document is malformed") from exc
    candidates = []
    for element in root.iter():
        if _local_name(element.tag) not in {"item", "entry"}:
            continue
        title = _xml_text(element, {"title"})
        published = _xml_text(element, {"pubdate", "published", "updated", "date"})
        canonical_url = _xml_link(element)
        timestamp = _date_from(published)
        if not title or not canonical_url or timestamp is None:
            continue
        candidates.append(EventCandidate(
            hashlib.sha256(f"{canonical_url}|{timestamp}".encode()).hexdigest(),
            title,
            timestamp,
            canonical_url,
            _bounded(_xml_text(element, {"description", "summary", "content"})),
        ))
    return _dedup(candidates, spec)


def _discourse_candidates(payload: Any, spec: EventTransportSpec) -> tuple[EventCandidate, ...]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("topic_list"), Mapping):
        raise ProviderResponseError("Discourse response has no topic_list")
    topics = payload["topic_list"].get("topics")
    if not isinstance(topics, list):
        raise ProviderResponseError("Discourse response has no topic array")
    candidates = []
    for item in topics:
        if not isinstance(item, Mapping):
            continue
        published = _date_from(item.get("created_at") or item.get("last_posted_at"))
        topic_id = item.get("id")
        title = item.get("title")
        url = item.get("url")
        if isinstance(url, str) and url.startswith("/"):
            url = f"https://governance.aave.com{url}"
        if published is None or topic_id is None or not isinstance(title, str) or not isinstance(url, str):
            continue
        candidates.append(EventCandidate(
            f"discourse:{topic_id}", title, published, url,
            _bounded(item.get("excerpt") or item.get("fancy_title")),
        ))
    return _dedup(candidates, spec)


def _rpc_candidates(payload: Any, spec: EventTransportSpec) -> tuple[EventCandidate, ...]:
    if not isinstance(payload, list):
        raise ProviderResponseError("RPC logs response must be an array")
    candidates = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        topics = item.get("topics")
        if not isinstance(topics, list) or not topics or str(topics[0]).lower() not in {topic.lower() for topic in RPC_EVENT_TOPICS}:
            continue
        transaction_hash = item.get("transactionHash")
        timestamp = _date_from(item.get("timestamp"))
        if timestamp is None:
            raise ProviderResponseError("RPC event log has no usable block timestamp")
        if not isinstance(transaction_hash, str) or not transaction_hash.strip():
            raise ProviderResponseError("RPC event log has no transaction hash")
        proposal = str(topics[1]) if len(topics) > 1 else transaction_hash
        block = item.get("blockNumber", "")
        candidates.append(EventCandidate(
            f"bnb-governor:{proposal}",
            f"BNB Governor proposal {proposal}",
            timestamp,
            f"https://bscscan.com/tx/{transaction_hash}",
            f"allowlisted Governor event at block {block}",
        ))
    return _dedup(candidates, spec)


class EventTransportCache:
    """Small content-addressed cache for safe normalized transport results."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root is not None else runtime_data_dir() / "event-cache"

    def _path(self, spec: EventTransportSpec) -> Path:
        digest = hashlib.sha256(json.dumps(spec.as_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return self.root / f"{digest}.json"

    def load(self, spec: EventTransportSpec) -> EventTransportResult | None:
        path = self._path(spec)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return EventTransportResult(
                value["source_id"], value["transport_kind"], value["endpoint"], value["reachable"],
                value["complete_for_source"], value["checked_at"],
                tuple(EventCandidate(**item) for item in value.get("candidates", ())), value.get("error"),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def save(self, spec: EventTransportSpec, result: EventTransportResult) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._path(spec), json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")


class StructuredEventTransport:
    """Fetch one allowlisted source with bounded structured transports."""

    def __init__(
        self,
        *,
        client: HttpClient | Any | None = None,
        cache: EventTransportCache | None = None,
        github_token: str | None = None,
        max_pages: int = MAX_GITHUB_PAGES,
    ) -> None:
        self.client = client or HttpClient()
        self.cache = cache
        self.github_token = github_token
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 10:
            raise ValueError("max_pages must be an integer in [1, 10]")
        self.max_pages = max_pages

    def _headers(self) -> Mapping[str, str]:
        return {"Authorization": f"Bearer {self.github_token}"} if self.github_token else {}

    def _fetch_one(self, spec: EventTransportSpec) -> EventTransportResult:
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        if spec.kind == "WEB":
            return EventTransportResult(spec.source_id, spec.kind, spec.endpoint, False, False, checked_at, error="NO_STRUCTURED_TRANSPORT")
        if spec.kind.startswith("GITHUB_"):
            endpoint = _github_endpoint(spec.endpoint, spec.kind)
            payloads = []
            complete = False
            for page in range(1, self.max_pages + 1):
                payload = self.client.get_json(
                    endpoint,
                    params={"per_page": GITHUB_PAGE_SIZE, "page": page},
                    headers=self._headers(),
                )
                if not isinstance(payload, list):
                    raise ProviderResponseError("GitHub event response must be an array")
                payloads.append(payload)
                if len(payload) < GITHUB_PAGE_SIZE:
                    complete = True
                    break
            return EventTransportResult(spec.source_id, spec.kind, endpoint, True, complete, checked_at, _github_candidates(payloads, spec))
        if spec.kind == "RSS_ATOM":
            getter = getattr(self.client, "get_text", None)
            if getter is not None:
                text = getter(spec.endpoint)
            elif getattr(self.client, "request_text", None) is not None:
                text = self.client.request_text("GET", spec.endpoint)
            else:
                value = self.client.get_json(spec.endpoint)
                text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
            return EventTransportResult(spec.source_id, spec.kind, spec.endpoint, True, True, checked_at, _rss_candidates(text, spec))
        if spec.kind == "DISCOURSE_JSON":
            endpoint = spec.endpoint if spec.endpoint.endswith(".json") else spec.endpoint.rstrip("/") + ".json"
            payload = self.client.get_json(endpoint)
            return EventTransportResult(spec.source_id, spec.kind, endpoint, True, True, checked_at, _discourse_candidates(payload, spec))
        if spec.kind == "RPC_LOGS":
            endpoint = spec.endpoint
            latest = self.client.post_json(
                endpoint,
                json_body={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
                idempotent=True,
            )
            if isinstance(latest, Mapping) and latest.get("error") is not None:
                error = latest["error"] if isinstance(latest["error"], Mapping) else {}
                raise ProviderResponseError(
                    f"RPC latest block request rejected ({error.get('code', 'unknown')})"
                )
            if not isinstance(latest, Mapping) or not isinstance(latest.get("result"), str):
                raise ProviderResponseError("RPC latest block response is malformed")
            to_block = int(latest["result"], 16)
            from_block = max(0, to_block - MAX_RPC_BLOCK_RANGE)
            response = self.client.post_json(
                endpoint,
                json_body={
                    "jsonrpc": "2.0", "id": 2, "method": "eth_getLogs",
                    "params": [{
                        "address": BNB_GOVERNOR_ADDRESS,
                        "fromBlock": hex(from_block),
                        "toBlock": hex(to_block),
                        "topics": [list(RPC_EVENT_TOPICS)],
                    }],
                },
                idempotent=True,
            )
            if isinstance(response, Mapping) and response.get("error") is not None:
                error = response["error"] if isinstance(response["error"], Mapping) else {}
                raise ProviderResponseError(
                    f"RPC logs request rejected ({error.get('code', 'unknown')})"
                )
            if not isinstance(response, Mapping) or not isinstance(response.get("result"), list):
                raise ProviderResponseError("RPC logs response is malformed")
            return EventTransportResult(spec.source_id, spec.kind, endpoint, True, True, checked_at, _rpc_candidates(response["result"], spec))
        raise ProviderResponseError(f"unsupported event transport {spec.kind}")

    def fetch_result(
        self,
        request: EventSourceScanRequest,
        *,
        allow_network: bool = True,
    ) -> EventTransportResult:
        endpoints = tuple(dict.fromkeys(getattr(request, "source_urls", ()) or (request.source_url,)))
        results: list[EventTransportResult] = []
        for endpoint in endpoints:
            spec = EventTransportSpec.from_request(request, endpoint)
            cached = self.cache.load(spec) if self.cache is not None else None
            if cached is not None:
                results.append(cached)
                continue
            if not allow_network:
                results.append(EventTransportResult(
                    request.source_id,
                    spec.kind,
                    spec.endpoint,
                    False,
                    False,
                    request.as_of,
                    error="EVENT_SOURCE_CACHE_MISS",
                ))
                continue
            try:
                result = self._fetch_one(spec)
            except Exception as exc:
                diagnostic = getattr(exc, "diagnostic", None)
                if hasattr(diagnostic, "as_dict"):
                    diagnostic = diagnostic.as_dict()
                code = str(diagnostic.get("error_code", "PROVIDER_TRANSPORT_ERROR")) if isinstance(diagnostic, Mapping) else "PROVIDER_TRANSPORT_ERROR"
                result = EventTransportResult(
                    request.source_id,
                    spec.kind,
                    spec.endpoint,
                    False,
                    False,
                    datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    error=redact_log(f"{code}: {exc}") or code,
                )
            if self.cache is not None:
                self.cache.save(spec, result)
            results.append(result)
        reachable = [item for item in results if item.reachable]
        complete = [item for item in reachable if item.complete_for_source]
        candidates: dict[str, EventCandidate] = {}
        for item in complete:
            for candidate in item.candidates:
                candidates.setdefault(candidate.external_id, candidate)
        errors = "; ".join(item.error for item in results if item.error) or None
        kind = complete[0].transport_kind if complete else results[0].transport_kind
        endpoint = complete[0].endpoint if complete else results[0].endpoint
        return EventTransportResult(
            request.source_id,
            kind,
            endpoint,
            bool(reachable),
            bool(complete),
            max((item.checked_at for item in results), default=request.as_of),
            tuple(sorted(candidates.values(), key=lambda item: (item.published_at, item.external_id)))[:MAX_CANDIDATES],
            errors if not complete else None,
        )

    def fetch(self, request: EventSourceScanRequest) -> EventSourceScanResponse:
        return self.fetch_result(request).as_response()

    def fetch_cached(self, request: EventSourceScanRequest) -> EventSourceScanResponse:
        """Return only cached normalized transport data; never use the network."""
        return self.fetch_result(request, allow_network=False).as_response()

    __call__ = fetch


def structured_event_source_fetcher(
    *,
    client: HttpClient | Any | None = None,
    cache: EventTransportCache | None = None,
    github_token: str | None = None,
) -> StructuredEventTransport:
    """Return the scanner-compatible structured source fetcher object."""
    return StructuredEventTransport(client=client, cache=cache, github_token=github_token)


__all__ = [
    "BNB_GOVERNOR_ADDRESS",
    "BNB_RPC_ENDPOINT",
    "BNB_RPC_ENDPOINTS",
    "EventCandidate",
    "EventTransportCache",
    "EventTransportResult",
    "EventTransportSpec",
    "GITHUB_PAGE_SIZE",
    "MAX_CANDIDATES",
    "MAX_RPC_BLOCK_RANGE",
    "RPC_EVENT_TOPICS",
    "StructuredEventTransport",
    "TRANSPORT_KINDS",
    "infer_transport_kind",
    "structured_event_source_fetcher",
]
