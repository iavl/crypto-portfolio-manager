"""Historical coverage and point-in-time eligibility audit."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ..models.backtest import BacktestSpec, HistoricalDataManifest, HistoricalSeriesManifest
from ..models.market import OHLCVSeries
from ..models.time import normalize_timestamp


def audit_ohlcv_series(
    series: OHLCVSeries | Mapping[str, Any],
    *,
    series_id: str | None = None,
    consumers: Iterable[str] = ("scoring", "regime", "execution", "valuation"),
    usd_conversion_series_id: str | None = None,
) -> HistoricalSeriesManifest:
    model = series if isinstance(series, OHLCVSeries) else OHLCVSeries.from_mapping(series)
    candles = model.completed_candles()
    cadence = model.cadence_metadata()
    quote = str(model.quote_currency or "UNKNOWN").upper()
    limitations: list[str] = []
    status = "AVAILABLE"
    point_in_time = "PUBLISHED_AT_TIME"
    if cadence["missing_interval_count"]:
        limitations.append("OHLCV_GAPS_PRESENT")
        status = "PARTIAL"
    if quote != "USD":
        if usd_conversion_series_id:
            limitations.append(f"USD_CONVERSION_REQUIRED:{usd_conversion_series_id}")
        else:
            limitations.append("UNVERIFIED_USD_CONVERSION")
            status = "BLOCKED"
            point_in_time = "HISTORICAL_APPROXIMATION"
    fetched_at = model.fetched_at or datetime.now(timezone.utc).isoformat()
    return HistoricalSeriesManifest(
        series_id=series_id or f"{model.source}:{model.symbol}:{model.timeframe}",
        symbol=model.symbol,
        metric="market.ohlcv",
        timeframe=model.timeframe,
        source=model.source,
        quote_currency=quote,
        observed_start_at=candles[0].timestamp if candles else None,
        observed_end_at=candles[-1].timestamp if candles else None,
        fetched_at=normalize_timestamp(fetched_at, "fetched_at"),
        available_at_field="candle_close_time",
        point_in_time_quality=point_in_time,
        content_sha256=model.ohlcv_hash,
        row_count=len(candles),
        missing_intervals=int(cadence["missing_interval_count"]),
        consumer=tuple(consumers),
        limitations=tuple(limitations),
        status=status,
    )


def unavailable_series(
    *,
    series_id: str,
    symbol: str,
    metric: str,
    source: str,
    consumers: Iterable[str],
    reason: str,
    fetched_at: str,
) -> HistoricalSeriesManifest:
    return HistoricalSeriesManifest(
        series_id=series_id, symbol=symbol, metric=metric, timeframe="EVENT",
        source=source, quote_currency="USD", observed_start_at=None, observed_end_at=None,
        fetched_at=fetched_at, available_at_field=None,
        point_in_time_quality="UNRECONSTRUCTABLE", content_sha256=None,
        row_count=0, missing_intervals=0, consumer=tuple(consumers),
        limitations=(reason,), status="UNAVAILABLE",
    )


def build_historical_manifest(
    spec: BacktestSpec,
    series: Iterable[HistoricalSeriesManifest | Mapping[str, Any]],
    *,
    created_at: str | None = None,
) -> HistoricalDataManifest:
    entries = tuple(item if isinstance(item, HistoricalSeriesManifest)
                    else HistoricalSeriesManifest.from_mapping(item) for item in series)
    blockers: list[str] = []
    required_assets = set().union(*(set(scope) for scope in spec.asset_scopes.values())) - {"USD"}
    for symbol in sorted(required_assets):
        daily = [item for item in entries if item.symbol == symbol and item.metric == "market.ohlcv"
                 and item.timeframe == "1D" and item.quote_currency == "USD"]
        hourly = [item for item in entries if item.symbol == symbol and item.metric == "market.ohlcv"
                  and item.timeframe == "1H" and item.quote_currency == "USD"]
        if not daily or all(item.status == "UNAVAILABLE" for item in daily):
            blockers.append(f"{symbol}:DAILY_OHLCV_REQUIRED")
        elif all(item.status != "AVAILABLE" for item in daily):
            blockers.append(f"{symbol}:DAILY_USD_VALUATION_BLOCKED")
        if not hourly or all(item.status == "UNAVAILABLE" for item in hourly):
            blockers.append(f"{symbol}:HOURLY_EXECUTION_OHLCV_REQUIRED")
        elif all(item.status != "AVAILABLE" for item in hourly):
            blockers.append(f"{symbol}:HOURLY_USD_VALUATION_BLOCKED")
    if any(item.point_in_time_quality != "PUBLISHED_AT_TIME" and item.status == "AVAILABLE" for item in entries):
        blockers.append("NON_POINT_IN_TIME_SERIES_PRESENT")
    now = created_at or datetime.now(timezone.utc).isoformat()
    return HistoricalDataManifest(
        manifest_id=f"{spec.run_id}:historical-data",
        created_at=normalize_timestamp(now, "created_at"), spec_hash=spec.content_hash,
        series=entries, strict_ready=not blockers, blockers=tuple(dict.fromkeys(blockers)),
    )


def coverage_matrix(manifest: HistoricalDataManifest) -> list[dict[str, Any]]:
    return [
        {
            "series_id": item.series_id, "symbol": item.symbol, "metric": item.metric,
            "timeframe": item.timeframe, "source": item.source,
            "range": [item.observed_start_at, item.observed_end_at],
            "rows": item.row_count, "missing_intervals": item.missing_intervals,
            "point_in_time_quality": item.point_in_time_quality,
            "status": item.status, "limitations": list(item.limitations),
        }
        for item in manifest.series
    ]


__all__ = ["audit_ohlcv_series", "build_historical_manifest", "coverage_matrix", "unavailable_series"]
