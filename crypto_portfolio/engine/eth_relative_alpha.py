"""ETH/BTC relative-alpha research and the discrete ETH tilt state.

Strategy V2.2 Phase B. The only question this module answers is whether
holding ETH beat holding BTC, and whether that is predictable:

    ETH/BTC point-in-time signals -> discrete alpha state -> tilt fraction

Everything here is preregistered: the signal set, the state rule thresholds,
and the 20%-of-core-budget tilt are fixed before evaluation; no parameter is
searched. The evaluation half (ranking power, buckets, bootstrap intervals,
admission) is research-only diagnostics and never feeds back into the
allocation engines. An unadmitted signal can never control a position: the
canonical policy keeps ``tilt_enabled=false`` until the admission rule
passes on both validation windows.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from ..models.market import OHLCVSeries
from ..models.policy import Policy, resolve_policy
from ..models.time import parse_timestamp

ETH_ALPHA_POSITIVE = "ETH_ALPHA_POSITIVE"
ETH_ALPHA_NEUTRAL = "ETH_ALPHA_NEUTRAL"
ETH_ALPHA_NEGATIVE = "ETH_ALPHA_NEGATIVE"

# Preregistered signal names. Each is computed point-in-time from completed
# daily candles only; a missing input yields a MISSING signal, never a
# neutralized fabrication.
SIGNAL_NAMES = (
    "rel_return_30d",
    "rel_return_90d",
    "rel_return_180d",
    "ma_structure",
    "momentum_persistence_90d",
    "risk_adjusted_momentum_90d",
    "relative_drawdown_180d",
    "etf_flow_differential_30d",
)

# State-rule thresholds (preregistered; do not tune to a window).
TREND_COMPOSITE_THRESHOLD = 0.03
MA_WINDOWS = (20, 50, 100)

_BOOTSTRAP_RESAMPLES = 1000
_BOOTSTRAP_SEED = 0
_MIN_INDEPENDENT_BLOCKS = 10


# ---------------------------------------------------------------------------
# Point-in-time ETH/BTC ratio series
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RatioPoint:
    timestamp: datetime
    ratio: float


def ethbtc_ratio_series(
    eth_daily: OHLCVSeries, btc_daily: OHLCVSeries
) -> tuple[RatioPoint, ...]:
    """Daily ETH/BTC close ratio over the shared completed-candle dates.

    The two daily series are joined on their candle timestamps, so a ratio
    point only exists where both candles completed. Prices are strictly
    positive by the candle contract, so every ratio is finite and positive.
    """
    if eth_daily.timeframe != btc_daily.timeframe:
        raise ValueError("ETH and BTC series must share a timeframe")
    btc_by_date = {
        parse_timestamp(candle.timestamp): candle.close
        for candle in btc_daily.completed_candles()
    }
    points: list[RatioPoint] = []
    for candle in eth_daily.completed_candles():
        moment = parse_timestamp(candle.timestamp)
        btc_close = btc_by_date.get(moment)
        if btc_close is not None:
            points.append(RatioPoint(moment, candle.close / btc_close))
    points.sort(key=lambda item: item.timestamp)
    return tuple(points)


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


def relative_signals(
    ratio_points: Sequence[RatioPoint], as_of: str | datetime
) -> dict[str, float | None]:
    """Every preregistered signal at ``as_of`` from completed data only.

    Candles after ``as_of`` are invisible; a signal whose lookback window is
    not fully available is ``MISSING`` (None), never partially computed.
    """
    moment = parse_timestamp(as_of) if not isinstance(as_of, datetime) else as_of
    closes = _closes_as_of(ratio_points, moment)
    values = [value for _, value in closes]
    result: dict[str, float | None] = {name: None for name in SIGNAL_NAMES}
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


def etf_flow_differential(
    eth_flow_to_aum: float | None, btc_flow_to_aum: float | None
) -> float | None:
    """ETH-vs-BTC normalized ETF flow differential (fraction of AUM).

    The caller produces both inputs point-in-time (the harvested ETF series
    normalized exactly like the capital-flows factor); this function only
    combines them, keeping the engine free of data-source glue.
    """
    if eth_flow_to_aum is None or btc_flow_to_aum is None:
        return None
    return float(eth_flow_to_aum) - float(btc_flow_to_aum)


# ---------------------------------------------------------------------------
# Discrete alpha state (preregistered rule)
# ---------------------------------------------------------------------------


def eth_alpha_state(signals: Mapping[str, float | None]) -> str:
    """Discrete ETH/BTC alpha state from the preregistered rule.

    POSITIVE requires the trend composite (mean of the 30/90/180-day ETH/BTC
    returns) at or above +3% AND the ratio above its 100-day moving average —
    trend and structure must agree. NEGATIVE is the mirror image. Anything
    else, including any missing input, is NEUTRAL: an unproven relative case
    never manufactures alpha in either direction.
    """
    returns = [signals.get(f"rel_return_{days}d") for days in (30, 90, 180)]
    ma_structure = signals.get("ma_structure")
    if any(value is None for value in returns) or ma_structure is None:
        return ETH_ALPHA_NEUTRAL
    composite = sum(float(value) for value in returns) / len(returns)
    above_ma100 = ma_structure > 0 or (
        ma_structure == 0 and composite > 0
    )
    if composite >= TREND_COMPOSITE_THRESHOLD and above_ma100:
        return ETH_ALPHA_POSITIVE
    if composite <= -TREND_COMPOSITE_THRESHOLD and ma_structure < 0:
        return ETH_ALPHA_NEGATIVE
    return ETH_ALPHA_NEUTRAL


def eth_tilt_fraction(alpha_state: str, policy: Policy | None = None) -> float:
    """Preregistered tilt: 20% of the core budget on POSITIVE, else zero."""
    resolved = policy or resolve_policy()
    if alpha_state == ETH_ALPHA_POSITIVE:
        return float(resolved.core_allocation["eth"]["tilt_fraction_positive"])
    if alpha_state in (ETH_ALPHA_NEUTRAL, ETH_ALPHA_NEGATIVE):
        return 0.0
    raise ValueError(
        "alpha_state must be ETH_ALPHA_POSITIVE, ETH_ALPHA_NEUTRAL, or ETH_ALPHA_NEGATIVE"
    )


# ---------------------------------------------------------------------------
# Ranking-power evaluation (research only)
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


def relative_signal_observations(
    ratio_points: Sequence[RatioPoint],
    moments: Sequence[str | datetime],
    *,
    horizons: Sequence[int] = (30, 90, 180),
) -> list[dict[str, Any]]:
    """Signal snapshots with forward ETH/BTC labels (evaluation ground truth).

    The forward label at horizon H is the ratio return between the first
    ratio point at or after ``moment + H days`` and the base point at or
    after ``moment``. Labels exist only where the future actually provides
    data; PENDING otherwise. Nothing here is visible to the decision path.
    """
    rows: list[dict[str, Any]] = []
    for raw_moment in moments:
        moment = parse_timestamp(raw_moment) if not isinstance(raw_moment, datetime) else raw_moment
        row: dict[str, Any] = {
            "timestamp": moment.isoformat().replace("+00:00", "Z"),
            "signals": relative_signals(ratio_points, moment),
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
                    "forward_ethbtc_return": endpoint.ratio / base.ratio - 1.0,
                }
        rows.append(row)
    return rows


def _independent_blocks(
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


def _bucket_means(
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


def bootstrap_mean_ci(values: Sequence[float]) -> dict[str, float | None]:
    """Seeded block-bootstrap CI of the mean; deterministic across runs."""
    return _bootstrap_ci(values)


def _bootstrap_ci(values: Sequence[float]) -> dict[str, float | None]:
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


def evaluate_relative_signals(
    observations: Sequence[Mapping[str, Any]],
    *,
    horizons: Sequence[int] = (30, 90, 180),
) -> dict[str, Any]:
    """Per-signal ranking power against forward ETH/BTC returns.

    For every signal and horizon: Spearman IC, Pearson correlation, tercile
    bucket mean returns, sign hit rate, independent block count, and a
    seeded bootstrap confidence interval over block ICs.
    """
    signals_report: dict[str, Any] = {}
    for name in SIGNAL_NAMES:
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
                (float(row["signals"][name]), _label(row)["forward_ethbtc_return"])
                for row in available
            ]
            blocks = _independent_blocks(available, horizon)
            block_pairs = [
                (float(row["signals"][name]), _label(row)["forward_ethbtc_return"])
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
            buckets = _bucket_means(pairs)
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
                "block_ic_bootstrap": _bootstrap_ci(block_ics) if block_ics else None,
            }
        signals_report[name] = horizon_report
    return {
        "contract": "STRICT_POINT_IN_TIME_INPUTS",
        "signals": signals_report,
        "observations": len(observations),
    }


def evaluate_signal_admission(
    window_evaluations: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Admission rule (plan 5.5) across the two validation windows.

    A signal is admitted only when both windows agree on direction, at least
    one of the 90D/180D horizons carries a positive IC in both windows, the
    tercile bucket means are not clearly inverted, and each window supplies
    at least 10 independent blocks. This is a research verdict: it never
    changes an allocation by itself, it only unlocks (or keeps locked) the
    ETH tilt mechanism.
    """
    window_names = sorted(window_evaluations)
    report: dict[str, Any] = {}
    for name in SIGNAL_NAMES:
        reasons: list[str] = []
        directions: list[int] = []
        blocks_ok = True
        for window in window_names:
            horizons = (window_evaluations[window].get("signals") or {}).get(name) or {}
            ic_90 = horizons.get("90", {}).get("spearman_ic")
            ic_180 = horizons.get("180", {}).get("spearman_ic")
            candidates = [value for value in (ic_90, ic_180) if value is not None]
            if not candidates:
                reasons.append(f"{window}: no 90D/180D IC available")
                directions.append(0)
                continue
            best = max(candidates, key=abs)
            directions.append(1 if best > 0 else -1 if best < 0 else 0)
            positive_ic = any(value > 0 for value in candidates)
            if not positive_ic:
                reasons.append(f"{window}: no positive IC at 90D/180D")
            for horizon, key in (("90", "90"), ("180", "180")):
                blocks = horizons.get(key, {}).get("independent_blocks", 0)
                if blocks < _MIN_INDEPENDENT_BLOCKS:
                    blocks_ok = False
                    reasons.append(f"{window}: only {blocks} independent blocks at {horizon}D")
            for horizon in ("90", "180"):
                buckets = horizons.get(horizon, {}).get("bucket_mean_forward_returns")
                if buckets and all(value is not None for value in buckets):
                    if buckets[0] is not None and buckets[-1] is not None and buckets[-1] < buckets[0]:
                        reasons.append(f"{window}: top tercile below bottom tercile at {horizon}D")
                        break
        consistent = len(directions) >= 2 and directions[0] == directions[1] and directions[0] != 0
        if not consistent:
            reasons.append("window directions disagree or are flat")
        report[name] = {
            "admitted": bool(consistent and all("no positive IC" not in r for r in reasons) and blocks_ok and not any("top tercile below" in r for r in reasons)),
            "direction": directions[0] if directions else 0,
            "reasons": reasons,
        }
    return {
        "admission_rule": (
            "both windows same direction; positive IC at 90D or 180D in both; "
            "terciles not inverted; >=10 independent blocks per window"
        ),
        "signals": report,
        "eth_tilt_admitted": bool(
            any(
                entry["admitted"] and entry["direction"] > 0
                for entry in report.values()
            )
        ),
    }


def eth_tilt_attribution(
    rows: Sequence[Mapping[str, Any]],
    reviews: Sequence[Any],
    *,
    policy: Policy | None = None,
) -> dict[str, Any]:
    """Per-review ETH tilt attribution (plan 5.8) from a replay record.

    Reconstructs, for every review the replay already produced: the alpha
    state, the tilt the state requested, the tilt the risk engine actually
    left in ETH, and the realized ETH-vs-BTC opportunity-cost return of the
    following period (evaluation ground truth only).
    """
    resolved = policy or resolve_policy()
    entries: list[dict[str, Any]] = []
    requested_total = 0.0
    final_total = 0.0
    states: dict[str, int] = {}
    for row, review in zip(rows, reviews):
        state = str(row.get("eth_alpha_state") or ETH_ALPHA_NEUTRAL)
        states[state] = states.get(state, 0) + 1
        hierarchy = ((row.get("allocation") or {}).get("risk_engine") or {}).get(
            "capital_hierarchy"
        ) or {}
        core_budget = max(
            0.0,
            float(hierarchy.get("approved_risky_budget", 0.0))
            - float(hierarchy.get("satellite_alpha_tilt", 0.0)),
        )
        requested_fraction = eth_tilt_fraction(state, resolved)
        requested_weight = requested_fraction * core_budget
        final_weight = float(hierarchy.get("eth_alpha_tilt", 0.0))
        requested_total += requested_weight
        final_total += final_weight
        label = getattr(review, "next_returns", None) or {}
        eth_return = label.get("ETH")
        btc_return = label.get("BTC")
        entries.append({
            "timestamp": row.get("as_of"),
            "eth_alpha_state": state,
            "tilt_requested_fraction": requested_fraction,
            "tilt_requested_weight": requested_weight,
            "tilt_final_weight": final_weight,
            "tilt_constrained_by_risk_engine": max(0.0, requested_weight - final_weight),
            "ethbtc_opportunity_cost_return": (
                float(eth_return) - float(btc_return)
                if eth_return is not None and btc_return is not None
                else None
            ),
        })
    return {
        "reviews": len(entries),
        "state_counts": dict(sorted(states.items())),
        "total_requested_tilt_weight": requested_total,
        "total_final_tilt_weight": final_total,
        "entries": entries,
    }


__all__ = [
    "ETH_ALPHA_NEGATIVE",
    "ETH_ALPHA_NEUTRAL",
    "ETH_ALPHA_POSITIVE",
    "bootstrap_mean_ci",
    "SIGNAL_NAMES",
    "TREND_COMPOSITE_THRESHOLD",
    "eth_alpha_state",
    "eth_tilt_attribution",
    "eth_tilt_fraction",
    "ethbtc_ratio_series",
    "etf_flow_differential",
    "evaluate_relative_signals",
    "evaluate_signal_admission",
    "pearson",
    "relative_signal_observations",
    "relative_signals",
    "spearman",
]
