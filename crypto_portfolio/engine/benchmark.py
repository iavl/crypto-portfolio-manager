"""BTC benchmark calculations with aligned periods and cash-flow treatment."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .ledger import PortfolioSnapshot, build_nav_history, nav_return
from .metrics import benchmark_70_30, period_returns, portfolio_weighted_return
from ..models.policy import Policy, resolve_policy


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def benchmark_return(
    asset_returns: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
    *,
    benchmark: str = "primary",
    policy: Policy | None = None,
) -> float:
    resolved = policy or resolve_policy()
    selected = dict(weights) if weights is not None else resolved.benchmarks.get(benchmark)
    if selected is None:
        raise ValueError(f"unknown benchmark: {benchmark}")
    normalized = {str(symbol).strip().upper(): value for symbol, value in selected.items()}
    returns = {str(symbol).strip().upper(): value for symbol, value in asset_returns.items()}
    return portfolio_weighted_return(normalized, returns)


def primary_benchmark_return(btc_return: float) -> float:
    return _finite(btc_return, "btc_return")


def secondary_benchmark_return(btc_return: float, eth_return: float) -> float:
    return 0.7 * _finite(btc_return, "btc_return") + 0.3 * _finite(eth_return, "eth_return")


def require_aligned_period(
    portfolio_start: Any,
    portfolio_end: Any,
    benchmark_start: Any,
    benchmark_end: Any,
) -> None:
    if portfolio_start != benchmark_start or portfolio_end != benchmark_end:
        raise ValueError("portfolio and benchmark periods must have matching start and end dates")


def compare_portfolio_to_benchmark(
    portfolio_return: float,
    benchmark_return_value: float,
    *,
    portfolio_start: Any,
    portfolio_end: Any,
    benchmark_start: Any,
    benchmark_end: Any,
) -> dict[str, float]:
    """Return comparable performance only after period alignment is verified."""
    require_aligned_period(portfolio_start, portfolio_end, benchmark_start, benchmark_end)
    portfolio_return = _finite(portfolio_return, "portfolio_return")
    benchmark_return_value = _finite(benchmark_return_value, "benchmark_return")
    return {
        "portfolio_return": portfolio_return,
        "benchmark_return": benchmark_return_value,
        "excess_return": portfolio_return - benchmark_return_value,
    }


def benchmark_return_with_cash_flows(
    period_returns_by_asset: Sequence[Mapping[str, float]],
    cash_flows: Sequence[float],
    weights: Mapping[str, float] | None = None,
    *,
    initial_value: float = 1.0,
    timestamps: Sequence[str] | None = None,
    policy: Policy | None = None,
) -> float:
    """Apply each external flow before the corresponding benchmark period return."""
    if len(period_returns_by_asset) != len(cash_flows):
        raise ValueError("period returns and cash flows must have equal lengths")
    initial_value = _finite(initial_value, "initial_value")
    if initial_value <= 0:
        raise ValueError("initial_value must be > 0")
    if timestamps is not None and len(timestamps) != len(period_returns_by_asset) + 1:
        raise ValueError("timestamps must contain one more item than period returns")
    value = initial_value
    snapshots = [
        PortfolioSnapshot(
            timestamp=timestamps[0] if timestamps else "000000000000",
            portfolio_value=value,
        )
    ]
    for index, (asset_returns, cash_flow) in enumerate(zip(period_returns_by_asset, cash_flows)):
        flow = _finite(cash_flow, f"cash_flows[{index}]")
        period_return = benchmark_return(asset_returns, weights, policy=policy)
        value = (value + flow) * (1.0 + period_return)
        if value <= 0:
            raise ValueError("benchmark value must remain > 0")
        snapshots.append(
            PortfolioSnapshot(
                timestamp=timestamps[index + 1] if timestamps else str(index + 1).zfill(12),
                portfolio_value=value,
                external_cash_flow=flow,
            )
        )
    return nav_return(build_nav_history(snapshots))


def benchmark_return_from_prices(
    prices_by_asset: Mapping[str, Sequence[float]],
    weights: Mapping[str, float] | None = None,
    *,
    cash_flows: Sequence[float] | None = None,
    timestamps: Sequence[str] | None = None,
    policy: Policy | None = None,
) -> float:
    if not prices_by_asset:
        raise ValueError("price history is required")
    series = {symbol.strip().upper(): period_returns(prices) for symbol, prices in prices_by_asset.items()}
    lengths = {len(values) for values in series.values()}
    if len(lengths) != 1:
        raise ValueError("benchmark asset histories must have equal lengths")
    count = lengths.pop()
    flows = list(cash_flows) if cash_flows is not None else [0.0] * count
    if len(flows) != count:
        raise ValueError("cash_flows must match the number of return periods")
    periods = [
        {symbol: values[index] for symbol, values in series.items()}
        for index in range(count)
    ]
    return benchmark_return_with_cash_flows(
        periods,
        flows,
        weights,
        timestamps=timestamps,
        policy=policy,
    )


calculate_benchmark_return = benchmark_return
benchmark_100_btc = primary_benchmark_return


__all__ = [
    "benchmark_return",
    "benchmark_return_from_prices",
    "benchmark_return_with_cash_flows",
    "compare_portfolio_to_benchmark",
    "primary_benchmark_return",
    "require_aligned_period",
    "secondary_benchmark_return",
    "benchmark_70_30",
    "benchmark_100_btc",
    "calculate_benchmark_return",
]
