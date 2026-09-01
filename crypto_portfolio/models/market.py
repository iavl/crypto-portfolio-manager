"""Strict normalized market candles and technical snapshots."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .execution import PriceZone
from .time import normalize_timestamp, parse_timestamp


def _timestamp(value: Any, field: str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must include a timezone")
        value = value.astimezone(timezone.utc).isoformat()
    return normalize_timestamp(value, field)


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return result


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class Candle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    completed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp, "candle.timestamp"))
        values = {
            field: _number(getattr(self, field), f"candle.{field}", minimum=0.0)
            for field in ("open", "high", "low", "close")
        }
        if any(value <= 0 for value in values.values()):
            raise ValueError("candle OHLC values must be > 0")
        volume = _number(self.volume, "candle.volume", minimum=0.0)
        if values["high"] < max(values["open"], values["close"], values["low"]):
            raise ValueError("candle.high must be >= open, close, and low")
        if values["low"] > min(values["open"], values["close"], values["high"]):
            raise ValueError("candle.low must be <= open, close, and high")
        if not isinstance(self.completed, bool):
            raise ValueError("candle.completed must be boolean")
        for field, value in values.items():
            object.__setattr__(self, field, value)
        object.__setattr__(self, "volume", volume)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Candle":
        if not isinstance(value, Mapping):
            raise ValueError("candle must be an object")
        required = ("timestamp", "open", "high", "low", "close", "volume")
        unknown = set(value) - set(required) - {"completed", "is_complete"}
        if unknown:
            raise ValueError(f"candle contains unknown fields: {', '.join(sorted(unknown))}")
        missing = [field for field in required if field not in value]
        if missing:
            raise ValueError(f"candle is missing fields: {', '.join(missing)}")
        completed = value.get("completed", value.get("is_complete", True))
        if "completed" in value and "is_complete" in value and value["completed"] != value["is_complete"]:
            raise ValueError("candle completed and is_complete disagree")
        return cls(completed=completed, **{field: value[field] for field in required})

    def as_dict(self) -> dict[str, Any]:
        result = {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }
        if not self.completed:
            result["completed"] = False
        return result

    @property
    def is_complete(self) -> bool:
        return self.completed


@dataclass(frozen=True)
class OHLCVSeries:
    symbol: str
    timeframe: str
    candles: tuple[Candle, ...]
    source: str = "unknown"
    fetched_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "series.symbol").upper())
        timeframe = _text(self.timeframe, "series.timeframe").upper()
        if timeframe != "1D":
            raise ValueError("only the 1D timeframe is supported")
        object.__setattr__(self, "timeframe", timeframe)
        candles = tuple(self.candles)
        if not candles:
            raise ValueError("series.candles must be non-empty")
        if any(not isinstance(candle, Candle) for candle in candles):
            raise ValueError("series.candles must contain Candle objects")
        timestamps = [parse_timestamp(candle.timestamp) for candle in candles]
        if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
            raise ValueError("series candles must have strictly increasing timestamps")
        object.__setattr__(self, "candles", candles)
        object.__setattr__(self, "source", _text(self.source, "series.source"))
        if self.fetched_at is not None:
            object.__setattr__(self, "fetched_at", _timestamp(self.fetched_at, "series.fetched_at"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OHLCVSeries":
        if not isinstance(value, Mapping):
            raise ValueError("OHLCV series must be an object")
        unknown = set(value) - {"symbol", "timeframe", "candles", "source", "fetched_at"}
        if unknown:
            raise ValueError(f"series contains unknown fields: {', '.join(sorted(unknown))}")
        candles = value.get("candles")
        if not isinstance(candles, (list, tuple)):
            raise ValueError("series.candles must be a list")
        return cls(
            symbol=value.get("symbol"),
            timeframe=value.get("timeframe", "1D"),
            candles=tuple(Candle.from_mapping(item) for item in candles),
            source=value.get("source", "unknown"),
            fetched_at=value.get("fetched_at"),
        )

    def completed_candles(self, as_of: str | datetime | None = None) -> tuple[Candle, ...]:
        """Return candles closed by ``as_of``; daily timestamps identify UTC days."""
        cutoff = None if as_of is None else parse_timestamp(_timestamp(as_of, "as_of"))
        day_start = None if cutoff is None else cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
        return tuple(
            candle
            for candle in self.candles
            if candle.completed and (day_start is None or parse_timestamp(candle.timestamp) < day_start)
        )

    @property
    def ohlcv_hash(self) -> str:
        payload = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candles": [candle.as_dict() for candle in self.candles],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def metadata(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "timeframe": self.timeframe,
            "start_timestamp": self.candles[0].timestamp,
            "end_timestamp": self.candles[-1].timestamp,
            "candle_count": len(self.candles),
            "fetched_at": self.fetched_at,
            "ohlcv_hash": self.ohlcv_hash,
        }


@dataclass(frozen=True)
class SwingPoint:
    timestamp: str
    price: float
    kind: str
    strength: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp, "swing.timestamp"))
        object.__setattr__(self, "price", _number(self.price, "swing.price", minimum=0.0))
        if self.price <= 0:
            raise ValueError("swing.price must be > 0")
        kind = _text(self.kind, "swing.kind").upper()
        if kind not in {"HIGH", "LOW"}:
            raise ValueError("swing.kind must be HIGH or LOW")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "strength", _number(self.strength, "swing.strength", minimum=0.0))

    def as_dict(self) -> dict[str, Any]:
        return {"timestamp": self.timestamp, "price": self.price, "kind": self.kind, "strength": self.strength}


def _optional_number(value: Any, field: str) -> float | None:
    return None if value is None else _number(value, field)


@dataclass(frozen=True)
class TechnicalSnapshot:
    symbol: str
    as_of: str
    current_spot_price: float
    last_completed_close: float
    history_days: int
    ma20: float | None = None
    ma50: float | None = None
    ma100: float | None = None
    ma200: float | None = None
    return_30d: float | None = None
    return_90d: float | None = None
    return_180d: float | None = None
    realized_vol_30d: float | None = None
    realized_vol_90d: float | None = None
    atr14: float | None = None
    atr_percent: float | None = None
    volume_ma20: float | None = None
    relative_volume: float | None = None
    history_high: float | None = None
    distance_from_history_high: float | None = None
    current_drawdown: float | None = None
    swing_highs: tuple[SwingPoint, ...] = ()
    swing_lows: tuple[SwingPoint, ...] = ()
    support_zones: tuple[PriceZone, ...] = ()
    resistance_zones: tuple[PriceZone, ...] = ()
    trend_state: str = "NEUTRAL"
    volatility_state: str = "UNKNOWN"
    volume_state: str = "UNKNOWN"
    technical_confidence: str = "LOW"
    data_quality: str = "INSUFFICIENT"
    ohlcv_hash: str = ""
    source: str | None = None
    timeframe: str = "1D"
    ohlcv_metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "snapshot.symbol").upper())
        object.__setattr__(self, "as_of", _timestamp(self.as_of, "snapshot.as_of"))
        for field in ("current_spot_price", "last_completed_close"):
            value = _number(getattr(self, field), f"snapshot.{field}", minimum=0.0)
            if value <= 0:
                raise ValueError(f"snapshot.{field} must be > 0")
            object.__setattr__(self, field, value)
        if isinstance(self.history_days, bool) or not isinstance(self.history_days, int) or self.history_days < 1:
            raise ValueError("snapshot.history_days must be a positive integer")
        for field in (
            "ma20", "ma50", "ma100", "ma200", "return_30d", "return_90d", "return_180d",
            "realized_vol_30d", "realized_vol_90d", "atr14", "atr_percent", "volume_ma20",
            "relative_volume", "history_high", "distance_from_history_high", "current_drawdown",
        ):
            object.__setattr__(self, field, _optional_number(getattr(self, field), f"snapshot.{field}"))
        for field in ("swing_highs", "swing_lows"):
            points = tuple(getattr(self, field))
            if any(not isinstance(point, SwingPoint) for point in points):
                raise ValueError(f"snapshot.{field} must contain SwingPoint objects")
            object.__setattr__(self, field, points)
        for field in ("support_zones", "resistance_zones"):
            zones = tuple(getattr(self, field))
            if any(not isinstance(zone, PriceZone) for zone in zones):
                raise ValueError(f"snapshot.{field} must contain PriceZone objects")
            object.__setattr__(self, field, zones)
        for field, allowed in (
            ("trend_state", {"STRONG_UPTREND", "UPTREND", "NEUTRAL", "DOWNTREND", "STRONG_DOWNTREND"}),
            ("volatility_state", {"LOW", "NORMAL", "HIGH", "EXTREME", "UNKNOWN"}),
            ("volume_state", {"SUPPORTIVE", "NEUTRAL", "WEAK", "UNKNOWN"}),
            ("technical_confidence", {"HIGH", "MEDIUM", "LOW"}),
        ):
            value = _text(getattr(self, field), f"snapshot.{field}").upper()
            if value not in allowed:
                raise ValueError(f"snapshot.{field} has an unsupported value")
            object.__setattr__(self, field, value)
        object.__setattr__(self, "data_quality", _text(self.data_quality, "snapshot.data_quality").upper())
        if self.ohlcv_hash:
            object.__setattr__(self, "ohlcv_hash", _hash(self.ohlcv_hash))
        if self.source is not None:
            object.__setattr__(self, "source", _text(self.source, "snapshot.source"))
        if self.ohlcv_metadata is not None:
            if not isinstance(self.ohlcv_metadata, Mapping):
                raise ValueError("snapshot.ohlcv_metadata must be an object or null")
            object.__setattr__(self, "ohlcv_metadata", dict(self.ohlcv_metadata))
            metadata_hash = self.ohlcv_metadata.get("ohlcv_hash")
            if metadata_hash is not None and self.ohlcv_hash and metadata_hash != self.ohlcv_hash:
                raise ValueError("snapshot.ohlcv_metadata ohlcv_hash does not match snapshot.ohlcv_hash")
        timeframe = _text(self.timeframe, "snapshot.timeframe").upper()
        if timeframe != "1D":
            raise ValueError("snapshot.timeframe must be 1D")
        object.__setattr__(self, "timeframe", timeframe)

    @property
    def spot_price(self) -> float:
        return self.current_spot_price

    @property
    def latest_completed_close(self) -> float:
        return self.last_completed_close

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "as_of": self.as_of,
            "current_spot_price": self.current_spot_price,
            "last_completed_close": self.last_completed_close,
            "history_days": self.history_days,
            "ma20": self.ma20,
            "ma50": self.ma50,
            "ma100": self.ma100,
            "ma200": self.ma200,
            "return_30d": self.return_30d,
            "return_90d": self.return_90d,
            "return_180d": self.return_180d,
            "realized_vol_30d": self.realized_vol_30d,
            "realized_vol_90d": self.realized_vol_90d,
            "atr14": self.atr14,
            "atr_percent": self.atr_percent,
            "volume_ma20": self.volume_ma20,
            "relative_volume": self.relative_volume,
            "history_high": self.history_high,
            "distance_from_history_high": self.distance_from_history_high,
            "current_drawdown": self.current_drawdown,
            "swing_highs": [point.as_dict() for point in self.swing_highs],
            "swing_lows": [point.as_dict() for point in self.swing_lows],
            "support_zones": [zone.as_dict() for zone in self.support_zones],
            "resistance_zones": [zone.as_dict() for zone in self.resistance_zones],
            "trend_state": self.trend_state,
            "volatility_state": self.volatility_state,
            "volume_state": self.volume_state,
            "technical_confidence": self.technical_confidence,
            "data_quality": self.data_quality,
            "ohlcv_hash": self.ohlcv_hash,
            "source": self.source,
            "timeframe": self.timeframe,
            "ohlcv_metadata": self.ohlcv_metadata,
        }


def _hash(value: Any, field: str = "hash") -> str:
    result = _text(value, field).lower()
    if len(result) != 64:
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    try:
        int(result, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be a SHA-256 hex digest") from exc
    return result


__all__ = ["Candle", "OHLCVSeries", "SwingPoint", "TechnicalSnapshot"]
