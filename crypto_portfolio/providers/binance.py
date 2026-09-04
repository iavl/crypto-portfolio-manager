"""Public Binance spot and USD-Market data normalization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable, Mapping

from ..engine.technical import (
    average_true_range,
    calendar_lookback_return,
    calendar_realized_volatility,
    history_position,
    moving_average,
    relative_volume,
    _trend_state,
    _volatility_state,
)
from ..metrics_registry import metric_definition
from ..models.market import Candle, OHLCVSeries, SpotPrice
from ..models.policy import resolve_policy
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderCapabilities,
    ProviderDataError,
    ProviderResponseError,
    ProviderRequest,
    ProviderUnsupportedMetric,
)
from .http import HttpClient


SPOT_BASE_URL = "https://api.binance.com"
FUTURES_BASE_URL = "https://fapi.binance.com"
_INTERVALS = {"1H": "1h", "4H": "4h", "1D": "1d"}
_QUOTE = "USDT"
BINANCE_SYMBOLS = {symbol: f"{symbol}{_QUOTE}" for symbol in ("BTC", "ETH", "SOL", "BNB", "LINK", "AAVE")}


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        value = value.isoformat()
    return normalize_timestamp(value, "fetched_at")


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ProviderDataError(f"{field} is invalid")
    return result


def _timestamp(value: Any, field: str) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = _number(value, field)
        if number > 100_000_000_000:
            number /= 1000
        try:
            return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), field)
        except (OverflowError, OSError, ValueError) as exc:
            raise ProviderDataError(f"{field} is an invalid epoch") from exc
    return normalize_timestamp(value, field)


def _epoch_millis(value: Any, field: str) -> int:
    return int(parse_timestamp(_timestamp(value, field)).timestamp() * 1000)


def _asset_symbol(symbol: str) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    value = symbol.strip().upper()
    return value[:-len(_QUOTE)] if value.endswith(_QUOTE) and len(value) > len(_QUOTE) else value


def binance_symbol(symbol: str, *, quote: str = _QUOTE) -> str:
    asset = _asset_symbol(symbol)
    quote = quote.strip().upper()
    if quote != _QUOTE:
        raise ProviderUnsupportedMetric(f"Binance quote currency is not supported: {quote}")
    try:
        return BINANCE_SYMBOLS[asset]
    except KeyError as exc:
        raise ProviderUnsupportedMetric(f"Binance has no approved public pair mapping for {asset}") from exc


def _list_response(value: Any, field: str) -> list[Any]:
    if isinstance(value, Mapping) and "code" in value and value.get("code") not in (0, "0", None):
        raise ProviderResponseError(f"Binance returned an error for {field}")
    if not isinstance(value, list):
        raise ProviderResponseError(f"Binance {field} response must be a list")
    return value


def _mapping_response(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderResponseError(f"Binance {field} response must be an object")
    if "code" in value and value.get("code") not in (0, "0", None):
        raise ProviderResponseError(f"Binance returned an error for {field}")
    return value


def _observation(
    asset: str,
    key: str,
    value: Any,
    *,
    observed_at: str,
    fetched_at: str,
    period: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    summary: str | None = None,
    source: str = "binance",
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
        "source": source,
        "confidence": "HIGH",
        "summary": summary,
        "metadata": {
            "venue": "BINANCE",
            "market": "spot" if not key.startswith("derivatives.") else "perpetual",
            **dict(metadata or {}),
        },
    }


def observations_from_ohlcv(
    series: OHLCVSeries,
    metric_keys: Iterable[str],
    *,
    as_of: str | datetime | None = None,
    fetched_at: str | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Derive all requested technical values from one normalized candle series."""
    if not isinstance(series, OHLCVSeries):
        raise ValueError("series must be an OHLCVSeries")
    cutoff = as_of.isoformat() if isinstance(as_of, datetime) else as_of
    candles = series.completed_candles(cutoff)
    if not candles:
        raise ProviderDataError("no completed Binance candles are available")
    keys = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    closes = [candle.close for candle in candles]
    volumes = [candle.volume for candle in candles]
    observed_at = candles[-1].timestamp
    fetched = fetched_at or series.fetched_at or _now()
    metadata = {
        "venue": series.venue or series.source.upper(),
        "market": series.market or "spot",
        "quote_currency": series.quote_currency or "USDT",
        "source_dataset": "spot_klines",
        "observed_range": {"start": candles[0].timestamp, "end": candles[-1].timestamp},
        "ohlcv_hash": series.ohlcv_hash,
        "calculation": "crypto_portfolio.engine.technical",
    }
    values: dict[str, tuple[Any, str | None]] = {
        "market.return_30d": (calendar_lookback_return(candles, 30), "30d"),
        "market.return_90d": (calendar_lookback_return(candles, 90), "90d"),
        "market.return_180d": (calendar_lookback_return(candles, 180), "180d"),
        "market.ma20": (moving_average(closes, 20) if len(closes) >= 20 else None, None),
        "market.ma50": (moving_average(closes, 50) if len(closes) >= 50 else None, None),
        "market.ma100": (moving_average(closes, 100) if len(closes) >= 100 else None, None),
        "market.ma200": (moving_average(closes, 200) if len(closes) >= 200 else None, None),
        "market.atr14": (average_true_range(candles, 14), None),
        "market.realized_vol_30d": (calendar_realized_volatility(candles, 30), "30d"),
        "market.realized_vol_90d": (calendar_realized_volatility(candles, 90), "90d"),
        "market.relative_volume": (relative_volume(volumes, 20), None),
        "market.drawdown": (history_position(candles)[2], None),
        "market.btc_trend": (_trend_state(closes, {
            "MA50": moving_average(closes, 50) if len(closes) >= 50 else None,
            "MA100": moving_average(closes, 100) if len(closes) >= 100 else None,
            "MA200": moving_average(closes, 200) if len(closes) >= 200 else None,
        }), None),
    }
    atr_value = values["market.atr14"][0]
    values["market.volatility_state"] = (
        _volatility_state(
            None if atr_value is None else atr_value / closes[-1],
            resolve_policy().execution["volatility_atr_percent"],
        ),
        None,
    )
    result = []
    for key in keys:
        if key not in values:
            raise ProviderUnsupportedMetric(f"Binance OHLCV cannot derive {key}")
        value, period = values[key]
        if value is not None:
            result.append(_observation(series.symbol, key, value, observed_at=observed_at, fetched_at=fetched, period=period, metadata=metadata, source=series.source))
    return tuple(result)


class BinanceProvider:
    name = "binance"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=(
                "market.spot_price", "market.return_30d", "market.return_90d", "market.return_180d",
                "market.ma20", "market.ma50", "market.ma100", "market.ma200", "market.atr14",
                "market.realized_vol_30d", "market.realized_vol_90d", "market.relative_volume", "market.drawdown",
                "market.btc_trend", "market.volatility_state",
                "derivatives.funding_rate", "derivatives.funding_rate_24h_avg", "derivatives.funding_rate_7d_avg",
                "derivatives.funding_rate_percentile", "derivatives.open_interest_usd",
                "derivatives.open_interest_change_1d", "derivatives.open_interest_change_7d",
                "derivatives.long_short_account_ratio", "derivatives.top_trader_long_short_ratio",
                "derivatives.futures_basis_annualized",
            ),
            historical_series=(
                "market.return_30d", "market.return_90d", "market.return_180d",
                "derivatives.funding_rate", "derivatives.open_interest_usd",
            ),
            supports_batching=True,
            requires_api_key=False,
        )

    def _get(self, base: str, path: str, params: Mapping[str, Any]) -> Any:
        return self.client.get_json(base + path, params=params)

    def spot_price(self, symbol: str) -> SpotPrice:
        asset = _asset_symbol(symbol)
        pair = binance_symbol(asset)
        value = _mapping_response(self._get(SPOT_BASE_URL, "/api/v3/ticker/price", {"symbol": pair}), "spot price")
        price = _number(value.get("price"), "Binance spot price", positive=True)
        observed = _timestamp(value.get("time", self._now()), "Binance spot timestamp")
        return SpotPrice(
            symbol=asset,
            price=price,
            observed_at=observed,
            source=self.name,
            fetched_at=_now(self.clock),
            venue="BINANCE",
            market="spot",
            quote_currency=_QUOTE,
        )

    def prices(
        self,
        symbols: Iterable[str],
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
        assets = tuple(dict.fromkeys(_asset_symbol(symbol) for symbol in symbols))
        if not assets:
            return {}
        fetched = _now(self.clock)
        payload = self._get(SPOT_BASE_URL, "/api/v3/ticker/price", {}) if len(assets) > 1 else self._get(
            SPOT_BASE_URL, "/api/v3/ticker/price", {"symbol": binance_symbol(assets[0])}
        )
        rows = payload if isinstance(payload, list) else [payload]
        by_symbol: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                raise ProviderDataError("Binance price batch row is malformed")
            pair = str(row.get("symbol", "")).upper()
            asset = _asset_symbol(pair)
            if asset not in assets:
                continue
            value = _number(row.get("price"), "Binance spot price", positive=True)
            by_symbol[asset] = ({
                "asset": asset,
                "metric_key": "market.spot_price",
                "value": value,
                "unit": "USD",
                "observed_at": _timestamp(row.get("time", fetched), "Binance spot timestamp"),
                "fetched_at": fetched,
                "source": self.name,
                "confidence": "HIGH",
                "metadata": {"venue": "BINANCE", "market": "spot", "quote_currency": _QUOTE, "source_dataset": "ticker_price"},
            },)
        return by_symbol

    def candles(
        self,
        symbol: str,
        *,
        timeframe: str = "1D",
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> OHLCVSeries:
        asset = _asset_symbol(symbol)
        frame = str(timeframe).strip().upper()
        if frame not in _INTERVALS:
            raise ValueError("timeframe must be 1H, 4H, or 1D")
        params: dict[str, Any] = {"symbol": binance_symbol(asset), "interval": _INTERVALS[frame], "limit": 1000}
        if start is not None:
            params["startTime"] = _epoch_millis(start, "start")
        if end is not None:
            params["endTime"] = _epoch_millis(end, "end")
        rows = _list_response(self._get(SPOT_BASE_URL, "/api/v3/klines", params), "klines")
        now = _now(self.clock)
        current = parse_timestamp(now)
        candles: list[Candle] = []
        for index, row in enumerate(rows):
            if not isinstance(row, (list, tuple)) or len(row) < 7:
                raise ProviderDataError(f"Binance kline row {index} is malformed")
            open_time = _timestamp(row[0], f"kline[{index}].open_time")
            close_time = _timestamp(row[6], f"kline[{index}].close_time")
            if parse_timestamp(close_time) < parse_timestamp(open_time):
                raise ProviderDataError(f"Binance kline row {index} has invalid time range")
            candles.append(Candle(
                timestamp=open_time,
                open=_number(row[1], f"kline[{index}].open", positive=True),
                high=_number(row[2], f"kline[{index}].high", positive=True),
                low=_number(row[3], f"kline[{index}].low", positive=True),
                close=_number(row[4], f"kline[{index}].close", positive=True),
                volume=_number(row[5], f"kline[{index}].volume"),
                completed=parse_timestamp(close_time) <= current,
            ))
        if not candles:
            raise ProviderDataError("Binance returned no klines")
        return OHLCVSeries(
            symbol=asset,
            timeframe=frame,
            candles=tuple(candles),
            source=self.name,
            fetched_at=now,
            venue="BINANCE",
            market="spot",
            quote_currency=_QUOTE,
        )

    def collect(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        if not isinstance(request, ProviderRequest):
            raise ValueError("request must be a ProviderRequest")
        if request.dataset == "spot":
            spot = self.spot_price(request.asset)
            return [{
                "asset": spot.symbol,
                "metric_key": "market.spot_price",
                "value": spot.price,
                "unit": "USD",
                "observed_at": spot.observed_at,
                "fetched_at": spot.fetched_at,
                "source": self.name,
                "confidence": "HIGH",
                "metadata": {
                    "venue": spot.venue,
                    "market": spot.market,
                    "quote_currency": spot.quote_currency,
                    "source_dataset": "ticker_price",
                },
            }] if "market.spot_price" in request.metric_keys else []
        if request.dataset == "ohlcv":
            series = self.candles(
                request.asset,
                timeframe=request.parameters.get("timeframe", "1D"),
                start=request.parameters.get("start"),
                end=request.parameters.get("end"),
            )
            return [dict(value) for value in observations_from_ohlcv(series, request.metric_keys, as_of=request.parameters.get("as_of"))]
        if request.dataset == "funding":
            return self._funding(request)
        if request.dataset == "open_interest":
            return self._open_interest(request)
        if request.dataset == "ratios":
            return self._ratios(request)
        if request.dataset == "basis":
            return self._basis(request)
        raise ProviderUnsupportedMetric(f"Binance does not support dataset {request.dataset}")

    def observations_from_series(self, series: OHLCVSeries, metric_keys: Iterable[str], *, as_of: str | datetime | None = None) -> tuple[Mapping[str, Any], ...]:
        return observations_from_ohlcv(series, metric_keys, as_of=as_of)

    def _funding(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        pair = binance_symbol(request.asset)
        params = {"symbol": pair, "limit": 1000}
        if request.parameters.get("start") is not None:
            params["startTime"] = _epoch_millis(request.parameters["start"], "start")
        if request.parameters.get("end") is not None:
            params["endTime"] = _epoch_millis(request.parameters["end"], "end")
        history = _list_response(self._get(FUTURES_BASE_URL, "/fapi/v1/fundingRate", params), "funding history")
        rows = []
        for index, item in enumerate(history):
            if not isinstance(item, Mapping):
                raise ProviderDataError(f"funding row {index} is malformed")
            rows.append((_timestamp(item.get("fundingTime"), f"funding[{index}].time"), _number(item.get("fundingRate"), f"funding[{index}].rate")))
        current = self._get(FUTURES_BASE_URL, "/fapi/v1/premiumIndex", {"symbol": pair})
        current = _mapping_response(current, "current funding")
        current_rate = _number(current.get("lastFundingRate"), "current funding rate")
        current_time = _timestamp(current.get("time", self._now()), "current funding timestamp")
        rows.append((current_time, current_rate))
        deduplicated: dict[str, float] = {}
        for timestamp, rate in rows:
            if timestamp in deduplicated and not math.isclose(deduplicated[timestamp], rate, rel_tol=1e-12, abs_tol=1e-12):
                raise ProviderDataError(f"conflicting funding records at {timestamp}")
            deduplicated[timestamp] = rate
        rows = sorted(deduplicated.items())
        observed = rows[-1][0]
        values: dict[str, float] = {"derivatives.funding_rate": current_rate}
        for key, days in (("derivatives.funding_rate_24h_avg", 1), ("derivatives.funding_rate_7d_avg", 7)):
            cutoff = parse_timestamp(observed) - timedelta(days=days)
            sample = [value for timestamp, value in rows if parse_timestamp(timestamp) >= cutoff]
            if sample:
                values[key] = sum(sample) / len(sample)
        if rows:
            ordered = [value for _, value in rows]
            rank = sum(value <= current_rate for value in ordered) / len(ordered)
            values["derivatives.funding_rate_percentile"] = rank
        return [
            _observation(request.asset, key, value, observed_at=observed, fetched_at=_now(self.clock), metadata={"source_dataset": "fundingRate+premiumIndex", "contract": "USDⓈ-M perpetual"})
            for key, value in values.items() if key in request.metric_keys
        ]

    def _open_interest(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        pair = binance_symbol(request.asset)
        current = _mapping_response(self._get(FUTURES_BASE_URL, "/fapi/v1/openInterest", {"symbol": pair}), "open interest")
        current_timestamp = _timestamp(current.get("time", self._now()), "open interest timestamp")
        history = self._get(FUTURES_BASE_URL, "/futures/data/openInterestHist", {"symbol": pair, "period": "1d", "limit": 30})
        rows = _list_response(history, "open interest history")
        parsed = []
        for index, item in enumerate(rows):
            if not isinstance(item, Mapping):
                raise ProviderDataError(f"open interest row {index} is malformed")
            timestamp = _timestamp(item.get("timestamp"), f"open_interest[{index}].timestamp")
            raw_value = item.get("sumOpenInterestValue")
            if raw_value is None:
                raise ProviderUnsupportedMetric("Binance open interest history has no USD value")
            value = _number(raw_value, f"open_interest[{index}].value", positive=True)
            if any(existing_timestamp == timestamp for existing_timestamp, _ in parsed):
                if any(existing_timestamp == timestamp and not math.isclose(existing_value, value, rel_tol=1e-12, abs_tol=1e-12) for existing_timestamp, existing_value in parsed):
                    raise ProviderDataError(f"conflicting open interest records at {timestamp}")
                continue
            parsed.append((timestamp, value))
        parsed.sort()
        if parsed:
            observed, current_oi = parsed[-1]
        else:
            raw_value = current.get("openInterestValue")
            if raw_value is None:
                raise ProviderUnsupportedMetric("Binance current open interest has no USD value")
            observed, current_oi = current_timestamp, _number(raw_value, "open interest", positive=True)
        result: dict[str, Any] = {"derivatives.open_interest_usd": current_oi}
        if parsed:
            reference_1d = parsed[-2][1] if len(parsed) >= 2 else None
            reference_7d = parsed[-8][1] if len(parsed) >= 8 else None
            if reference_1d:
                result["derivatives.open_interest_change_1d"] = current_oi / reference_1d - 1
            if reference_7d:
                result["derivatives.open_interest_change_7d"] = current_oi / reference_7d - 1
        return [
            _observation(request.asset, key, value, observed_at=observed, fetched_at=_now(self.clock), metadata={"source_dataset": "openInterest+openInterestHist", "contract": "USDⓈ-M perpetual"})
            for key, value in result.items() if key in request.metric_keys
        ]

    def _ratios(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        pair = binance_symbol(request.asset)
        params = {"symbol": pair, "period": "1d", "limit": 1}
        global_rows = _list_response(self._get(FUTURES_BASE_URL, "/futures/data/globalLongShortAccountRatio", params), "global long short ratio")
        top_rows = _list_response(self._get(FUTURES_BASE_URL, "/futures/data/topLongShortAccountRatio", params), "top trader ratio")
        if not global_rows or not top_rows or not isinstance(global_rows[-1], Mapping) or not isinstance(top_rows[-1], Mapping):
            raise ProviderDataError("Binance ratio response is empty")
        global_row, top_row = global_rows[-1], top_rows[-1]
        observed = _timestamp(global_row.get("timestamp"), "ratio timestamp")
        values = {
            "derivatives.long_short_account_ratio": _number(global_row.get("longShortRatio"), "account ratio", positive=True),
            "derivatives.top_trader_long_short_ratio": _number(top_row.get("longShortRatio"), "top trader ratio", positive=True),
        }
        return [
            _observation(request.asset, key, value, observed_at=observed, fetched_at=_now(self.clock), metadata={"source_dataset": "globalLongShortAccountRatio+topLongShortAccountRatio", "contract": "USDⓈ-M perpetual"})
            for key, value in values.items() if key in request.metric_keys
        ]

    def _basis(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        pair = binance_symbol(request.asset)
        rows = _list_response(self._get(FUTURES_BASE_URL, "/futures/data/basis", {"pair": pair, "contractType": "PERPETUAL", "period": "1d", "limit": 1}), "basis")
        if not rows or not isinstance(rows[-1], Mapping):
            raise ProviderDataError("Binance basis response is empty")
        item = rows[-1]
        observed = _timestamp(item.get("timestamp"), "basis timestamp")
        raw = item.get("annualizedBasisRate", item.get("basisRateAnnualized"))
        value = _number(raw, "annualized basis")
        return [_observation(request.asset, "derivatives.futures_basis_annualized", value, observed_at=observed, fetched_at=_now(self.clock), metadata={"source_dataset": "basis", "contract": "PERPETUAL"})] if "derivatives.futures_basis_annualized" in request.metric_keys else []

    def _now(self) -> str:
        return _now(self.clock)


__all__ = [
    "BinanceProvider",
    "BINANCE_SYMBOLS",
    "FUTURES_BASE_URL",
    "SPOT_BASE_URL",
    "binance_symbol",
    "observations_from_ohlcv",
]
