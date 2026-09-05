"""Catalog-aware Coin Metrics Community and optional authenticated provider."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import ProviderAuthenticationError, ProviderCapabilities, ProviderDataError, ProviderRequest, ProviderResponseError, ProviderUnsupportedMetric
from .http import HttpClient


COMMUNITY_BASE_URL = "https://community-api.coinmetrics.io"
AUTHENTICATED_BASE_URL = "https://api.coinmetrics.io"
COINMETRICS_METRIC_MAP = {
    "onchain.btc.mvrv": "CapMVRVCur",
    "onchain.btc.mvrv_zscore": "CapMVRVZ",
    "onchain.btc.realized_price": "PriceRealizedUSD",
    "onchain.btc.market_to_realized_price": "CapMVRVCur",
    "onchain.btc.sopr": "Sopr",
    "onchain.btc.lth_supply_pct": "SplyLTHPct",
    "onchain.btc.lth_net_position_change": "SplyLTHNetChange",
    "onchain.btc.sth_realized_price": "PriceRealizedSthUSD",
    "onchain.btc.lth_realized_price": "PriceRealizedLthUSD",
    "onchain.btc.nupl": "CapNUPL",
}


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        value = value.isoformat()
    return normalize_timestamp(value, "fetched_at")


def _timestamp(value: Any, field: str) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            raise ProviderDataError(f"{field} is not finite")
        if number > 100_000_000_000:
            number /= 1000
        return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), field)
    return normalize_timestamp(value, field)


def catalog_metrics(payload: Mapping[str, Any]) -> frozenset[str]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("Coin Metrics catalog response must be an object")
    raw = payload.get("metrics", payload.get("data", payload.get("available_metrics")))
    if isinstance(raw, Mapping):
        raw = raw.values()
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        raise ProviderResponseError("Coin Metrics catalog has no metric list")
    result: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            result.add(item.strip().lower())
        elif isinstance(item, Mapping):
            for field in ("metric", "metric_name", "name"):
                if item.get(field):
                    result.add(str(item[field]).strip().lower())
                    break
    return frozenset(item for item in result if item)


available_metrics = catalog_metrics


def _rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("Coin Metrics response must be an object")
    rows = payload.get("data", payload.get("rows"))
    if not isinstance(rows, list):
        raise ProviderResponseError("Coin Metrics response has no data rows")
    return [item for item in rows if isinstance(item, Mapping)]


def parse_timeseries(
    payload: Mapping[str, Any],
    metric_keys: Iterable[str],
    *,
    asset: str = "BTC",
    source: str = "coinmetrics_community",
    fetched_at: str,
    as_of: str | None = None,
) -> tuple[Mapping[str, Any], ...]:
    rows = _rows(payload)
    cutoff = parse_timestamp(as_of) if as_of else None
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    selected: dict[str, tuple[str, float]] = {}
    for row in rows:
        observed = _timestamp(row.get("time", row.get("timestamp")), "Coin Metrics observation time")
        if cutoff is not None and parse_timestamp(observed) > cutoff:
            continue
        for key in requested:
            metric = COINMETRICS_METRIC_MAP.get(key)
            if metric is None or metric not in row:
                continue
            try:
                value = float(row[metric])
            except (TypeError, ValueError) as exc:
                raise ProviderDataError(f"Coin Metrics value for {metric} is not numeric") from exc
            if not math.isfinite(value):
                raise ProviderDataError(f"Coin Metrics value for {metric} is not finite")
            prior = selected.get(key)
            if prior is None or parse_timestamp(observed) > parse_timestamp(prior[0]):
                selected[key] = (observed, value)
    result = []
    for key in requested:
        if key not in selected:
            raise ProviderUnsupportedMetric(f"Coin Metrics returned no value for {key}")
        observed, value = selected[key]
        result.append({
            "asset": asset.strip().upper(),
            "metric_key": key,
            "value": value,
            "unit": metric_definition(key).unit,
            "period": "1d",
            "observed_at": observed,
            "fetched_at": fetched_at,
            "source": source,
            "confidence": "MEDIUM",
            "metadata": {
                "source_dataset": "timeseries/asset-metrics",
                "coinmetrics_metric": COINMETRICS_METRIC_MAP[key],
            },
        })
    return tuple(result)


class CoinMetricsProvider:
    name = "coinmetrics_community"

    def __init__(
        self,
        *,
        client: HttpClient | Any | None = None,
        clock: Any | None = None,
        authenticated: bool = False,
        api_key: str | None = None,
    ) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.authenticated = authenticated
        self.api_key = api_key
        self.name = "coinmetrics_pro" if authenticated else "coinmetrics_community"
        self.base_url = AUTHENTICATED_BASE_URL if authenticated else COMMUNITY_BASE_URL
        self._catalog: frozenset[str] | None = None
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=tuple(COINMETRICS_METRIC_MAP),
            historical_series=tuple(COINMETRICS_METRIC_MAP),
            supports_batching=True,
            requires_api_key=authenticated,
        )

    def _headers(self) -> Mapping[str, str]:
        if self.authenticated:
            if not self.api_key:
                raise ProviderAuthenticationError("Coin Metrics API key is not configured")
            return {"X-API-Key": self.api_key}
        return {}

    def catalog(self) -> frozenset[str]:
        if self._catalog is None:
            payload = self.client.get_json(
                self.base_url + "/v4/catalog/asset-metrics",
                headers=self._headers(),
            )
            self._catalog = catalog_metrics(payload)
        return self._catalog

    def collect(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        requested = tuple(key for key in request.metric_keys if key in COINMETRICS_METRIC_MAP)
        if len(requested) != len(request.metric_keys):
            raise ProviderUnsupportedMetric("Coin Metrics only supports mapped BTC metrics")
        available = self.catalog()
        available_requested = tuple(
            key for key in requested
            if COINMETRICS_METRIC_MAP[key].lower() in available
        )
        if not available_requested:
            raise ProviderUnsupportedMetric("Coin Metrics catalog does not include requested metrics")
        params: dict[str, Any] = {
            "assets": "btc",
            "metrics": ",".join(dict.fromkeys(COINMETRICS_METRIC_MAP[key] for key in available_requested)),
            "frequency": "1d",
            "page_size": 1000,
        }
        if request.parameters.get("start") is not None:
            params["start_time"] = request.parameters["start"]
        if request.parameters.get("end") is not None:
            params["end_time"] = request.parameters["end"]
        payload = self.client.get_json(
            self.base_url + "/v4/timeseries/asset-metrics",
            params=params,
            headers=self._headers(),
        )
        return [dict(item) for item in parse_timeseries(
            payload,
            available_requested,
            source=self.name,
            fetched_at=_now(self.clock),
            as_of=request.parameters.get("as_of"),
        )]


class CoinMetricsAuthenticatedProvider(CoinMetricsProvider):
    name = "coinmetrics_pro"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None, api_key: str | None = None) -> None:
        super().__init__(client=client, clock=clock, authenticated=True, api_key=api_key)


__all__ = [
    "AUTHENTICATED_BASE_URL",
    "COINMETRICS_METRIC_MAP",
    "COMMUNITY_BASE_URL",
    "CoinMetricsAuthenticatedProvider",
    "CoinMetricsProvider",
    "catalog_metrics",
    "available_metrics",
    "parse_timeseries",
]
