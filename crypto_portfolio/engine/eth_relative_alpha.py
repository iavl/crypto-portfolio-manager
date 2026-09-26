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

Strategy V2.3 Phase 1 moved the shared machinery (ratio series, price
signals, observations, statistics, admission rule) into
``relative_alpha_core``; this module keeps only the ETH-specific contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from ..models.market import OHLCVSeries
from ..models.policy import Policy, resolve_policy
from .relative_alpha_core import (
    RatioPoint,
    bootstrap_mean_ci,
    evaluate_signal_admission as _core_admission,
    evaluate_signals as _core_evaluate,
    pearson,
    price_relative_signals,
    ratio_series,
    signal_observations as _core_observations,
    spearman,
)

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

_LABEL_KEY = "forward_ethbtc_return"


# ---------------------------------------------------------------------------
# Point-in-time ETH/BTC ratio series
# ---------------------------------------------------------------------------


def ethbtc_ratio_series(
    eth_daily: OHLCVSeries, btc_daily: OHLCVSeries
) -> tuple[RatioPoint, ...]:
    """Daily ETH/BTC close ratio over the shared completed-candle dates."""
    return ratio_series(eth_daily, btc_daily)


def relative_signals(
    ratio_points: Sequence[RatioPoint], as_of: str | datetime
) -> dict[str, float | None]:
    """Every preregistered signal at ``as_of`` from completed data only.

    The ETF flow differential defaults to MISSING (None): only the
    observation builder with a caller-supplied series can ever fill it.
    """
    signals = price_relative_signals(ratio_points, as_of)
    signals.setdefault("etf_flow_differential_30d", None)
    return signals


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


def relative_signal_observations(
    ratio_points: Sequence[RatioPoint],
    moments: Sequence[str | datetime],
    *,
    horizons: Sequence[int] = (30, 90, 180),
    etf_differential_by_moment: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Signal snapshots with forward ETH/BTC labels (evaluation truth).

    ``etf_differential_by_moment`` carries a caller-computed point-in-time
    ETF flow differential keyed by the normalized moment string; the engine
    stays free of data-source glue. Labels exist only where the future
    actually provides data; PENDING otherwise. Nothing here is visible to
    the decision path.
    """

    def signals_at(moment: datetime) -> dict[str, float | None]:
        signals = relative_signals(ratio_points, moment)
        if etf_differential_by_moment is not None:
            key = moment.isoformat().replace("+00:00", "Z")
            if key in etf_differential_by_moment:
                signals["etf_flow_differential_30d"] = etf_differential_by_moment[key]
        return signals

    return _core_observations(
        ratio_points, moments, horizons=horizons,
        label_key=_LABEL_KEY, signals_at=signals_at,
    )


def evaluate_relative_signals(
    observations: Sequence[Mapping[str, Any]],
    *,
    horizons: Sequence[int] = (30, 90, 180),
) -> dict[str, Any]:
    """Per-signal ranking power against forward ETH/BTC returns."""
    return _core_evaluate(
        observations, signal_names=SIGNAL_NAMES,
        horizons=horizons, label_key=_LABEL_KEY,
    )


def evaluate_signal_admission(
    window_evaluations: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Admission rule across the two validation windows (plan 5.5)."""
    return _core_admission(
        window_evaluations, signal_names=SIGNAL_NAMES,
        admitted_flag_key="eth_tilt_admitted",
    )


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
