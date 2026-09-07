"""L2BEAT project-classification and Ethereum-secured activity adapter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
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
PROJECTS_PATH = "/v1/projects"
TVS_PATH = "/v1/tvs"
ACTIVITY_PATH = "/v1/activity"

_KEYS = {"eth.l2.tvs_usd", "eth.l2.activity_30d"}


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
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, Mapping):
        raw = payload.get("data", payload.get("projects", payload.get("rows", payload.get("results"))))
        if isinstance(raw, Mapping):
            raw = raw.get("data", raw.get("projects", raw.get("rows", raw.get("results"))))
    else:
        raw = None
    if not isinstance(raw, list):
        raise ProviderResponseError("L2BEAT response has no row list")
    if any(not isinstance(row, Mapping) for row in raw):
        raise ProviderDataError("L2BEAT response contains a malformed row")
    return tuple(raw)


def _project_id(row: Mapping[str, Any]) -> str | None:
    value = row.get("id", row.get("projectId", row.get("slug")))
    return str(value).strip() if value is not None and str(value).strip() else None


def _is_ethereum_project(row: Mapping[str, Any]) -> bool:
    if row.get("isEthereum") is True or row.get("ethereum") is True:
        return True
    values: list[str] = []
    for key in ("settlementLayer", "settlement", "hostChain", "chain", "daLayer"):
        value = row.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, Mapping):
            values.extend(str(item) for item in value.values())
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return any("ethereum" in value.lower() or value.strip().lower() in {"eth", "mainnet"} for value in values)


def ethereum_project_ids(payload: Any) -> frozenset[str]:
    ids = {
        project_id
        for row in _rows(payload)
        if _is_ethereum_project(row)
        for project_id in (_project_id(row),)
        if project_id
    }
    if not ids:
        raise ProviderUnsupportedMetric("L2BEAT project catalog has no explicitly Ethereum-secured projects")
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
    allowed = set(project_ids)
    cutoff = parse_timestamp(as_of) if as_of else None
    latest_by_project: dict[str, tuple[str, float]] = {}
    for row in _rows(payload):
        project = _project_id(row)
        if project not in allowed:
            continue
        timestamp = _timestamp(row)
        if cutoff is not None and parse_timestamp(timestamp) > cutoff:
            continue
        value = _value(row, ("tvs", "tvl", "value", "usd"))
        if value is None:
            continue
        prior = latest_by_project.get(project)
        if prior is None or parse_timestamp(timestamp) > parse_timestamp(prior[0]):
            latest_by_project[project] = (timestamp, value)
    if not latest_by_project:
        raise ProviderInsufficientHistory("L2BEAT TVS has no usable Ethereum-secured project rows")
    observed = max(item[0] for item in latest_by_project.values())
    return {
        "asset": "ETH",
        "metric_key": "eth.l2.tvs_usd",
        "value": sum(item[1] for item in latest_by_project.values()),
        "unit": metric_definition("eth.l2.tvs_usd").unit,
        "period": "current",
        "observed_at": observed,
        "fetched_at": fetched_at,
        "source": "l2beat",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "v1/tvs",
            "source_url": BASE_URL + endpoint,
            "ethereum_secured_project_ids": sorted(latest_by_project),
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
    allowed = set(project_ids)
    cutoff = parse_timestamp(as_of) if as_of else None
    rows: list[tuple[str, float]] = []
    for row in _rows(payload):
        project = _project_id(row)
        if project not in allowed:
            continue
        timestamp = _timestamp(row)
        if cutoff is not None and parse_timestamp(timestamp) > cutoff:
            continue
        value = _value(row, ("txCount", "transactions", "activity", "value", "count"))
        if value is not None:
            rows.append((timestamp, value))
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
            "source_dataset": "v1/activity",
            "source_url": BASE_URL + endpoint,
            "window": "30d",
            "rows_used": len(selected),
            "ethereum_secured_project_ids": sorted(allowed),
        },
    }


class L2BeatProvider:
    name = "l2beat"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=tuple(sorted(_KEYS)),
            historical_series=tuple(sorted(_KEYS)),
            supports_batching=True,
            requires_api_key=False,
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.asset != "ETH":
            raise ProviderUnsupportedMetric("L2BEAT metrics require ETH scope")
        requested = tuple(dict.fromkeys(request.metric_keys))
        fetched_at = _now(self.clock)
        project_payload = self.client.get_json(BASE_URL + PROJECTS_PATH)
        ids = ethereum_project_ids(project_payload)
        values: list[Mapping[str, Any]] = []
        if "eth.l2.tvs_usd" in requested:
            values.append(parse_tvs_payload(
                self.client.get_json(BASE_URL + TVS_PATH), ids,
                fetched_at=fetched_at, as_of=request.parameters.get("as_of"),
            ))
        if "eth.l2.activity_30d" in requested:
            values.append(parse_activity_payload(
                self.client.get_json(BASE_URL + ACTIVITY_PATH), ids,
                fetched_at=fetched_at, as_of=request.parameters.get("as_of"),
            ))
        return ProviderResponse(tuple(values), network_requests=1 + len(values))


L2beatProvider = L2BeatProvider
L2BEATProvider = L2BeatProvider
parse_tvs = parse_tvs_payload
parse_activity = parse_activity_payload


__all__ = [
    "ACTIVITY_PATH",
    "BASE_URL",
    "L2BeatProvider",
    "L2beatProvider",
    "L2BEATProvider",
    "PROJECTS_PATH",
    "TVS_PATH",
    "ethereum_project_ids",
    "parse_activity_payload",
    "parse_activity",
    "parse_tvs_payload",
    "parse_tvs",
]
