"""Deterministic technical indicators and structural price zones."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from ..models.execution import PriceZone
from ..models.market import Candle, OHLCVSeries, SwingPoint, TechnicalSnapshot
from ..models.policy import Policy, resolve_policy
from ..models.time import parse_timestamp
from .metrics import annualized_volatility, moving_average as _moving_average, simple_return


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return result


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _candle_values(candles: Iterable[Candle]) -> tuple[Candle, ...]:
    result = tuple(candles)
    if not result or any(not isinstance(candle, Candle) for candle in result):
        raise ValueError("candles must be a non-empty sequence of Candle objects")
    return result


def _prices(values: Sequence[float] | Sequence[Candle]) -> list[float]:
    return [value.close if isinstance(value, Candle) else value for value in values]


def moving_average(prices: Sequence[float] | Sequence[Candle], window: int) -> float:
    return _moving_average(_prices(prices), window)


def completed_candles(
    series_or_candles: OHLCVSeries | Iterable[Candle],
    *,
    as_of: str | datetime | None = None,
) -> tuple[Candle, ...]:
    """Filter daily candles before any indicator or swing calculation."""
    if isinstance(series_or_candles, OHLCVSeries):
        return series_or_candles.completed_candles(as_of)
    candles = _candle_values(series_or_candles)
    if as_of is None:
        return tuple(candle for candle in candles if candle.completed)
    if isinstance(as_of, datetime):
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        cutoff = as_of.astimezone(timezone.utc)
    else:
        cutoff = parse_timestamp(as_of)
    day_start = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    return tuple(
        candle
        for candle in candles
        if candle.completed and parse_timestamp(candle.timestamp) < day_start
    )


def canonical_ohlcv_hash(series: OHLCVSeries | Mapping[str, Any]) -> str:
    if isinstance(series, Mapping):
        series = OHLCVSeries.from_mapping(series)
    if not isinstance(series, OHLCVSeries):
        raise ValueError("series must be an OHLCVSeries or mapping")
    return series.ohlcv_hash


def lookback_return(prices: Sequence[float] | Sequence[Candle], days: int) -> float | None:
    """Return the close-to-close return over ``days`` completed candles."""
    days = _positive_int(days, "days")
    values = [_number(value, "price", minimum=0.0) for value in _prices(prices)]
    if any(value <= 0 for value in values):
        raise ValueError("prices must be > 0")
    if len(values) <= days:
        return None
    return simple_return(values[-days - 1], values[-1])


def true_range(candle: Candle, previous_close: float | None = None) -> float:
    if not isinstance(candle, Candle):
        raise ValueError("candle must be a Candle")
    if previous_close is None:
        return candle.high - candle.low
    previous_close = _number(previous_close, "previous_close", minimum=0.0)
    if previous_close <= 0:
        raise ValueError("previous_close must be > 0")
    return max(
        candle.high - candle.low,
        abs(candle.high - previous_close),
        abs(candle.low - previous_close),
    )


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    values = _candle_values(candles)
    return [
        true_range(candle, values[index - 1].close if index else None)
        for index, candle in enumerate(values)
    ]


def average_true_range(candles: Sequence[Candle], period: int = 14) -> float | None:
    period = _positive_int(period, "period")
    values = _candle_values(candles)
    if len(values) < period:
        return None
    return sum(true_ranges(values)[-period:]) / period


atr = average_true_range
atr14 = average_true_range
calculate_atr = average_true_range


def realized_volatility(
    prices: Sequence[float] | Sequence[Candle], window: int, *, annualization_days: int = 365
) -> float | None:
    window = _positive_int(window, "window")
    annualization_days = _positive_int(annualization_days, "annualization_days")
    values = [_number(value, "price", minimum=0.0) for value in _prices(prices)]
    if any(value <= 0 for value in values):
        raise ValueError("prices must be > 0")
    if len(values) <= window:
        return None
    return annualized_volatility(values[-window - 1:], annualization_days)


calculate_realized_volatility = realized_volatility


def volume_moving_average(volumes: Sequence[float], window: int = 20) -> float | None:
    window = _positive_int(window, "window")
    values = [_number(value, "volume", minimum=0.0) for value in volumes]
    if len(values) <= window:
        return None
    previous = values[-window - 1 : -1]
    if not previous or sum(previous) <= 0:
        return None
    return sum(previous) / window


volume_ma20 = volume_moving_average


def relative_volume(volumes: Sequence[float], window: int = 20) -> float | None:
    average = volume_moving_average(volumes, window)
    if average is None:
        return None
    latest = _number(volumes[-1], "latest volume", minimum=0.0)
    if latest <= 0:
        return None
    return latest / average


def history_position(
    prices: Sequence[float] | Sequence[Candle], current_price: float | None = None
) -> tuple[float, float, float]:
    values = [_number(value, "price", minimum=0.0) for value in _prices(prices)]
    if not values or any(value <= 0 for value in values):
        raise ValueError("prices must be a non-empty sequence of values > 0")
    current = values[-1] if current_price is None else _number(current_price, "current_price", minimum=0.0)
    if current <= 0:
        raise ValueError("current_price must be > 0")
    high = max(values)
    drawdown = current / high - 1.0
    return high, drawdown, drawdown


def _swing_strength(kind: str, center: float, neighbours: Sequence[float]) -> float:
    if kind == "LOW":
        gap = min((value - center) / center for value in neighbours)
    else:
        gap = min((center - value) / center for value in neighbours)
    return min(100.0, max(0.0, gap * 100.0))


def detect_swings(
    candles: Iterable[Candle],
    swing_window: int = 5,
    *,
    as_of: str | datetime | None = None,
) -> tuple[SwingPoint, ...]:
    """Return only points with ``swing_window`` completed candles on both sides."""
    window = _positive_int(swing_window, "swing_window")
    values = completed_candles(candles, as_of=as_of)
    if len(values) < 2 * window + 1:
        return ()
    result: list[SwingPoint] = []
    for index in range(window, len(values) - window):
        candle = values[index]
        left = values[index - window : index]
        right = values[index + 1 : index + window + 1]
        neighbours = left + right
        if all(candle.low < item.low for item in neighbours):
            result.append(
                SwingPoint(
                    candle.timestamp,
                    candle.low,
                    "LOW",
                    _swing_strength("LOW", candle.low, [item.low for item in neighbours]),
                )
            )
        if all(candle.high > item.high for item in neighbours):
            result.append(
                SwingPoint(
                    candle.timestamp,
                    candle.high,
                    "HIGH",
                    _swing_strength("HIGH", candle.high, [item.high for item in neighbours]),
                )
            )
    return tuple(sorted(result, key=lambda point: parse_timestamp(point.timestamp)))


def _level_strength(source: str, strength: float) -> float:
    base = {
        "MA20": 25.0,
        "MA50": 45.0,
        "MA100": 50.0,
        "MA200": 55.0,
        "SWING_LOW": strength,
        "SWING_HIGH": strength,
        "BREAKOUT_RETEST": 60.0,
        "ATR_PULLBACK": 20.0,
    }.get(source, strength)
    return min(100.0, max(0.0, base))


def build_structural_zones(
    current_price: float,
    atr_value: float | None,
    *,
    moving_averages: Mapping[str, float | None] | None = None,
    swing_points: Iterable[SwingPoint] = (),
    kind: str = "SUPPORT",
    zone_half_width_atr: float = 0.25,
    minimum_zone_separation_atr: float = 0.75,
) -> tuple[PriceZone, ...]:
    """Build and ATR-cluster structural support or resistance zones."""
    current = _number(current_price, "current_price", minimum=0.0)
    atr_number = None if atr_value is None else _number(atr_value, "atr_value", minimum=0.0)
    if current <= 0:
        raise ValueError("current_price must be > 0")
    if atr_number is None or atr_number <= 0:
        return ()
    width_factor = _number(zone_half_width_atr, "zone_half_width_atr", minimum=0.0)
    separation_factor = _number(
        minimum_zone_separation_atr,
        "minimum_zone_separation_atr",
        minimum=0.0,
    )
    if width_factor <= 0 or separation_factor <= 0:
        raise ValueError("ATR zone settings must be > 0")
    zone_kind = str(kind).strip().upper()
    if zone_kind not in {"SUPPORT", "RESISTANCE"}:
        raise ValueError("kind must be SUPPORT or RESISTANCE")
    levels: list[tuple[float, str, float]] = []
    for source, raw_level in (moving_averages or {}).items():
        if raw_level is None:
            continue
        level = _number(raw_level, f"{source} level", minimum=0.0)
        if level <= 0:
            continue
        source_name = str(source).strip().upper()
        if zone_kind == "SUPPORT" and level > current + atr_number * width_factor:
            continue
        if zone_kind == "RESISTANCE" and level < current - atr_number * width_factor:
            continue
        levels.append((level, source_name, _level_strength(source_name, 0.0)))
    for point in swing_points:
        if not isinstance(point, SwingPoint):
            raise ValueError("swing_points must contain SwingPoint objects")
        if point.kind != ("LOW" if zone_kind == "SUPPORT" else "HIGH"):
            continue
        if zone_kind == "SUPPORT" and point.price > current + atr_number * width_factor:
            continue
        if zone_kind == "RESISTANCE" and point.price < current - atr_number * width_factor:
            continue
        levels.append((point.price, f"SWING_{point.kind}", _level_strength(f"SWING_{point.kind}", point.strength)))
    if not levels:
        return ()

    width = atr_number * width_factor
    threshold = atr_number * separation_factor
    levels.sort(key=lambda item: item[0])
    clusters: list[list[tuple[float, str, float]]] = []
    for level in levels:
        if not clusters or level[0] - clusters[-1][-1][0] > threshold:
            clusters.append([level])
        else:
            clusters[-1].append(level)
    zones: list[PriceZone] = []
    for cluster in clusters:
        low = min(level - width for level, _, _ in cluster)
        high = max(level + width for level, _, _ in cluster)
        if zone_kind == "SUPPORT":
            high = min(high, current)
            if low >= high:
                continue
        else:
            low = max(low, current)
            if low >= high:
                continue
        sources = tuple(dict.fromkeys(source for _, source, _ in cluster))
        strength = min(100.0, max(strength for _, _, strength in cluster) + 12.0 * (len(sources) - 1))
        zones.append(PriceZone(low, high, kind=zone_kind, strength=strength, sources=sources))
    zones.sort(key=lambda zone: zone.midpoint, reverse=zone_kind == "SUPPORT")
    return tuple(zones)


build_price_zones = build_structural_zones
build_support_zones = build_structural_zones
detect_swing_points = detect_swings


def _trend_state(
    closes: Sequence[float],
    moving_averages: Mapping[str, float | None],
    current_price: float | None = None,
) -> str:
    close = closes[-1] if current_price is None else current_price
    ma50 = moving_averages.get("MA50")
    ma100 = moving_averages.get("MA100")
    ma200 = moving_averages.get("MA200")
    if ma50 is not None and ma100 is not None and ma200 is not None:
        if close > ma50 > ma100 > ma200:
            return "STRONG_UPTREND"
        if close < ma50 < ma100 < ma200:
            return "STRONG_DOWNTREND"
    if ma50 is not None and close > ma50:
        return "UPTREND"
    if ma50 is not None and close < ma50:
        return "DOWNTREND"
    return "NEUTRAL"


def _volatility_state(atr_percent: float | None, thresholds: Mapping[str, float]) -> str:
    if atr_percent is None:
        return "UNKNOWN"
    if atr_percent <= thresholds["low_max"]:
        return "LOW"
    if atr_percent <= thresholds["normal_max"]:
        return "NORMAL"
    if atr_percent <= thresholds["high_max"]:
        return "HIGH"
    return "EXTREME"


def _volume_state(relative: float | None, supportive_min: float) -> str:
    if relative is None:
        return "UNKNOWN"
    if relative >= supportive_min:
        return "SUPPORTIVE"
    if relative < 1.0 / supportive_min:
        return "WEAK"
    return "NEUTRAL"


def _technical_confidence(
    history_days: int,
    preferred_days: int,
    minimum_days: int,
    relative: float | None,
    atr_value: float | None,
    zones: Sequence[PriceZone],
) -> tuple[str, str]:
    if history_days < minimum_days:
        return "LOW", "INSUFFICIENT_HISTORY"
    if atr_value is None or not zones:
        return "LOW" if history_days < preferred_days else "MEDIUM", "REDUCED_STRUCTURE"
    if history_days >= preferred_days and relative is not None:
        return "HIGH", "FULL"
    return "MEDIUM", "REDUCED" if history_days < preferred_days else "VOLUME_UNKNOWN"


def build_technical_snapshot(
    series: OHLCVSeries | Mapping[str, Any],
    current_spot_price: float | None = None,
    *,
    spot_price: float | None = None,
    as_of: str | datetime | None = None,
    policy: Policy | None = None,
    execution_config: Mapping[str, Any] | None = None,
    volume_reliable: bool = True,
) -> TechnicalSnapshot:
    """Build a replayable snapshot from completed daily candles only."""
    if isinstance(series, Mapping):
        series = OHLCVSeries.from_mapping(series)
    if not isinstance(series, OHLCVSeries):
        raise ValueError("series must be an OHLCVSeries or mapping")
    if current_spot_price is not None and spot_price is not None:
        raise ValueError("provide only one of current_spot_price and spot_price")
    current = current_spot_price if current_spot_price is not None else spot_price
    if current is None:
        raise ValueError("current_spot_price is required")
    current = _number(current, "current_spot_price", minimum=0.0)
    if current <= 0:
        raise ValueError("current_spot_price must be > 0")
    config = dict(execution_config or (policy or resolve_policy()).execution)
    if not config:
        raise ValueError("execution configuration is required")
    if as_of is None:
        last_timestamp = parse_timestamp(series.candles[-1].timestamp)
        as_of_value = last_timestamp + timedelta(days=1)
    else:
        as_of_value = as_of
    candles = completed_candles(series, as_of=as_of_value)
    if not candles:
        raise ValueError("no completed candles are available as of the requested timestamp")
    used_series = OHLCVSeries(series.symbol, series.timeframe, candles, series.source, series.fetched_at)
    closes = [candle.close for candle in candles]
    volumes = [candle.volume for candle in candles]
    windows = config["moving_average_windows"]
    moving_averages = {
        f"MA{window}": moving_average(closes, window) if len(closes) >= window else None
        for window in windows
    }
    ma_values = {
        "MA20": moving_averages.get("MA20"),
        "MA50": moving_averages.get("MA50"),
        "MA100": moving_averages.get("MA100"),
        "MA200": moving_averages.get("MA200"),
    }
    atr_value = average_true_range(candles, config["atr_period"])
    atr_percent = None if atr_value is None else atr_value / closes[-1]
    relative = None if not volume_reliable or not any(volumes) else relative_volume(volumes, config["volume_average_window"])
    high = max(candle.high for candle in candles)
    distance = current / high - 1.0
    swings = detect_swings(candles, config["swing_window"])
    swing_highs = tuple(point for point in swings if point.kind == "HIGH")
    swing_lows = tuple(point for point in swings if point.kind == "LOW")
    all_levels = tuple(ma_values.items())
    zones = build_structural_zones(
        current,
        atr_value,
        moving_averages=dict(all_levels),
        swing_points=swing_lows,
        kind="SUPPORT",
        zone_half_width_atr=config["zone_half_width_atr"],
        minimum_zone_separation_atr=config["minimum_zone_separation_atr"],
    )
    resistance = build_structural_zones(
        current,
        atr_value,
        moving_averages=dict(all_levels),
        swing_points=swing_highs,
        kind="RESISTANCE",
        zone_half_width_atr=config["zone_half_width_atr"],
        minimum_zone_separation_atr=config["minimum_zone_separation_atr"],
    )
    confidence, quality = _technical_confidence(
        len(candles),
        config["preferred_history_days"],
        config["minimum_history_days"],
        relative,
        atr_value,
        zones,
    )
    as_of_timestamp = parse_timestamp(as_of_value if isinstance(as_of_value, str) else as_of_value.isoformat())
    if series.fetched_at is not None:
        fetched_at = parse_timestamp(series.fetched_at)
        age_days = (as_of_timestamp - fetched_at).total_seconds() / 86400
        if age_days < 0:
            quality = "CONFLICTING_METADATA"
            confidence = "LOW"
        elif age_days > config.get("max_fetched_age_days", 7):
            quality = "STALE"
            confidence = "LOW"
    if not volume_reliable:
        quality = f"{quality}_VOLUME_UNRELIABLE"
    threshold = config["volatility_atr_percent"]
    return TechnicalSnapshot(
        symbol=series.symbol,
        as_of=as_of_value if isinstance(as_of_value, str) else as_of_value,
        current_spot_price=current,
        last_completed_close=closes[-1],
        history_days=len(candles),
        ma20=ma_values["MA20"],
        ma50=ma_values["MA50"],
        ma100=ma_values["MA100"],
        ma200=ma_values["MA200"],
        return_30d=lookback_return(closes, 30),
        return_90d=lookback_return(closes, 90),
        return_180d=lookback_return(closes, 180),
        realized_vol_30d=realized_volatility(closes, 30, annualization_days=config["volatility_annualization_days"]),
        realized_vol_90d=realized_volatility(closes, 90, annualization_days=config["volatility_annualization_days"]),
        atr14=atr_value,
        atr_percent=atr_percent,
        volume_ma20=volume_moving_average(volumes, config["volume_average_window"]) if volume_reliable else None,
        relative_volume=relative,
        history_high=high,
        distance_from_history_high=distance,
        current_drawdown=min(0.0, distance),
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        support_zones=zones,
        resistance_zones=resistance,
        trend_state=_trend_state(closes, dict(all_levels), current),
        volatility_state=_volatility_state(atr_percent, threshold),
        volume_state=_volume_state(relative, config["breakout"]["minimum_relative_volume"]),
        technical_confidence=confidence,
        data_quality=quality,
        ohlcv_hash=used_series.ohlcv_hash,
        source=series.source,
        timeframe=series.timeframe,
        ohlcv_metadata=used_series.metadata(),
    )


technical_snapshot = build_technical_snapshot


__all__ = [
    "atr",
    "atr14",
    "average_true_range",
    "build_price_zones",
    "build_support_zones",
    "build_structural_zones",
    "build_technical_snapshot",
    "completed_candles",
    "canonical_ohlcv_hash",
    "calculate_atr",
    "calculate_realized_volatility",
    "detect_swings",
    "detect_swing_points",
    "history_position",
    "lookback_return",
    "realized_volatility",
    "relative_volume",
    "technical_snapshot",
    "true_range",
    "true_ranges",
    "moving_average",
    "volume_moving_average",
    "volume_ma20",
]
