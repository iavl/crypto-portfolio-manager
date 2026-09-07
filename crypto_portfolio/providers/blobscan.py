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


def _rows(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, Mapping):
        raw = payload.get("data", payload.get("rows", payload.get("result")))
        if isinstance(raw, Mapping):
            raw = raw.get("data", raw.get("rows", raw.get("result")))
    else:
        raw = None
    if not isinstance(raw, list):
        raise ProviderResponseError("Blobscan response has no row list")
    if any(not isinstance(row, Mapping) for row in raw):
        raise ProviderDataError("Blobscan response contains a malformed row")
    return tuple(raw)


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
    cutoff = parse_timestamp(as_of) if as_of else None
    by_day: dict[str, Mapping[str, Any]] = {}
    for row in _rows(payload):
        timestamp = _timestamp(row.get("date", row.get("timestamp", row.get("time"))))
        if cutoff is not None and parse_timestamp(timestamp) > cutoff:
            continue
        day = timestamp[:10]
        previous = by_day.get(day)
        if previous is not None and previous != row:
            raise ProviderDataError(f"Blobscan has conflicting duplicate interval for {day}")
        by_day[day] = row
    if not by_day:
        raise ProviderInsufficientHistory("Blobscan time series is empty")
    ordered = sorted(by_day.items())
    latest_day = ordered[-1][0]
    latest_timestamp = _timestamp(ordered[-1][1].get("date", ordered[-1][1].get("timestamp", ordered[-1][1].get("time"))))
    start_day = (parse_timestamp(latest_day + "T00:00:00Z") - timedelta(days=29)).date().isoformat()
    selected = [(day, row) for day, row in ordered if day >= start_day]
    if not selected:
        raise ProviderInsufficientHistory("Blobscan time series has no selected interval")
    count_values = [(_field(row, ("blob_count", "blobCount", "count", "blobs"))) for _, row in selected]
    bytes_values = [(_field(row, ("data_bytes", "dataBytes", "blob_data_bytes"))) for _, row in selected]
    tx_values = [(_field(row, ("blob_transactions", "blobTransactions", "transactions", "transaction_count"))) for _, row in selected]
    utilization_values = [(_field(row, ("utilization", "blob_utilization"), fraction=True)) for _, row in selected]
    if any(value is not None and value > 1 for value in utilization_values):
        utilization_values = [value / 100 if value is not None else None for value in utilization_values]
    result: list[Mapping[str, Any]] = []
    for key in requested:
        if key.endswith("count_1d"):
            value = count_values[-1]
            period = "1d"
        elif key.endswith("count_30d"):
            values = [value for value in count_values if value is not None]
            value = sum(values) if values else None
            period = "30d"
        elif key.endswith("data_bytes_30d"):
            values = [value for value in bytes_values if value is not None]
            value = sum(values) if values else None
            period = "30d"
        elif key.endswith("blob_transactions_30d"):
            values = [value for value in tx_values if value is not None]
            value = sum(values) if values else None
            period = "30d"
        else:
            values = [value for value in utilization_values if value is not None]
            value = sum(values) / len(values) if values else None
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
                "rows_used": len(selected),
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
                "start": request.parameters.get("start"),
                "end": request.parameters.get("end"),
                "interval": "day",
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
