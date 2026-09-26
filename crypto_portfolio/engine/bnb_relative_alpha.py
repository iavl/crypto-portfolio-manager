"""BNB/BTC relative-alpha research and the discrete BNB alpha state.

Strategy V2.3 Phase 1. BNB is the only satellite with demonstrated
BTC-relative realized return and structural ranking power; this module makes
its active tilt an asset-specific, admission-gated decision instead of the
generic market-score tactical path. The signal set and the equal-weight
ensemble rule are preregistered; the tilt (10% of the approved risky sleeve
when the ensemble is POSITIVE) is fixed before evaluation and locked behind
``satellite_alpha.BNB.tilt_enabled`` until signal admission passes on both
validation windows. No parameter is searched.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from ..models.market import OHLCVSeries
from .relative_alpha_core import (
    ENSEMBLE_THRESHOLD,
    PRICE_SIGNAL_NAMES,
    RatioPoint,
    ensemble_alpha_state,
    evaluate_signal_admission as _core_admission,
    evaluate_signals as _core_evaluate,
    growth_signals,
    price_relative_signals,
    ratio_series,
    signal_observations as _core_observations,
)

BNB_ALPHA_POSITIVE = "BNB_ALPHA_POSITIVE"
BNB_ALPHA_NEUTRAL = "BNB_ALPHA_NEUTRAL"
BNB_ALPHA_NEGATIVE = "BNB_ALPHA_NEGATIVE"

# Preregistered on-chain growth signals (caller-supplied dated series,
# point-in-time; a missing lookback stays MISSING).
# Signal name -> (structural series key, lookback days).
GROWTH_WINDOWS: Mapping[str, tuple[str, int]] = {
    "chain_tvl_growth_30d": ("chain_tvl", 30),
    "chain_tvl_growth_90d": ("chain_tvl", 90),
    "stablecoin_supply_growth_30d": ("stablecoins", 30),
    "stablecoin_supply_growth_90d": ("stablecoins", 90),
    "fees_growth_30d": ("fees", 30),
}

SIGNAL_NAMES = (*PRICE_SIGNAL_NAMES, *GROWTH_WINDOWS)

_LABEL_KEY = "forward_bnbbtc_return"


def bnbbtc_ratio_series(
    bnb_daily: OHLCVSeries, btc_daily: OHLCVSeries
) -> tuple[RatioPoint, ...]:
    """Daily BNB/BTC close ratio over the shared completed-candle dates."""
    return ratio_series(bnb_daily, btc_daily)


def bnb_growth_signals(
    series_by_name: Mapping[str, Any] | None, as_of: str | datetime
) -> dict[str, float | None]:
    """Point-in-time BNB chain growth signals (TVL, stablecoins, fees)."""
    return growth_signals(series_by_name or {}, as_of, windows=GROWTH_WINDOWS)


def bnb_relative_signals(
    ratio_points: Sequence[RatioPoint],
    as_of: str | datetime,
    growth_series: Mapping[str, Any] | None = None,
) -> dict[str, float | None]:
    """Every preregistered BNB/BTC signal at ``as_of`` from completed data."""
    signals = price_relative_signals(ratio_points, as_of)
    signals.update(bnb_growth_signals(growth_series, as_of))
    return signals


def bnb_alpha_state(
    signals: Mapping[str, float | None],
    admitted_signals: Sequence[str] = (),
) -> str:
    """Discrete BNB/BTC alpha state from the admitted-signal ensemble.

    Equal weight over the admitted signals only (each voting -1/0/+1 by
    sign, MISSING voting 0); the ensemble mean must reach the preregistered
    half-strength threshold. An empty admitted set is NEUTRAL by definition:
    nothing unadmitted ever controls a position.
    """
    unknown = sorted(set(admitted_signals) - set(SIGNAL_NAMES))
    if unknown:
        raise ValueError(
            "admitted_signals contains signals outside the preregistered BNB "
            "set: " + ", ".join(unknown)
        )
    return ensemble_alpha_state("BNB", signals, admitted_signals)


# ---------------------------------------------------------------------------
# Ranking-power evaluation (research only)
# ---------------------------------------------------------------------------


def bnb_signal_observations(
    ratio_points: Sequence[RatioPoint],
    moments: Sequence[str | datetime],
    *,
    horizons: Sequence[int] = (30, 90, 180),
    growth_series_by_moment: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Signal snapshots with forward BNB/BTC labels (evaluation truth)."""

    def signals_at(moment: datetime) -> dict[str, float | None]:
        growth = (
            (growth_series_by_moment or {}).get(
                moment.isoformat().replace("+00:00", "Z")
            )
        )
        return bnb_relative_signals(ratio_points, moment, growth)

    return _core_observations(
        ratio_points, moments, horizons=horizons,
        label_key=_LABEL_KEY, signals_at=signals_at,
    )


def evaluate_bnb_signals(
    observations: Sequence[Mapping[str, Any]],
    *,
    horizons: Sequence[int] = (30, 90, 180),
) -> dict[str, Any]:
    """Per-signal ranking power against forward BNB/BTC returns."""
    return _core_evaluate(
        observations, signal_names=SIGNAL_NAMES,
        horizons=horizons, label_key=_LABEL_KEY,
    )


def evaluate_bnb_admission(
    window_evaluations: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Unified admission rule across the validation windows."""
    return _core_admission(
        window_evaluations, signal_names=SIGNAL_NAMES,
        admitted_flag_key="bnb_tilt_admitted",
    )


__all__ = [
    "BNB_ALPHA_NEGATIVE",
    "BNB_ALPHA_NEUTRAL",
    "BNB_ALPHA_POSITIVE",
    "ENSEMBLE_THRESHOLD",
    "GROWTH_WINDOWS",
    "SIGNAL_NAMES",
    "bnb_alpha_state",
    "bnb_growth_signals",
    "bnb_relative_signals",
    "bnb_signal_observations",
    "bnbbtc_ratio_series",
    "evaluate_bnb_admission",
    "evaluate_bnb_signals",
]
