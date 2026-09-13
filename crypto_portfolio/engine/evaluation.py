"""Deterministic offline evaluation harness over frozen synthetic periods.

This is research tooling for phase 7 of the strategy-review plan, not a
production strategy input. It reuses decimal-fraction arithmetic conventions
and evaluates a *given* sequence of per-period target weights against
realized per-period asset returns; it never chooses weights itself and never
reaches into live providers.

Discrete-time ordering keeps the no-lookahead property structural: the
weights selected for period ``t`` are applied to the return realized over
``(t, t+1]``, so a signal can only fill after it existed. Costs are explicit
bps on traded notional; zero-turnover periods are recorded, never hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Sequence


_DEFAULT_STABLE = frozenset({"USDT", "USDC", "DAI", "USD"})


@dataclass(frozen=True)
class EvaluationPeriod:
    """One frozen evaluation period: chosen weights and realized returns."""

    as_of: date
    target_weights: Mapping[str, float]
    asset_returns: Mapping[str, float]

    def __post_init__(self) -> None:
        weights = dict(self.target_weights)
        for symbol, weight in weights.items():
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(float(weight)) or float(weight) < 0:
                raise ValueError(f"period {self.as_of} weight for {symbol} must be a finite fraction >= 0")
        total = sum(weights.values())
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(f"period {self.as_of} target weights must sum to 1")
        returns = dict(self.asset_returns)
        for symbol, value in returns.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"period {self.as_of} return for {symbol} must be finite")
            if float(value) < -1.0:
                raise ValueError(f"period {self.as_of} return for {symbol} cannot be below -100%")
        missing = sorted(symbol for symbol, weight in weights.items() if weight > 0 and symbol not in returns)
        if missing:
            raise ValueError(
                f"period {self.as_of} is missing realized return for weighted asset(s): " + ", ".join(missing)
            )


def _cost_bps(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return float(value)


def simulate_periods(
    periods: Sequence[EvaluationPeriod],
    *,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    stable_symbols: Iterable[str] = _DEFAULT_STABLE,
) -> dict[str, object]:
    """Simulate one weight path with explicit costs; returns are net.

    Each period applies the weights chosen at ``as_of`` to the return of the
    following interval; turnover is the sum of absolute weight changes and
    its cost is charged against the period return. Zero-turnover periods are
    counted separately so deferred/WAIT behavior stays visible.
    """
    fee = _cost_bps(fee_bps, "fee_bps")
    slippage = _cost_bps(slippage_bps, "slippage_bps")
    if not periods:
        raise ValueError("at least one evaluation period is required")
    ordered = list(periods)
    dates = [period.as_of for period in ordered]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ValueError("evaluation periods must be unique and ordered by as_of")
    stables = frozenset(str(symbol).strip().upper() for symbol in stable_symbols)

    navs: list[float] = []
    turnover_total = 0.0
    zero_turnover_periods = 0
    cash_weight_sum = 0.0
    period_rows: list[dict[str, object]] = []
    previous_weights: dict[str, float] = {}
    for period in ordered:
        weights = {str(symbol).strip().upper(): float(weight) for symbol, weight in period.target_weights.items()}
        symbols = sorted(set(weights) | set(previous_weights))
        turnover = sum(abs(weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0)) for symbol in symbols)
        turnover_total += turnover
        if turnover == 0.0:
            zero_turnover_periods += 1
        gross = sum(
            weight * float(period.asset_returns.get(symbol, 0.0))
            for symbol, weight in weights.items()
        )
        cost = turnover * (fee + slippage) / 10000.0
        net = gross - cost
        navs.append((1.0 + net) if not navs else navs[-1] * (1.0 + net))
        cash_weight_sum += sum(weight for symbol, weight in weights.items() if symbol in stables)
        period_rows.append({
            "as_of": period.as_of.isoformat(),
            "gross_return": gross,
            "cost": cost,
            "net_return": net,
            "turnover": turnover,
            "nav": navs[-1],
        })
        previous_weights = weights

    # Peak-relative drawdown and recovery: a recovery is the first period the
    # NAV regains the peak that preceded the deepest trough.
    peak = 1.0
    max_drawdown = 0.0
    trough_index = -1
    trough_peak = 1.0
    recovery_index = -1
    for index, nav in enumerate([1.0, *navs]):
        if nav >= peak:
            peak = nav
        drawdown = nav / peak - 1.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            trough_index = index
            trough_peak = peak
            recovery_index = -1
        elif recovery_index < 0 and max_drawdown < 0.0 and nav >= trough_peak:
            recovery_index = index

    count = len(ordered)
    return {
        "periods": count,
        "final_nav": navs[-1],
        "total_return": navs[-1] - 1.0,
        "max_drawdown": max_drawdown,
        "trough_period_index": trough_index,
        "recovery_period_index": recovery_index,
        "total_turnover": turnover_total,
        "average_turnover": turnover_total / count,
        "average_cash_weight": cash_weight_sum / count,
        "zero_turnover_periods": zero_turnover_periods,
        "period_detail": period_rows,
    }


def buy_and_hold_path(
    asset_returns: Sequence[Mapping[str, float]],
    *,
    weights: Mapping[str, float],
) -> dict[str, float]:
    """Buy-and-hold benchmark with the initial weights fixed once.

    Benchmarks never rebalance: each sleeve compounds its own return stream,
    so the 70/30 BTC/ETH secondary benchmark drifts with the market exactly
    like a real buy-and-hold sleeve. Portfolio and benchmark evaluation must
    share period boundaries and external cash flows.
    """
    if not asset_returns:
        raise ValueError("at least one period of asset returns is required")
    total = sum(float(weight) for weight in weights.values())
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        raise ValueError("benchmark weights must sum to 1")
    sleeves = {str(symbol).strip().upper(): float(weight) for symbol, weight in weights.items() if float(weight) > 0}
    nav = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for returns in asset_returns:
        for symbol in sleeves:
            value = returns.get(symbol)
            if value is None:
                raise ValueError(f"benchmark period is missing return for {symbol}")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < -1.0:
                raise ValueError(f"benchmark return for {symbol} is invalid")
            sleeves[symbol] *= 1.0 + float(value)
        nav = sum(sleeves.values())
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1.0)
    return {"final_nav": nav, "total_return": nav - 1.0, "max_drawdown": max_drawdown}


def sequential_splits(
    dates: Sequence[date], *, train_fraction: float = 0.6, validation_fraction: float = 0.2
) -> dict[str, tuple[date, ...]]:
    """Contiguous chronological train/validation/holdout blocks.

    Splits are block-wise so overlapping 90/180-day forward labels never leak
    across a boundary; the holdout is always the final block and is evaluated
    once, last.
    """
    for name, value in (("train_fraction", train_fraction), ("validation_fraction", validation_fraction)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) < 1:
            raise ValueError(f"{name} must be a fraction in (0, 1)")
    if train_fraction + validation_fraction >= 1.0:
        raise ValueError("train_fraction plus validation_fraction must leave a holdout block")
    ordered = sorted(set(dates))
    if len(ordered) < 3:
        raise ValueError("at least three distinct dates are required")
    train_end = max(1, int(len(ordered) * train_fraction))
    validation_end = max(train_end + 1, train_end + int(len(ordered) * validation_fraction))
    return {
        "train": tuple(ordered[:train_end]),
        "validation": tuple(ordered[train_end:validation_end]),
        "holdout": tuple(ordered[validation_end:]),
    }


__all__ = [
    "EvaluationPeriod",
    "buy_and_hold_path",
    "sequential_splits",
    "simulate_periods",
]
