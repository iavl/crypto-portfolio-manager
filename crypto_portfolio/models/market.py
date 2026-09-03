"""Strict normalized market candles and technical snapshots."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .execution import PriceZone
from .time import normalize_timestamp, parse_timestamp
from .volume_profile import VolumeNode


_TIMEFRAME_SECONDS = {"1H": 60 * 60, "4H": 4 * 60 * 60, "1D": 24 * 60 * 60}


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
class SpotPrice:
    symbol: str
    price: float
    observed_at: str
    source: str
    fetched_at: str | None = None
    venue: str | None = None
    market: str | None = None
    quote_currency: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "spot.symbol").upper())
        price = _number(self.price, "spot.price", minimum=0.0)
        if price <= 0:
            raise ValueError("spot.price must be > 0")
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "spot.observed_at"))
        object.__setattr__(self, "source", _text(self.source, "spot.source"))
        if self.fetched_at is not None:
            object.__setattr__(self, "fetched_at", _timestamp(self.fetched_at, "spot.fetched_at"))
        for field in ("venue", "market", "quote_currency"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _text(value, f"spot.{field}"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SpotPrice":
        if not isinstance(value, Mapping):
            raise ValueError("spot must be an object")
        required = ("symbol", "price", "observed_at", "source")
        missing = [field for field in required if field not in value]
        if missing:
            raise ValueError(f"spot is missing fields: {', '.join(missing)}")
        unknown = set(value) - set(required) - {"fetched_at", "venue", "market", "quote_currency"}
        if unknown:
            raise ValueError(f"spot contains unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            **{field: value[field] for field in required},
            fetched_at=value.get("fetched_at"),
            venue=value.get("venue"),
            market=value.get("market"),
            quote_currency=value.get("quote_currency"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "observed_at": self.observed_at,
            "source": self.source,
            "fetched_at": self.fetched_at,
            "venue": self.venue,
            "market": self.market,
            "quote_currency": self.quote_currency,
        }


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
    venue: str | None = None
    market: str | None = None
    quote_currency: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "series.symbol").upper())
        timeframe = _text(self.timeframe, "series.timeframe").upper()
        if timeframe not in _TIMEFRAME_SECONDS:
            raise ValueError("series.timeframe must be 1H, 4H, or 1D")
        object.__setattr__(self, "timeframe", timeframe)
        candles = tuple(self.candles)
        if not candles:
            raise ValueError("series.candles must be non-empty")
        if any(not isinstance(candle, Candle) for candle in candles):
            raise ValueError("series.candles must contain Candle objects")
        timestamps = [parse_timestamp(candle.timestamp) for candle in candles]
        if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
            raise ValueError("series candles must have strictly increasing timestamps")
        market_dates = [timestamp.date() for timestamp in timestamps]
        if timeframe == "1D" and len(market_dates) != len(set(market_dates)):
            raise ValueError("1D series cannot contain duplicate UTC market dates")
        object.__setattr__(self, "candles", candles)
        object.__setattr__(self, "source", _text(self.source, "series.source"))
        if self.fetched_at is not None:
            object.__setattr__(self, "fetched_at", _timestamp(self.fetched_at, "series.fetched_at"))
        for field in ("venue", "market", "quote_currency"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _text(value, f"series.{field}"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OHLCVSeries":
        if not isinstance(value, Mapping):
            raise ValueError("OHLCV series must be an object")
        unknown = set(value) - {
            "symbol", "timeframe", "candles", "source", "fetched_at",
            "venue", "market", "quote_currency",
        }
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
            venue=value.get("venue"),
            market=value.get("market"),
            quote_currency=value.get("quote_currency"),
        )

    def completed_candles(self, as_of: str | datetime | None = None) -> tuple[Candle, ...]:
        """Return candles whose timeframe interval is closed by ``as_of``."""
        cutoff = None if as_of is None else parse_timestamp(_timestamp(as_of, "as_of"))
        interval = _TIMEFRAME_SECONDS[self.timeframe]
        return tuple(
            candle
            for candle in self.candles
            if candle.completed
            and (cutoff is None or parse_timestamp(candle.timestamp) + timedelta(seconds=interval) <= cutoff)
        )

    @property
    def interval_seconds(self) -> int:
        return _TIMEFRAME_SECONDS[self.timeframe]

    def cadence_metadata(self, as_of: str | datetime | None = None) -> dict[str, int | float]:
        candles = self.completed_candles(as_of)
        if not candles:
            raise ValueError("no completed candles are available")
        interval = self.interval_seconds
        first = parse_timestamp(candles[0].timestamp)
        last = parse_timestamp(candles[-1].timestamp)
        expected = int((last - first).total_seconds() / interval) + 1
        gaps = [
            int((parse_timestamp(right.timestamp) - parse_timestamp(left.timestamp)).total_seconds() / interval) - 1
            for left, right in zip(candles, candles[1:])
        ]
        return {
            "candle_count": len(candles),
            "expected_candle_count": expected,
            "missing_interval_count": max(0, expected - len(candles)),
            "coverage_ratio": len(candles) / expected,
            "max_gap_intervals": max(gaps, default=0),
        }

    @property
    def ohlcv_hash(self) -> str:
        encoded = json.dumps(
            self.content_identity(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def content_identity(self) -> dict[str, Any]:
        """Canonical cache identity: data plus provenance, excluding fetch time.

        ``fetched_at`` is fetch metadata (it changes on every re-fetch of the
        same data), so it is deliberately excluded: re-fetching identical
        candles from the same source is a cache hit, not a content change.
        """
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candles": [candle.as_dict() for candle in self.candles],
            "source": self.source,
            "venue": self.venue,
            "market": self.market,
            "quote_currency": self.quote_currency,
        }

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
            "venue": self.venue,
            "market": self.market,
            "quote_currency": self.quote_currency,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candles": [candle.as_dict() for candle in self.candles],
            "source": self.source,
            "fetched_at": self.fetched_at,
            "venue": self.venue,
            "market": self.market,
            "quote_currency": self.quote_currency,
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
    spot_observed_at: str | None = None
    spot_source: str | None = None
    spot_fetched_at: str | None = None
    candle_count: int | None = None
    calendar_span_days: int | None = None
    missing_day_count: int | None = None
    coverage_ratio: float | None = None
    max_gap_days: int | None = None
    observation_lag_days: int | None = None
    data_confidence: str | None = None
    setup_quality: float = 0.0
    data_quality_flags: tuple[str, ...] = ()
    history_sufficient: bool = False
    market_data_fresh: bool = False
    cadence_valid: bool = False
    source_known: bool = False
    spot_time_valid: bool = False
    volume_reliable: bool = False
    spot_close_gap_atr: float | None = None
    provenance_consistent: bool = True
    spot_venue: str | None = None
    spot_market: str | None = None
    spot_quote_currency: str | None = None
    volume_profile_confidence: str | None = None
    volume_profile_poc: float | None = None
    volume_profile_val: float | None = None
    volume_profile_vah: float | None = None
    volume_hvns: tuple[VolumeNode, ...] = ()
    volume_lvns: tuple[VolumeNode, ...] = ()
    volume_profile_summary: Mapping[str, Any] | None = None
    volume_profile_hash: str | None = None
    volume_profile_metadata: Mapping[str, Any] | None = None

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
        for field in ("candle_count", "calendar_span_days", "missing_day_count", "max_gap_days", "observation_lag_days"):
            value = getattr(self, field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"snapshot.{field} must be a non-negative integer or null")
        if self.candle_count is None:
            object.__setattr__(self, "candle_count", self.history_days)
        if self.coverage_ratio is not None:
            object.__setattr__(self, "coverage_ratio", _number(self.coverage_ratio, "snapshot.coverage_ratio", minimum=0.0))
            if self.coverage_ratio > 1:
                raise ValueError("snapshot.coverage_ratio must be <= 1")
        if self.observation_lag_days is not None and self.observation_lag_days < 0:
            raise ValueError("snapshot.observation_lag_days must be >= 0")
        for field in (
            "ma20", "ma50", "ma100", "ma200", "return_30d", "return_90d", "return_180d",
            "realized_vol_30d", "realized_vol_90d", "atr14", "atr_percent", "volume_ma20",
            "relative_volume", "history_high", "distance_from_history_high", "current_drawdown",
            "volume_profile_poc", "volume_profile_val", "volume_profile_vah",
        ):
            object.__setattr__(self, field, _optional_number(getattr(self, field), f"snapshot.{field}"))
        # Price/volume levels must be non-negative; requiring them > 0 would
        # reject legitimate zero-volume or zero-ATR snapshots, but a negative
        # level is always a data error.
        for field in (
            "ma20", "ma50", "ma100", "ma200", "realized_vol_30d", "realized_vol_90d",
            "atr14", "atr_percent", "volume_ma20", "relative_volume", "history_high",
            "volume_profile_poc", "volume_profile_val", "volume_profile_vah",
        ):
            value = getattr(self, field)
            if value is not None and value < 0:
                raise ValueError(f"snapshot.{field} must be non-negative")
        if all(getattr(self, field) is not None for field in ("volume_profile_poc", "volume_profile_val", "volume_profile_vah")):
            if not self.volume_profile_val <= self.volume_profile_poc <= self.volume_profile_vah:
                raise ValueError("snapshot Volume Profile levels must satisfy VAL <= POC <= VAH")
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
        for field in ("volume_hvns", "volume_lvns"):
            nodes = tuple(getattr(self, field))
            if any(not isinstance(node, VolumeNode) for node in nodes):
                raise ValueError(f"snapshot.{field} must contain VolumeNode objects")
            object.__setattr__(self, field, nodes)
        if self.volume_profile_confidence is not None:
            confidence = _text(self.volume_profile_confidence, "snapshot.volume_profile_confidence").upper()
            if confidence not in {"HIGH", "MEDIUM", "LOW", "UNAVAILABLE"}:
                raise ValueError("snapshot.volume_profile_confidence is unsupported")
            object.__setattr__(self, "volume_profile_confidence", confidence)
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
        data_confidence = self.technical_confidence if self.data_confidence is None else self.data_confidence
        data_confidence = _text(data_confidence, "snapshot.data_confidence").upper()
        if data_confidence not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("snapshot.data_confidence has an unsupported value")
        object.__setattr__(self, "data_confidence", data_confidence)
        setup_quality = _number(self.setup_quality, "snapshot.setup_quality", minimum=0.0)
        if setup_quality > 100:
            raise ValueError("snapshot.setup_quality must be <= 100")
        object.__setattr__(self, "setup_quality", setup_quality)
        flags = tuple(_text(flag, "snapshot.data_quality_flags") for flag in self.data_quality_flags)
        if len(flags) != len(set(flags)):
            raise ValueError("snapshot.data_quality_flags must not contain duplicates")
        object.__setattr__(self, "data_quality_flags", flags)
        for field in (
            "history_sufficient", "market_data_fresh", "cadence_valid", "source_known",
            "spot_time_valid", "volume_reliable", "provenance_consistent",
        ):
            if not isinstance(getattr(self, field), bool):
                raise ValueError(f"snapshot.{field} must be boolean")
        for field in ("spot_observed_at", "spot_fetched_at"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _timestamp(value, f"snapshot.{field}"))
        if self.spot_source is not None:
            object.__setattr__(self, "spot_source", _text(self.spot_source, "snapshot.spot_source"))
        for field in ("spot_venue", "spot_market", "spot_quote_currency"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _text(value, f"snapshot.{field}"))
        if self.spot_close_gap_atr is not None:
            object.__setattr__(self, "spot_close_gap_atr", _number(self.spot_close_gap_atr, "snapshot.spot_close_gap_atr", minimum=0.0))
        if self.ohlcv_hash:
            object.__setattr__(self, "ohlcv_hash", _hash(self.ohlcv_hash))
        if self.volume_profile_hash:
            object.__setattr__(self, "volume_profile_hash", _hash(self.volume_profile_hash, "snapshot.volume_profile_hash"))
        if self.source is not None:
            object.__setattr__(self, "source", _text(self.source, "snapshot.source"))
        if self.ohlcv_metadata is not None:
            if not isinstance(self.ohlcv_metadata, Mapping):
                raise ValueError("snapshot.ohlcv_metadata must be an object or null")
            object.__setattr__(self, "ohlcv_metadata", dict(self.ohlcv_metadata))
            metadata_hash = self.ohlcv_metadata.get("ohlcv_hash")
            if metadata_hash is not None and self.ohlcv_hash and metadata_hash != self.ohlcv_hash:
                raise ValueError("snapshot.ohlcv_metadata ohlcv_hash does not match snapshot.ohlcv_hash")
        for field in ("volume_profile_summary", "volume_profile_metadata"):
            value = getattr(self, field)
            if value is not None:
                if not isinstance(value, Mapping):
                    raise ValueError(f"snapshot.{field} must be an object or null")
                object.__setattr__(self, field, dict(value))
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

    def technical_summary(self, selected_zones: Iterable[PriceZone] = ()) -> dict[str, Any]:
        return {
            "summary_version": 1,
            "symbol": self.symbol,
            "spot_price": self.current_spot_price,
            "spot_observed_at": self.spot_observed_at,
            "spot_source": self.spot_source,
            "spot_fetched_at": self.spot_fetched_at,
            "spot_venue": self.spot_venue,
            "spot_market": self.spot_market,
            "spot_quote_currency": self.spot_quote_currency,
            "ma20": self.ma20,
            "ma50": self.ma50,
            "ma100": self.ma100,
            "ma200": self.ma200,
            "atr14": self.atr14,
            "atr_percent": self.atr_percent,
            "return_30d": self.return_30d,
            "return_90d": self.return_90d,
            "return_180d": self.return_180d,
            "realized_vol_30d": self.realized_vol_30d,
            "realized_vol_90d": self.realized_vol_90d,
            "relative_volume": self.relative_volume,
            "volume_profile_confidence": self.volume_profile_confidence,
            "volume_profile_poc": self.volume_profile_poc,
            "volume_profile_val": self.volume_profile_val,
            "volume_profile_vah": self.volume_profile_vah,
            "volume_hvns": [node.as_dict() for node in self.volume_hvns],
            "volume_lvns": [node.as_dict() for node in self.volume_lvns],
            "volume_profile_summary": self.volume_profile_summary,
            "volume_profile_hash": self.volume_profile_hash,
            "volume_profile_metadata": self.volume_profile_metadata,
            "trend_state": self.trend_state,
            "data_confidence": self.data_confidence,
            "setup_quality": self.setup_quality,
            "data_quality": self.data_quality,
            "data_quality_flags": list(self.data_quality_flags),
            "selected_zones": [zone.as_dict() for zone in selected_zones],
            "ohlcv_hash": self.ohlcv_hash or None,
        }

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
            "volume_profile_confidence": self.volume_profile_confidence,
            "volume_profile_poc": self.volume_profile_poc,
            "volume_profile_val": self.volume_profile_val,
            "volume_profile_vah": self.volume_profile_vah,
            "volume_hvns": [node.as_dict() for node in self.volume_hvns],
            "volume_lvns": [node.as_dict() for node in self.volume_lvns],
            "volume_profile_summary": self.volume_profile_summary,
            "volume_profile_hash": self.volume_profile_hash,
            "volume_profile_metadata": self.volume_profile_metadata,
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
            "data_confidence": self.data_confidence,
            "setup_quality": self.setup_quality,
            "data_quality": self.data_quality,
            "data_quality_flags": list(self.data_quality_flags),
            "history_sufficient": self.history_sufficient,
            "market_data_fresh": self.market_data_fresh,
            "cadence_valid": self.cadence_valid,
            "source_known": self.source_known,
            "spot_time_valid": self.spot_time_valid,
            "volume_reliable": self.volume_reliable,
            "spot_close_gap_atr": self.spot_close_gap_atr,
            "provenance_consistent": self.provenance_consistent,
            "spot_observed_at": self.spot_observed_at,
            "spot_source": self.spot_source,
            "spot_fetched_at": self.spot_fetched_at,
            "spot_venue": self.spot_venue,
            "spot_market": self.spot_market,
            "spot_quote_currency": self.spot_quote_currency,
            "candle_count": self.candle_count,
            "calendar_span_days": self.calendar_span_days,
            "missing_day_count": self.missing_day_count,
            "coverage_ratio": self.coverage_ratio,
            "max_gap_days": self.max_gap_days,
            "observation_lag_days": self.observation_lag_days,
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


__all__ = ["Candle", "OHLCVSeries", "SpotPrice", "SwingPoint", "TechnicalSnapshot"]
