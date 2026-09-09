"""Public structured Ethereum burn-rate provider."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderCapabilities,
    ProviderDataError,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient


BASE_URL = "https://ultrasound.money"
BURN_RATES_PATH = "/api/v2/fees/burn-rates"
_METRIC = "eth.monetary.burn_30d_eth"
MINUTES_IN_30D = 30 * 24 * 60


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")


def _timestamp(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            raise ProviderDataError("Ultrasound timestamp is not finite")
        if number > 100_000_000_000:
            number /= 1000
        return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), "observed_at")
    return normalize_timestamp(value, "observed_at")


def _rate(payload: Any) -> tuple[float, str, int | None]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("d30"), Mapping):
        raise ProviderResponseError("Ultrasound response is missing the d30 object")
    daily = payload["d30"]
    rate = daily.get("rate")
    if not isinstance(rate, Mapping):
        raise ProviderResponseError("Ultrasound d30 response is missing rate")
    raw_value = rate.get("eth_per_minute")
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float, str)):
        raise ProviderDataError("Ultrasound d30 eth_per_minute is not numeric")
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError("Ultrasound d30 eth_per_minute is not numeric") from exc
    if not math.isfinite(value) or value < 0:
        raise ProviderDataError("Ultrasound d30 eth_per_minute is invalid")
    observed_at = _timestamp(daily.get("timestamp"))
    block_number = daily.get("block_number")
    if block_number is not None and (isinstance(block_number, bool) or not isinstance(block_number, int) or block_number < 0):
        raise ProviderDataError("Ultrasound d30 block_number is invalid")
    return value, observed_at, block_number


def parse_burn_rates(
    payload: Any,
    metric_keys: tuple[str, ...] | list[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str = BURN_RATES_PATH,
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if not requested:
        raise ProviderUnsupportedMetric("Ultrasound request contains no metrics")
    if any(key != _METRIC for key in requested):
        raise ProviderUnsupportedMetric("Ultrasound only supports the trailing 30d ETH burn metric")
    eth_per_minute, observed_at, block_number = _rate(payload)
    if as_of is not None and parse_timestamp(observed_at) > parse_timestamp(as_of):
        raise ProviderDataError("Ultrasound observation is after as_of")
    return ({
        "asset": "ETH",
        "metric_key": _METRIC,
        "value": eth_per_minute * MINUTES_IN_30D,
        "unit": metric_definition(_METRIC).unit,
        "period": "30d",
        "observed_at": observed_at,
        "fetched_at": normalize_timestamp(fetched_at, "fetched_at"),
        "source": "ultrasound_money",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "fees/burn-rates",
            "source_url": BASE_URL + endpoint,
            "methodology": "d30.eth_per_minute * 60 * 24 * 30",
            "rate_unit": "ETH_per_minute",
            "eth_per_minute": eth_per_minute,
            "block_number": block_number,
            "window": "trailing_30d_average_rate",
        },
    },)


class UltrasoundMoneyProvider:
    name = "ultrasound_money"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=(_METRIC,),
            historical_series=(_METRIC,),
            supports_batching=False,
            requires_api_key=False,
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.asset != "ETH":
            raise ProviderUnsupportedMetric("Ultrasound burn rates require ETH scope")
        payload = self.client.get_json(BASE_URL + BURN_RATES_PATH)
        return ProviderResponse(
            observations=parse_burn_rates(
                payload,
                request.metric_keys,
                fetched_at=_now(self.clock),
                as_of=request.parameters.get("as_of"),
            ),
            network_requests=1,
        )


__all__ = [
    "BASE_URL",
    "BURN_RATES_PATH",
    "MINUTES_IN_30D",
    "UltrasoundMoneyProvider",
    "parse_burn_rates",
]
