"""Minimal provider protocols; implementations must normalize their own output."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from ..models.market import OHLCVSeries, SpotPrice


class MarketDataProvider(Protocol):
    def candles(
        self,
        symbol: str,
        *,
        timeframe: str = "1D",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> OHLCVSeries:
        """Return normalized OHLCV candles without portfolio-side effects."""

    def spot_price(self, symbol: str) -> SpotPrice:
        """Return a normalized timestamped spot observation."""

    def prices(
        self,
        symbols: Sequence[str],
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """Return normalized spot observations without portfolio-side effects."""


class FundamentalDataProvider(Protocol):
    def fundamentals(self, symbols: Sequence[str]) -> Mapping[str, Mapping[str, Any]]:
        """Return normalized asset fundamental observations."""


class OnchainDataProvider(Protocol):
    def onchain(self, symbols: Sequence[str]) -> Mapping[str, Mapping[str, Any]]:
        """Return normalized on-chain observations."""


class EventDataProvider(Protocol):
    def events(self, symbols: Sequence[str]) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """Return normalized security, governance, and regulatory events."""


class MetricDataProvider(Protocol):
    def collect(self, collection_plan: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        """Return one structured result for each requested metric."""


__all__ = [
    "EventDataProvider",
    "FundamentalDataProvider",
    "MarketDataProvider",
    "MetricDataProvider",
    "OnchainDataProvider",
]
