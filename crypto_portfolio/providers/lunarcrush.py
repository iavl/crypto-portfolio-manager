"""LunarCrush v4 daily social-context provider."""

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
    ProviderInsufficientHistory,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient, redact_secrets


BASE_URL = "https://lunarcrush.com/api4"
SUPPORTED_ASSETS = frozenset({"BTC", "ETH", "SOL", "BNB", "LINK", "AAVE"})
SUPPORTED_METRICS = (
    "sentiment.social_bullish_share",
    "sentiment.social_mentions_change_7d",
    "sentiment.social_attention_percentile",
)
_SOURCE_FIELDS = ("sentiment", "posts_active")


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        value = value.isoformat()
    return normalize_timestamp(value, "fetched_at")


def _epoch_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, bool):
        raise ProviderDataError(f"{field} is not a valid UNIX timestamp")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not a valid UNIX timestamp") from exc
    if not math.isfinite(number) or number < 0:
        raise ProviderDataError(f"{field} is not a valid UNIX timestamp")
    if number > 100_000_000_000:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not a valid UNIX timestamp") from exc


def _number(value: Any, field: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ProviderDataError(f"{field} is not numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not math.isfinite(number):
        raise ProviderDataError(f"{field} is not finite")
    if minimum is not None and number < minimum or maximum is not None and number > maximum:
        raise ProviderDataError(f"{field} is outside the allowed range")
    return number


def _diagnostic(error: ProviderError) -> Mapping[str, Any]:
    diagnostic = getattr(error, "diagnostic", None)
    if hasattr(diagnostic, "as_dict"):
        result = dict(redact_secrets(diagnostic.as_dict()))
    elif isinstance(diagnostic, Mapping):
        result = dict(redact_secrets(dict(diagnostic)))
    else:
        code = (
            "PROVIDER_INSUFFICIENT_HISTORY" if isinstance(error, ProviderInsufficientHistory)
            else "PROVIDER_UNSUPPORTED" if isinstance(error, ProviderUnsupportedMetric)
            else "PROVIDER_SCHEMA_ERROR"
        )
        result = {"error_code": code, "detail": redact_secrets(str(error)) or error.__class__.__name__}
    raw_code = str(result.get("error_code", "")).strip().upper()
    detail = str(result.get("detail", "")).lower()
    status_code = result.get("status_code")
    if raw_code in {"HTTP_402", "PROVIDER_PLAN_RESTRICTED"} or status_code == 402 or any(
        marker in detail for marker in ("entitlement", "subscription", "upgrade", "plan")
    ) and status_code in {402, 403}:
        result["error_code"] = "ENTITLEMENT_REQUIRED"
        result["retryable"] = False
    elif raw_code in {"HTTP_429", "HTTP_403_RATE_LIMIT", "RATE_LIMITED"} or status_code == 429:
        result["error_code"] = "RATE_LIMITED"
        result["retryable"] = True
    return result


def _completed_rows(
    payload: Any,
    *,
    as_of: str | datetime | None,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise ProviderResponseError("LunarCrush time-series response has no data list")
    cutoff = parse_timestamp(
        as_of.isoformat() if isinstance(as_of, datetime) else as_of
    ) if as_of is not None else datetime.now(timezone.utc)
    by_day: dict[str, dict[str, Any]] = {}
    for raw in payload["data"]:
        if not isinstance(raw, Mapping) or "time" not in raw:
            raise ProviderResponseError("LunarCrush time-series row is malformed")
        observed = _epoch_datetime(raw["time"], "LunarCrush time")
        day = observed.replace(hour=0, minute=0, second=0, microsecond=0)
        if day + timedelta(days=1) > cutoff:
            continue
        normalized: dict[str, Any] = {
            "day": day.date(),
            "observed_at": normalize_timestamp(day.isoformat(), "observed_at"),
        }
        if "sentiment" in raw:
            normalized["sentiment"] = _number(raw["sentiment"], "sentiment", minimum=0, maximum=100)
        if "posts_active" in raw:
            normalized["posts_active"] = _number(raw["posts_active"], "posts_active", minimum=0)
        prior = by_day.get(normalized["observed_at"])
        if prior is not None:
            if any(prior.get(field) != normalized.get(field) for field in _SOURCE_FIELDS):
                raise ProviderDataError("LunarCrush has conflicting duplicate daily rows")
            continue
        by_day[normalized["observed_at"]] = normalized
    if not by_day:
        raise ProviderInsufficientHistory("LunarCrush has no completed data at or before as_of")
    return tuple(sorted(by_day.values(), key=lambda item: item["day"]))


def _required(row: Mapping[str, Any], field: str) -> float:
    value = row.get(field)
    if value is None:
        raise ProviderInsufficientHistory(f"LunarCrush daily history has no {field} value")
    return float(value)


def _midrank(values: list[float], current: float) -> float:
    if len(values) < 90:
        raise ProviderInsufficientHistory("LunarCrush percentile requires 90 completed daily observations")
    result = (sum(value < current for value in values) + 0.5 * sum(value == current for value in values)) / len(values)
    if not 0 <= result <= 1:
        raise ProviderDataError("LunarCrush percentile is outside [0, 1]")
    return result


def _observation(
    asset: str,
    key: str,
    value: float,
    *,
    row: Mapping[str, Any],
    fetched_at: str,
    period: str,
    metadata: Mapping[str, Any],
) -> Mapping[str, Any]:
    return {
        "asset": asset,
        "metric_key": key,
        "value": value,
        "unit": metric_definition(key).unit,
        "period": period,
        "observed_at": row["observed_at"],
        "fetched_at": fetched_at,
        "source": "lunarcrush",
        "confidence": "MEDIUM",
        "metadata": dict(metadata),
    }


def parse_timeseries(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    asset: str = "BTC",
    fetched_at: str,
    as_of: str | datetime | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Normalize completed daily LunarCrush rows into portfolio metrics."""
    scope = asset.strip().upper()
    if scope not in SUPPORTED_ASSETS:
        raise ProviderUnsupportedMetric(f"LunarCrush has no approved asset mapping for {scope}")
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if not requested or any(key not in SUPPORTED_METRICS for key in requested):
        raise ProviderUnsupportedMetric("LunarCrush social metric is not supported")
    rows = _completed_rows(payload, as_of=as_of)
    latest = rows[-1]
    by_day = {row["day"]: row for row in rows}
    result: list[Mapping[str, Any]] = []
    for key in requested:
        if key == "sentiment.social_bullish_share":
            raw = _required(latest, "sentiment")
            result.append(_observation(
                scope, key, raw / 100, row=latest, fetched_at=fetched_at, period="1d",
                metadata={
                    "source_metric": "sentiment",
                    "raw_unit": "percent",
                    "methodology": "lunarcrush_daily_sentiment_percent_to_fraction",
                    "bucket": "day",
                },
            ))
        elif key == "sentiment.social_mentions_change_7d":
            prior = by_day.get(latest["day"] - timedelta(days=7))
            if prior is None:
                raise ProviderInsufficientHistory("LunarCrush has no exactly aligned 7D prior daily row")
            denominator = _required(prior, "posts_active")
            if denominator <= 0:
                raise ProviderDataError("LunarCrush 7D posts_active denominator must be positive")
            result.append(_observation(
                scope, key, _required(latest, "posts_active") / denominator - 1,
                row=latest, fetched_at=fetched_at, period="7d",
                metadata={
                    "source_metric": "posts_active",
                    "methodology": "lunarcrush_daily_active_posts_aligned_7d_change",
                    "bucket": "day",
                    "prior_observed_at": prior["observed_at"],
                },
            ))
        else:
            field = "posts_active"
            window = rows[-90:]
            values = [_required(row, field) for row in window]
            current = _required(latest, field)
            result.append(_observation(
                scope,
                key,
                _midrank(values, current),
                row=latest,
                fetched_at=fetched_at,
                period="90d",
                metadata={
                    "source_metric": field,
                    "window": "90d",
                    "rows_used": len(window),
                    "methodology": "empirical_midrank_percentile",
                    "bucket": "day",
                },
            ))
    return tuple(result)


class LunarCrushProvider:
    name = "lunarcrush"

    def __init__(
        self,
        *,
        client: HttpClient | Any | None = None,
        api_key: str | None = None,
        clock: Any | None = None,
    ) -> None:
        self.client = client or HttpClient()
        self.api_key = api_key
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=SUPPORTED_METRICS,
            historical_series=SUPPORTED_METRICS,
            supports_batching=True,
            requires_api_key=True,
        )

    def _headers(self) -> Mapping[str, str]:
        if not self.api_key:
            raise ProviderAuthenticationError("LunarCrush API key is not configured")
        return {"Authorization": f"Bearer {self.api_key}"}

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        scope = request.asset.strip().upper()
        if scope not in SUPPORTED_ASSETS:
            raise ProviderUnsupportedMetric(f"LunarCrush has no approved asset mapping for {scope}")
        requested = tuple(dict.fromkeys(request.metric_keys))
        if not requested or any(key not in self.capabilities.metric_keys for key in requested):
            raise ProviderUnsupportedMetric("LunarCrush social metric is not supported")
        headers = self._headers()
        end_value = request.parameters.get("end") or request.parameters.get("as_of") or _now(self.clock)
        end = parse_timestamp(end_value)
        start_value = request.parameters.get("start")
        start = parse_timestamp(start_value) if start_value is not None else end - timedelta(days=2)
        endpoint = f"{BASE_URL}/public/coins/{scope.lower()}/time-series/v2"
        try:
            payload = self.client.get_json(
                endpoint,
                params={
                    "bucket": "day",
                    "start": int(start.timestamp()),
                    "end": int(end.timestamp()),
                },
                headers=headers,
            )
        except ProviderError as exc:
            diagnostic = _diagnostic(exc)
            raise exc.__class__(str(exc), diagnostic=diagnostic) from exc
        fetched_at = _now(self.clock)
        observations: list[Mapping[str, Any]] = []
        diagnostics: dict[str, Mapping[str, Any]] = {}
        for key in requested:
            try:
                observations.extend(parse_timeseries(
                    payload,
                    (key,),
                    asset=scope,
                    fetched_at=fetched_at,
                    as_of=end_value,
                ))
            except (ProviderInsufficientHistory, ProviderResponseError, ProviderDataError, ProviderUnsupportedMetric) as exc:
                diagnostics[key] = _diagnostic(exc)
        return ProviderResponse(
            observations=tuple(observations),
            payload=payload,
            diagnostics=diagnostics,
            network_requests=1,
        )


__all__ = [
    "BASE_URL",
    "SUPPORTED_ASSETS",
    "SUPPORTED_METRICS",
    "LunarCrushProvider",
    "parse_timeseries",
]
