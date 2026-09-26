"""Shared point-in-time asset/BTC relative-alpha machinery.

Strategy V2.3 Phase 1. One implementation of the ratio series, the price
signals, observation/label construction, ranking-power evaluation, and the
preregistered admission rule. ETH (Strategy V2.2), BNB, and AAVE define
their own signal sets and discrete state rules on top of this core; nothing
here is asset-specific. Everything is computed from completed daily candles
only, a missing input stays MISSING (None), and no evaluation output feeds
back into the allocation engines.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from ..models.market import OHLCVSeries
from ..models.time import parse_timestamp

# Preregistered ensemble rule: an admitted-signal ensemble in [-1, +1] must
# reach at least half strength before it may tilt in either direction.
ENSEMBLE_THRESHOLD = 0.5

PRICE_SIGNAL_NAMES = (
    "rel_return_30d",
    "rel_return_90d",
    "rel_return_180d",
    "ma_structure",
    "momentum_persistence_90d",
    "risk_adjusted_momentum_90d",
    "relative_drawdown_180d",
)

MA_WINDOWS = (20, 50, 100)

_BOOTSTRAP_RESAMPLES = 1000
_BOOTSTRAP_SEED = 0
_MIN_INDEPENDENT_BLOCKS = 10


# ---------------------------------------------------------------------------
# Point-in-time asset/BTC ratio series
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RatioPoint:
    timestamp: datetime
    ratio: float


def ratio_series(
    asset_daily: OHLCVSeries, btc_daily: OHLCVSeries
) -> tuple[RatioPoint, ...]:
    """Daily asset/BTC close ratio over the shared completed-candle dates.

    The two daily series are joined on their candle timestamps, so a ratio
    point only exists where both candles completed. Prices are strictly
    positive by the candle contract, so every ratio is finite and positive.
    """
    if asset_daily.timeframe != btc_daily.timeframe:
        raise ValueError("asset and BTC series must share a timeframe")
    btc_by_date = {
        parse_timestamp(candle.timestamp): candle.close
        for candle in btc_daily.completed_candles()
    }
    points: list[RatioPoint] = []
    for candle in asset_daily.completed_candles():
        moment = parse_timestamp(candle.timestamp)
        btc_close = btc_by_date.get(moment)
        if btc_close is not None:
            points.append(RatioPoint(moment, candle.close / btc_close))
    points.sort(key=lambda item: item.timestamp)
    return tuple(points)


def asset_alpha_state_names(symbol: str) -> tuple[str, str, str]:
    """(POSITIVE, NEUTRAL, NEGATIVE) state names for one asset's alpha."""
    name = str(symbol).strip().upper()
    if not name:
        raise ValueError("symbol must be non-empty")
    return (f"{name}_ALPHA_POSITIVE", f"{name}_ALPHA_NEUTRAL", f"{name}_ALPHA_NEGATIVE")


def _closes_as_of(points: Sequence[RatioPoint], as_of: datetime) -> list[tuple[datetime, float]]:
    return [(item.timestamp, item.ratio) for item in points if item.timestamp <= as_of]


def _window_return(closes: Sequence[tuple[datetime, float]], days: int) -> float | None:
    if len(closes) < 2:
        return None
    horizon = timedelta(days=days)
    latest_moment, latest = closes[-1]
    target = latest_moment - horizon
    prior = next((value for moment, value in reversed(closes) if moment <= target), None)
    if prior is None:
        return None
    return latest / prior - 1.0


def _moving_average(closes: Sequence[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def price_relative_signals(
    ratio_points: Sequence[RatioPoint], as_of: str | datetime
) -> dict[str, float | None]:
    """Preregistered price signals at ``as_of`` from completed data only.

    Candles after ``as_of`` are invisible; a signal whose lookback window is
    not fully available is MISSING (None), never partially computed.
    """
    moment = parse_timestamp(as_of) if not isinstance(as_of, datetime) else as_of
    closes = _closes_as_of(ratio_points, moment)
    values = [value for _, value in closes]
    result: dict[str, float | None] = {name: None for name in PRICE_SIGNAL_NAMES}
    if len(closes) < 2:
        return result
    for days in (30, 90, 180):
        result[f"rel_return_{days}d"] = _window_return(closes, days)
    above = 0
    known = 0
    for window in MA_WINDOWS:
        average = _moving_average(values, window)
        if average is not None:
            known += 1
            if values[-1] > average:
                above += 1
    if known == len(MA_WINDOWS):
        # Net MA position in [-1, 1]: all windows above is +1, all below -1.
        result["ma_structure"] = (above - (len(MA_WINDOWS) - above)) / len(MA_WINDOWS)
    window = [value for _, value in closes[-90:]]
    if len(window) >= 60:
        changes = [later / earlier - 1.0 for earlier, later in zip(window, window[1:])]
        positive = sum(1 for change in changes if change > 0)
        result["momentum_persistence_90d"] = positive / len(changes)
        mean = sum(changes) / len(changes)
        variance = sum((change - mean) ** 2 for change in changes) / max(len(changes) - 1, 1)
        deviation = math.sqrt(variance)
        if deviation > 0:
            result["risk_adjusted_momentum_90d"] = mean / deviation * math.sqrt(365.25)
    if len(values) >= 2:
        high = max(values[-180:]) if len(values) >= 180 else max(values)
        if high > 0:
            result["relative_drawdown_180d"] = values[-1] / high - 1.0
    return result


def growth_signals(
    series_by_name: Mapping[str, Any],
    as_of: str | datetime,
    *,
    windows: Mapping[str, tuple[str, int]],
) -> dict[str, float | None]:
    """Point-in-time growth signals from dated observation series.

    Each entry of ``windows`` maps a signal name to ``(series_key, days)``;
    ``series_by_name`` is keyed by series key and its objects only need
    ``change_over_days(as_of, days)``. A missing series or an incomplete
    lookback stays MISSING (None).
    """
    result: dict[str, float | None] = {}
    for name, (series_key, days) in windows.items():
        series = series_by_name.get(series_key)
        try:
            result[name] = series.change_over_days(str(as_of), days) if series is not None else None
        except ValueError:
            result[name] = None
    return result


def ensemble_alpha_state(
    symbol: str,
    signals: Mapping[str, float | None],
    admitted_signals: Sequence[str],
) -> str:
    """Discrete state of the equal-weight admitted-signal ensemble.

    Each admitted signal votes -1 / 0 / +1 by the sign of its value; a
    MISSING value votes 0 (missing data pulls toward neutral, it never
    manufactures alpha). The ensemble mean must reach the preregistered
    half-strength threshold before the state may leave NEUTRAL, and an
    empty admitted set is NEUTRAL by definition — nothing unadmitted ever
    controls a position.
    """
    positive, neutral, negative = asset_alpha_state_names(symbol)
    admitted = tuple(str(name) for name in admitted_signals)
    if not admitted:
        return neutral
    votes: list[float] = []
    for name in admitted:
        value = signals.get(name)
        if value is None:
            votes.append(0.0)
            continue
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"signal {name} is not finite")
        votes.append(1.0 if value > 0 else -1.0 if value < 0 else 0.0)
    mean = sum(votes) / len(votes)
    if mean >= ENSEMBLE_THRESHOLD:
        return positive
    if mean <= -ENSEMBLE_THRESHOLD:
        return negative
    return neutral


# ---------------------------------------------------------------------------
# Ranking-power statistics (research only)
# ---------------------------------------------------------------------------


def _ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + 1 + end) / 2.0
        for original, _ in ordered[index:end]:
            result[original] = rank
        index = end
    return result


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3 or len(set(left)) < 2 or len(set(right)) < 2:
        return None
    mean_left, mean_right = sum(left) / len(left), sum(right) / len(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    denominator = math.sqrt(
        sum((a - mean_left) ** 2 for a in left)
        * sum((b - mean_right) ** 2 for b in right)
    )
    return numerator / denominator if denominator > 0 else None


def spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    return _correlation(_ranks(list(left)), _ranks(list(right)))


def pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    return _correlation(list(left), list(right))


def bootstrap_mean_ci(values: Sequence[float]) -> dict[str, float | None]:
    """Seeded block-bootstrap CI of the mean; deterministic across runs."""
    if not values:
        return {"lower": None, "upper": None, "mean": None}
    generator = random.Random(_BOOTSTRAP_SEED)
    means: list[float] = []
    for _ in range(_BOOTSTRAP_RESAMPLES):
        sample = [values[generator.randrange(len(values))] for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    def percentile(fraction: float) -> float:
        position = fraction * (len(means) - 1)
        lower = int(math.floor(position))
        upper = min(lower + 1, len(means) - 1)
        return means[lower] + (means[upper] - means[lower]) * (position - lower)
    return {
        "lower": percentile(0.025),
        "upper": percentile(0.975),
        "mean": sum(values) / len(values),
    }


def independent_blocks(
    rows: Sequence[Mapping[str, Any]], horizon: int
) -> list[Mapping[str, Any]]:
    ordered = sorted(rows, key=lambda item: parse_timestamp(item["timestamp"]))
    blocks: list[Mapping[str, Any]] = []
    last: datetime | None = None
    for row in ordered:
        moment = parse_timestamp(row["timestamp"])
        if last is None or moment >= last + timedelta(days=horizon):
            blocks.append(row)
            last = moment
    return blocks


def bucket_means(
    pairs: Sequence[tuple[float, float]], buckets: int = 3
) -> list[float | None]:
    ordered = sorted(pairs, key=lambda item: item[0])
    if not ordered:
        return [None] * buckets
    means: list[float | None] = []
    size = len(ordered) / buckets
    for index in range(buckets):
        start = int(round(index * size))
        end = int(round((index + 1) * size))
        members = ordered[start:end]
        means.append(
            sum(value for _, value in members) / len(members) if members else None
        )
    return means


def signal_observations(
    ratio_points: Sequence[RatioPoint],
    moments: Sequence[str | datetime],
    *,
    horizons: Sequence[int] = (30, 90, 180),
    label_key: str,
    signals_at: Any = None,
) -> list[dict[str, Any]]:
    """Signal snapshots with forward relative-return labels (ground truth).

    ``signals_at(moment)`` (when supplied) returns the full signal mapping at
    that moment — price signals merged with any caller-computed fundamental
    signals — keeping this core free of data-source glue. The forward label
    at horizon H is the ratio return between the first ratio point at or
    after ``moment + H days`` and the base point at or after ``moment``.
    Labels exist only where the future actually provides data; PENDING
    otherwise. Nothing here is visible to the decision path.
    """
    rows: list[dict[str, Any]] = []
    for raw_moment in moments:
        moment = parse_timestamp(raw_moment) if not isinstance(raw_moment, datetime) else raw_moment
        if signals_at is not None:
            signals = dict(signals_at(moment))
        else:
            signals = price_relative_signals(ratio_points, moment)
        row: dict[str, Any] = {
            "timestamp": moment.isoformat().replace("+00:00", "Z"),
            "signals": signals,
            "labels": {},
        }
        for raw_horizon in horizons:
            horizon = int(raw_horizon)
            target = moment + timedelta(days=horizon)
            base = next((item for item in ratio_points if item.timestamp >= moment), None)
            endpoint = next((item for item in ratio_points if item.timestamp >= target), None)
            if base is None or endpoint is None:
                row["labels"][str(horizon)] = {"status": "PENDING"}
            else:
                row["labels"][str(horizon)] = {
                    "status": "AVAILABLE",
                    label_key: endpoint.ratio / base.ratio - 1.0,
                }
        rows.append(row)
    return rows


def evaluate_signals(
    observations: Sequence[Mapping[str, Any]],
    *,
    signal_names: Sequence[str],
    horizons: Sequence[int] = (30, 90, 180),
    label_key: str,
) -> dict[str, Any]:
    """Per-signal ranking power against forward relative returns.

    For every signal and horizon: Spearman IC, Pearson correlation, tercile
    bucket mean returns, sign hit rate, independent block count, and a
    seeded bootstrap confidence interval over block ICs.
    """
    signals_report: dict[str, Any] = {}
    for name in signal_names:
        horizon_report: dict[str, Any] = {}
        for raw_horizon in horizons:
            horizon = int(raw_horizon)
            def _label(row: Mapping[str, Any]) -> Mapping[str, Any]:
                return row["labels"].get(str(horizon)) or {"status": "PENDING"}

            available = [
                row for row in observations
                if _label(row)["status"] == "AVAILABLE"
                and row["signals"].get(name) is not None
            ]
            pairs = [
                (float(row["signals"][name]), _label(row)[label_key])
                for row in available
            ]
            blocks = independent_blocks(available, horizon)
            block_pairs = [
                (float(row["signals"][name]), _label(row)[label_key])
                for row in blocks
            ]
            block_ics = [
                value for value in (
                    spearman(
                        [pair[0] for pair in block_pairs[i:i + max(4, len(block_pairs) // 4)]],
                        [pair[1] for pair in block_pairs[i:i + max(4, len(block_pairs) // 4)]],
                    )
                    for i in range(0, len(block_pairs), max(4, len(block_pairs) // 4))
                )
                if value is not None
            ] if len(block_pairs) >= 8 else []
            xs = [pair[0] for pair in pairs]
            ys = [pair[1] for pair in pairs]
            buckets = bucket_means(pairs)
            hits = sum(
                1
                for signal_value, forward in pairs
                if (signal_value > 0 and forward > 0) or (signal_value < 0 and forward < 0)
            )
            horizon_report[str(horizon)] = {
                "samples": len(pairs),
                "independent_blocks": len(blocks),
                "spearman_ic": spearman(xs, ys),
                "pearson": pearson(xs, ys),
                "bucket_mean_forward_returns": buckets,
                "bucket_monotonic": (
                    all(
                        later is not None and earlier is not None and later >= earlier - 1e-12
                        for earlier, later in zip(buckets, buckets[1:])
                    )
                    if len(buckets) >= 3 and all(value is not None for value in buckets)
                    else None
                ),
                "sign_hit_rate": hits / len(pairs) if pairs else None,
                "block_ic_bootstrap": bootstrap_mean_ci(block_ics) if block_ics else None,
            }
        signals_report[name] = horizon_report
    return {
        "contract": "STRICT_POINT_IN_TIME_INPUTS",
        "signals": signals_report,
        "observations": len(observations),
    }


def evaluate_signal_admission(
    window_evaluations: Mapping[str, Mapping[str, Any]],
    *,
    signal_names: Sequence[str],
    admitted_flag_key: str,
) -> dict[str, Any]:
    """Unified admission rule across the validation windows.

    The 90D horizon is the decision horizon — it matches the strategy's
    3-6 month active-allocation horizon, and a window of a few years
    structurally cannot supply ten independent 180D blocks. A signal is
    admitted when both windows agree on the 90D direction, the 90D IC is
    positive in both windows, the 90D tercile means are not inverted, and
    each window supplies at least ten independent 90D blocks. The 180D
    horizon is reported as confirmation only. This is a research verdict:
    it never changes an allocation by itself, it only unlocks (or keeps
    locked) the asset's tilt mechanism.
    """
    window_names = sorted(window_evaluations)
    report: dict[str, Any] = {}
    for name in signal_names:
        reasons: list[str] = []
        directions: list[int] = []
        for window in window_names:
            horizons = (window_evaluations[window].get("signals") or {}).get(name) or {}
            primary = horizons.get("90", {})
            ic_90 = primary.get("spearman_ic")
            blocks = primary.get("independent_blocks", 0)
            if ic_90 is None:
                reasons.append(f"{window}: no 90D IC available")
                directions.append(0)
                continue
            directions.append(1 if ic_90 > 0 else -1 if ic_90 < 0 else 0)
            if ic_90 <= 0:
                reasons.append(f"{window}: 90D IC is not positive")
            if blocks < _MIN_INDEPENDENT_BLOCKS:
                reasons.append(
                    f"{window}: only {blocks} independent 90D blocks"
                )
            buckets = primary.get("bucket_mean_forward_returns")
            if (
                buckets
                and buckets[0] is not None and buckets[-1] is not None
                and buckets[-1] < buckets[0]
            ):
                reasons.append(f"{window}: 90D top tercile below bottom tercile")
        consistent = len(directions) >= 2 and directions[0] == directions[1] and directions[0] != 0
        if not consistent:
            reasons.append("window 90D directions disagree or are flat")
        report[name] = {
            "admitted": bool(consistent and not reasons),
            "direction": directions[0] if directions else 0,
            "reasons": reasons,
        }
    return {
        "admission_rule": (
            "both windows: positive 90D IC, same 90D direction, terciles not "
            "inverted, >=10 independent 90D blocks; 180D reported as confirmation"
        ),
        "signals": report,
        admitted_flag_key: bool(
            any(
                entry["admitted"] and entry["direction"] > 0
                for entry in report.values()
            )
        ),
    }


__all__ = [
    "ENSEMBLE_THRESHOLD",
    "MA_WINDOWS",
    "PRICE_SIGNAL_NAMES",
    "RatioPoint",
    "asset_alpha_state_names",
    "bootstrap_mean_ci",
    "bucket_means",
    "ensemble_alpha_state",
    "evaluate_signal_admission",
    "evaluate_signals",
    "growth_signals",
    "independent_blocks",
    "pearson",
    "price_relative_signals",
    "ratio_series",
    "signal_observations",
    "spearman",
]
