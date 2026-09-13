"""Deterministic offline evaluation harness over frozen synthetic periods.

This is research tooling for phase 7 of the strategy-review plan, not a
production strategy input. It reuses decimal-fraction arithmetic conventions
and evaluates a *given* sequence of per-period target weights against
realized per-period asset returns; it never chooses weights itself and never
reaches into live providers.

Discrete-time ordering keeps the no-lookahead property structural: the
weights selected for period ``t`` are applied to the return realized over
``(t, t+1]``, so a signal can only fill after it existed. Between rebalances
holdings drift with realized returns and turnover is measured against the
drifted holdings, so a strategy that restores targets after a rally pays for
the round trip. Costs are explicit bps on traded notional; zero-turnover
periods are recorded, never hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence


def _default_stable_symbols() -> frozenset[str]:
    from ..models.policy import resolve_policy

    return frozenset(resolve_policy().stable_symbols)


def _normalized_weights(value: Mapping[str, float], field: str, *, require_sum: bool = True) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw_symbol, raw_weight in value.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError(f"{field} contains an empty symbol")
        if symbol in result:
            raise ValueError(f"{field} contains duplicate symbol {symbol} after normalization")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError(f"{field}.{symbol} must be a number")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"{field}.{symbol} must be a finite fraction >= 0")
        result[symbol] = weight
    if require_sum and result and not math.isclose(sum(result.values()), 1.0, abs_tol=1e-9):
        raise ValueError(f"{field} must sum to 1")
    return result


def _normalized_returns(value: Mapping[str, float], field: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw_symbol, raw_return in value.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError(f"{field} contains an empty symbol")
        if symbol in result:
            raise ValueError(f"{field} contains duplicate symbol {symbol} after normalization")
        if isinstance(raw_return, bool) or not isinstance(raw_return, (int, float)) or not math.isfinite(float(raw_return)):
            raise ValueError(f"{field}.{symbol} must be a finite decimal fraction")
        if float(raw_return) < -1.0:
            raise ValueError(f"{field}.{symbol} cannot be below -100%")
        result[symbol] = float(raw_return)
    return result


@dataclass(frozen=True)
class EvaluationPeriod:
    """One frozen evaluation period: chosen weights and realized returns.

    Symbols are normalized (uppercased, duplicates rejected) so weights and
    returns always reference the same keys; a weighted asset missing its
    realized return is a fail-closed error, never a zero fill.
    """

    as_of: date
    target_weights: Mapping[str, float]
    asset_returns: Mapping[str, float]

    def __post_init__(self) -> None:
        weights = _normalized_weights(self.target_weights, "period target_weights")
        returns = _normalized_returns(self.asset_returns, "period asset_returns")
        missing = sorted(symbol for symbol, weight in weights.items() if weight > 0 and symbol not in returns)
        if missing:
            raise ValueError(
                f"period {self.as_of} is missing realized return for weighted asset(s): " + ", ".join(missing)
            )
        object.__setattr__(self, "target_weights", weights)
        object.__setattr__(self, "asset_returns", returns)


def _cost_bps(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return float(value)


def simulate_periods(
    periods: Sequence[EvaluationPeriod],
    *,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    stable_symbols: Iterable[str] | None = None,
) -> dict[str, object]:
    """Simulate one weight path with explicit costs; returns are net.

    Holdings drift with realized returns between periods and turnover is the
    total absolute difference between the period's target weights and the
    drifted holdings; its cost is charged against the period return.
    Zero-turnover periods are counted separately so deferred/WAIT behavior
    stays visible.
    """
    fee = _cost_bps(fee_bps, "fee_bps")
    slippage = _cost_bps(slippage_bps, "slippage_bps")
    if not periods:
        raise ValueError("at least one evaluation period is required")
    ordered = list(periods)
    dates = [period.as_of for period in ordered]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ValueError("evaluation periods must be unique and ordered by as_of")
    stables = frozenset(str(symbol).strip().upper() for symbol in stable_symbols) if stable_symbols is not None else _default_stable_symbols()

    holdings: dict[str, float] = {}
    navs: list[float] = []
    turnover_total = 0.0
    zero_turnover_periods = 0
    cash_weight_sum = 0.0
    period_rows: list[dict[str, object]] = []
    for period in ordered:
        weights = dict(period.target_weights)
        drifted_total = sum(holdings.values())
        drifted_weights = (
            {symbol: dollars / drifted_total for symbol, dollars in holdings.items()}
            if drifted_total > 0 else {}
        )
        symbols = sorted(set(weights) | set(drifted_weights))
        turnover = sum(
            abs(weights.get(symbol, 0.0) - drifted_weights.get(symbol, 0.0)) for symbol in symbols
        )
        turnover_total += turnover
        if turnover == 0.0:
            zero_turnover_periods += 1
        gross = sum(
            weight * period.asset_returns.get(symbol, 0.0)
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
        # Drift the chosen holdings through the realized returns so the next
        # period's turnover is measured against actual positions.
        holdings = {
            symbol: weight * (1.0 + period.asset_returns.get(symbol, 0.0))
            for symbol, weight in weights.items()
            if weight > 0
        }

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
    external_flows: Sequence[float] | None = None,
) -> dict[str, float]:
    """Buy-and-hold benchmark with fixed initial weights and optional flows.

    Each sleeve compounds its own return stream, so the 70/30 BTC/ETH
    secondary benchmark drifts with the market like a real buy-and-hold
    sleeve. ``external_flows``, one per period and applied at the period
    start, is allocated to the sleeves at the configured initial weight
    ratio (matching the 70/30-at-event benchmark convention) instead of the
    drifted weights; a zero-flow evaluation simply omits it. Portfolio and
    benchmark evaluation must share period boundaries and flow events.
    """
    if not asset_returns:
        raise ValueError("at least one period of asset returns is required")
    normalized_weights = _normalized_weights(weights, "benchmark weights")
    if external_flows is not None and len(external_flows) != len(asset_returns):
        raise ValueError("external_flows must align with asset_returns periods")
    sleeves = {symbol: weight for symbol, weight in normalized_weights.items() if weight > 0}
    nav = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for index, raw_returns in enumerate(asset_returns):
        returns = _normalized_returns(raw_returns, "benchmark period returns")
        if external_flows is not None:
            flow = float(external_flows[index])
            if not math.isfinite(flow) or flow <= -1.0:
                raise ValueError("external flows must be finite and above -100% of NAV")
            nav += flow
            sleeve_total = sum(sleeves.values())
            if sleeve_total > 0:
                for symbol in sleeves:
                    sleeves[symbol] += flow * normalized_weights[symbol]
        for symbol in sleeves:
            value = returns.get(symbol)
            if value is None:
                raise ValueError(f"benchmark period is missing return for {symbol}")
            sleeves[symbol] *= 1.0 + value
        nav = sum(sleeves.values())
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1.0)
    return {"final_nav": nav, "total_return": nav - 1.0, "max_drawdown": max_drawdown}


def sequential_splits(
    dates: Sequence[date],
    *,
    train_fraction: float = 0.6,
    validation_fraction: float = 0.2,
    label_horizon_days: int = 0,
) -> dict[str, tuple[date, ...]]:
    """Contiguous chronological train/validation/holdout blocks.

    Splits are block-wise. When ``label_horizon_days`` is supplied, boundary
    samples whose forward-label window crosses into the next block are
    purged from the end of the training and validation blocks, so 90/180-day
    overlapping labels cannot leak across a boundary; the holdout stays
    untouched because nothing is selected after it.
    """
    for name, value in (("train_fraction", train_fraction), ("validation_fraction", validation_fraction)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) < 1:
            raise ValueError(f"{name} must be a fraction in (0, 1)")
    if train_fraction + validation_fraction >= 1.0:
        raise ValueError("train_fraction plus validation_fraction must leave a holdout block")
    if isinstance(label_horizon_days, bool) or not isinstance(label_horizon_days, int) or label_horizon_days < 0:
        raise ValueError("label_horizon_days must be a non-negative integer")
    ordered = sorted(set(dates))
    if len(ordered) < 3:
        raise ValueError("at least three distinct dates are required")
    train_end = max(1, int(len(ordered) * train_fraction))
    validation_end = max(train_end + 1, train_end + int(len(ordered) * validation_fraction))
    train = list(ordered[:train_end])
    validation = list(ordered[train_end:validation_end])
    holdout = list(ordered[validation_end:])
    if label_horizon_days > 0 and validation:
        cutoff = validation[0]
        train = [day for day in train if day + timedelta(days=label_horizon_days) <= cutoff]
        if holdout:
            holdout_cutoff = holdout[0]
            validation = [day for day in validation if day + timedelta(days=label_horizon_days) <= holdout_cutoff]
        if not train or not validation:
            raise ValueError(
                "label horizon purges every sample in a block; provide more history or a smaller horizon"
            )
    return {
        "train": tuple(train),
        "validation": tuple(validation),
        "holdout": tuple(holdout),
    }


__all__ = [
    "EvaluationPeriod",
    "buy_and_hold_path",
    "sequential_splits",
    "simulate_periods",
]
