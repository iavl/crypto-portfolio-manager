"""Catalog-aware Coin Metrics Community and optional authenticated provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import ProviderAuthenticationError, ProviderCapabilities, ProviderDataError, ProviderRequest, ProviderResponseError, ProviderUnsupportedMetric
from .http import HttpClient


COMMUNITY_BASE_URL = "https://community-api.coinmetrics.io"
AUTHENTICATED_BASE_URL = "https://api.coinmetrics.io"
COINMETRICS_ASSETS = {
    "BTC": "btc",
    "ETH": "eth",
    "BNB": "bnb",
    "AAVE": "aave",
}
COINMETRICS_GENERIC_NETWORK_METRICS = {
    "onchain.active_addresses": "AdrActCnt",
    "onchain.transfer_volume": "TxTfrValAdjUSD",
    "onchain.blockspace_fees": "FeeTotUSD",
    "onchain.transaction_count": "TxCnt",
}
COINMETRICS_MARKET_VALUATION_METRICS = {
    "valuation.market_cap": "CapMrktEstUSD",
}
COINMETRICS_BTC_CYCLE_METRICS = {
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
COINMETRICS_TOKENOMICS_INPUTS = {
    "tokenomics.annualized_emissions": ("IssTotNtv", "SplyCur"),
    "tokenomics.supply_growth": ("SplyCur",),
}
COINMETRICS_EXCHANGE_FLOW_INPUTS = ("FlowInExUSD", "FlowOutExUSD")
COINMETRICS_METRIC_MAP = {
    **COINMETRICS_GENERIC_NETWORK_METRICS,
    **COINMETRICS_MARKET_VALUATION_METRICS,
    **COINMETRICS_BTC_CYCLE_METRICS,
}
COINMETRICS_SUPPORTED_METRICS = tuple(dict.fromkeys((
    *COINMETRICS_METRIC_MAP,
    *COINMETRICS_TOKENOMICS_INPUTS,
    "flows.exchange_netflow",
)))
SUPPLY_LOOKBACK_DAYS = 365
SUPPLY_LOOKBACK_TOLERANCE_DAYS = 7


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


def _catalog_rows(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("Coin Metrics catalog response must be an object")
    raw = payload.get("metrics", payload.get("data", payload.get("available_metrics")))
    if isinstance(raw, Mapping):
        raw = raw.values()
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        raise ProviderResponseError("Coin Metrics catalog has no metric list")
    return tuple(raw)


def _catalog_metric(item: Any) -> str | None:
    if isinstance(item, str):
        return item.strip().lower() or None
    if isinstance(item, Mapping):
        for field in ("metric", "metric_name", "name"):
            if item.get(field):
                return str(item[field]).strip().lower()
    return None


def catalog_metrics(payload: Mapping[str, Any]) -> frozenset[str]:
    result: set[str] = set()
    for item in _catalog_rows(payload):
        metric = _catalog_metric(item)
        if metric:
            result.add(metric)
    return frozenset(item for item in result if item)


available_metrics = catalog_metrics


def catalog_metrics_by_asset(payload: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """Parse the official catalog's per-asset frequency declarations."""
    by_asset: dict[str, set[str]] = {}
    global_metrics: set[str] = set()
    for item in _catalog_rows(payload):
        metric = _catalog_metric(item)
        if metric is None:
            continue
        if not isinstance(item, Mapping):
            global_metrics.add(metric)
            continue
        frequencies = item.get("frequencies")
        if isinstance(frequencies, (list, tuple)):
            found_asset = False
            for frequency in frequencies:
                if not isinstance(frequency, Mapping) or str(frequency.get("frequency", "")).lower() != "1d":
                    continue
                assets = frequency.get("assets", ())
                if isinstance(assets, str):
                    assets = (assets,)
                for asset in assets:
                    if isinstance(asset, str) and asset.strip():
                        by_asset.setdefault(asset.strip().lower(), set()).add(metric)
                        found_asset = True
            if found_asset:
                continue
            # A catalog entry with only non-daily frequencies is not a valid
            # source for this provider's 1D acquisition contract.
            continue
        assets = item.get("assets", item.get("asset"))
        if isinstance(assets, str):
            assets = (assets,)
        if isinstance(assets, (list, tuple, set, frozenset)):
            found_asset = False
            for asset in assets:
                if isinstance(asset, str) and asset.strip():
                    by_asset.setdefault(asset.strip().lower(), set()).add(metric)
                    found_asset = True
            if found_asset:
                continue
        global_metrics.add(metric)
    if global_metrics:
        by_asset["*"] = global_metrics
    return {asset: frozenset(metrics) for asset, metrics in by_asset.items()}


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
    metric_map: Mapping[str, str] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    rows = _rows(payload)
    cutoff = parse_timestamp(as_of) if as_of else None
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    mapping = metric_map or COINMETRICS_METRIC_MAP
    selected: dict[str, tuple[str, float]] = {}
    for row in rows:
        observed = _timestamp(row.get("time", row.get("timestamp")), "Coin Metrics observation time")
        if cutoff is not None and parse_timestamp(observed) > cutoff:
            continue
        for key in requested:
            metric = mapping.get(key)
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
                "coinmetrics_metric": mapping[key],
                **(
                    {"methodology": "coinmetrics_estimated_circulating_supply_market_cap"}
                    if key == "valuation.market_cap" else {}
                ),
            },
        })
    return tuple(result)


def _series_rows(
    payload: Mapping[str, Any],
    *,
    fields: Iterable[str],
    as_of: str | None,
) -> tuple[Mapping[str, Any], ...]:
    cutoff = parse_timestamp(as_of) if as_of else None
    result = []
    for row in _rows(payload):
        observed = _timestamp(row.get("time", row.get("timestamp")), "Coin Metrics observation time")
        if cutoff is not None and parse_timestamp(observed) > cutoff:
            continue
        values: dict[str, float] = {}
        for field in fields:
            if row.get(field) is None:
                continue
            try:
                value = float(row[field])
            except (TypeError, ValueError) as exc:
                raise ProviderDataError(f"Coin Metrics value for {field} is not numeric") from exc
            if not math.isfinite(value):
                raise ProviderDataError(f"Coin Metrics value for {field} is not finite")
            values[field] = value
        if values:
            result.append({"observed_at": observed, "values": values})
    return tuple(sorted(result, key=lambda item: parse_timestamp(item["observed_at"])))


def parse_tokenomics(
    payload: Mapping[str, Any],
    asset: str,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    source: str = "coinmetrics_community",
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    rows = _series_rows(payload, fields=("IssTotNtv", "SplyCur"), as_of=as_of)
    supply_rows = [row for row in rows if "SplyCur" in row["values"] and row["values"]["SplyCur"] > 0]
    if not supply_rows:
        raise ProviderUnsupportedMetric("Coin Metrics returned no current supply")
    current = supply_rows[-1]
    current_time = parse_timestamp(current["observed_at"])
    cutoff = current_time - timedelta(days=SUPPLY_LOOKBACK_DAYS)
    prior = [row for row in supply_rows if parse_timestamp(row["observed_at"]) <= cutoff]
    if not prior or (cutoff - parse_timestamp(prior[-1]["observed_at"])).total_seconds() > SUPPLY_LOOKBACK_TOLERANCE_DAYS * 86400:
        raise ProviderUnsupportedMetric("Coin Metrics supply history is insufficient for a 365d calculation")
    values: dict[str, tuple[float, str, str]] = {}
    if "tokenomics.supply_growth" in requested:
        values["tokenomics.supply_growth"] = (
            current["values"]["SplyCur"] / prior[-1]["values"]["SplyCur"] - 1,
            current["observed_at"],
            "current_supply / supply_at_or_before_365d - 1",
        )
    if "tokenomics.annualized_emissions" in requested:
        issuance_rows = [
            row for row in rows
            if "IssTotNtv" in row["values"] and parse_timestamp(row["observed_at"]) >= cutoff
        ]
        if (
            not issuance_rows
            or (parse_timestamp(issuance_rows[0]["observed_at"]) - cutoff).total_seconds()
            > SUPPLY_LOOKBACK_TOLERANCE_DAYS * 86400
        ):
            raise ProviderUnsupportedMetric("Coin Metrics issuance history is insufficient for a 365d calculation")
        values["tokenomics.annualized_emissions"] = (
            sum(row["values"]["IssTotNtv"] for row in issuance_rows) / current["values"]["SplyCur"],
            current["observed_at"],
            "gross_issuance_trailing_365d / current_supply",
        )
    return tuple({
        "asset": asset.strip().upper(),
        "metric_key": key,
        "value": value,
        "unit": metric_definition(key).unit,
        "period": "365d",
        "observed_at": observed,
        "fetched_at": fetched_at,
        "source": source,
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "timeseries/asset-metrics",
            "coinmetrics_metrics": list(COINMETRICS_TOKENOMICS_INPUTS[key]),
            "methodology": methodology,
            "window": "365d",
        },
    } for key, (value, observed, methodology) in values.items())


def parse_exchange_netflow(
    payload: Mapping[str, Any],
    asset: str,
    *,
    fetched_at: str,
    as_of: str | None = None,
    source: str = "coinmetrics_community",
) -> Mapping[str, Any]:
    rows = _series_rows(payload, fields=COINMETRICS_EXCHANGE_FLOW_INPUTS, as_of=as_of)
    if not rows or not all(field in rows[-1]["values"] for field in COINMETRICS_EXCHANGE_FLOW_INPUTS):
        raise ProviderUnsupportedMetric("Coin Metrics exchange-flow history has no complete current row")
    row = rows[-1]
    inflow = row["values"]["FlowInExUSD"]
    outflow = row["values"]["FlowOutExUSD"]
    return {
        "asset": asset.strip().upper(),
        "metric_key": "flows.exchange_netflow",
        "value": inflow - outflow,
        "unit": "USD",
        "period": "1d",
        "observed_at": row["observed_at"],
        "fetched_at": fetched_at,
        "source": source,
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "timeseries/asset-metrics",
            "coinmetrics_metrics": list(COINMETRICS_EXCHANGE_FLOW_INPUTS),
            "methodology": "exchange_inflow_usd_minus_exchange_outflow_usd",
        },
    }


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
        self._catalog_by_asset: dict[str, frozenset[str]] | None = None
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=COINMETRICS_SUPPORTED_METRICS,
            historical_series=tuple(dict.fromkeys((
                *COINMETRICS_METRIC_MAP,
                *COINMETRICS_TOKENOMICS_INPUTS,
                "flows.exchange_netflow",
            ))),
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
        if self._catalog_by_asset is None:
            payload = self.client.get_json(
                self.base_url + "/v4/catalog/asset-metrics",
                headers=self._headers(),
            )
            self._catalog = catalog_metrics(payload)
            self._catalog_by_asset = catalog_metrics_by_asset(payload)
        return self._catalog

    def available_metrics_for_asset(self, asset: str) -> frozenset[str]:
        self.catalog()
        normalized = asset.strip().lower()
        if self._catalog_by_asset and normalized in self._catalog_by_asset:
            return self._catalog_by_asset[normalized]
        return self._catalog_by_asset.get("*", frozenset()) if self._catalog_by_asset else frozenset()

    def collect(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        asset = request.asset.strip().upper()
        try:
            asset_id = COINMETRICS_ASSETS[asset]
        except KeyError as exc:
            raise ProviderUnsupportedMetric(f"Coin Metrics has no approved asset mapping for {asset}") from exc
        requested = tuple(dict.fromkeys(request.metric_keys))
        if any(key not in self.capabilities.metric_keys for key in requested):
            raise ProviderUnsupportedMetric("Coin Metrics does not support one or more requested metrics")
        available = self.available_metrics_for_asset(asset)
        required_inputs = {
            key: (
                (COINMETRICS_METRIC_MAP[key],)
                if key in COINMETRICS_METRIC_MAP else
                COINMETRICS_TOKENOMICS_INPUTS[key]
                if key in COINMETRICS_TOKENOMICS_INPUTS else
                COINMETRICS_EXCHANGE_FLOW_INPUTS
            )
            for key in requested
        }
        available_requested = tuple(
            key for key in requested
            if all(input_metric.lower() in available for input_metric in required_inputs[key])
        )
        if not available_requested:
            raise ProviderUnsupportedMetric("Coin Metrics catalog does not include requested metrics for this asset")
        input_metrics = tuple(dict.fromkeys(
            input_metric
            for key in available_requested
            for input_metric in required_inputs[key]
        ))
        params: dict[str, Any] = {
            "assets": asset_id,
            "metrics": ",".join(input_metrics),
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
        fetched_at = _now(self.clock)
        direct = tuple(key for key in available_requested if key in COINMETRICS_METRIC_MAP)
        result: list[Mapping[str, Any]] = []
        if direct:
            result.extend(parse_timeseries(
                payload,
                direct,
                asset=asset,
                source=self.name,
                fetched_at=fetched_at,
                as_of=request.parameters.get("as_of"),
            ))
        tokenomics = tuple(key for key in available_requested if key in COINMETRICS_TOKENOMICS_INPUTS)
        if tokenomics:
            result.extend(parse_tokenomics(
                payload,
                asset,
                tokenomics,
                source=self.name,
                fetched_at=fetched_at,
                as_of=request.parameters.get("as_of"),
            ))
        if "flows.exchange_netflow" in available_requested:
            result.append(parse_exchange_netflow(
                payload,
                asset,
                source=self.name,
                fetched_at=fetched_at,
                as_of=request.parameters.get("as_of"),
            ))
        return [dict(item) for item in result]


class CoinMetricsAuthenticatedProvider(CoinMetricsProvider):
    name = "coinmetrics_pro"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None, api_key: str | None = None) -> None:
        super().__init__(client=client, clock=clock, authenticated=True, api_key=api_key)


__all__ = [
    "AUTHENTICATED_BASE_URL",
    "COINMETRICS_ASSETS",
    "COINMETRICS_BTC_CYCLE_METRICS",
    "COINMETRICS_GENERIC_NETWORK_METRICS",
    "COINMETRICS_MARKET_VALUATION_METRICS",
    "COINMETRICS_METRIC_MAP",
    "COINMETRICS_SUPPORTED_METRICS",
    "COMMUNITY_BASE_URL",
    "CoinMetricsAuthenticatedProvider",
    "CoinMetricsProvider",
    "catalog_metrics",
    "catalog_metrics_by_asset",
    "available_metrics",
    "parse_exchange_netflow",
    "parse_tokenomics",
    "parse_timeseries",
]
