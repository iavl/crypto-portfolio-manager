"""Deterministic bar-level Volume Profile calculations."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from ..models.market import OHLCVSeries
from ..models.policy import Policy, resolve_policy
from ..models.time import normalize_timestamp, parse_timestamp
from ..models.volume_profile import VolumeNode, VolumeProfile, VolumeProfileBin


_TIMEFRAME_SECONDS = {"1H": 60 * 60, "4H": 4 * 60 * 60, "1D": 24 * 60 * 60}


def timeframe_seconds(timeframe: str) -> int:
    normalized = str(timeframe).strip().upper()
    try:
        return _TIMEFRAME_SECONDS[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported OHLCV timeframe: {normalized}") from exc


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
    number = _number(value, field, minimum=1)
    if not number.is_integer():
        raise ValueError(f"{field} must be a positive integer")
    return int(number)


def _as_of(series: OHLCVSeries, value: str | datetime | None) -> str:
    if value is not None:
        raw = value.isoformat() if isinstance(value, datetime) else value
        return normalize_timestamp(raw, "as_of")
    end = parse_timestamp(series.candles[-1].timestamp) + timedelta(seconds=timeframe_seconds(series.timeframe))
    return normalize_timestamp(end.isoformat(), "as_of")


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile requires values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def timeframe_coverage(
    series: OHLCVSeries | Mapping[str, Any],
    *,
    as_of: str | datetime | None = None,
) -> dict[str, int | float]:
    """Measure completed-bar cadence without pretending gaps are candles."""
    if isinstance(series, Mapping):
        series = OHLCVSeries.from_mapping(series)
    if not isinstance(series, OHLCVSeries):
        raise ValueError("series must be an OHLCVSeries or mapping")
    candles = series.completed_candles(_as_of(series, as_of))
    if not candles:
        raise ValueError("no completed candles are available")
    interval = timeframe_seconds(series.timeframe)
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
        "timeframe_seconds": interval,
    }


def _merge_nodes(
    nodes: Iterable[VolumeNode],
    *,
    separation: float,
    atr_value: float | None,
    bin_width: float,
) -> tuple[VolumeNode, ...]:
    ordered = sorted(nodes, key=lambda node: node.midpoint)
    if not ordered:
        return ()
    threshold = (atr_value if atr_value is not None and atr_value > 0 else bin_width) * max(1.0, separation)
    merged: list[VolumeNode] = []
    for node in ordered:
        if merged and node.midpoint - merged[-1].midpoint <= threshold:
            previous = merged[-1]
            low = min(previous.price_low, node.price_low)
            high = max(previous.price_high, node.price_high)
            fraction = previous.volume_fraction + node.volume_fraction
            merged[-1] = VolumeNode(
                low,
                high,
                (low + high) / 2,
                node.kind,
                max(previous.strength, node.strength),
                fraction,
            )
        else:
            merged.append(node)
    return tuple(merged)


def _nodes(
    bins: tuple[VolumeProfileBin, ...],
    *,
    total_volume: float,
    hvn_percentile: float,
    max_hvn_nodes: int,
    minimum_node_separation_atr: float,
    atr_value: float | None,
) -> tuple[tuple[VolumeNode, ...], tuple[VolumeNode, ...]]:
    volumes = [item.volume for item in bins]
    maximum = max(volumes)
    nonzero_volumes = [volume for volume in volumes if volume > 0]
    threshold = _percentile(nonzero_volumes, hvn_percentile)
    high_candidates: list[VolumeNode] = []
    low_threshold = _percentile(volumes, 1.0 - hvn_percentile)
    low_candidates: list[VolumeNode] = []
    for index, item in enumerate(bins):
        left = volumes[index - 1] if index else None
        right = volumes[index + 1] if index + 1 < len(bins) else None
        is_edge_peak = (
            (index == 0 and item.volume > (right or 0))
            or (index == len(bins) - 1 and item.volume > (left or 0))
        )
        is_inner_peak = (
            0 < index < len(bins) - 1
            and item.volume >= left
            and item.volume >= right
            and item.volume > min(left, right)
        )
        if item.volume > 0 and item.volume >= threshold and (is_edge_peak or is_inner_peak):
            high_candidates.append(
                VolumeNode(
                    item.price_low,
                    item.price_high,
                    item.midpoint,
                    "HVN",
                    item.volume / maximum * 100 if maximum else 0,
                    item.volume / total_volume,
                )
            )
        if 0 < index < len(bins) - 1 and item.volume <= low_threshold and item.volume < max(left, right) and max(left, right) > 0:
            low_candidates.append(
                VolumeNode(
                    item.price_low,
                    item.price_high,
                    item.midpoint,
                    "LVN",
                    (1 - item.volume / maximum) * 100 if maximum else 0,
                    item.volume / total_volume,
                )
            )
    high_candidates = list(
        _merge_nodes(
            high_candidates,
            separation=minimum_node_separation_atr,
            atr_value=atr_value,
            bin_width=bins[0].price_high - bins[0].price_low,
        )
    )
    high_candidates.sort(key=lambda node: (-node.strength, -node.volume_fraction, node.midpoint))
    low_candidates = list(_merge_nodes(
        low_candidates,
        separation=minimum_node_separation_atr,
        atr_value=atr_value,
        bin_width=bins[0].price_high - bins[0].price_low,
    ))
    low_candidates.sort(key=lambda node: (node.volume_fraction, node.midpoint))
    return tuple(high_candidates[:max_hvn_nodes]), tuple(low_candidates[:max_hvn_nodes])


def build_volume_profile(
    series: OHLCVSeries | Mapping[str, Any],
    *,
    lookback_days: int = 180,
    price_bins: int = 64,
    value_area_fraction: float = 0.70,
    hvn_percentile: float = 0.75,
    max_hvn_nodes: int = 6,
    minimum_node_separation_atr: float = 0.50,
    atr_value: float | None = None,
    as_of: str | datetime | None = None,
    volume_reliable: bool = True,
    daily_approximation_confidence_cap: str = "MEDIUM",
) -> VolumeProfile | None:
    """Build a bar-typical-price volume profile using only completed bars."""
    if isinstance(series, Mapping):
        series = OHLCVSeries.from_mapping(series)
    if not isinstance(series, OHLCVSeries):
        raise ValueError("series must be an OHLCVSeries or mapping")
    lookback_days = _positive_int(lookback_days, "lookback_days")
    price_bins = _positive_int(price_bins, "price_bins")
    if not 0 < value_area_fraction <= 1:
        raise ValueError("value_area_fraction must be in (0, 1]")
    if not 0 < hvn_percentile <= 1:
        raise ValueError("hvn_percentile must be in (0, 1]")
    max_hvn_nodes = _positive_int(max_hvn_nodes, "max_hvn_nodes")
    minimum_node_separation_atr = _number(
        minimum_node_separation_atr,
        "minimum_node_separation_atr",
        minimum=0.0,
    )
    if minimum_node_separation_atr <= 0:
        raise ValueError("minimum_node_separation_atr must be > 0")
    if not isinstance(volume_reliable, bool):
        raise ValueError("volume_reliable must be boolean")
    cap = str(daily_approximation_confidence_cap).strip().upper()
    if cap not in {"LOW", "MEDIUM"}:
        raise ValueError("daily_approximation_confidence_cap must be LOW or MEDIUM")
    if not volume_reliable:
        return None
    as_of_value = _as_of(series, as_of)
    completed = list(series.completed_candles(as_of_value))
    if not completed or not any(candle.volume > 0 for candle in completed):
        return None
    end_timestamp = parse_timestamp(completed[-1].timestamp)
    start_timestamp = end_timestamp - timedelta(days=lookback_days)
    candles = [candle for candle in completed if parse_timestamp(candle.timestamp) >= start_timestamp]
    if not candles:
        return None
    low = min(candle.low for candle in candles)
    high = max(candle.high for candle in candles)
    if math.isclose(low, high):
        epsilon = max(low * 1e-9, 1e-9)
        low -= epsilon
        high += epsilon
    width = (high - low) / price_bins
    volumes = [0.0] * price_bins
    for candle in candles:
        representative = (candle.high + candle.low + candle.close) / 3
        index = min(price_bins - 1, max(0, int((representative - low) / width)))
        volumes[index] += candle.volume
    total_volume = sum(volumes)
    if total_volume <= 0:
        return None
    used_series = OHLCVSeries(
        series.symbol,
        series.timeframe,
        tuple(candles),
        source=series.source,
        fetched_at=series.fetched_at,
        venue=series.venue,
        market=series.market,
        quote_currency=series.quote_currency,
    )
    bins = tuple(
        VolumeProfileBin(
            low + index * width,
            low + (index + 1) * width,
            low + (index + 0.5) * width,
            volume,
            volume / total_volume,
        )
        for index, volume in enumerate(volumes)
    )
    poc_index = max(range(price_bins), key=lambda index: (volumes[index], -index))
    selected = {poc_index}
    cumulative = volumes[poc_index]
    target = total_volume * value_area_fraction
    while cumulative < target and len(selected) < price_bins:
        left = min(selected) - 1
        right = max(selected) + 1
        options = [index for index in (left, right) if 0 <= index < price_bins and index not in selected]
        if not options:
            break
        chosen = max(options, key=lambda index: (volumes[index], -index))
        selected.add(chosen)
        cumulative += volumes[chosen]
    val_index = min(selected)
    vah_index = max(selected)
    high_nodes, low_nodes = _nodes(
        bins,
        total_volume=total_volume,
        hvn_percentile=hvn_percentile,
        max_hvn_nodes=max_hvn_nodes,
        minimum_node_separation_atr=minimum_node_separation_atr,
        atr_value=atr_value,
    )
    timeframe = series.timeframe
    interval = timeframe_seconds(timeframe)
    expected_count = max(1, int((end_timestamp - parse_timestamp(candles[0].timestamp)).total_seconds() / interval) + 1)
    coverage = len(candles) / expected_count
    gaps = [
        int((parse_timestamp(right.timestamp) - parse_timestamp(left.timestamp)).total_seconds() / interval) - 1
        for left, right in zip(candles, candles[1:])
    ]
    max_gap = max(gaps, default=0)
    complete_lookback = parse_timestamp(candles[0].timestamp) <= start_timestamp
    material_gap = max_gap > 3
    if timeframe == "1D":
        confidence = cap if coverage >= 0.9 and not material_gap else "LOW"
    elif coverage >= 0.9 and not material_gap and complete_lookback:
        confidence = "HIGH"
    elif coverage >= 0.75 and not material_gap:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    metadata = {
        "candle_count": len(candles),
        "expected_candle_count": expected_count,
        "coverage_ratio": coverage,
        "max_gap_intervals": max_gap,
        "missing_interval_count": max(0, expected_count - len(candles)),
        "lookback_complete": complete_lookback,
        "approximation": "typical_price_per_bar",
        "as_of": as_of_value,
        "range_start": candles[0].timestamp,
        "range_end": candles[-1].timestamp,
        "source_consistent": True,
    }
    return VolumeProfile(
        symbol=series.symbol,
        as_of=as_of_value,
        timeframe=timeframe,
        lookback_days=lookback_days,
        total_volume=total_volume,
        bins=bins,
        poc=bins[poc_index].midpoint,
        value_area_low=bins[val_index].price_low,
        value_area_high=bins[vah_index].price_high,
        high_volume_nodes=high_nodes,
        low_volume_nodes=low_nodes,
        data_confidence=confidence,
        source=series.source,
        ohlcv_hash=used_series.ohlcv_hash,
        value_area_fraction=value_area_fraction,
        metadata=metadata,
    )


def build_multi_horizon_profiles(
    series: OHLCVSeries | Mapping[str, Any],
    *,
    lookback_days: Iterable[int] = (90, 180),
    policy: Policy | None = None,
    atr_value: float | None = None,
    as_of: str | datetime | None = None,
    volume_reliable: bool = True,
) -> dict[int, VolumeProfile]:
    if isinstance(series, Mapping):
        series = OHLCVSeries.from_mapping(series)
    if not isinstance(series, OHLCVSeries):
        raise ValueError("series must be an OHLCVSeries or mapping")
    config = (policy or resolve_policy()).volume_profile
    if series.timeframe == "1D" and not config["allow_daily_approximation"]:
        return {}
    values = tuple(lookback_days)
    if not values:
        raise ValueError("lookback_days must not be empty")
    result: dict[int, VolumeProfile] = {}
    for days in values:
        profile = build_volume_profile(
            series,
            lookback_days=days,
            price_bins=config["price_bins"],
            value_area_fraction=config["value_area_fraction"],
            hvn_percentile=config["hvn_percentile"],
            max_hvn_nodes=config["max_hvn_nodes"],
            minimum_node_separation_atr=config["minimum_node_separation_atr"],
            atr_value=atr_value,
            as_of=as_of,
            volume_reliable=volume_reliable,
            daily_approximation_confidence_cap=config["daily_approximation_confidence_cap"],
        )
        if profile is not None:
            result[days] = profile
    return result


def profile_levels(
    profiles: Mapping[int, VolumeProfile] | Iterable[VolumeProfile],
    *,
    atr_value: float | None = None,
) -> tuple[tuple[float, str, float], ...]:
    values = tuple(profiles.values()) if isinstance(profiles, Mapping) else tuple(profiles)
    entries: list[tuple[float, str, float, int]] = []
    for profile_index, profile in enumerate(values):
        entries.extend(
            (
                (profile.poc, "VOLUME_POC", 70.0, profile_index),
                (profile.value_area_low, "VOLUME_VAL", 45.0, profile_index),
                (profile.value_area_high, "VOLUME_VAH", 45.0, profile_index),
            )
        )
        entries.extend(
            (node.midpoint, "VOLUME_HVN", node.strength, profile_index)
            for node in profile.high_volume_nodes
        )
    levels = [(price, source, strength) for price, source, strength, _ in entries]
    if len(values) > 1:
        default_tolerance = min(
            (profile.bins[0].price_high - profile.bins[0].price_low for profile in values),
            default=0.0,
        )
        tolerance = atr_value * 0.5 if atr_value is not None and atr_value > 0 else default_tolerance
        for price, source, strength, profile_index in entries:
            if any(
                other_index != profile_index
                and abs(price - other_price) <= tolerance
                for other_price, _other_source, _, other_index in entries
            ):
                levels.append((price, "VOLUME_PROFILE_CONFLUENCE", min(100.0, strength + 10.0)))
    return tuple(levels)


calculate_volume_profile = build_volume_profile
build_volume_profiles = build_multi_horizon_profiles


__all__ = [
    "build_multi_horizon_profiles",
    "build_volume_profile",
    "build_volume_profiles",
    "calculate_volume_profile",
    "profile_levels",
    "timeframe_coverage",
    "timeframe_seconds",
]
