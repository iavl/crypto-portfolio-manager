"""Small, no-key BGeometrics adapter for the latest BTC MVRV Z-score."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Mapping

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


BASE_URL = "https://bitcoin-data.com"
MVRV_ZSCORE_PATH = "/v1/mvrv-zscore/last"
METRIC = "btc_valuation.mvrv_zscore"
MAX_AGE_DAYS = 7


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
    if not math.isfinite(result):
        raise ProviderDataError(f"{field} is not finite")
    return result


def _observed_at(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderDataError("BGeometrics response is missing the source date")
    text = value.strip()
    if len(text) == 10:
        text += "T00:00:00Z"
    return normalize_timestamp(text, "BGeometrics source date")


def parse_mvrv_zscore(
    payload: Any,
    *,
    fetched_at: str,
    as_of: str | None = None,
    max_age_days: int = MAX_AGE_DAYS,
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("BGeometrics response must be an object")
    if "d" not in payload or "mvrvZscore" not in payload:
        raise ProviderResponseError("BGeometrics response is missing d or mvrvZscore")
    observed_at = _observed_at(payload["d"])
    value = _number(payload["mvrvZscore"], "BGeometrics mvrvZscore")
    unix_timestamp = payload.get("unixTs")
    if unix_timestamp is not None:
        unix_timestamp = _number(unix_timestamp, "BGeometrics unixTs")
    fetched = normalize_timestamp(fetched_at, "fetched_at")
    cutoff = parse_timestamp(as_of) if as_of is not None else parse_timestamp(fetched)
    observed = parse_timestamp(observed_at)
    if observed > cutoff:
        raise ProviderDataError("BGeometrics observation is after as_of")
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) or max_age_days < 1:
        raise ValueError("max_age_days must be a positive integer")
    if cutoff - observed > timedelta(days=max_age_days):
        raise ProviderInsufficientHistory("BGeometrics MVRV Z-score is stale")
    return {
        "asset": "BTC",
        "metric_key": METRIC,
        "value": value,
        "unit": metric_definition(METRIC).unit,
        "period": "current",
        "observed_at": observed_at,
        "fetched_at": fetched,
        "source": "bgeometrics",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "mvrv-zscore/last",
            "source_metric": "mvrvZscore",
            "source_url": BASE_URL + MVRV_ZSCORE_PATH,
            "methodology": "BGeometrics latest MVRV Z-score",
            "source_date_field": "d",
            "source_unix_timestamp": unix_timestamp,
            "cache_ttl_seconds": 86400,
        },
    }


class BGeometricsProvider:
    name = "bgeometrics"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=(METRIC,),
            historical_series=(METRIC,),
            supports_batching=False,
            requires_api_key=False,
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.asset != "BTC" or request.metric_keys != (METRIC,):
            raise ProviderUnsupportedMetric("BGeometrics only supports BTC MVRV Z-score")
        payload = self.client.get_json(BASE_URL + MVRV_ZSCORE_PATH)
        return ProviderResponse((parse_mvrv_zscore(
            payload,
            fetched_at=_now(self.clock),
            as_of=request.parameters.get("as_of"),
        ),), network_requests=1)


__all__ = [
    "BASE_URL",
    "BGeometricsProvider",
    "MAX_AGE_DAYS",
    "METRIC",
    "MVRV_ZSCORE_PATH",
    "parse_mvrv_zscore",
]
