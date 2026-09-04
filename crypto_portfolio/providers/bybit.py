"""Public Bybit V5 market fallback provider."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ..models.market import Candle, OHLCVSeries
from ..models.time import normalize_timestamp, parse_timestamp
from .base import ProviderCapabilities, ProviderDataError, ProviderRequest, ProviderResponseError, ProviderUnsupportedMetric
from .binance import _asset_symbol, _epoch_millis, _number, _observation, _timestamp, observations_from_ohlcv
from .http import HttpClient


BASE_URL = "https://api.bybit.com"
_INTERVALS = {"1H": "60", "4H": "240", "1D": "D"}
BYBIT_SYMBOLS = {symbol: f"{symbol}USDT" for symbol in ("BTC", "ETH", "SOL", "BNB", "LINK", "AAVE")}


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        value = value.isoformat()
    return normalize_timestamp(value, "fetched_at")


def _result(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or value.get("retCode", 0) not in (0, "0", None):
        raise ProviderResponseError(f"Bybit {field} response is invalid")
    result = value.get("result")
    if not isinstance(result, Mapping):
        raise ProviderResponseError(f"Bybit {field} result is invalid")
    return result


def _rows(value: Any, field: str) -> list[Any]:
    result = _result(value, field)
    rows = result.get("list", [])
    if not isinstance(rows, list):
        raise ProviderResponseError(f"Bybit {field} list is invalid")
    return rows


class BybitProvider:
    name = "bybit"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=(
                "market.return_30d", "market.return_90d", "market.return_180d",
                "market.ma20", "market.ma50", "market.ma100", "market.ma200", "market.atr14",
                "market.realized_vol_30d", "market.realized_vol_90d", "market.relative_volume", "market.drawdown",
                "market.btc_trend", "market.volatility_state",
                "derivatives.funding_rate", "derivatives.funding_rate_24h_avg", "derivatives.funding_rate_7d_avg",
                "derivatives.open_interest_usd", "derivatives.open_interest_change_1d", "derivatives.open_interest_change_7d",
                "derivatives.long_short_account_ratio",
            ),
            historical_series=("market.return_30d", "market.return_90d", "market.return_180d", "derivatives.funding_rate"),
            supports_batching=True,
            requires_api_key=False,
        )

    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        return self.client.get_json(BASE_URL + path, params=params)

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
        try:
            pair = BYBIT_SYMBOLS[asset]
        except KeyError as exc:
            raise ProviderUnsupportedMetric(f"Bybit has no approved public pair mapping for {asset}") from exc
        params: dict[str, Any] = {
            "category": "spot",
            "symbol": pair,
            "interval": _INTERVALS[frame],
            "limit": 1000,
        }
        if start is not None:
            params["start"] = _epoch_millis(start, "start")
        if end is not None:
            params["end"] = _epoch_millis(end, "end")
        rows = _rows(self._get("/v5/market/kline", params), "kline")
        now = parse_timestamp(_now(self.clock))
        interval_seconds = {"1H": 3600, "4H": 14400, "1D": 86400}[frame]
        candles: list[Candle] = []
        for index, row in enumerate(rows):
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise ProviderDataError(f"Bybit kline row {index} is malformed")
            opened = _timestamp(row[0], f"kline[{index}].timestamp")
            candles.append(Candle(
                timestamp=opened,
                open=_number(row[1], f"kline[{index}].open", positive=True),
                high=_number(row[2], f"kline[{index}].high", positive=True),
                low=_number(row[3], f"kline[{index}].low", positive=True),
                close=_number(row[4], f"kline[{index}].close", positive=True),
                volume=_number(row[5], f"kline[{index}].volume"),
                completed=parse_timestamp(opened).timestamp() + interval_seconds <= now.timestamp(),
            ))
        candles.sort(key=lambda item: parse_timestamp(item.timestamp))
        if not candles:
            raise ProviderDataError("Bybit returned no klines")
        return OHLCVSeries(
            symbol=asset,
            timeframe=frame,
            candles=tuple(candles),
            source=self.name,
            fetched_at=_now(self.clock),
            venue="BYBIT",
            market="spot",
            quote_currency="USDT",
        )

    def collect(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        if request.dataset == "ohlcv":
            series = self.candles(
                request.asset,
                timeframe=request.parameters.get("timeframe", "1D"),
                start=request.parameters.get("start"),
                end=request.parameters.get("end"),
            )
            return [dict(item) for item in observations_from_ohlcv(series, request.metric_keys, as_of=request.parameters.get("as_of"))]
        if request.dataset == "funding":
            return self._funding(request)
        if request.dataset == "open_interest":
            return self._open_interest(request)
        if request.dataset == "ratios":
            return self._ratios(request)
        raise ProviderUnsupportedMetric(f"Bybit does not support dataset {request.dataset}")

    def observations_from_series(self, series: OHLCVSeries, metric_keys: Iterable[str], *, as_of: str | datetime | None = None) -> tuple[Mapping[str, Any], ...]:
        return observations_from_ohlcv(series, metric_keys, as_of=as_of)

    def _funding(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        params = {"category": "linear", "symbol": BYBIT_SYMBOLS.get(request.asset, ""), "limit": 200}
        if not params["symbol"]:
            raise ProviderUnsupportedMetric(f"Bybit has no approved public pair mapping for {request.asset}")
        if request.parameters.get("start") is not None:
            params["startTime"] = _epoch_millis(request.parameters["start"], "start")
        if request.parameters.get("end") is not None:
            params["endTime"] = _epoch_millis(request.parameters["end"], "end")
        rows = _rows(self._get("/v5/market/funding/history", params), "funding")
        parsed = []
        for index, item in enumerate(rows):
            if not isinstance(item, Mapping):
                raise ProviderDataError(f"Bybit funding row {index} is malformed")
            parsed.append((_timestamp(item.get("fundingRateTimestamp"), f"funding[{index}].timestamp"), _number(item.get("fundingRate"), f"funding[{index}].rate")))
        if not parsed:
            raise ProviderDataError("Bybit returned no funding history")
        parsed.sort()
        observed = parsed[-1][0]
        current = parsed[-1][1]
        values: dict[str, float] = {"derivatives.funding_rate": current}
        for key, days in (("derivatives.funding_rate_24h_avg", 1), ("derivatives.funding_rate_7d_avg", 7)):
            cutoff = parse_timestamp(observed).timestamp() - days * 86400
            sample = [value for timestamp, value in parsed if parse_timestamp(timestamp).timestamp() >= cutoff]
            if sample:
                values[key] = sum(sample) / len(sample)
        return [
            _observation(request.asset, key, value, observed_at=observed, fetched_at=_now(self.clock), metadata={"source_dataset": "funding/history", "venue": "BYBIT", "market": "perpetual"}, source=self.name)
            for key, value in values.items() if key in request.metric_keys
        ]

    def _open_interest(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        pair = BYBIT_SYMBOLS.get(request.asset)
        if not pair:
            raise ProviderUnsupportedMetric(f"Bybit has no approved public pair mapping for {request.asset}")
        params = {"category": "linear", "symbol": pair, "intervalTime": "1d", "limit": 200}
        rows = _rows(self._get("/v5/market/open-interest", params), "open interest")
        parsed = []
        for index, item in enumerate(rows):
            if not isinstance(item, Mapping):
                raise ProviderDataError(f"Bybit open interest row {index} is malformed")
            raw = item.get("openInterestValue")
            if raw is None:
                raise ProviderUnsupportedMetric("Bybit open interest response has no USD value")
            parsed.append((_timestamp(item.get("timestamp"), f"open_interest[{index}].timestamp"), _number(raw, f"open_interest[{index}].value", positive=True)))
        if not parsed:
            raise ProviderDataError("Bybit returned no open interest history")
        parsed.sort()
        observed, current = parsed[-1]
        values: dict[str, float] = {"derivatives.open_interest_usd": current}
        if len(parsed) >= 2:
            values["derivatives.open_interest_change_1d"] = current / parsed[-2][1] - 1
        if len(parsed) >= 8:
            values["derivatives.open_interest_change_7d"] = current / parsed[-8][1] - 1
        return [
            _observation(request.asset, key, value, observed_at=observed, fetched_at=_now(self.clock), metadata={"source_dataset": "open-interest", "venue": "BYBIT", "market": "perpetual"}, source=self.name)
            for key, value in values.items() if key in request.metric_keys
        ]

    def _ratios(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        pair = BYBIT_SYMBOLS.get(request.asset)
        if not pair:
            raise ProviderUnsupportedMetric(f"Bybit has no approved public pair mapping for {request.asset}")
        rows = _rows(self._get("/v5/market/account-ratio", {"category": "linear", "symbol": pair, "period": "1d"}), "account ratio")
        if not rows or not isinstance(rows[-1], Mapping):
            raise ProviderDataError("Bybit returned no account ratio")
        item = rows[-1]
        buy = _number(item.get("buyRatio"), "Bybit buy ratio", positive=True)
        sell = _number(item.get("sellRatio"), "Bybit sell ratio", positive=True)
        observed = _timestamp(item.get("timestamp"), "account ratio timestamp")
        value = buy / sell
        return [_observation(request.asset, "derivatives.long_short_account_ratio", value, observed_at=observed, fetched_at=_now(self.clock), metadata={"source_dataset": "account-ratio", "venue": "BYBIT", "market": "perpetual"}, source=self.name)] if "derivatives.long_short_account_ratio" in request.metric_keys else []


__all__ = ["BASE_URL", "BYBIT_SYMBOLS", "BybitProvider"]
