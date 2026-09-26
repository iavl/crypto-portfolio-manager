"""AAVE/BTC relative-alpha research and the discrete AAVE alpha state.

Strategy V2.3 Phase 1. AAVE showed partial structural signal but the generic
tactical allocation proved no value; this module makes any AAVE tilt an
asset-specific, admission-gated decision built from its own protocol
evidence (TVL, borrows, fees) plus the AAVE/BTC price relative. The signal
set and the equal-weight ensemble rule are preregistered; the tilt (10% of
the approved risky sleeve when the ensemble is POSITIVE) is locked behind
``satellite_alpha.AAVE.tilt_enabled`` until signal admission passes on both
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

AAVE_ALPHA_POSITIVE = "AAVE_ALPHA_POSITIVE"
AAVE_ALPHA_NEUTRAL = "AAVE_ALPHA_NEUTRAL"
AAVE_ALPHA_NEGATIVE = "AAVE_ALPHA_NEGATIVE"

# Preregistered protocol growth signals (caller-supplied dated series,
# point-in-time; a missing lookback stays MISSING).
# Signal name -> (structural series key, lookback days).
GROWTH_WINDOWS: Mapping[str, tuple[str, int]] = {
    "protocol_tvl_growth_90d": ("tvl", 90),
    "protocol_tvl_growth_180d": ("tvl", 180),
    "borrow_growth_90d": ("borrowed", 90),
    "fees_growth_30d": ("fees", 30),
}

SIGNAL_NAMES = (*PRICE_SIGNAL_NAMES, *GROWTH_WINDOWS)

_LABEL_KEY = "forward_aavebtc_return"


def aavebtc_ratio_series(
    aave_daily: OHLCVSeries, btc_daily: OHLCVSeries
) -> tuple[RatioPoint, ...]:
    """Daily AAVE/BTC close ratio over the shared completed-candle dates."""
    return ratio_series(aave_daily, btc_daily)


def aave_growth_signals(
    series_by_name: Mapping[str, Any] | None, as_of: str | datetime
) -> dict[str, float | None]:
    """Point-in-time AAVE protocol growth signals (TVL, borrows, fees)."""
    return growth_signals(series_by_name or {}, as_of, windows=GROWTH_WINDOWS)


def aave_relative_signals(
    ratio_points: Sequence[RatioPoint],
    as_of: str | datetime,
    growth_series: Mapping[str, Any] | None = None,
) -> dict[str, float | None]:
    """Every preregistered AAVE/BTC signal at ``as_of`` from completed data."""
    signals = price_relative_signals(ratio_points, as_of)
    signals.update(aave_growth_signals(growth_series, as_of))
    return signals


def aave_alpha_state(
    signals: Mapping[str, float | None],
    admitted_signals: Sequence[str] = (),
) -> str:
    """Discrete AAVE/BTC alpha state from the admitted-signal ensemble."""
    unknown = sorted(set(admitted_signals) - set(SIGNAL_NAMES))
    if unknown:
        raise ValueError(
            "admitted_signals contains signals outside the preregistered AAVE "
            "set: " + ", ".join(unknown)
        )
    return ensemble_alpha_state("AAVE", signals, admitted_signals)


# ---------------------------------------------------------------------------
# Ranking-power evaluation (research only)
# ---------------------------------------------------------------------------


def aave_signal_observations(
    ratio_points: Sequence[RatioPoint],
    moments: Sequence[str | datetime],
    *,
    horizons: Sequence[int] = (30, 90, 180),
    growth_series_by_moment: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Signal snapshots with forward AAVE/BTC labels (evaluation truth)."""

    def signals_at(moment: datetime) -> dict[str, float | None]:
        growth = (
            (growth_series_by_moment or {}).get(
                moment.isoformat().replace("+00:00", "Z")
            )
        )
        return aave_relative_signals(ratio_points, moment, growth)

    return _core_observations(
        ratio_points, moments, horizons=horizons,
        label_key=_LABEL_KEY, signals_at=signals_at,
    )


def evaluate_aave_signals(
    observations: Sequence[Mapping[str, Any]],
    *,
    horizons: Sequence[int] = (30, 90, 180),
) -> dict[str, Any]:
    """Per-signal ranking power against forward AAVE/BTC returns."""
    return _core_evaluate(
        observations, signal_names=SIGNAL_NAMES,
        horizons=horizons, label_key=_LABEL_KEY,
    )


def evaluate_aave_admission(
    window_evaluations: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Unified admission rule across the validation windows."""
    return _core_admission(
        window_evaluations, signal_names=SIGNAL_NAMES,
        admitted_flag_key="aave_tilt_admitted",
    )


__all__ = [
    "AAVE_ALPHA_NEGATIVE",
    "AAVE_ALPHA_NEUTRAL",
    "AAVE_ALPHA_POSITIVE",
    "ENSEMBLE_THRESHOLD",
    "GROWTH_WINDOWS",
    "SIGNAL_NAMES",
    "aave_alpha_state",
    "aave_growth_signals",
    "aave_relative_signals",
    "aave_signal_observations",
    "aavebtc_ratio_series",
    "evaluate_aave_admission",
    "evaluate_aave_signals",
]
