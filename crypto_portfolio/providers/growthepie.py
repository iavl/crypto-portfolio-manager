"""Small, attribution-preserving growthepie public-data adapter."""

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
from .http import HttpClient, classify_transport_error


BASE_URL = "https://api.growthepie.com/v1"
RENT_PATH = "/metrics/rent_paid.json"
DA_OVERVIEW_PATH = "/daoverview.json"
DA_TIMESERIES_PATH = "/datimeseries.json"
MASTER_PATH = "/master.json"
ATTRIBUTION = "growthepie / orbal GmbH"
LICENSE = "CC BY 4.0"

_RENT_KEYS = {"eth.l2.rent_paid_30d_usd", "eth.l2.rent_paid_90d_usd"}
_DA_KEYS = {
    "eth.da.ethereum_blob_data_30d_mb",
    "eth.da.ethereum_blob_fees_30d_usd",
    "eth.da.ethereum_share_of_tracked_da_bytes_30d",
    "eth.da.ethereum_share_of_tracked_da_fees_30d",
}


def _diagnostic(error: Exception) -> Mapping[str, Any]:
    value = getattr(error, "diagnostic", None)
    if hasattr(value, "as_dict"):
        return dict(value.as_dict())
    if isinstance(value, Mapping):
        return dict(value)
    return {
        "error_code": classify_transport_error(error),
        "exception_class": error.__class__.__name__,
        "detail": str(error) or error.__class__.__name__,
    }


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


def _timestamp(value: Any, field: str) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = _number(value, field)
        if number > 100_000_000_000:
            number /= 1000
        return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), field)
    if not isinstance(value, str) or not value.strip():
        raise ProviderDataError(f"{field} is missing")
    if isinstance(value, str) and len(value) == 10:
        value += "T00:00:00Z"
    return normalize_timestamp(value, field)


def _rows(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, Mapping):
        raw = payload.get("data", payload.get("rows", payload.get("results")))
        if isinstance(raw, Mapping):
            raw = raw.get("data", raw.get("rows", raw.get("results")))
    else:
        raw = None
    if not isinstance(raw, list):
        raise ProviderResponseError("growthepie response has no row list")
    if any(not isinstance(item, Mapping) for item in raw):
        raise ProviderDataError("growthepie response contains a malformed row")
    return tuple(raw)


def _row_date(row: Mapping[str, Any]) -> str:
    return _timestamp(row.get("date", row.get("timestamp", row.get("time"))), "growthepie row timestamp")


def _value(row: Mapping[str, Any], names: Iterable[str]) -> float | None:
    for name in names:
        if name in row and row[name] is not None:
            return _number(row[name], f"growthepie {name}")
    for container_name in ("metrics", "values", "value"):
        nested = row.get(container_name)
        if isinstance(nested, Mapping):
            found = _value(nested, names)
            if found is not None:
                return found
    return None


def _observation(
    asset: str,
    key: str,
    value: float,
    *,
    observed_at: str,
    fetched_at: str,
    period: str,
    endpoint: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "asset": asset,
        "metric_key": key,
        "value": value,
        "unit": metric_definition(key).unit,
        "period": period,
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "source": "growthepie",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": endpoint.rsplit("/", 1)[-1],
            "source_url": BASE_URL + endpoint,
            "attribution": ATTRIBUTION,
            "license": LICENSE,
            **dict(metadata or {}),
        },
    }


def _filtered_rows(payload: Any, as_of: str | None) -> list[tuple[str, Mapping[str, Any]]]:
    cutoff = parse_timestamp(as_of) if as_of else None
    values: list[tuple[str, Mapping[str, Any]]] = []
    for row in _rows(payload):
        timestamp = _row_date(row)
        if cutoff is not None and parse_timestamp(timestamp) > cutoff:
            continue
        values.append((timestamp, row))
    values.sort(key=lambda item: parse_timestamp(item[0]))
    return values


def parse_rent_payload(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str = RENT_PATH,
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if not requested or any(key not in _RENT_KEYS for key in requested):
        raise ProviderUnsupportedMetric("growthepie rent endpoint does not support the requested metrics")
    rows = _filtered_rows(payload, as_of)
    if not rows:
        raise ProviderInsufficientHistory("growthepie rent history is empty")
    numeric: list[tuple[str, float]] = []
    seen: dict[str, float] = {}
    for timestamp, row in rows:
        value = _value(row, ("rent_paid_usd", "rentPaidUsd", "rent_paid", "value", "total"))
        if value is None:
            raise ProviderDataError("growthepie rent row has no rent value")
        day = timestamp[:10]
        if day in seen and seen[day] != value:
            raise ProviderDataError(f"growthepie has conflicting duplicate rent row for {day}")
        seen[day] = value
    numeric = sorted((day, value) for day, value in seen.items())
    latest = parse_timestamp(numeric[-1][0] + "T00:00:00Z")
    result: list[Mapping[str, Any]] = []
    for key in requested:
        days = 30 if key.endswith("30d_usd") else 90
        start = latest - timedelta(days=days - 1)
        selected = [(day, value) for day, value in numeric if parse_timestamp(day + "T00:00:00Z") >= start]
        if not selected or parse_timestamp(selected[0][0] + "T00:00:00Z") > start + timedelta(days=7):
            raise ProviderInsufficientHistory(f"growthepie rent history is insufficient for {days}d")
        result.append(_observation(
            "ETH", key, sum(value for _, value in selected),
            observed_at=latest.isoformat().replace("+00:00", "Z"),
            fetched_at=fetched_at,
            period=f"{days}d",
            endpoint=endpoint,
            metadata={"window": f"{days}d", "rows_used": len(selected)},
        ))
    return tuple(result)


def _layer(row: Mapping[str, Any]) -> str:
    value = row.get("layer", row.get("da_layer", row.get("data_layer", row.get("chain", ""))))
    return str(value).strip().lower().replace(" ", "_")


def _is_ethereum(row: Mapping[str, Any]) -> bool:
    if row.get("is_ethereum") is True or row.get("isEthereum") is True:
        return True
    return _layer(row) in {"ethereum", "ethereum_l1", "ethereum_mainnet", "ethereum_blob"}


def parse_da_payload(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str = DA_TIMESERIES_PATH,
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if not requested or any(key not in _DA_KEYS for key in requested):
        raise ProviderUnsupportedMetric("growthepie DA endpoint does not support the requested metrics")
    rows = _filtered_rows(payload, as_of)
    if not rows:
        raise ProviderInsufficientHistory("growthepie DA history is empty")
    seen_layers: dict[tuple[str, str], Mapping[str, Any]] = {}
    for timestamp, row in rows:
        identity = (timestamp[:10], _layer(row))
        previous = seen_layers.get(identity)
        if previous is not None and previous != row:
            raise ProviderDataError(f"growthepie has conflicting duplicate DA row for {identity[0]}:{identity[1]}")
        seen_layers[identity] = row
    latest = rows[-1][0]
    start = parse_timestamp(latest[:10] + "T00:00:00Z") - timedelta(days=29)
    selected = [(timestamp, row) for timestamp, row in rows if parse_timestamp(timestamp) >= start]
    ethereum_rows = [(timestamp, row) for timestamp, row in selected if _is_ethereum(row)]
    if not ethereum_rows:
        # Explicit ethereum_* fields are already classified by the source.
        ethereum_rows = [(timestamp, row) for timestamp, row in selected if any(str(key).lower().startswith("ethereum_") for key in row)]
    if not ethereum_rows:
        raise ProviderUnsupportedMetric("growthepie DA rows have no explicit Ethereum layer")
    data_values = [
        _value(row, ("ethereum_blob_data_mb", "ethereum_data_mb", "data_mb"))
        for _, row in ethereum_rows
    ]
    byte_values = [_value(row, ("data_bytes", "blob_data_bytes", "ethereum_data_bytes")) for _, row in ethereum_rows]
    fee_values = [_value(row, ("ethereum_blob_fees_usd", "ethereum_fees_usd", "fees_usd", "fees")) for _, row in ethereum_rows]
    eth_bytes = sum(value for value in byte_values if value is not None)
    eth_mb = sum(value for value in data_values if value is not None) + eth_bytes / 1_000_000
    eth_fees = sum(value for value in fee_values if value is not None)
    has_data = any(value is not None for value in data_values + byte_values)
    has_fees = any(value is not None for value in fee_values)
    all_bytes = sum(
        value for _, row in selected
        for value in (_value(row, ("data_bytes", "blob_data_bytes", "ethereum_data_bytes")),)
        if value is not None
    )
    all_fees = sum(
        value for _, row in selected
        for value in (_value(row, ("fees_usd", "fees")),)
        if value is not None
    )
    result: list[Mapping[str, Any]] = []
    for key in requested:
        if key.endswith("blob_data_30d_mb"):
            value = eth_mb if has_data else None
        elif key.endswith("blob_fees_30d_usd"):
            value = eth_fees if has_fees else None
        elif key.endswith("share_of_tracked_da_bytes_30d"):
            value = eth_bytes / all_bytes if all_bytes > 0 else None
        else:
            value = eth_fees / all_fees if all_fees > 0 else None
        if value is None:
            raise ProviderUnsupportedMetric(f"growthepie cannot derive {key}")
        result.append(_observation(
            "ETH", key, value,
            observed_at=latest,
            fetched_at=fetched_at,
            period="30d",
            endpoint=endpoint,
            metadata={"window": "30d", "rows_used": len(ethereum_rows), "da_layer": "ethereum"},
        ))
    return tuple(result)


class GrowthepieProvider:
    name = "growthepie"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=tuple(sorted(_RENT_KEYS | _DA_KEYS)),
            historical_series=tuple(sorted(_RENT_KEYS | _DA_KEYS)),
            supports_batching=True,
            requires_api_key=False,
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.asset != "ETH":
            raise ProviderUnsupportedMetric("growthepie metrics require ETH scope")
        requested = tuple(dict.fromkeys(request.metric_keys))
        fetched_at = _now(self.clock)
        values: list[Mapping[str, Any]] = []
        diagnostics: dict[str, Mapping[str, Any]] = {}
        as_of = request.parameters.get("as_of")
        if any(key in _RENT_KEYS for key in requested):
            try:
                values.extend(parse_rent_payload(
                    self.client.get_json(BASE_URL + RENT_PATH),
                    tuple(key for key in requested if key in _RENT_KEYS),
                    fetched_at=fetched_at,
                    as_of=as_of,
                ))
            except Exception as exc:
                for key in requested:
                    if key in _RENT_KEYS:
                        diagnostics[key] = _diagnostic(exc)
        if any(key in _DA_KEYS for key in requested):
            try:
                values.extend(parse_da_payload(
                    self.client.get_json(BASE_URL + DA_TIMESERIES_PATH),
                    tuple(key for key in requested if key in _DA_KEYS),
                    fetched_at=fetched_at,
                    as_of=as_of,
                ))
            except Exception as exc:
                for key in requested:
                    if key in _DA_KEYS:
                        diagnostics[key] = _diagnostic(exc)
        return ProviderResponse(tuple(values), diagnostics=diagnostics, network_requests=int(any(key in _RENT_KEYS for key in requested)) + int(any(key in _DA_KEYS for key in requested)))


__all__ = [
    "ATTRIBUTION",
    "BASE_URL",
    "DA_OVERVIEW_PATH",
    "DA_TIMESERIES_PATH",
    "GrowthepieProvider",
    "LICENSE",
    "MASTER_PATH",
    "RENT_PATH",
    "parse_da_payload",
    "parse_rent_payload",
]
