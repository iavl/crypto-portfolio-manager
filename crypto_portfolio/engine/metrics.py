"""Dependency-free deterministic portfolio metrics."""

from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence


def _clean(values: Iterable[float]) -> list[float]:
    raw_values = list(values)
    if any(isinstance(value, bool) for value in raw_values):
        raise ValueError("values must not contain booleans")
    result = [float(value) for value in raw_values]
    if not result:
        raise ValueError("at least one value is required")
    if any(not math.isfinite(value) for value in result):
        raise ValueError("values must be finite")
    return result


def simple_return(start: float, end: float) -> float:
    start = float(start)
    end = float(end)
    if not math.isfinite(start) or not math.isfinite(end) or start <= 0 or end < 0:
        raise ValueError("start must be finite and > 0 and end must be >= 0")
    return end / start - 1.0


def annualized_futures_basis(futures_price: float, index_price: float, seconds_to_expiry: float) -> float:
    """Simple ACT/365 delivery basis, as a signed decimal fraction."""
    futures_price, index_price, seconds_to_expiry = _clean(
        (futures_price, index_price, seconds_to_expiry)
    )
    if min(futures_price, index_price, seconds_to_expiry) <= 0:
        raise ValueError("basis prices and remaining expiry must be positive")
    result = (futures_price / index_price - 1.0) * (365 * 86400 / seconds_to_expiry)
    if not math.isfinite(result):
        raise ValueError("annualized basis must be finite")
    return result


def period_returns(prices: Sequence[float]) -> list[float]:
    values = _clean(prices)
    if len(values) < 2:
        return []
    if any(value <= 0 for value in values):
        raise ValueError("prices must be > 0")
    return [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]


def max_drawdown(values: Sequence[float]) -> float:
    """Return maximum drawdown as a negative fraction, e.g. -0.20."""
    cleaned = _clean(values)
    if any(value <= 0 for value in cleaned):
        raise ValueError("values must be > 0")
    peak = cleaned[0]
    worst = 0.0
    for value in cleaned:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst


def current_drawdown(values: Sequence[float]) -> float:
    cleaned = _clean(values)
    if any(value <= 0 for value in cleaned):
        raise ValueError("values must be > 0")
    return cleaned[-1] / max(cleaned) - 1.0


def annualized_volatility(prices: Sequence[float], periods_per_year: int = 365) -> float:
    if isinstance(periods_per_year, bool) or periods_per_year <= 0:
        raise ValueError("periods_per_year must be > 0")
    returns = period_returns(prices)
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year)


def moving_average(prices: Sequence[float], window: int) -> float:
    values = _clean(prices)
    if isinstance(window, bool) or window <= 0:
        raise ValueError("window must be > 0")
    if len(values) < window:
        raise ValueError(f"need at least {window} prices")
    return sum(values[-window:]) / window


def weighted_score(factor_scores: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Compute a missing-data-aware 0–100 score with weight renormalization."""
    common = [key for key in weights if key in factor_scores and factor_scores[key] is not None]
    if not common:
        raise ValueError("no scored factors available")
    for key in common:
        score = float(factor_scores[key])
        if not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError(f"factor {key!r} score must be in [0, 100]")
    available_weights = []
    for key in common:
        weight = float(weights[key])
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"weight {key!r} must be finite and >= 0")
        available_weights.append(weight)
    total_weight = sum(available_weights)
    if total_weight <= 0:
        raise ValueError("sum of available weights must be > 0")
    return sum(float(factor_scores[key]) * float(weights[key]) for key in common) / total_weight


def portfolio_weighted_return(weights: Mapping[str, float], returns: Mapping[str, float]) -> float:
    """Return a weighted portfolio return; missing held assets fail explicitly."""
    if not weights:
        raise ValueError("at least one held asset is required")
    missing = sorted(set(weights) - set(returns))
    if missing:
        raise ValueError(f"missing returns for held assets: {', '.join(missing)}")
    total_weight = 0.0
    result = 0.0
    for symbol, raw_weight in weights.items():
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError(f"weight for {symbol!r} must be a number")
        if isinstance(returns[symbol], bool) or not isinstance(returns[symbol], (int, float)):
            raise ValueError(f"return for {symbol!r} must be a number")
        weight = float(raw_weight)
        value = float(returns[symbol])
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"weight for {symbol!r} must be finite and >= 0")
        if not math.isfinite(value):
            raise ValueError(f"return for {symbol!r} must be finite")
        if value < -1:
            raise ValueError(f"return for {symbol!r} must be >= -1")
        total_weight += weight
        result += weight * value
    if total_weight <= 0:
        raise ValueError("portfolio weight must be > 0")
    if not math.isclose(total_weight, 1.0, abs_tol=1e-9):
        raise ValueError("portfolio weights must sum to 1")
    return result


def benchmark_70_30(btc_return: float, eth_return: float) -> float:
    btc_return = float(btc_return)
    eth_return = float(eth_return)
    if not math.isfinite(btc_return) or not math.isfinite(eth_return):
        raise ValueError("benchmark returns must be finite")
    return 0.7 * btc_return + 0.3 * eth_return


__all__ = [
    "annualized_futures_basis",
    "annualized_volatility",
    "benchmark_70_30",
    "current_drawdown",
    "max_drawdown",
    "moving_average",
    "period_returns",
    "portfolio_weighted_return",
    "simple_return",
    "weighted_score",
]
