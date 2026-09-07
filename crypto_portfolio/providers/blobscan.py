"""Public Blobscan time-series adapter for Ethereum blob demand."""

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


BASE_URL = "https://api.blobscan.com"
TIMESERIES_PATH = "/stats/timeseries"
OVERALL_PATH = "/stats/overall"
BLOCKS_PATH = "/blocks"

_KEYS = {
    "eth.blobs.count_1d",
    "eth.blobs.count_30d",
    "eth.blobs.data_bytes_30d",
    "eth.blobs.blob_transactions_30d",
    "eth.blobs.utilization_30d",
}


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")


def _number(value: Any, field: str, *, allow_fraction: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProviderDataError(f"{field} is not numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result < 0 or (not allow_fraction and result < 0):
        raise ProviderDataError(f"{field} is invalid")
    return result


def _timestamp(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = _number(value, "Blobscan timestamp")
        if number > 100_000_000_000:
            number /= 1000
        return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), "Blobscan timestamp")
    if not isinstance(value, str) or not value.strip():
        raise ProviderDataError("Blobscan row timestamp is missing")
    if isinstance(value, str) and len(value) == 10:
        value += "T00:00:00Z"
    return normalize_timestamp(value, "Blobscan row timestamp")


def _series(payload: Any) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...]]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
        raise ProviderResponseError("Blobscan response must contain a data object")
    data = payload["data"]
    timestamps = data.get("timestamps")
    series = data.get("series")
    if not isinstance(timestamps, list) or not isinstance(series, list):
        raise ProviderResponseError("Blobscan timeseries data requires timestamps and series")
    parsed_timestamps = tuple(_timestamp(item) for item in timestamps)
    parsed_series: list[Mapping[str, Any]] = []
    for index, item in enumerate(series):
        if not isinstance(item, Mapping) or set(item) != {"dimension", "startTimestampIdx", "metrics"}:
            raise ProviderResponseError(f"Blobscan series {index} does not match the official schema")
        if not isinstance(item["dimension"], Mapping) or not isinstance(item["metrics"], Mapping):
            raise ProviderDataError(f"Blobscan series {index} is malformed")
        start = item["startTimestampIdx"]
        if isinstance(start, bool) or not isinstance(start, (int, float)) or int(start) != start or start < 0:
            raise ProviderDataError("Blobscan startTimestampIdx is invalid")
        parsed_series.append({"dimension": dict(item["dimension"]), "startTimestampIdx": int(start), "metrics": dict(item["metrics"])})
    return parsed_timestamps, tuple(parsed_series)


def _field(row: Mapping[str, Any], names: Iterable[str], *, fraction: bool = False) -> float | None:
    for name in names:
        if name in row and row[name] is not None:
            return _number(row[name], f"Blobscan {name}", allow_fraction=fraction)
    return None


def parse_timeseries(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str = TIMESERIES_PATH,
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if not requested or any(key not in _KEYS for key in requested):
        raise ProviderUnsupportedMetric("Blobscan does not support the requested metrics")
    timestamps, series = _series(payload)
    cutoff = parse_timestamp(as_of) if as_of else None
    global_series = next((item for item in series if item["dimension"].get("type") == "global"), None)
    if global_series is None:
        raise ProviderUnsupportedMetric("Blobscan timeseries has no global dimension")
    metric_values: dict[str, list[tuple[str, float]]] = {}
    start = global_series["startTimestampIdx"]
    for metric, values in global_series["metrics"].items():
        if not isinstance(values, list):
            raise ProviderDataError(f"Blobscan metric {metric} is not an array")
        points = []
        for offset, value in enumerate(values):
            index = start + offset
            if index >= len(timestamps):
                raise ProviderResponseError(f"Blobscan metric {metric} exceeds timestamp array")
            points.append((timestamps[index], _number(value, f"Blobscan {metric}")))
        metric_values[metric] = points
    if not metric_values:
        raise ProviderInsufficientHistory("Blobscan time series has no metrics")
    latest_candidates = [timestamp for points in metric_values.values() for timestamp, _ in points if cutoff is None or parse_timestamp(timestamp) <= cutoff]
    if not latest_candidates:
        raise ProviderInsufficientHistory("Blobscan time series has no value at or before as_of")
    latest_timestamp = max(latest_candidates, key=parse_timestamp)
    start_time = parse_timestamp(latest_timestamp) - timedelta(days=29)

    def selected(metric: str) -> list[tuple[str, float]]:
        return [
            (timestamp, value)
            for timestamp, value in metric_values.get(metric, ())
            if parse_timestamp(timestamp) <= parse_timestamp(latest_timestamp)
            and parse_timestamp(timestamp) >= start_time
        ]

    count_values = selected("totalBlobs")
    bytes_values = selected("totalBlobUsageSize")
    max_bytes_values = selected("totalBlobSize")
    tx_values = selected("totalTransactions")
    if not count_values:
        raise ProviderInsufficientHistory("Blobscan totalBlobs series is empty")
    result: list[Mapping[str, Any]] = []
    for key in requested:
        if key.endswith("count_1d"):
            latest_count = [value for timestamp, value in count_values if timestamp == latest_timestamp]
            if not latest_count:
                raise ProviderInsufficientHistory("Blobscan totalBlobs has no latest value")
            value = latest_count[0]
            period = "1d"
        elif key.endswith("count_30d"):
            if not count_values:
                raise ProviderUnsupportedMetric("Blobscan cannot derive count_30d without totalBlobs")
            value = sum(value for _, value in count_values)
            period = "30d"
        elif key.endswith("data_bytes_30d"):
            if not bytes_values:
                raise ProviderUnsupportedMetric("Blobscan cannot derive data_bytes_30d without totalBlobUsageSize")
            value = sum(value for _, value in bytes_values)
            period = "30d"
        elif key.endswith("blob_transactions_30d"):
            if not tx_values:
                raise ProviderUnsupportedMetric("Blobscan cannot derive blob_transactions_30d without totalTransactions")
            value = sum(value for _, value in tx_values)
            period = "30d"
        else:
            if not bytes_values or not max_bytes_values:
                raise ProviderUnsupportedMetric("Blobscan cannot derive utilization_30d without blob usage and capacity series")
            denominator = sum(value for _, value in max_bytes_values)
            value = sum(value for _, value in bytes_values) / denominator if denominator > 0 else None
            period = "30d"
        if value is None:
            raise ProviderUnsupportedMetric(f"Blobscan cannot derive {key}")
        result.append({
            "asset": "ETH",
            "metric_key": key,
            "value": value,
            "unit": metric_definition(key).unit,
            "period": period,
            "observed_at": latest_timestamp,
            "fetched_at": fetched_at,
            "source": "blobscan",
            "confidence": "MEDIUM",
            "metadata": {
                "source_dataset": "stats/timeseries",
                "source_url": BASE_URL + endpoint,
                "window": period,
                "rows_used": len(count_values),
                "raw_metrics": ["totalBlobs", "totalBlobUsageSize", "totalBlobSize", "totalTransactions"],
                "utilization_methodology": "sum(totalBlobUsageSize) / sum(totalBlobSize)",
                "protocol_cross_check_policy": "sample Ethereum JSON-RPC blobGasUsed/excessBlobGas when available",
            },
        })
    return tuple(result)


class BlobscanProvider:
    name = "blobscan"

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
            raise ProviderUnsupportedMetric("Blobscan metrics require ETH scope")
        fetched_at = _now(self.clock)
        payload = self.client.get_json(
            BASE_URL + TIMESERIES_PATH,
            params={
                "timeFrame": "30d",
                "metrics": "totalBlobs,totalBlobSize,totalBlobUsageSize,totalTransactions",
                "sort": "asc",
            },
        )
        return ProviderResponse(observations=parse_timeseries(
            payload,
            request.metric_keys,
            fetched_at=fetched_at,
            as_of=request.parameters.get("as_of"),
        ))


__all__ = [
    "BASE_URL",
    "BLOCKS_PATH",
    "BlobscanProvider",
    "OVERALL_PATH",
    "TIMESERIES_PATH",
    "parse_timeseries",
]
