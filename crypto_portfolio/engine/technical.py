"""Deterministic technical indicators and structural price zones."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from ..models.execution import PriceZone
from ..models.market import Candle, OHLCVSeries, SpotPrice, SwingPoint, TechnicalSnapshot
from ..models.policy import Policy, resolve_policy
from ..models.time import normalize_timestamp, parse_timestamp
from ..models.volume_profile import VolumeProfile
from .metrics import annualized_volatility, moving_average as _moving_average, simple_return
from .volume_profile import build_multi_horizon_profiles, profile_levels


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
    """Filter completed candles before any indicator or swing calculation."""
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


def expected_latest_completed_date(as_of: str | datetime) -> date:
    """Return the latest UTC date whose daily candle should be complete."""
    raw = as_of.isoformat() if isinstance(as_of, datetime) else as_of
    timestamp = parse_timestamp(normalize_timestamp(raw, "as_of"))
    return timestamp.date() - timedelta(days=1)


def daily_coverage(
    candles: Sequence[Candle] | Iterable[Candle],
    *,
    as_of: str | datetime | None = None,
) -> dict[str, int | float | str]:
    """Measure calendar coverage of completed daily candles."""
    values = completed_candles(candles, as_of=as_of)
    if not values:
        raise ValueError("at least one completed candle is required")
    dates = [parse_timestamp(candle.timestamp).date() for candle in values]
    span = (dates[-1] - dates[0]).days + 1
    gaps = [(right - left).days - 1 for left, right in zip(dates, dates[1:])]
    count = len(values)
    return {
        "candle_count": count,
        "calendar_span_days": span,
        "missing_day_count": span - count,
        "coverage_ratio": count / span,
        "max_gap_days": max(gaps, default=0),
        "latest_completed_date": dates[-1].isoformat(),
    }


def _calendar_window(candles: Sequence[Candle], days: int) -> list[float] | None:
    days = _positive_int(days, "days")
    by_date = {parse_timestamp(candle.timestamp).date(): candle.close for candle in candles}
    latest = parse_timestamp(candles[-1].timestamp).date()
    start = latest - timedelta(days=days)
    if any(day not in by_date for day in (start + timedelta(days=index) for index in range(days + 1))):
        return None
    return [by_date[start + timedelta(days=index)] for index in range(days + 1)]


def calendar_lookback_return(candles: Sequence[Candle], days: int) -> float | None:
    window = _calendar_window(candles, days)
    return None if window is None else simple_return(window[0], window[-1])


def calendar_realized_volatility(
    candles: Sequence[Candle], window: int, *, annualization_days: int = 365
) -> float | None:
    prices = _calendar_window(candles, window)
    return None if prices is None else annualized_volatility(prices, annualization_days)


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
    # The first candle has no previous close, so it cannot contribute a fully
    # defined true range to the ATR window.
    if len(values) <= period:
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
        "VOLUME_POC": 62.0,
        "VOLUME_HVN": strength,
        "VOLUME_VAL": 48.0,
        "VOLUME_VAH": 48.0,
        "VOLUME_LVN": strength,
        "VOLUME_PROFILE_CONFLUENCE": strength,
    }.get(source, strength)
    return min(100.0, max(0.0, base))


def build_structural_zones(
    current_price: float,
    atr_value: float | None,
    *,
    moving_averages: Mapping[str, float | None] | None = None,
    swing_points: Iterable[SwingPoint] = (),
    volume_levels: Iterable[tuple[float, str, float]] = (),
    kind: str = "SUPPORT",
    zone_half_width_atr: float = 0.25,
    minimum_zone_separation_atr: float = 0.75,
    maximum_zone_span_atr: float = 1.0,
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
    span_factor = _number(maximum_zone_span_atr, "maximum_zone_span_atr", minimum=0.0)
    if span_factor <= 0:
        raise ValueError("maximum_zone_span_atr must be > 0")
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
        if level - atr_number * width_factor <= 0:
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
        if point.price - atr_number * width_factor <= 0:
            continue
        levels.append((point.price, f"SWING_{point.kind}", _level_strength(f"SWING_{point.kind}", point.strength)))
    profile_values = tuple(volume_levels)
    for raw_level, source, raw_strength in profile_values:
        level = _number(raw_level, f"{source} level", minimum=0.0)
        if level <= 0:
            continue
        source_name = str(source).strip().upper()
        if zone_kind == "SUPPORT" and level > current + atr_number * width_factor:
            continue
        if zone_kind == "RESISTANCE" and level < current - atr_number * width_factor:
            continue
        if level - atr_number * width_factor <= 0:
            continue
        strength = _level_strength(source_name, _number(raw_strength, f"{source_name} strength", minimum=0.0))
        levels.append((level, source_name, strength))
    if not levels:
        return ()

    width = atr_number * width_factor
    threshold = atr_number * separation_factor
    maximum_span = atr_number * span_factor
    levels.sort(key=lambda item: item[0])
    clusters: list[list[tuple[float, str, float]]] = []
    for level in levels:
        if (
            not clusters
            or level[0] - clusters[-1][-1][0] > threshold
            or level[0] - clusters[-1][0][0] > maximum_span
        ):
            clusters.append([level])
        else:
            clusters[-1].append(level)
    zones: list[PriceZone] = []
    for cluster in clusters:
        low = min(level - width for level, _, _ in cluster)
        high = max(level + width for level, _, _ in cluster)
        if low <= 0:
            continue
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


def structural_confluence(sources: Iterable[str]) -> float:
    categories: set[str] = set()
    normalized = tuple(str(source).strip().upper() for source in sources)
    for source in normalized:
        if source.startswith("MA"):
            categories.add("MA")
        elif source.startswith("SWING"):
            categories.add("SWING")
        elif source.startswith("VOLUME_"):
            categories.add("VOLUME_PROFILE")
        else:
            categories.add(source)
    bonus = 5.0 if "VOLUME_PROFILE_CONFLUENCE" in normalized else 0.0
    return min(35.0, 18.0 + 9.0 * max(0, len(categories) - 1) + bonus)


def _setup_quality(
    zones: Sequence[PriceZone],
    current_price: float,
    atr_value: float | None,
    *,
    volume_state: str = "UNKNOWN",
) -> float:
    if atr_value is None or atr_value <= 0 or not zones:
        return 0.0
    best = 0.0
    for zone in zones:
        distance_atr = max(0.0, (current_price - zone.midpoint) / atr_value)
        confluence = structural_confluence(zone.sources)
        proximity = max(0.0, 22.0 - min(22.0, distance_atr * 4.0))
        source_bonus = min(
            25.0,
            max(
                (
                    {
                        "SWING_LOW": 20.0,
                        "MA50": 18.0,
                        "MA100": 20.0,
                        "MA200": 22.0,
                        "MA20": 10.0,
                    }.get(source, 0.0)
                    for source in zone.sources
                ),
                default=0.0,
            ),
        )
        profile_sources = [source for source in zone.sources if source.startswith("VOLUME_")]
        profile_bonus = min(
            12.0,
            4.0 * len(profile_sources) + (4.0 if "VOLUME_PROFILE_CONFLUENCE" in zone.sources else 0.0),
        )
        relative_volume_bonus = 5.0 if volume_state == "SUPPORTIVE" else 0.0
        volume_bonus = min(15.0, profile_bonus + relative_volume_bonus)
        quality = zone.strength * 0.20 + confluence + proximity + source_bonus + volume_bonus
        if zone.sources and all(source.startswith("VOLUME_") for source in zone.sources):
            quality = min(50.0, quality)
        best = max(best, min(100.0, quality))
    return best


def _quality_label(flags: Sequence[str], *, full: bool) -> str:
    if "DATA_CONFLICT" in flags:
        return "DATA_CONFLICT"
    if "STALE_MARKET_DATA" in flags:
        return "STALE_MARKET_DATA"
    if "STALE_RETRIEVAL" in flags:
        return "STALE_RETRIEVAL"
    if "INSUFFICIENT_HISTORY" in flags:
        return "INSUFFICIENT_HISTORY"
    if "LARGE_CADENCE_GAP" in flags:
        return "INSUFFICIENT_COVERAGE"
    if "UNKNOWN_PROVENANCE" in flags:
        return "UNKNOWN_PROVENANCE"
    return "FULL" if full else "REDUCED"


def _profile_confidence(profiles: Mapping[int, VolumeProfile]) -> str:
    if not profiles:
        return "UNAVAILABLE"
    order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNAVAILABLE": 0}
    return min((profile.data_confidence for profile in profiles.values()), key=order.get)


def _profile_context(
    profiles: Mapping[int, VolumeProfile],
    preferred_lookback_days: int | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not profiles:
        return None, None
    preferred_days = preferred_lookback_days if preferred_lookback_days in profiles else max(profiles)
    preferred = profiles[preferred_days]
    horizons = {
        str(days): {
            "profile_hash": profile.profile_hash,
            "ohlcv_hash": profile.ohlcv_hash,
            "timeframe": profile.timeframe,
            "lookback_days": profile.lookback_days,
            "poc": profile.poc,
            "val": profile.value_area_low,
            "vah": profile.value_area_high,
            "hvn_midpoints": [node.midpoint for node in profile.high_volume_nodes],
            "lvn_midpoints": [node.midpoint for node in profile.low_volume_nodes],
            "confidence": profile.data_confidence,
        }
        for days, profile in sorted(profiles.items())
    }
    return {
        "description": "Volume Profile is a historical traded-volume concentration proxy, not holder cost basis",
        "preferred_lookback_days": preferred_days,
        "preferred_timeframe": preferred.timeframe,
        "horizons": horizons,
        "confidence": _profile_confidence(profiles),
    }, {
        "preferred_lookback_days": preferred_days,
        "preferred_timeframe": preferred.timeframe,
        "profiles": {
            key: {
                "profile_hash": value["profile_hash"],
                "ohlcv_hash": value["ohlcv_hash"],
                "timeframe": value["timeframe"],
                "lookback_days": value["lookback_days"],
                "confidence": value["confidence"],
            }
            for key, value in horizons.items()
        },
    }


def build_technical_snapshot(
    series: OHLCVSeries | Mapping[str, Any],
    spot: SpotPrice | Mapping[str, Any] | float | None = None,
    *,
    current_spot_price: float | None = None,
    spot_price: float | None = None,
    as_of: str | datetime | None = None,
    policy: Policy | None = None,
    execution_config: Mapping[str, Any] | None = None,
    volume_reliable: bool = True,
    profile_series: OHLCVSeries | Mapping[str, Any] | None = None,
    volume_profile: VolumeProfile | Mapping[str, Any] | None = None,
    volume_profiles: Mapping[int, VolumeProfile | Mapping[str, Any]] | None = None,
) -> TechnicalSnapshot:
    """Build a replayable snapshot from completed daily candles only."""
    if isinstance(series, Mapping):
        series = OHLCVSeries.from_mapping(series)
    if not isinstance(series, OHLCVSeries):
        raise ValueError("series must be an OHLCVSeries or mapping")
    if series.timeframe != "1D":
        raise ValueError("technical snapshots use authoritative 1D OHLCV; pass intraday data as profile_series")
    if not isinstance(volume_reliable, bool):
        raise ValueError("volume_reliable must be boolean")
    supplied_legacy_spot = False
    supplied_spot = spot
    if current_spot_price is not None or spot_price is not None:
        if supplied_spot is not None or (current_spot_price is not None and spot_price is not None):
            raise ValueError("provide one spot observation")
        supplied_spot = current_spot_price if current_spot_price is not None else spot_price
    if isinstance(supplied_spot, Mapping):
        supplied_spot = SpotPrice.from_mapping(supplied_spot)
    elif supplied_spot is not None and not isinstance(supplied_spot, SpotPrice):
        if as_of is not None:
            raise ValueError("historical replay requires a timestamped SpotPrice")
        supplied_legacy_spot = True
        supplied_spot = _number(supplied_spot, "current_spot_price", minimum=0.0)
    if supplied_spot is None:
        raise ValueError("a timestamped SpotPrice is required")
    if not isinstance(supplied_spot, SpotPrice):
        last_timestamp = parse_timestamp(series.candles[-1].timestamp)
        legacy_as_of = normalize_timestamp((last_timestamp + timedelta(days=1)).isoformat(), "as_of")
        supplied_spot = SpotPrice(
            series.symbol,
            supplied_spot,
            legacy_as_of,
            series.source,
            series.fetched_at,
        )
    if supplied_spot.symbol != series.symbol:
        raise ValueError("spot.symbol must match series.symbol")
    config = dict((policy or resolve_policy()).execution)
    if execution_config is not None:
        config.update(execution_config)
    if not config:
        raise ValueError("execution configuration is required")
    as_of_was_omitted = as_of is None
    if as_of is None:
        as_of_value = supplied_spot.observed_at
    else:
        raw_as_of = as_of.isoformat() if isinstance(as_of, datetime) else as_of
        as_of_value = normalize_timestamp(raw_as_of, "as_of")
    as_of_timestamp = parse_timestamp(as_of_value)
    if parse_timestamp(supplied_spot.observed_at) > as_of_timestamp:
        raise ValueError("spot.observed_at must be no later than as_of")
    candles = completed_candles(series, as_of=as_of_timestamp)
    if not candles:
        raise ValueError("no completed candles are available as of the requested timestamp")
    used_series = OHLCVSeries(
        series.symbol,
        series.timeframe,
        candles,
        series.source,
        series.fetched_at,
        series.venue,
        series.market,
        series.quote_currency,
    )
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
    if atr_value is not None and atr_value <= 0:
        atr_value = None
    atr_percent = None if atr_value is None else atr_value / closes[-1]
    relative = None if not volume_reliable or not any(volumes) else relative_volume(volumes, config["volume_average_window"])
    high = max(candle.high for candle in candles)
    swings = detect_swings(candles, config["swing_window"])
    swing_highs = tuple(point for point in swings if point.kind == "HIGH")
    swing_lows = tuple(point for point in swings if point.kind == "LOW")
    policy_volume_profile = (policy or resolve_policy()).volume_profile
    profiles: dict[int, VolumeProfile] = {}
    if sum(value is not None for value in (profile_series, volume_profile, volume_profiles)) > 1:
        raise ValueError("provide only one of profile_series, volume_profile, or volume_profiles")
    if volume_profile is not None:
        parsed_profile = volume_profile if isinstance(volume_profile, VolumeProfile) else VolumeProfile.from_mapping(volume_profile)
        if parsed_profile.symbol != series.symbol:
            raise ValueError("volume_profile.symbol must match series.symbol")
        profiles[parsed_profile.lookback_days] = parsed_profile
    elif volume_profiles is not None:
        if not isinstance(volume_profiles, Mapping):
            raise ValueError("volume_profiles must be an object")
        for raw_days, value in volume_profiles.items():
            parsed_profile = value if isinstance(value, VolumeProfile) else VolumeProfile.from_mapping(value)
            days = int(raw_days)
            if days != parsed_profile.lookback_days or parsed_profile.symbol != series.symbol:
                raise ValueError("volume_profiles keys and symbols must match their profiles")
            profiles[days] = parsed_profile
    elif profile_series is not None:
        if isinstance(profile_series, Mapping):
            profile_series = OHLCVSeries.from_mapping(profile_series)
        if not isinstance(profile_series, OHLCVSeries) or profile_series.symbol != series.symbol:
            raise ValueError("profile_series must be a matching OHLCVSeries")
        profiles = build_multi_horizon_profiles(
            profile_series,
            lookback_days=policy_volume_profile["lookback_days"],
            policy=policy,
            atr_value=atr_value,
            as_of=as_of_value,
            volume_reliable=volume_reliable,
        )
    elif policy_volume_profile.get("enabled") and policy_volume_profile.get("allow_daily_approximation"):
        profiles = build_multi_horizon_profiles(
            series,
            lookback_days=policy_volume_profile["lookback_days"],
            policy=policy,
            atr_value=atr_value,
            as_of=as_of_value,
            volume_reliable=volume_reliable,
        )
    preferred_days = policy_volume_profile.get("preferred_lookback_days")
    profile_summary, profile_metadata = _profile_context(profiles, preferred_days)
    profile_source_mismatch = bool(
        profiles
        and profile_series is not None
        and profile_series.source.strip().lower() != series.source.strip().lower()
    )
    if profile_source_mismatch:
        if profile_summary is not None:
            profile_summary = {**profile_summary, "confidence": "LOW"}
        if profile_metadata is not None:
            profile_metadata = {**profile_metadata, "provenance_consistent": False}
    preferred_profile = profiles.get(preferred_days) if profiles and preferred_days is not None else None
    if preferred_profile is None and profiles:
        preferred_profile = profiles[max(profiles)]
    profile_level_values = profile_levels(profiles, atr_value=atr_value)
    all_levels = tuple(ma_values.items())
    zones = build_structural_zones(
        supplied_spot.price,
        atr_value,
        moving_averages=dict(all_levels),
        swing_points=swing_lows,
        volume_levels=profile_level_values,
        kind="SUPPORT",
        zone_half_width_atr=config["zone_half_width_atr"],
        minimum_zone_separation_atr=config["minimum_zone_separation_atr"],
        maximum_zone_span_atr=config["maximum_zone_span_atr"],
    )
    resistance = build_structural_zones(
        supplied_spot.price,
        atr_value,
        moving_averages=dict(all_levels),
        swing_points=swing_highs,
        volume_levels=profile_level_values,
        kind="RESISTANCE",
        zone_half_width_atr=config["zone_half_width_atr"],
        minimum_zone_separation_atr=config["minimum_zone_separation_atr"],
        maximum_zone_span_atr=config["maximum_zone_span_atr"],
    )
    coverage = daily_coverage(candles)
    expected_date = expected_latest_completed_date(as_of_timestamp)
    latest_date = date.fromisoformat(coverage["latest_completed_date"])
    observation_lag = max(0, (expected_date - latest_date).days)
    history_sufficient = (
        len(candles) >= config["minimum_history_days"]
        and coverage["calendar_span_days"] >= config["minimum_history_days"]
    )
    cadence_valid = (
        coverage["coverage_ratio"] >= config["minimum_daily_coverage_ratio"]
        and coverage["max_gap_days"] <= config["maximum_daily_gap_days"]
    )
    market_data_fresh = observation_lag <= config["maximum_daily_candle_lag_days"]
    source_known = all(
        str(value).strip().lower() not in {"", "unknown"}
        for value in (series.source, supplied_spot.source)
    )
    volume_available = volume_reliable and relative is not None
    test_semantics = all(
        str(value).strip().lower() in {"synthetic", "test"}
        for value in (series.source, supplied_spot.source)
    )
    spot_time_valid = not supplied_legacy_spot or test_semantics
    provenance_complete = (
        series.fetched_at is not None and supplied_spot.fetched_at is not None
    ) or test_semantics
    provenance_consistent = not (
        source_known and series.source.strip().lower() != supplied_spot.source.strip().lower()
    )
    atr_available = atr_value is not None and atr_value > 0
    spot_close_gap_atr = (
        None
        if atr_value is None or atr_value <= 0
        else abs(supplied_spot.price - closes[-1]) / atr_value
    )
    data_quality_flags: list[str] = []
    if not history_sufficient:
        data_quality_flags.append("INSUFFICIENT_HISTORY")
    if coverage["missing_day_count"]:
        data_quality_flags.append("COVERAGE_GAP")
    if not cadence_valid:
        data_quality_flags.append("LARGE_CADENCE_GAP")
    if not market_data_fresh:
        data_quality_flags.append("STALE_MARKET_DATA")
    if not source_known:
        data_quality_flags.append("UNKNOWN_PROVENANCE")
    if not provenance_complete:
        data_quality_flags.append("INCOMPLETE_PROVENANCE")
    if not spot_time_valid:
        data_quality_flags.append("UNTIMESTAMPED_SPOT")
    if not volume_available:
        data_quality_flags.append("VOLUME_UNRELIABLE")
    if not atr_available:
        data_quality_flags.append("ATR_UNAVAILABLE")
    if not provenance_consistent:
        data_quality_flags.append("PROVENANCE_MISMATCH")
    if profile_source_mismatch:
        data_quality_flags.append("PROFILE_PROVENANCE_MISMATCH")
    if spot_close_gap_atr is not None and spot_close_gap_atr > config["maximum_spot_close_gap_atr"]:
        data_quality_flags.append("SPOT_CLOSE_GAP")
    retrieval_stale = False
    if as_of_was_omitted:
        retrieval_stale = any(
            (as_of_timestamp - parse_timestamp(value)).total_seconds() / 86400
            > config["max_fetched_age_days"]
            for value in (series.fetched_at, supplied_spot.fetched_at)
            if value is not None and parse_timestamp(value) <= as_of_timestamp
        )
    if retrieval_stale:
        data_quality_flags.append("STALE_RETRIEVAL")
    if (
        "SPOT_CLOSE_GAP" in data_quality_flags
        and "PROVENANCE_MISMATCH" in data_quality_flags
    ):
        data_quality_flags.append("DATA_CONFLICT")
    hard_data_failure = any(
        flag in data_quality_flags
        for flag in ("STALE_MARKET_DATA", "LARGE_CADENCE_GAP", "DATA_CONFLICT", "STALE_RETRIEVAL")
    )
    data_confidence = "LOW" if hard_data_failure or not history_sufficient or not atr_available else "MEDIUM"
    if (
        not hard_data_failure
        and history_sufficient
        and cadence_valid
        and atr_available
        and source_known
        and provenance_consistent
        and volume_available
        and provenance_complete
        and (spot_time_valid or test_semantics)
        and len(candles) >= config["preferred_history_days"]
        and not coverage["missing_day_count"]
    ):
        data_confidence = "HIGH"
    full_quality = (
        data_confidence == "HIGH"
        and not coverage["missing_day_count"]
        and not data_quality_flags
    )
    data_quality = _quality_label(data_quality_flags, full=full_quality)
    setup_quality = _setup_quality(
        zones,
        supplied_spot.price,
        atr_value,
        volume_state=_volume_state(relative, config["breakout"]["minimum_relative_volume"]),
    )
    threshold = config["volatility_atr_percent"]
    return TechnicalSnapshot(
        symbol=series.symbol,
        as_of=as_of_value,
        current_spot_price=supplied_spot.price,
        last_completed_close=closes[-1],
        history_days=len(candles),
        ma20=ma_values["MA20"],
        ma50=ma_values["MA50"],
        ma100=ma_values["MA100"],
        ma200=ma_values["MA200"],
        return_30d=calendar_lookback_return(candles, 30) if cadence_valid else None,
        return_90d=calendar_lookback_return(candles, 90) if cadence_valid else None,
        return_180d=calendar_lookback_return(candles, 180) if cadence_valid else None,
        realized_vol_30d=calendar_realized_volatility(candles, 30, annualization_days=config["volatility_annualization_days"]) if cadence_valid else None,
        realized_vol_90d=calendar_realized_volatility(candles, 90, annualization_days=config["volatility_annualization_days"]) if cadence_valid else None,
        atr14=atr_value,
        atr_percent=atr_percent,
        volume_ma20=volume_moving_average(volumes, config["volume_average_window"]) if volume_reliable else None,
        relative_volume=relative,
        history_high=high,
        distance_from_history_high=supplied_spot.price / high - 1.0,
        current_drawdown=min(0.0, supplied_spot.price / high - 1.0),
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        support_zones=zones,
        resistance_zones=resistance,
        trend_state=_trend_state(closes, dict(all_levels), supplied_spot.price),
        volatility_state=_volatility_state(atr_percent, threshold),
        volume_state=_volume_state(relative, config["breakout"]["minimum_relative_volume"]),
        technical_confidence=data_confidence,
        data_quality=data_quality,
        ohlcv_hash=used_series.ohlcv_hash,
        source=series.source,
        timeframe=series.timeframe,
        ohlcv_metadata={
            **used_series.metadata(),
            **{key: value for key, value in coverage.items() if key != "latest_completed_date"},
            "latest_completed_candle_date": latest_date.isoformat(),
            "expected_latest_completed_date": expected_date.isoformat(),
            "observation_lag_days": observation_lag,
            "as_of": as_of_value,
        },
        spot_observed_at=supplied_spot.observed_at,
        spot_source=supplied_spot.source,
        spot_fetched_at=supplied_spot.fetched_at,
        candle_count=coverage["candle_count"],
        calendar_span_days=coverage["calendar_span_days"],
        missing_day_count=coverage["missing_day_count"],
        coverage_ratio=coverage["coverage_ratio"],
        max_gap_days=coverage["max_gap_days"],
        observation_lag_days=observation_lag,
        data_confidence=data_confidence,
        setup_quality=setup_quality,
        data_quality_flags=tuple(data_quality_flags),
        history_sufficient=history_sufficient,
        market_data_fresh=market_data_fresh,
        cadence_valid=cadence_valid,
        source_known=source_known,
        spot_time_valid=spot_time_valid,
        volume_reliable=volume_available,
        spot_close_gap_atr=spot_close_gap_atr,
        provenance_consistent=provenance_consistent,
        spot_venue=supplied_spot.venue,
        spot_market=supplied_spot.market,
        spot_quote_currency=supplied_spot.quote_currency,
        volume_profile_confidence="LOW" if profile_source_mismatch else _profile_confidence(profiles),
        volume_profile_poc=preferred_profile.poc if preferred_profile else None,
        volume_profile_val=preferred_profile.value_area_low if preferred_profile else None,
        volume_profile_vah=preferred_profile.value_area_high if preferred_profile else None,
        volume_hvns=preferred_profile.high_volume_nodes if preferred_profile else (),
        volume_lvns=preferred_profile.low_volume_nodes if preferred_profile else (),
        volume_profile_summary=profile_summary,
        volume_profile_hash=preferred_profile.profile_hash if preferred_profile else None,
        volume_profile_metadata=profile_metadata,
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
    "calendar_lookback_return",
    "calendar_realized_volatility",
    "completed_candles",
    "canonical_ohlcv_hash",
    "calculate_atr",
    "calculate_realized_volatility",
    "detect_swings",
    "detect_swing_points",
    "daily_coverage",
    "expected_latest_completed_date",
    "history_position",
    "lookback_return",
    "realized_volatility",
    "relative_volume",
    "structural_confluence",
    "technical_snapshot",
    "true_range",
    "true_ranges",
    "moving_average",
    "volume_moving_average",
    "volume_ma20",
]
