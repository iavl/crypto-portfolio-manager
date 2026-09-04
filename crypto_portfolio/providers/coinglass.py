"""Optional CoinGlass API V4 provider for ETF flows and liquidations."""

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
    ProviderError,
    ProviderRequest,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient, redact_secrets


BASE_URL = "https://open-api-v4.coinglass.com"
API_KEY_HEADER = "CG-API-KEY"
ETF_FLOW_PATH = "/api/etf/bitcoin/flow-history"
ETF_FLOW_PATHS = {"BTC": ETF_FLOW_PATH, "MARKET": ETF_FLOW_PATH, "ETH": "/api/etf/ethereum/flow-history"}
LIQUIDATION_PATH = "/api/futures/liquidation/aggregated-history"
_ETF_KEYS = ("flows.etf_net_1d", "flows.etf_net_7d", "flows.etf_net_30d")
_LIQUIDATION_KEYS = (
    "derivatives.long_liquidations_24h_usd",
    "derivatives.short_liquidations_24h_usd",
    "derivatives.total_liquidations_24h_usd",
    "derivatives.long_liquidations_7d_usd",
    "derivatives.short_liquidations_7d_usd",
)


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
        try:
            value = datetime.fromtimestamp(number, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError) as exc:
            raise ProviderDataError(f"{field} is invalid") from exc
    try:
        return normalize_timestamp(value, field)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is invalid") from exc


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise ProviderDataError(f"{field} is invalid")
    return result


def _signed_number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result):
        raise ProviderDataError(f"{field} is invalid")
    return result


def _rows(payload: Any, field: str) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        code = payload.get("code")
        if code is not None and str(code) not in {"0", "0.0"}:
            message = str(payload.get("msg", "")).lower()
            if any(word in message for word in ("plan", "tier", "permission", "subscription", "upgrade", "limit")):
                raise ProviderUnsupportedMetric("CoinGlass endpoint is unavailable for the configured plan")
            raise ProviderResponseError("CoinGlass endpoint returned an error")
        payload = payload.get("data")
    if not isinstance(payload, list):
        raise ProviderResponseError(f"CoinGlass {field} response has no data rows")
    rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise ProviderDataError(f"CoinGlass {field} row {index} is malformed")
        rows.append(row)
    return rows


def _cutoff(as_of: str | datetime | None) -> datetime | None:
    if as_of is None:
        return None
    return parse_timestamp(as_of.isoformat() if isinstance(as_of, datetime) else as_of)


def _usable_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    timestamp_field: str,
    as_of: str | datetime | None,
) -> list[tuple[str, Mapping[str, Any]]]:
    cutoff = _cutoff(as_of)
    selected: list[tuple[str, Mapping[str, Any]]] = []
    for row in rows:
        observed = _timestamp(row.get(timestamp_field), f"CoinGlass {timestamp_field}")
        if cutoff is None or parse_timestamp(observed) <= cutoff:
            selected.append((observed, row))
    selected.sort(key=lambda item: parse_timestamp(item[0]))
    if not selected:
        raise ProviderDataError("CoinGlass has no data at or before as_of")
    return selected


def _window(
    rows: list[tuple[str, Mapping[str, Any]]],
    days: int,
    *,
    field: str,
) -> tuple[list[tuple[str, Mapping[str, Any]]], str]:
    latest = parse_timestamp(rows[-1][0])
    start = latest - timedelta(days=days - 1)
    selected = [item for item in rows if parse_timestamp(item[0]) >= start]
    if days > 1 and (not selected or latest - parse_timestamp(selected[0][0]) < timedelta(days=days - 1)):
        raise ProviderUnsupportedMetric(f"CoinGlass {field} history is insufficient for {days}d aggregation")
    return selected, rows[-1][0]


def _observation(
    asset: str,
    key: str,
    value: float,
    *,
    observed_at: str,
    fetched_at: str,
    period: str,
    source_range: Mapping[str, str],
    rows_used: int,
    endpoint: str = ETF_FLOW_PATH,
) -> dict[str, Any]:
    definition = metric_definition(key)
    return {
        "asset": asset,
        "metric_key": key,
        "value": value,
        "unit": definition.unit,
        "period": period,
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "source": "coinglass",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": endpoint.rsplit("/", 1)[-1],
            "endpoint": endpoint,
            "aggregation_scope": "CoinGlass API V4 historical response",
            "source_range": dict(source_range),
            "rows_used": rows_used,
            "as_of_filter": observed_at,
        },
    }


def parse_etf_flow_history(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    asset: str = "MARKET",
    fetched_at: str,
    as_of: str | datetime | None = None,
    endpoint: str = ETF_FLOW_PATH,
) -> tuple[Mapping[str, Any], ...]:
    """Derive 1d/7d/30d ETF flows from one historical response."""
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if any(key not in _ETF_KEYS for key in requested):
        raise ProviderUnsupportedMetric("CoinGlass ETF flow metric is not supported")
    rows = _usable_rows(_rows(payload, "ETF flow"), timestamp_field="timestamp", as_of=as_of)
    parsed: list[tuple[str, float]] = []
    for observed, row in rows:
        raw = row.get("flow_usd", row.get("change_usd"))
        if raw is None:
            raise ProviderDataError("CoinGlass ETF flow row has no USD flow")
        parsed.append((observed, _signed_number(raw, "ETF flow")))
    result: list[Mapping[str, Any]] = []
    fetched = normalize_timestamp(fetched_at, "fetched_at")
    source_range = {"start": parsed[0][0], "end": parsed[-1][0]}
    for key, days, period in (
        ("flows.etf_net_1d", 1, "1d"),
        ("flows.etf_net_7d", 7, "7d"),
        ("flows.etf_net_30d", 30, "30d"),
    ):
        if key not in requested:
            continue
        try:
            window, observed = _window(
                [(timestamp, {"flow_usd": value}) for timestamp, value in parsed],
                days,
                field="ETF flow",
            )
        except ProviderUnsupportedMetric:
            continue
        result.append(_observation(
            asset.strip().upper(), key, sum(float(item[1]["flow_usd"]) for item in window),
            observed_at=observed, fetched_at=fetched, period=period, endpoint=endpoint,
            source_range=source_range, rows_used=len(window),
        ))
    return tuple(result)


def parse_liquidation_history(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    asset: str,
    fetched_at: str,
    as_of: str | datetime | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Derive current daily and optional 7d liquidation totals."""
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if any(key not in _LIQUIDATION_KEYS for key in requested):
        raise ProviderUnsupportedMetric("CoinGlass liquidation metric is not supported")
    rows = _usable_rows(_rows(payload, "liquidation"), timestamp_field="time", as_of=as_of)
    parsed: list[tuple[str, float, float]] = []
    for observed, row in rows:
        long_value = row.get("aggregated_long_liquidation_usd", row.get("long_liquidation_usd"))
        short_value = row.get("aggregated_short_liquidation_usd", row.get("short_liquidation_usd"))
        if long_value is None or short_value is None:
            raise ProviderDataError("CoinGlass liquidation row has incomplete long/short values")
        parsed.append((observed, _number(long_value, "long liquidation"), _number(short_value, "short liquidation")))
    fetched = normalize_timestamp(fetched_at, "fetched_at")
    latest = parsed[-1]
    source_range = {"start": parsed[0][0], "end": parsed[-1][0]}
    values = {
        "derivatives.long_liquidations_24h_usd": (latest[1], "24h"),
        "derivatives.short_liquidations_24h_usd": (latest[2], "24h"),
        "derivatives.total_liquidations_24h_usd": (latest[1] + latest[2], "24h"),
    }
    seven_day = None
    if any(key in requested for key in ("derivatives.long_liquidations_7d_usd", "derivatives.short_liquidations_7d_usd")):
        try:
            seven_day_rows, _ = _window(
                [(timestamp, {"long": long, "short": short}) for timestamp, long, short in parsed],
                7,
                field="liquidation",
            )
        except ProviderUnsupportedMetric:
            seven_day_rows = []
        if seven_day_rows:
            seven_day = (
                sum(float(item[1]["long"]) for item in seven_day_rows),
                sum(float(item[1]["short"]) for item in seven_day_rows),
            )
    if seven_day is not None:
        values.update({
            "derivatives.long_liquidations_7d_usd": (seven_day[0], "7d"),
            "derivatives.short_liquidations_7d_usd": (seven_day[1], "7d"),
        })
    result = []
    for key in requested:
        if key not in values:
            continue
        value, period = values[key]
        result.append(_observation(
            asset.strip().upper(), key, value,
            observed_at=latest[0], fetched_at=fetched, period=period, endpoint=LIQUIDATION_PATH,
            source_range=source_range, rows_used=1 if period == "24h" else 7,
        ))
    return tuple(result)


class CoinglassProvider:
    """Authenticated, read-only CoinGlass V4 provider."""

    name = "coinglass"

    def __init__(self, *, client: HttpClient | Any | None = None, api_key: str | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.api_key = api_key
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=(*_ETF_KEYS, *_LIQUIDATION_KEYS),
            historical_series=(*_ETF_KEYS, *_LIQUIDATION_KEYS),
            supports_batching=True,
            requires_api_key=True,
        )

    def _get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        if not self.api_key:
            raise ProviderAuthenticationError("CoinGlass API key is not configured")
        try:
            return self.client.get_json(
                BASE_URL + path,
                params=params,
                headers={API_KEY_HEADER: self.api_key},
            )
        except ProviderError as exc:
            message = redact_secrets(str(exc), (self.api_key,)) or exc.__class__.__name__
            if message == str(exc):
                raise
            raise exc.__class__(message) from None
        except Exception as exc:
            message = redact_secrets(str(exc), (self.api_key,)) or exc.__class__.__name__
            raise ProviderResponseError(message) from None

    def collect(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        if not isinstance(request, ProviderRequest):
            raise ValueError("request must be a ProviderRequest")
        fetched = _now(self.clock)
        if request.dataset == "etf" or any(key.startswith("flows.etf_") for key in request.metric_keys):
            asset = request.asset.strip().upper()
            try:
                endpoint = ETF_FLOW_PATHS[asset]
            except KeyError as exc:
                raise ProviderUnsupportedMetric(f"CoinGlass ETF flow history is not supported for {asset}") from exc
            return [dict(item) for item in parse_etf_flow_history(
                self._get(endpoint), request.metric_keys,
                asset=asset, fetched_at=fetched, as_of=request.parameters.get("as_of"), endpoint=endpoint,
            )]
        if request.dataset == "liquidations" or any("liquidations" in key for key in request.metric_keys):
            params: dict[str, Any] = {
                "exchange_list": "Binance,OKX,Bybit",
                "symbol": request.asset,
                "interval": "1d",
                "limit": 1000,
            }
            for source, target in (("start", "start_time"), ("end", "end_time")):
                if request.parameters.get(source) is not None:
                    value = parse_timestamp(request.parameters[source]).timestamp() * 1000
                    params[target] = int(value)
            return [dict(item) for item in parse_liquidation_history(
                self._get(LIQUIDATION_PATH, params=params), request.metric_keys,
                asset=request.asset, fetched_at=fetched, as_of=request.parameters.get("as_of"),
            )]
        raise ProviderUnsupportedMetric(f"CoinGlass does not support dataset {request.dataset}")


CoinGlassProvider = CoinglassProvider


__all__ = [
    "API_KEY_HEADER",
    "BASE_URL",
    "ETF_FLOW_PATH",
    "ETF_FLOW_PATHS",
    "LIQUIDATION_PATH",
    "CoinGlassProvider",
    "CoinglassProvider",
    "parse_etf_flow_history",
    "parse_liquidation_history",
]
