"""Alternative.me public Fear & Greed provider."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping

from ..models.time import normalize_timestamp, parse_timestamp
from .base import ProviderCapabilities, ProviderDataError, ProviderRequest, ProviderResponseError, ProviderUnsupportedMetric
from .http import HttpClient


BASE_URL = "https://api.alternative.me"


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        value = value.isoformat()
    return normalize_timestamp(value, "fetched_at")


def _epoch(value: Any, field: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not a valid epoch") from exc
    if not math.isfinite(number):
        raise ProviderDataError(f"{field} is not finite")
    try:
        return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), field)
    except (OverflowError, OSError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not a valid epoch") from exc


def parse_fear_greed(
    payload: Mapping[str, Any],
    *,
    fetched_at: str,
    as_of: str | None = None,
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list) or not payload["data"]:
        raise ProviderResponseError("Alternative.me Fear & Greed response is empty")
    cutoff = parse_timestamp(as_of) if as_of is not None else None
    selected = []
    for row in payload["data"]:
        if not isinstance(row, Mapping):
            continue
        observed = _epoch(row.get("timestamp"), "Fear & Greed timestamp")
        if cutoff is None or parse_timestamp(observed) <= cutoff:
            selected.append((row, observed))
    if not selected:
        raise ProviderDataError("Alternative.me has no value at or before as_of")
    row, observed = max(selected, key=lambda item: parse_timestamp(item[1]))
    try:
        value = float(row.get("value"))
    except (TypeError, ValueError) as exc:
        raise ProviderDataError("Fear & Greed value is not numeric") from exc
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ProviderDataError("Fear & Greed value is outside [0, 100]")
    return {
        "asset": "MARKET",
        "metric_key": "sentiment.market_fear_greed",
        "value": value,
        "unit": "score",
        "period": "1d",
        "observed_at": observed,
        "fetched_at": fetched_at,
        "source": "alternative.me",
        "confidence": "MEDIUM",
        "summary": row.get("value_classification"),
        "metadata": {
            "source_dataset": "fear-and-greed",
            "classification": row.get("value_classification"),
        },
    }


class AlternativeMeProvider:
    name = "alternative_me"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=("sentiment.market_fear_greed",),
            historical_series=("sentiment.market_fear_greed",),
            supports_batching=True,
            requires_api_key=False,
        )

    def collect(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        if "sentiment.market_fear_greed" not in request.metric_keys:
            raise ProviderUnsupportedMetric("Alternative.me only supports market Fear & Greed")
        payload = self.client.get_json(BASE_URL + "/fng/", params={"limit": 30, "format": "json"})
        value = parse_fear_greed(payload, fetched_at=_now(self.clock), as_of=request.parameters.get("as_of"))
        return [value]


__all__ = ["AlternativeMeProvider", "BASE_URL", "parse_fear_greed"]
