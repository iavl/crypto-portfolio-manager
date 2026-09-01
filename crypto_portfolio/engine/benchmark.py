"""BTC benchmark calculations with aligned periods and cash-flow treatment."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
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
    """Calculate a buy-and-hold benchmark with flows at period end."""
    if len(period_returns_by_asset) != len(cash_flows):
        raise ValueError("period returns and cash flows must have equal lengths")
    initial_value = _finite(initial_value, "initial_value")
    if initial_value <= 0:
        raise ValueError("initial_value must be > 0")
    if timestamps is not None and len(timestamps) != len(period_returns_by_asset) + 1:
        raise ValueError("timestamps must contain one more item than period returns")
    resolved = policy or resolve_policy()
    selected = dict(weights) if weights is not None else resolved.benchmarks["primary"]
    normalized_weights = {
        str(symbol).strip().upper(): _finite(weight, f"weight for {symbol}")
        for symbol, weight in selected.items()
    }
    if not normalized_weights or any(weight < 0 for weight in normalized_weights.values()):
        raise ValueError("benchmark weights must be non-negative")
    if not math.isclose(sum(normalized_weights.values()), 1.0, abs_tol=1e-9):
        raise ValueError("benchmark weights must sum to 1")
    components = {
        symbol: initial_value * weight for symbol, weight in normalized_weights.items()
    }
    value = initial_value
    snapshots = [
        PortfolioSnapshot(
            timestamp=timestamps[0] if timestamps else "2000-01-01T00:00:00Z",
            portfolio_value=value,
        )
    ]
    for index, (asset_returns, cash_flow) in enumerate(zip(period_returns_by_asset, cash_flows)):
        flow = _finite(cash_flow, f"cash_flows[{index}]")
        normalized_returns = {
            str(symbol).strip().upper(): _finite(raw_return, f"return for {symbol}")
            for symbol, raw_return in asset_returns.items()
        }
        missing = sorted(set(normalized_weights) - set(normalized_returns))
        if missing:
            raise ValueError(f"missing returns for held assets: {', '.join(missing)}")
        for symbol in normalized_weights:
            period_return = normalized_returns[symbol]
            if period_return < -1:
                raise ValueError(f"return for {symbol!r} must be >= -1")
            components[symbol] *= 1.0 + period_return
        pre_flow_value = sum(components.values())
        value = pre_flow_value + flow
        if value <= 0 or not math.isfinite(value):
            raise ValueError("benchmark value must remain > 0")
        for symbol, weight in normalized_weights.items():
            components[symbol] += flow * weight
        snapshots.append(
            PortfolioSnapshot(
                timestamp=(
                    timestamps[index + 1]
                    if timestamps
                    else (date(2000, 1, 1) + timedelta(days=index + 1)).isoformat()
                    + "T00:00:00Z"
                ),
                portfolio_value=value,
                external_cash_flow=flow,
            )
        )
    return nav_return(build_nav_history(snapshots))


def benchmark_return_from_prices(
    prices_by_asset: Mapping[str, Sequence[float]],
    weights: Mapping[str, float] | None = None,
    *,
    benchmark: str | None = None,
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
    selected_weights = weights
    if benchmark is not None:
        if weights is not None:
            raise ValueError("provide either weights or benchmark, not both")
        resolved = policy or resolve_policy()
        selected_weights = resolved.benchmarks.get(benchmark)
        if selected_weights is None:
            raise ValueError(f"unknown benchmark: {benchmark}")
    return benchmark_return_with_cash_flows(
        periods,
        flows,
        selected_weights,
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
