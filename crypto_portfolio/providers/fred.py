"""Read-only FRED macro series with deterministic local feature derivation."""

from __future__ import annotations

from dataclasses import dataclass
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
    ProviderResponse,
    ProviderResponseError,
)
from .http import HttpClient, redact_secrets


BASE_URL = "https://api.stlouisfed.org"
OBSERVATIONS_PATH = "/fred/series/observations"


@dataclass(frozen=True)
class SeriesSpec:
    metric_key: str
    series_id: str
    frequency: str
    freshness: str
    unit: str


FRED_SERIES: dict[str, SeriesSpec] = {
    "macro.dff": SeriesSpec("macro.dff", "DFF", "daily", "7d", "percent"),
    "macro.dfii10": SeriesSpec("macro.dfii10", "DFII10", "daily", "7d", "percent"),
    "macro.dtwexbgs": SeriesSpec("macro.dtwexbgs", "DTWEXBGS", "daily", "7d", "index"),
    "macro.walcl": SeriesSpec("macro.walcl", "WALCL", "weekly", "14d", "USD_millions"),
    "macro.m2sl": SeriesSpec("macro.m2sl", "M2SL", "monthly", "45d", "USD_billions"),
}
FRED_DERIVED_INPUTS: dict[str, tuple[str, ...]] = {
    "macro.fed_funds_change_90d": ("macro.dff",),
    "macro.real_yield_change_90d": ("macro.dfii10",),
    "macro.broad_dollar_change_90d": ("macro.dtwexbgs",),
    "macro.fed_balance_sheet_change_13w": ("macro.walcl",),
    "macro.m2_change_6m": ("macro.m2sl",),
    "macro.m2_change_12m": ("macro.m2sl",),
}
FRED_METRICS = tuple((*FRED_SERIES, *FRED_DERIVED_INPUTS))


@dataclass(frozen=True)
class FREDPoint:
    observed_at: str
    value: float
    realtime_start: str | None = None
    realtime_end: str | None = None


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderDataError(f"{field} is missing")
    text = value.strip()
    if len(text) == 10:
        text += "T00:00:00Z"
    return normalize_timestamp(text, field)


def _as_of(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "as_of")


def parse_fred_series(
    payload: Mapping[str, Any],
    series_id: str,
    *,
    as_of: str | datetime | None = None,
) -> tuple[FREDPoint, ...]:
    """Parse FRED observations; a dot is missing, never zero."""
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("FRED response must be an object")
    if payload.get("error_code") is not None:
        raise ProviderResponseError("FRED API returned an error")
    rows = payload.get("observations")
    if not isinstance(rows, list):
        raise ProviderResponseError("FRED response has no observations list")
    cutoff = parse_timestamp(_as_of(as_of)) if as_of is not None else None
    result: list[FREDPoint] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ProviderDataError(f"FRED observation {index} is malformed")
        observed_at = _timestamp(row.get("date"), f"FRED {series_id} observation date")
        if cutoff is not None and parse_timestamp(observed_at) > cutoff:
            continue
        raw_value = row.get("value")
        if raw_value is None or (isinstance(raw_value, str) and raw_value.strip() in {".", ""}):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ProviderDataError(f"FRED {series_id} observation is not numeric") from exc
        if not math.isfinite(value):
            raise ProviderDataError(f"FRED {series_id} observation is not finite")
        result.append(FREDPoint(
            observed_at,
            value,
            str(row["realtime_start"]).strip() if row.get("realtime_start") is not None else None,
            str(row["realtime_end"]).strip() if row.get("realtime_end") is not None else None,
        ))
    result.sort(key=lambda item: parse_timestamp(item.observed_at))
    if not result:
        raise ProviderDataError(f"FRED {series_id} has no numeric observation at or before as_of")
    return tuple(result)


def _latest_before(points: Iterable[FREDPoint], cutoff: datetime) -> FREDPoint | None:
    candidates = [point for point in points if parse_timestamp(point.observed_at) <= cutoff]
    return max(candidates, key=lambda item: parse_timestamp(item.observed_at)) if candidates else None


def _change(
    points: tuple[FREDPoint, ...],
    *,
    lookback_days: int,
    as_of: str | datetime | None,
    relative: bool,
) -> tuple[float, FREDPoint, FREDPoint]:
    cutoff = parse_timestamp(_as_of(as_of)) if as_of is not None else parse_timestamp(points[-1].observed_at)
    current = _latest_before(points, cutoff)
    if current is None:
        raise ProviderDataError("FRED current observation is unavailable")
    prior = _latest_before(points, parse_timestamp(current.observed_at) - timedelta(days=lookback_days))
    if prior is None:
        raise ProviderDataError(f"FRED history is insufficient for {lookback_days}d change")
    if relative:
        if prior.value == 0:
            raise ProviderDataError("FRED change denominator is zero")
        value = current.value / prior.value - 1.0
    else:
        value = current.value - prior.value
    return value, current, prior


def derive_macro_features(
    series: Mapping[str, tuple[FREDPoint, ...]],
    *,
    fetched_at: str,
    as_of: str | datetime | None = None,
    source: str = "fred",
    metric_keys: Iterable[str] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Derive only date-matched macro changes in Python."""
    calculations = {
        "macro.fed_funds_change_90d": ("macro.dff", 90, False),
        "macro.real_yield_change_90d": ("macro.dfii10", 90, False),
        "macro.broad_dollar_change_90d": ("macro.dtwexbgs", 90, True),
        "macro.fed_balance_sheet_change_13w": ("macro.walcl", 13 * 7, True),
        "macro.m2_change_6m": ("macro.m2sl", 182, True),
        "macro.m2_change_12m": ("macro.m2sl", 365, True),
    }
    result: list[Mapping[str, Any]] = []
    requested = set(metric_keys) if metric_keys is not None else set(calculations)
    for key, (input_key, days, relative) in calculations.items():
        if key not in requested:
            continue
        value, current, prior = _change(
            series[input_key], lookback_days=days, as_of=as_of, relative=relative,
        )
        result.append({
            "asset": "BTC",
            "metric_key": key,
            "value": value,
            "unit": metric_definition(key).unit,
            "period": f"{days}d",
            "observed_at": current.observed_at,
            "fetched_at": fetched_at,
            "source": "python-derived",
            "confidence": "HIGH",
            "metadata": {
                "source_provider": source,
                "source_series_id": FRED_SERIES[input_key].series_id,
                "source_frequency": FRED_SERIES[input_key].frequency,
                "source_mode": "DERIVED",
                "calculation": "difference" if not relative else "current / prior - 1",
                "lookback_days": days,
                "current_observed_at": current.observed_at,
                "prior_observed_at": prior.observed_at,
                "vintage_semantics": "LATEST_REVISION",
            },
        })
    return tuple(result)


class FREDProvider:
    name = "fred"

    def __init__(self, *, client: HttpClient | Any | None = None, api_key: str | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.api_key = api_key
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=FRED_METRICS,
            historical_series=FRED_METRICS,
            supports_batching=True,
            requires_api_key=True,
        )

    def _fetched_at(self) -> str:
        value = self.clock() if callable(self.clock) else datetime.now(timezone.utc)
        return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")

    def _fetch(self, spec: SeriesSpec, request: ProviderRequest) -> tuple[FREDPoint, ...]:
        if not self.api_key:
            raise ProviderAuthenticationError("FRED API key is not configured")
        params = {
            "series_id": spec.series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": str(request.parameters.get("start", ""))[:10] or None,
            "observation_end": str(request.parameters.get("as_of") or request.parameters.get("end") or "")[:10] or None,
        }
        try:
            payload = self.client.get_json(BASE_URL + OBSERVATIONS_PATH, params=params)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderResponseError(redact_secrets(str(exc), (self.api_key,))) from None
        return parse_fred_series(payload, spec.series_id, as_of=request.parameters.get("as_of"))

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if not isinstance(request, ProviderRequest):
            raise ValueError("request must be a ProviderRequest")
        if request.dataset != "macro" or request.asset != "BTC":
            raise ProviderDataError("FRED supports only BTC macro requests")
        requested = tuple(dict.fromkeys(request.metric_keys))
        raw_keys = set(requested)
        for key in requested:
            raw_keys.update(FRED_DERIVED_INPUTS.get(key, ()))
        series: dict[str, tuple[FREDPoint, ...]] = {}
        for key in raw_keys:
            spec = FRED_SERIES.get(key)
            if spec is None:
                continue
            series[key] = self._fetch(spec, request)
        fetched_at = self._fetched_at()
        observations: list[Mapping[str, Any]] = []
        for key in requested:
            spec = FRED_SERIES.get(key)
            if spec is None:
                continue
            point = series[key][-1]
            observations.append({
                "asset": "BTC",
                "metric_key": key,
                "value": point.value,
                "unit": metric_definition(key).unit,
                "period": "current",
                "observed_at": point.observed_at,
                "fetched_at": fetched_at,
                "source": self.name,
                "confidence": "HIGH",
                "metadata": {
                    "series_id": spec.series_id,
                    "frequency": spec.frequency,
                    "vintage_semantics": "LATEST_REVISION",
                    "realtime_start": point.realtime_start,
                    "realtime_end": point.realtime_end,
                },
            })
        derived = derive_macro_features(
            series,
            fetched_at=fetched_at,
            as_of=request.parameters.get("as_of"),
            source=self.name,
            metric_keys=tuple(key for key in requested if key in FRED_DERIVED_INPUTS),
        )
        observations.extend(item for item in derived if item["metric_key"] in requested)
        return ProviderResponse(observations=tuple(observations), network_requests=len(series))


__all__ = [
    "BASE_URL",
    "FRED_DERIVED_INPUTS",
    "FRED_METRICS",
    "FRED_SERIES",
    "FREDProvider",
    "FREDPoint",
    "OBSERVATIONS_PATH",
    "derive_macro_features",
    "parse_fred_series",
]
