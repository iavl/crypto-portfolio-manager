#!/usr/bin/env python3
"""Small dependency-free metrics helpers for medium-term crypto portfolio analysis.

These functions intentionally avoid fetching market data. The agent/CLI layer is expected
to supply fresh observations from appropriate sources.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence


def _clean(values: Iterable[float]) -> list[float]:
    out = [float(v) for v in values]
    if not out:
        raise ValueError("at least one value is required")
    if any(not math.isfinite(v) for v in out):
        raise ValueError("values must be finite")
    return out


def simple_return(start: float, end: float) -> float:
    start = float(start)
    end = float(end)
    if start <= 0:
        raise ValueError("start must be > 0")
    return end / start - 1.0


def period_returns(prices: Sequence[float]) -> list[float]:
    p = _clean(prices)
    if len(p) < 2:
        return []
    if any(v <= 0 for v in p):
        raise ValueError("prices must be > 0")
    return [p[i] / p[i - 1] - 1.0 for i in range(1, len(p))]


def max_drawdown(values: Sequence[float]) -> float:
    """Return maximum drawdown as a negative fraction, e.g. -0.20."""
    v = _clean(values)
    if any(x <= 0 for x in v):
        raise ValueError("values must be > 0")
    peak = v[0]
    worst = 0.0
    for x in v:
        peak = max(peak, x)
        dd = x / peak - 1.0
        worst = min(worst, dd)
    return worst


def current_drawdown(values: Sequence[float]) -> float:
    v = _clean(values)
    if any(x <= 0 for x in v):
        raise ValueError("values must be > 0")
    peak = max(v)
    return v[-1] / peak - 1.0


def annualized_volatility(prices: Sequence[float], periods_per_year: int = 365) -> float:
    """Annualized realized volatility from equally spaced price observations."""
    returns = period_returns(prices)
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year)


def moving_average(prices: Sequence[float], window: int) -> float:
    p = _clean(prices)
    if window <= 0:
        raise ValueError("window must be > 0")
    if len(p) < window:
        raise ValueError(f"need at least {window} prices")
    return sum(p[-window:]) / window


def weighted_score(factor_scores: dict[str, float], weights: dict[str, float]) -> float:
    """Compute a missing-data-aware 0-100 weighted score.

    Only factors present in both dictionaries are used. Their weights are renormalized.
    """
    common = [k for k in weights if k in factor_scores and factor_scores[k] is not None]
    if not common:
        raise ValueError("no scored factors available")
    for k in common:
        s = float(factor_scores[k])
        if not 0 <= s <= 100:
            raise ValueError(f"factor {k!r} score must be in [0, 100]")
    total_weight = sum(float(weights[k]) for k in common)
    if total_weight <= 0:
        raise ValueError("sum of available weights must be > 0")
    return sum(float(factor_scores[k]) * float(weights[k]) for k in common) / total_weight


def portfolio_weighted_return(weights: dict[str, float], returns: dict[str, float]) -> float:
    common = set(weights) & set(returns)
    if not common:
        raise ValueError("no overlapping assets")
    total = sum(float(weights[k]) for k in common)
    if total <= 0:
        raise ValueError("overlapping weight must be > 0")
    return sum(float(weights[k]) * float(returns[k]) for k in common) / total


def benchmark_70_30(btc_return: float, eth_return: float) -> float:
    return 0.7 * float(btc_return) + 0.3 * float(eth_return)
