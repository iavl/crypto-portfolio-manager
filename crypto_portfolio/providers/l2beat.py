"""L2BEAT project-classification and Ethereum-secured activity adapter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderAuthenticationError,
    ProviderCapabilities,
    ProviderDataError,
    ProviderInsufficientHistory,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient


BASE_URL = "https://api.l2beat.com"
OPENAPI_PATH = "/openapi"
PROJECTS_PATH = "/v1/projects"
TVS_PATH = "/v1/tvs"
ACTIVITY_PATH = "/v1/activity"

_KEYS = {"eth.l2.tvs_usd", "eth.l2.activity_30d"}
MAX_PROJECT_DETAILS = 50


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProviderDataError(f"{field} is not numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise ProviderDataError(f"{field} is invalid")
    return result


def _rows(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(payload, list):
        raise ProviderResponseError("L2BEAT response must be an array")
    raw = payload
    if any(not isinstance(row, Mapping) for row in raw):
        raise ProviderDataError("L2BEAT response contains a malformed row")
    return tuple(raw)


def _project_id(row: Mapping[str, Any]) -> str | None:
    value = row.get("id")
    return str(value).strip() if value is not None and str(value).strip() else None


def _is_ethereum_project(row: Mapping[str, Any]) -> bool:
    value = row.get("hostChain")
    return isinstance(value, str) and value.strip().lower() in {"ethereum", "ethereum mainnet"}


def ethereum_project_ids(payload: Any) -> frozenset[str]:
    ids = {
        project_id
        for row in _rows(payload)
        if _is_ethereum_project(row)
        for project_id in (_project_id(row),)
        if project_id
    }
    if not ids:
        raise ProviderUnsupportedMetric("L2BEAT project details have no explicitly Ethereum host chain")
    return frozenset(ids)


def _timestamp(row: Mapping[str, Any]) -> str:
    value = row.get("date", row.get("timestamp", row.get("time")))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = _number(value, "L2BEAT timestamp")
        if number > 100_000_000_000:
            number /= 1000
        return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), "L2BEAT timestamp")
    if not isinstance(value, str) or not value.strip():
        raise ProviderDataError("L2BEAT row timestamp is missing")
    if isinstance(value, str) and len(value) == 10:
        value += "T00:00:00Z"
    return normalize_timestamp(value, "L2BEAT timestamp")


def _value(row: Mapping[str, Any], names: Iterable[str]) -> float | None:
    for name in names:
        if name in row and row[name] is not None:
            return _number(row[name], f"L2BEAT {name}")
    for container in ("data", "value", "metrics"):
        nested = row.get(container)
        if isinstance(nested, Mapping):
            found = _value(nested, names)
            if found is not None:
                return found
    return None


def parse_tvs_payload(
    payload: Any,
    project_ids: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str = TVS_PATH,
) -> Mapping[str, Any]:
    cutoff = parse_timestamp(as_of) if as_of else None
    latest: tuple[str, float] | None = None
    for row in _rows(payload):
        if set(row) != {"timestamp", "totalTvs", "bySource", "byCategory"}:
            raise ProviderResponseError("L2BEAT TVS row does not match TvsChartDataPoint")
        timestamp = _timestamp(row)
        if cutoff is not None and parse_timestamp(timestamp) > cutoff:
            continue
        for field in ("bySource", "byCategory"):
            if not isinstance(row[field], Mapping):
                raise ProviderDataError(f"L2BEAT TVS {field} is malformed")
        if set(row["bySource"]) != {"native", "canonical", "external"}:
            raise ProviderResponseError("L2BEAT bySource does not match TvsChartDataPoint")
        if set(row["byCategory"]) != {"stablecoins", "eth", "btc", "other", "publicRwa", "restrictedRwa"}:
            raise ProviderResponseError("L2BEAT byCategory does not match TvsChartDataPoint")
        value = _number(row["totalTvs"], "L2BEAT totalTvs")
        if latest is None or parse_timestamp(timestamp) > parse_timestamp(latest[0]):
            latest = (timestamp, value)
    if latest is None:
        raise ProviderInsufficientHistory("L2BEAT TVS has no usable data at or before as_of")
    observed, value = latest
    return {
        "asset": "ETH",
        "metric_key": "eth.l2.tvs_usd",
        "value": value,
        "unit": metric_definition("eth.l2.tvs_usd").unit,
        "period": "current",
        "observed_at": observed,
        "fetched_at": fetched_at,
        "source": "l2beat",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "v1/tvs/{projectId}",
            "source_url": BASE_URL + endpoint,
            "ethereum_secured_project_ids": sorted(project_ids),
        },
    }


def parse_activity_payload(
    payload: Any,
    project_ids: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str = ACTIVITY_PATH,
) -> Mapping[str, Any]:
    cutoff = parse_timestamp(as_of) if as_of else None
    rows: list[tuple[str, float]] = []
    for row in _rows(payload):
        if set(row) != {"timestamp", "txCount", "uopsCount"}:
            raise ProviderResponseError("L2BEAT activity row does not match ActivityChartDataPoint")
        timestamp = _timestamp(row)
        if cutoff is not None and parse_timestamp(timestamp) > cutoff:
            continue
        rows.append((timestamp, _number(row["txCount"], "L2BEAT txCount")))
    if not rows:
        raise ProviderInsufficientHistory("L2BEAT activity has no usable Ethereum-secured project rows")
    latest = max(timestamp for timestamp, _ in rows)
    start = parse_timestamp(latest) - timedelta(days=29)
    selected = [(timestamp, value) for timestamp, value in rows if parse_timestamp(timestamp) >= start]
    return {
        "asset": "ETH",
        "metric_key": "eth.l2.activity_30d",
        "value": sum(value for _, value in selected),
        "unit": metric_definition("eth.l2.activity_30d").unit,
        "period": "30d",
        "observed_at": latest,
        "fetched_at": fetched_at,
        "source": "l2beat",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "v1/activity/{projectId}",
            "source_url": BASE_URL + endpoint,
            "window": "30d",
            "rows_used": len(selected),
            "ethereum_secured_project_ids": sorted(project_ids),
        },
    }


class L2BeatProvider:
    name = "l2beat"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None, api_key: str | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.api_key = api_key
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=tuple(sorted(_KEYS)),
            historical_series=tuple(sorted(_KEYS)),
            supports_batching=True,
            requires_api_key=True,
        )

    def _get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        if not self.api_key:
            raise ProviderAuthenticationError("L2BEAT API key is not configured")
        return self.client.get_json(
            BASE_URL + path,
            params={**dict(params or {}), "apiKey": self.api_key},
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.asset != "ETH":
            raise ProviderUnsupportedMetric("L2BEAT metrics require ETH scope")
        requested = tuple(dict.fromkeys(request.metric_keys))
        fetched_at = _now(self.clock)
        project_payload = self._get(PROJECTS_PATH)
        try:
            ids = ethereum_project_ids(project_payload)
        except ProviderUnsupportedMetric:
            details = []
            project_rows = _rows(project_payload)
            if len(project_rows) > MAX_PROJECT_DETAILS:
                raise ProviderInsufficientHistory(
                    "L2BEAT project host-chain classification exceeds the bounded detail budget"
                )
            for row in project_rows:
                project_id = _project_id(row)
                if project_id:
                    details.append(self._get(f"/v1/project/{project_id}"))
            ids = ethereum_project_ids(details)
        values: list[Mapping[str, Any]] = []
        network_requests = 1 + len(ids)
        if "eth.l2.tvs_usd" in requested:
            observations = [parse_tvs_payload(
                self._get(f"{TVS_PATH}/{project_id}", params={"range": "30d"}), {project_id},
                fetched_at=fetched_at, as_of=request.parameters.get("as_of"),
                endpoint=f"{TVS_PATH}/{project_id}",
            ) for project_id in ids]
            if not observations:
                raise ProviderInsufficientHistory("L2BEAT TVS has no Ethereum-secured project data")
            combined = dict(observations[0])
            combined["value"] = sum(float(item["value"]) for item in observations)
            combined["metadata"] = {**dict(combined.get("metadata") or {}), "ethereum_secured_project_ids": sorted(ids)}
            values.append(combined)
            network_requests += len(ids)
        if "eth.l2.activity_30d" in requested:
            observations = [parse_activity_payload(
                self._get(f"{ACTIVITY_PATH}/{project_id}", params={"range": "30d"}), {project_id},
                fetched_at=fetched_at, as_of=request.parameters.get("as_of"),
                endpoint=f"{ACTIVITY_PATH}/{project_id}",
            ) for project_id in ids]
            if not observations:
                raise ProviderInsufficientHistory("L2BEAT activity has no Ethereum-secured project data")
            combined = dict(observations[0])
            combined["value"] = sum(float(item["value"]) for item in observations)
            combined["metadata"] = {**dict(combined.get("metadata") or {}), "ethereum_secured_project_ids": sorted(ids)}
            values.append(combined)
            network_requests += len(ids)
        return ProviderResponse(tuple(values), network_requests=network_requests)


__all__ = [
    "ACTIVITY_PATH",
    "BASE_URL",
    "L2BeatProvider",
    "MAX_PROJECT_DETAILS",
    "OPENAPI_PATH",
    "PROJECTS_PATH",
    "TVS_PATH",
    "ethereum_project_ids",
    "parse_activity_payload",
    "parse_tvs_payload",
]
