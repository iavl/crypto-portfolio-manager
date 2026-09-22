"""Public Coinbase Exchange OHLCV provider for USD quote conversion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..models.market import Candle, OHLCVSeries
from ..models.time import normalize_timestamp, parse_timestamp
from .base import ProviderDataError, ProviderResponseError
from .http import HttpClient


BASE_URL = "https://api.exchange.coinbase.com"
_GRANULARITY = {"1H": 3600, "1D": 86400}


class CoinbaseProvider:
    name = "coinbase"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock

    def _now(self) -> str:
        value = self.clock() if callable(self.clock) else datetime.now(timezone.utc)
        return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")

    def candles(
        self,
        symbol: str,
        *,
        timeframe: str = "1H",
        start: datetime | str,
        end: datetime | str,
    ) -> OHLCVSeries:
        asset = str(symbol).strip().upper()
        frame = str(timeframe).strip().upper()
        if not asset:
            raise ValueError("symbol must be non-empty")
        if frame not in _GRANULARITY:
            raise ValueError("timeframe must be 1H or 1D")
        start_at = parse_timestamp(start.isoformat() if isinstance(start, datetime) else start)
        end_at = parse_timestamp(end.isoformat() if isinstance(end, datetime) else end)
        if end_at <= start_at:
            raise ValueError("end must be after start")
        interval = _GRANULARITY[frame]
        product = f"{asset}-USD"
        rows: dict[int, Any] = {}
        cursor = start_at
        # Coinbase documents at most 300 candles per request. Use 299
        # intervals so inclusive endpoints cannot exceed that limit.
        while cursor < end_at:
            page_end = min(end_at, cursor + timedelta(seconds=interval * 299))
            payload = self.client.get_json(
                f"{BASE_URL}/products/{product}/candles",
                params={
                    "granularity": interval,
                    "start": cursor.isoformat().replace("+00:00", "Z"),
                    "end": page_end.isoformat().replace("+00:00", "Z"),
                },
            )
            if not isinstance(payload, list):
                raise ProviderResponseError("Coinbase candles response must be an array")
            for row in payload:
                if not isinstance(row, (list, tuple)) or len(row) < 6:
                    raise ProviderDataError("Coinbase candle row is malformed")
                rows[int(row[0])] = row
            next_cursor = page_end + timedelta(seconds=interval)
            if next_cursor <= cursor:
                raise ProviderDataError("Coinbase candle pagination did not advance")
            cursor = next_cursor
        now = parse_timestamp(self._now())
        candles = []
        for epoch, row in sorted(rows.items()):
            opened = datetime.fromtimestamp(epoch, timezone.utc)
            if opened < start_at or opened > end_at:
                continue
            candles.append(Candle(
                opened.isoformat(), open=float(row[3]), high=float(row[2]), low=float(row[1]),
                close=float(row[4]), volume=float(row[5]),
                completed=opened + timedelta(seconds=interval) <= now,
            ))
        if not candles:
            raise ProviderDataError("Coinbase returned no candles")
        return OHLCVSeries(
            symbol=asset, timeframe=frame, candles=tuple(candles), source=self.name,
            fetched_at=self._now(), venue="COINBASE", market="spot", quote_currency="USD",
        )


__all__ = ["CoinbaseProvider"]
