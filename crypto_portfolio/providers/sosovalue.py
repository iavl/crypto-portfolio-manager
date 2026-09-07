"""Optional SoSoValue ETF-flow provider using the documented v2 API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
import re
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from ..metrics_registry import metric_definition
from ..metrics_registry import normalize_metric_key
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderAuthenticationError,
    ProviderCapabilities,
    ProviderDataError,
    ProviderDiagnostic,
    ProviderInsufficientHistory,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient, redact_secrets


BASE_URL = "https://api.sosovalue.xyz"
API_KEY_HEADER = "x-soso-api-key"
ETF_HISTORICAL_INFLOW_PATH = "/openapi/v2/etf/historicalInflowChart"
ETF_FLOW_PATH = ETF_HISTORICAL_INFLOW_PATH
ETF_FLOW_PATHS = {"BTC": ETF_HISTORICAL_INFLOW_PATH, "ETH": ETF_HISTORICAL_INFLOW_PATH, "MARKET": ETF_HISTORICAL_INFLOW_PATH}
_ETF_KEYS = (
    "flows.etf_net_1d", "flows.etf_net_7d", "flows.etf_net_30d",
    "flows.btc_etf_net_1d", "flows.btc_etf_net_to_aum_7d", "flows.btc_etf_net_to_aum_30d",
    "flows.btc_etf_aum_usd",
    "flows.eth_etf_net_to_aum_7d", "flows.eth_etf_net_to_aum_30d",
    "flows.eth_etf_aum_usd",
)
_ETF_TYPES = {"BTC": "us-btc-spot", "ETH": "us-eth-spot"}
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_MARKET_TIMEZONE = ZoneInfo("America/New_York")


class _SoSoValueCapabilities(ProviderCapabilities):
    """Keep the historical public tuple while accepting new BTC metrics."""

    def supports(self, metric_key: str) -> bool:
        return super().supports(metric_key) or normalize_metric_key(metric_key) in _ETF_KEYS


@dataclass(frozen=True)
class _ETFPoint:
    source_date: date
    observed_at: str
    flow: float
    aum: float | None = None


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")


def _signed_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProviderDataError(f"{field} is not numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result):
        raise ProviderDataError(f"{field} is not finite")
    return result


def _observation_timestamp(source_date: date) -> str:
    local = datetime.combine(source_date, time(16), tzinfo=_MARKET_TIMEZONE)
    return local.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _response_rows(payload: Any) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("SoSoValue response is not an object")
    if "code" not in payload:
        raise ProviderResponseError("SoSoValue response is missing code")
    code = payload["code"]
    if isinstance(code, bool) or str(code).strip() not in {"0", "0.0"}:
        message = redact_secrets(str(payload.get("message", payload.get("msg", ""))))
        lowered = message.lower()
        code_text = str(code).strip()
        if code_text == "429" or code_text.startswith("429") or "rate limit" in lowered:
            raise ProviderRateLimited("SoSoValue rate limit rejected the request")
        if code_text in {"401", "403"} or any(word in lowered for word in ("api key", "apikey", "unauthorized", "authentication", "forbidden")):
            raise ProviderAuthenticationError("SoSoValue authentication was rejected")
        if any(word in lowered for word in ("plan", "tier", "permission", "subscription", "upgrade", "entitlement")):
            raise ProviderUnsupportedMetric("SoSoValue ETF endpoint is unavailable for the configured plan")
        raise ProviderResponseError(f"SoSoValue endpoint returned an error{': ' + message if message else ''}")
    data = payload.get("data")
    if isinstance(data, Mapping) and "list" in data:
        data = data["list"]
    if not isinstance(data, list):
        raise ProviderResponseError("SoSoValue response data is not a list")
    rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(data):
        if not isinstance(row, Mapping):
            raise ProviderDataError(f"SoSoValue ETF row {index} is malformed")
        rows.append(row)
    return rows


def _flow_value(row: Mapping[str, Any], index: int) -> float | None:
    fields = tuple(field for field in ("total_net_inflow", "totalNetInflow") if field in row)
    if not fields:
        raise ProviderDataError(f"SoSoValue ETF row {index} has no total net inflow")
    values = [row[field] for field in fields]
    if any(value is None for value in values):
        if len(values) > 1 and any(value is not None for value in values):
            raise ProviderDataError(f"SoSoValue ETF row {index} has conflicting inflow fields")
        return None
    parsed = [_signed_number(value, f"SoSoValue ETF row {index} total net inflow") for value in values]
    if len(parsed) == 2 and parsed[0] != parsed[1]:
        raise ProviderDataError(f"SoSoValue ETF row {index} has conflicting inflow fields")
    return parsed[0]


def _aum_value(row: Mapping[str, Any], index: int) -> float | None:
    fields = tuple(field for field in ("total_net_assets", "totalNetAssets") if field in row)
    if not fields:
        return None
    values = [row[field] for field in fields]
    if any(value is None for value in values):
        return None
    parsed = [_signed_number(value, f"SoSoValue ETF row {index} total net assets") for value in values]
    if len(parsed) == 2 and parsed[0] != parsed[1]:
        raise ProviderDataError(f"SoSoValue ETF row {index} has conflicting AUM fields")
    if parsed[0] <= 0:
        raise ProviderDataError(f"SoSoValue ETF row {index} total net assets must be positive")
    return parsed[0]


def _history_points(payload: Any, *, as_of: str | datetime | None = None) -> list[_ETFPoint]:
    cutoff = parse_timestamp(as_of.isoformat() if isinstance(as_of, datetime) else as_of) if as_of is not None else None
    by_date: dict[date, _ETFPoint] = {}
    for index, row in enumerate(_response_rows(payload)):
        raw_date = row.get("date")
        if not isinstance(raw_date, str) or not _DATE_RE.fullmatch(raw_date):
            raise ProviderDataError(f"SoSoValue ETF row {index} date is invalid")
        try:
            source_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ProviderDataError(f"SoSoValue ETF row {index} date is invalid") from exc
        flow = _flow_value(row, index)
        if flow is None:
            # The current official per-ETF history docs allow the newest T+1 row
            # to expose null settled flow fields; it is not a zero-flow session.
            continue
        point = _ETFPoint(source_date, _observation_timestamp(source_date), flow, _aum_value(row, index))
        previous = by_date.get(source_date)
        if previous is not None and (previous.flow != point.flow or previous.aum != point.aum):
            raise ProviderDataError(f"SoSoValue has conflicting duplicate row for {raw_date}")
        by_date[source_date] = point
    points = sorted(by_date.values(), key=lambda item: item.source_date)
    if cutoff is not None:
        points = [item for item in points if parse_timestamp(item.observed_at) <= cutoff]
    if not points:
        raise ProviderDataError("SoSoValue has no settled ETF data at or before as_of")
    return points


def _window(points: list[_ETFPoint], days: int) -> tuple[list[_ETFPoint], _ETFPoint]:
    latest = points[-1]
    start = latest.source_date - timedelta(days=days - 1)
    if days > 1 and points[0].source_date > start:
        raise ProviderInsufficientHistory(f"SoSoValue ETF history is insufficient for {days}d aggregation")
    selected = [item for item in points if start <= item.source_date <= latest.source_date]
    if not selected:
        raise ProviderInsufficientHistory(f"SoSoValue ETF history is insufficient for {days}d aggregation")
    return selected, latest


def _observation(
    asset: str,
    key: str,
    value: float,
    *,
    points: list[_ETFPoint],
    selected: list[_ETFPoint],
    fetched_at: str,
    period: str,
    endpoint: str,
    excluded_dates: Iterable[date] = (),
) -> dict[str, Any]:
    definition = metric_definition(key)
    latest = selected[-1]
    return {
        "asset": asset,
        "metric_key": key,
        "value": value,
        "unit": definition.unit,
        "period": period,
        "observed_at": latest.observed_at,
        "fetched_at": fetched_at,
        "source": "sosovalue",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "spot_etf_historical_inflow_chart",
            "api_version": "v2",
            "endpoint": endpoint,
            "etf_scope": asset,
            "window": period,
            "source_start_date": points[0].source_date.isoformat(),
            "source_end_date": points[-1].source_date.isoformat(),
            "history_rows": len(points),
            "window_start_date": selected[0].source_date.isoformat(),
            "window_end_date": selected[-1].source_date.isoformat(),
            "rows_used": len(selected),
            "aggregation": "sum_total_net_inflow",
            "market_timezone": "America/New_York",
            "settled_flow_rows_only": True,
            "excluded_incomplete_dates": sorted(item.isoformat() for item in excluded_dates),
        },
    }


def _parse_points(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    asset: str,
    fetched_at: str,
    as_of: str | datetime | None,
    endpoint: str,
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if not requested or any(key not in _ETF_KEYS for key in requested):
        raise ProviderUnsupportedMetric("SoSoValue ETF flow metric is not supported")
    if any(key.startswith("flows.btc_") for key in requested) and asset.strip().upper() != "BTC":
        raise ProviderUnsupportedMetric("BTC ETF normalized flow metrics require BTC scope")
    if any(key.startswith("flows.eth_") for key in requested) and asset.strip().upper() != "ETH":
        raise ProviderUnsupportedMetric("ETH ETF normalized flow metrics require ETH scope")
    points = _history_points(payload, as_of=as_of)
    result: list[Mapping[str, Any]] = []
    for key, days, period in (
        ("flows.etf_net_1d", 1, "1d"),
        ("flows.etf_net_7d", 7, "7d"),
        ("flows.etf_net_30d", 30, "30d"),
    ):
        if key not in requested:
            continue
        selected, _ = _window(points, days)
        result.append(_observation(
            asset,
            key,
            sum(item.flow for item in selected),
            points=points,
            selected=selected,
            fetched_at=fetched_at,
            period=period,
            endpoint=endpoint,
        ))
    if asset.strip().upper() == "BTC":
        latest = points[-1]
        if "flows.btc_etf_aum_usd" in requested:
            if latest.aum is None:
                raise ProviderDataError("SoSoValue BTC ETF latest settled row has no AUM")
            result.append(_observation(
                asset, "flows.btc_etf_aum_usd", latest.aum,
                points=points, selected=[latest], fetched_at=fetched_at,
                period="current", endpoint=endpoint,
            ))
        if "flows.btc_etf_net_1d" in requested:
            result.append(_observation(
                asset, "flows.btc_etf_net_1d", latest.flow,
                points=points, selected=[latest], fetched_at=fetched_at,
                period="1d", endpoint=endpoint,
            ))
        for key, days, period in (
            ("flows.btc_etf_net_to_aum_7d", 7, "7d"),
            ("flows.btc_etf_net_to_aum_30d", 30, "30d"),
        ):
            if key not in requested:
                continue
            selected, anchor = _window(points, days)
            if anchor.aum is None:
                raise ProviderDataError(
                    f"SoSoValue BTC ETF AUM is missing on the {period} flow anchor date"
                )
            normalized = _observation(
                asset,
                key,
                sum(item.flow for item in selected) / anchor.aum,
                points=points,
                selected=selected,
                fetched_at=fetched_at,
                period=period,
                endpoint=endpoint,
            )
            normalized["metadata"].update({
                "normalization": "sum_completed_net_inflow / aligned_ending_aum",
                "normalized_flow_ratio": normalized["value"],
                "aum_anchor_date": anchor.source_date.isoformat(),
                "aum_anchor_usd": anchor.aum,
            })
            result.append(normalized)
    if asset.strip().upper() == "ETH":
        latest = points[-1]
        if "flows.eth_etf_aum_usd" in requested:
            if latest.aum is None:
                raise ProviderDataError("SoSoValue ETH ETF latest settled row has no AUM")
            result.append(_observation(
                asset, "flows.eth_etf_aum_usd", latest.aum,
                points=points, selected=[latest], fetched_at=fetched_at,
                period="current", endpoint=endpoint,
            ))
        for key, days, period in (
            ("flows.eth_etf_net_to_aum_7d", 7, "7d"),
            ("flows.eth_etf_net_to_aum_30d", 30, "30d"),
        ):
            if key not in requested:
                continue
            selected, anchor = _window(points, days)
            if anchor.aum is None:
                raise ProviderDataError(
                    f"SoSoValue ETH ETF AUM is missing on the {period} flow anchor date"
                )
            normalized = _observation(
                asset,
                key,
                sum(item.flow for item in selected) / anchor.aum,
                points=points,
                selected=selected,
                fetched_at=fetched_at,
                period=period,
                endpoint=endpoint,
            )
            normalized["metadata"].update({
                "normalization": "sum_completed_net_inflow / aligned_ending_aum",
                "normalized_flow_ratio": normalized["value"],
                "aum_anchor_date": anchor.source_date.isoformat(),
                "aum_anchor_usd": anchor.aum,
            })
            result.append(normalized)
    return tuple(result)


def _partial_parse_error(error: ProviderError) -> Mapping[str, Any]:
    diagnostic = getattr(error, "diagnostic", None)
    if hasattr(diagnostic, "as_dict"):
        return dict(diagnostic.as_dict())
    return {
        "error_code": (
            "PROVIDER_INSUFFICIENT_HISTORY"
            if isinstance(error, ProviderInsufficientHistory)
            else "PROVIDER_UNSUPPORTED"
            if isinstance(error, ProviderUnsupportedMetric)
            else "PROVIDER_SCHEMA_ERROR"
        ),
        "detail": redact_secrets(str(error)) or error.__class__.__name__,
    }


def _parse_requested_points(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    asset: str,
    fetched_at: str,
    as_of: str | datetime | None,
    endpoint: str,
) -> tuple[tuple[Mapping[str, Any], ...], dict[str, Mapping[str, Any]]]:
    values: list[Mapping[str, Any]] = []
    diagnostics: dict[str, Mapping[str, Any]] = {}
    for key in tuple(dict.fromkeys(str(item).strip().lower() for item in metric_keys)):
        try:
            values.extend(parse_etf_flow_history(
                payload,
                (key,),
                asset=asset,
                fetched_at=fetched_at,
                as_of=as_of,
                endpoint=endpoint,
            ))
        except ProviderError as exc:
            diagnostics[key] = _partial_parse_error(exc)
    return tuple(values), diagnostics


def parse_etf_flow_history(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    asset: str = "BTC",
    fetched_at: str,
    as_of: str | datetime | None = None,
    endpoint: str = ETF_HISTORICAL_INFLOW_PATH,
) -> tuple[Mapping[str, Any], ...]:
    """Derive 1d/7d/30d ETF flows from one documented v2 history response."""
    scope = asset.strip().upper()
    if scope not in _ETF_TYPES:
        raise ProviderUnsupportedMetric("SoSoValue history parser requires BTC or ETH scope")
    return _parse_points(
        payload,
        metric_keys,
        asset=scope,
        fetched_at=normalize_timestamp(fetched_at, "fetched_at"),
        as_of=as_of,
        endpoint=endpoint,
    )


def _parse_market_history(
    btc_payload: Any,
    eth_payload: Any,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | datetime | None,
    endpoint: str,
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if not requested or any(key not in _ETF_KEYS for key in requested):
        raise ProviderUnsupportedMetric("SoSoValue ETF flow metric is not supported")
    btc = _history_points(btc_payload, as_of=as_of)
    eth = _history_points(eth_payload, as_of=as_of)
    btc_by_date = {item.source_date: item for item in btc}
    eth_by_date = {item.source_date: item for item in eth}
    common_dates = sorted(set(btc_by_date) & set(eth_by_date))
    if not common_dates:
        raise ProviderDataError("SoSoValue BTC and ETH ETF histories have no common settled date")
    points = [
        _ETFPoint(source_date, _observation_timestamp(source_date), btc_by_date[source_date].flow + eth_by_date[source_date].flow)
        for source_date in common_dates
    ]
    excluded_dates = (set(btc_by_date) ^ set(eth_by_date))
    result: list[Mapping[str, Any]] = []
    for key, days, period in (
        ("flows.etf_net_1d", 1, "1d"),
        ("flows.etf_net_7d", 7, "7d"),
        ("flows.etf_net_30d", 30, "30d"),
    ):
        if key not in requested:
            continue
        selected, _ = _window(points, days)
        result.append(_observation(
            "MARKET",
            key,
            sum(item.flow for item in selected),
            points=points,
            selected=selected,
            fetched_at=normalize_timestamp(fetched_at, "fetched_at"),
            period=period,
            endpoint=endpoint,
            excluded_dates=excluded_dates,
        ))
    for item in result:
        item["metadata"]["etf_scope"] = "BTC+ETH"
        item["metadata"]["source_assets"] = ["BTC", "ETH"]
    return tuple(result)


class SoSoValueProvider:
    """Authenticated, read-only SoSoValue ETF historical-inflow provider."""

    name = "sosovalue"

    def __init__(self, *, client: HttpClient | Any | None = None, api_key: str | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.api_key = api_key
        self.clock = clock
        self.capabilities = _SoSoValueCapabilities(
            provider=self.name,
            metric_keys=_ETF_KEYS[:3],
            historical_series=_ETF_KEYS[:3],
            supports_batching=True,
            requires_api_key=True,
        )

    def _post(self, *, etf_type: str) -> Any:
        if not self.api_key:
            raise ProviderAuthenticationError("SoSoValue API key is not configured")
        try:
            return self.client.post_json(
                BASE_URL + ETF_HISTORICAL_INFLOW_PATH,
                json_body={"type": etf_type},
                headers={API_KEY_HEADER: self.api_key},
                idempotent=True,
            )
        except ProviderError as exc:
            message = redact_secrets(str(exc), (self.api_key,)) or exc.__class__.__name__
            diagnostic = exc.diagnostic
            if isinstance(diagnostic, ProviderDiagnostic):
                safe_diagnostic = dict(redact_secrets(diagnostic.as_dict(), (self.api_key,)))
                diagnostic = diagnostic if safe_diagnostic == diagnostic.as_dict() else ProviderDiagnostic(**safe_diagnostic)
            elif isinstance(diagnostic, Mapping):
                diagnostic = redact_secrets(dict(diagnostic), (self.api_key,))
            if message == str(exc) and diagnostic == exc.diagnostic:
                raise
            raise exc.__class__(message, diagnostic=diagnostic) from None
        except Exception as exc:
            raise ProviderResponseError(
                redact_secrets(str(exc), (self.api_key,)) or exc.__class__.__name__
            ) from None

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if not isinstance(request, ProviderRequest):
            raise ValueError("request must be a ProviderRequest")
        if request.dataset != "etf" and not any(key.startswith("flows.etf_") for key in request.metric_keys):
            raise ProviderUnsupportedMetric(f"SoSoValue does not support dataset {request.dataset}")
        scope = request.asset.strip().upper()
        as_of = request.parameters.get("as_of")
        fetched_at = _now(self.clock)
        endpoint = BASE_URL + ETF_HISTORICAL_INFLOW_PATH
        if scope in _ETF_TYPES:
            values, diagnostics = _parse_requested_points(
                self._post(etf_type=_ETF_TYPES[scope]),
                request.metric_keys,
                asset=scope,
                fetched_at=fetched_at,
                as_of=as_of,
                endpoint=endpoint,
            )
            return ProviderResponse(observations=values, diagnostics=diagnostics, network_requests=1)
        if scope == "MARKET":
            btc_payload = self._post(etf_type=_ETF_TYPES["BTC"])
            eth_payload = self._post(etf_type=_ETF_TYPES["ETH"])
            values: list[Mapping[str, Any]] = []
            diagnostics: dict[str, Mapping[str, Any]] = {}
            for key in tuple(dict.fromkeys(str(item).strip().lower() for item in request.metric_keys)):
                try:
                    values.extend(_parse_market_history(
                        btc_payload,
                        eth_payload,
                        (key,),
                        fetched_at=fetched_at,
                        as_of=as_of,
                        endpoint=endpoint,
                    ))
                except ProviderError as exc:
                    diagnostics[key] = _partial_parse_error(exc)
            return ProviderResponse(observations=tuple(values), diagnostics=diagnostics, network_requests=2)
        raise ProviderUnsupportedMetric(f"SoSoValue ETF history is not supported for {scope}")


__all__ = [
    "API_KEY_HEADER",
    "BASE_URL",
    "ETF_HISTORICAL_INFLOW_PATH",
    "ETF_FLOW_PATH",
    "ETF_FLOW_PATHS",
    "SoSoValueProvider",
    "parse_etf_flow_history",
]
