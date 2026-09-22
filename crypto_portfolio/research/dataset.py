"""Build and load immutable historical research datasets outside Git."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models.backtest import BacktestSpec, HistoricalDataManifest, HistoricalSeriesManifest
from ..models.market import Candle, OHLCVSeries
from ..models.time import normalize_timestamp
from ..providers.binance import BinanceProvider
from ..providers.coinbase import CoinbaseProvider
from ..state.snapshots import runtime_data_dir
from .data_audit import audit_ohlcv_series, build_historical_manifest, unavailable_series


def default_backtest_root(run_id: str) -> Path:
    return runtime_data_dir() / "research" / "backtests" / run_id


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _series_filename(series_id: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "-" for character in series_id) + ".json"


def _load_cached_series(root: Path, series_id: str) -> OHLCVSeries | None:
    path = root / "series" / _series_filename(series_id)
    if not path.is_file():
        return None
    try:
        return OHLCVSeries.from_mapping(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def convert_usdt_series_to_usd(asset: OHLCVSeries, usdt_usd: OHLCVSeries) -> OHLCVSeries:
    if asset.timeframe != usdt_usd.timeframe or asset.quote_currency != "USDT" \
            or usdt_usd.symbol != "USDT" or usdt_usd.quote_currency != "USD":
        raise ValueError("USD conversion requires matching USDT-quoted asset and USDT/USD series")
    fx = {candle.timestamp: candle for candle in usdt_usd.completed_candles()}
    converted = []
    missing = []
    for candle in asset.completed_candles():
        rate = fx.get(candle.timestamp)
        if rate is None:
            missing.append(candle.timestamp)
            continue
        converted.append(Candle(
            candle.timestamp,
            open=candle.open * rate.open,
            high=candle.high * rate.high,
            low=candle.low * rate.low,
            close=candle.close * rate.close,
            volume=candle.volume,
            completed=True,
        ))
    if missing:
        raise ValueError(f"USDT/USD conversion is missing {len(missing)} asset intervals")
    return OHLCVSeries(
        symbol=asset.symbol, timeframe=asset.timeframe, candles=tuple(converted),
        source=f"{asset.source}+{usdt_usd.source}_fx", fetched_at=asset.fetched_at,
        venue=asset.venue, market=asset.market, quote_currency="USD",
    )


def _unavailable_ohlcv(
    *, symbol: str, timeframe: str, fetched_at: str, reason: str,
    series_id: str | None = None, source: str = "binance", quote_currency: str = "USDT",
) -> HistoricalSeriesManifest:
    return HistoricalSeriesManifest(
        series_id=series_id or f"binance:{symbol}:{timeframe}", symbol=symbol,
        metric="market.ohlcv", timeframe=timeframe, source=source,
        quote_currency=quote_currency, observed_start_at=None, observed_end_at=None,
        fetched_at=fetched_at, available_at_field="candle_close_time",
        point_in_time_quality="UNRECONSTRUCTABLE", content_sha256=None,
        row_count=0, missing_intervals=0,
        consumer=("scoring", "regime", "execution", "valuation"),
        limitations=(reason,), status="UNAVAILABLE",
    )


def build_binance_dataset(
    spec: BacktestSpec,
    *,
    output_root: str | Path | None = None,
    provider: BinanceProvider | None = None,
    coinbase_provider: CoinbaseProvider | None = None,
) -> dict[str, Any]:
    """Fetch free public OHLCV and write a content-addressed research bundle.

    Binance rows are USDT quoted.  Without an independently audited USDT/USD
    conversion series the manifest deliberately blocks a formal USD result;
    the same data may still support a clearly labelled USDT approximation.
    """
    root = Path(output_root) if output_root is not None else default_backtest_root(spec.run_id)
    source = provider or BinanceProvider()
    coinbase = coinbase_provider or CoinbaseProvider()
    fetched_at = normalize_timestamp(datetime.now(timezone.utc).isoformat(), "fetched_at")
    symbols = sorted(set().union(*(set(scope) for scope in spec.asset_scopes.values())) - {"USD"})
    entries: list[HistoricalSeriesManifest] = []
    acquired: dict[str, OHLCVSeries] = {}
    failures: list[dict[str, str]] = []
    fx_by_timeframe: dict[str, OHLCVSeries] = {}
    for timeframe, start_at in (("1D", spec.warmup_start_at), ("1H", spec.start_at)):
        series_id = f"coinbase:USDT-USD:{timeframe}"
        try:
            fx = _load_cached_series(root, series_id) or coinbase.candles(
                "USDT", timeframe=timeframe, start=start_at, end=spec.end_at,
            )
            fx_by_timeframe[timeframe] = fx
            acquired[series_id] = fx
            entries.append(audit_ohlcv_series(fx, series_id=series_id, consumers=("valuation", "quote_conversion")))
            _write_json(root / "series" / _series_filename(series_id), fx.as_dict())
        except Exception as exc:
            reason = f"{exc.__class__.__name__}: {exc}"
            failures.append({"series_id": series_id, "reason": reason})
            entries.append(_unavailable_ohlcv(
                symbol="USDT", timeframe=timeframe, fetched_at=fetched_at, reason=reason,
            ))
    for symbol in symbols:
        for timeframe, start_at in (("1D", spec.warmup_start_at), ("1H", spec.start_at)):
            series_id = f"binance:{symbol}:{timeframe}"
            try:
                series = _load_cached_series(root, series_id) or source.candles(
                    symbol, timeframe=timeframe, start=start_at, end=spec.end_at,
                )
            except Exception as exc:
                reason = f"{exc.__class__.__name__}: {exc}"
                failures.append({"series_id": series_id, "reason": reason})
                entries.append(_unavailable_ohlcv(
                    symbol=symbol, timeframe=timeframe, fetched_at=fetched_at, reason=reason,
                ))
                if fx_by_timeframe.get(timeframe) is not None:
                    entries.append(_unavailable_ohlcv(
                        symbol=symbol, timeframe=timeframe, fetched_at=fetched_at, reason=reason,
                        series_id=f"normalized:{symbol}-USD:{timeframe}", source="binance+coinbase_fx",
                        quote_currency="USD",
                    ))
                continue
            acquired[series_id] = series
            entries.append(audit_ohlcv_series(series, series_id=series_id))
            _write_json(root / "series" / _series_filename(series_id), series.as_dict())
            fx = fx_by_timeframe.get(timeframe)
            if fx is None:
                continue
            converted_id = f"normalized:{symbol}-USD:{timeframe}"
            try:
                converted = convert_usdt_series_to_usd(series, fx)
                acquired[converted_id] = converted
                entries.append(audit_ohlcv_series(converted, series_id=converted_id))
                _write_json(root / "series" / _series_filename(converted_id), converted.as_dict())
            except Exception as exc:
                reason = f"{exc.__class__.__name__}: {exc}"
                failures.append({"series_id": converted_id, "reason": reason})
                entries.append(_unavailable_ohlcv(
                    symbol=symbol, timeframe=timeframe, fetched_at=fetched_at, reason=reason,
                    series_id=converted_id, source="binance+coinbase_fx", quote_currency="USD",
                ))
    for symbol in symbols:
        entries.extend((
            unavailable_series(
                series_id=f"events:{symbol}", symbol=symbol, metric="risk.security_event",
                source="historical-event-archive", consumers=("risk_gate", "eligibility"),
                reason="NO_VERIFIED_POINT_IN_TIME_EVENT_ARCHIVE", fetched_at=fetched_at,
            ),
            unavailable_series(
                series_id=f"liveness:{symbol}", symbol=symbol, metric="risk.chain_liveness_status",
                source="historical-chain-liveness", consumers=("risk_gate",),
                reason="CURRENT_OPERATIONAL_CHECK_IS_NOT_HISTORICAL_EVIDENCE", fetched_at=fetched_at,
            ),
        ))
    entries.append(unavailable_series(
        series_id="macro:fred-vintage", symbol="BTC", metric="macro.point_in_time",
        source="fred-alfred", consumers=("scoring", "regime"),
        reason="REQUIRES_FRED_API_KEY_AND_VINTAGE_DOWNLOAD", fetched_at=fetched_at,
    ))
    manifest = build_historical_manifest(spec, entries, created_at=fetched_at)
    _write_json(root / "spec.json", spec.as_dict())
    _write_json(root / "manifest.json", manifest.as_dict())
    return {
        "root": str(root), "manifest": manifest.as_dict(),
        "downloaded_series": len(acquired), "failures": failures,
    }


def load_dataset(root: str | Path) -> tuple[BacktestSpec, HistoricalDataManifest, dict[str, OHLCVSeries]]:
    base = Path(root)
    spec = BacktestSpec.from_mapping(json.loads((base / "spec.json").read_text(encoding="utf-8")))
    manifest = HistoricalDataManifest.from_mapping(json.loads((base / "manifest.json").read_text(encoding="utf-8")))
    if manifest.spec_hash != spec.content_hash:
        raise ValueError("dataset manifest does not match the frozen spec")
    series: dict[str, OHLCVSeries] = {}
    for entry in manifest.series:
        if entry.metric != "market.ohlcv" or entry.row_count == 0:
            continue
        path = base / "series" / _series_filename(entry.series_id)
        model = OHLCVSeries.from_mapping(json.loads(path.read_text(encoding="utf-8")))
        if model.ohlcv_hash != entry.content_sha256:
            raise ValueError(f"dataset series hash mismatch: {entry.series_id}")
        series[entry.series_id] = model
    return spec, manifest, series


__all__ = ["build_binance_dataset", "convert_usdt_series_to_usd", "default_backtest_root", "load_dataset"]
