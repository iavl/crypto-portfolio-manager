"""BTC benchmark calculations with aligned periods and cash-flow treatment."""

from __future__ import annotations

import math
from dataclasses import replace
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from .ledger import PortfolioSnapshot, build_nav_history, nav_return
from ..models.performance import NAVHistoryResult
from ..models.time import normalize_timestamp
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
    benchmark: str = "opportunity_cost_btc",
    policy: Policy | None = None,
) -> float:
    resolved = policy or resolve_policy()
    selected = dict(weights) if weights is not None else resolved.benchmarks.get(benchmark)
    if selected is None:
        raise ValueError(f"unknown benchmark: {benchmark}")
    if "type" in selected:
        raise ValueError(
            "the vol-matched primary comparison is strategy-dependent and has "
            "no static weight map; compare through its own benchmark path"
        )
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
    # The static per-period anchor is the BTC opportunity-cost reference;
    # the risk-matched primary comparison has no static weight map.
    selected = dict(weights) if weights is not None else resolved.benchmarks["opportunity_cost_btc"]
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
            raise ValueError(
                f"benchmark value must remain > 0 after cash flow {flow} in period {index}; "
                "the benchmark must be scaled with the portfolio's initial value"
            )
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
    initial_value: float = 1.0,
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
        if "type" in selected_weights:
            raise ValueError(
                "the vol-matched primary comparison is strategy-dependent and "
                "has no static weight map"
            )
    return benchmark_return_with_cash_flows(
        periods,
        flows,
        selected_weights,
        initial_value=initial_value,
        timestamps=timestamps,
        policy=policy,
    )


def build_aligned_benchmark_result(
    portfolio: NAVHistoryResult | Sequence[PortfolioSnapshot | Mapping[str, Any]],
    btc_prices: Sequence[float],
    *,
    eth_prices: Sequence[float] | None = None,
    cash_flows: Sequence[float] | None = None,
    timestamps: Sequence[str] | None = None,
    policy: Policy | None = None,
) -> NAVHistoryResult:
    """Attach buy-and-hold BTC/70-30 benchmark returns to an aligned NAV result."""
    if isinstance(portfolio, NAVHistoryResult):
        result = portfolio
    else:
        from .ledger import build_nav_history_result

        result = build_nav_history_result(portfolio)
    if result.status != "AVAILABLE":
        return replace(result, benchmark_status=result.status, explanations=tuple(result.explanations) + ("benchmark is unavailable until NAV history is finalized",))
    if not btc_prices:
        return replace(result, benchmark_status="UNAVAILABLE", explanations=tuple(result.explanations) + ("BTC benchmark anchor is missing",))
    if timestamps is not None:
        normalized = tuple(normalize_timestamp(item, "benchmark timestamp") for item in timestamps)
        if len(normalized) != len(btc_prices):
            raise ValueError("benchmark timestamps must match BTC price history")
        if result.states and (
            normalized[0] != result.states[0].timestamp or normalized[-1] != result.states[-1].timestamp
        ):
            raise ValueError("benchmark and portfolio periods must share start and end anchors")
    flows = list(cash_flows) if cash_flows is not None else [state.external_cash_flow for state in result.states[1:]]
    if len(flows) != len(btc_prices) - 1:
        raise ValueError("benchmark cash flows must match price return periods")
    # Real-dollar flows only make sense against a benchmark sized like the
    # portfolio: with the default unit start value every flow would dwarf the
    # benchmark and silently rebalance the 70/30 sleeve back to its anchor.
    initial_value = result.states[0].portfolio_value
    btc_return = benchmark_return_from_prices(
        {"BTC": btc_prices},
        cash_flows=flows,
        timestamps=timestamps,
        initial_value=initial_value,
        benchmark="opportunity_cost_btc",
        policy=policy,
    )
    secondary = None
    if eth_prices is not None:
        if len(eth_prices) != len(btc_prices):
            raise ValueError("BTC and ETH benchmark histories must have equal lengths")
        secondary = benchmark_return_from_prices(
            {"BTC": btc_prices, "ETH": eth_prices},
            cash_flows=flows,
            timestamps=timestamps,
            initial_value=initial_value,
            benchmark="secondary_static",
            policy=policy,
        )
    return replace(
        result,
        benchmark_status="AVAILABLE",
        btc_return=btc_return,
        btc_excess_return=result.nav_return - btc_return if result.nav_return is not None else None,
        secondary_benchmark_return=secondary,
    )


def _return_series(values: Sequence[Any], field: str) -> list[float]:
    series = [_finite(value, f"{field}[{index}]") for index, value in enumerate(values)]
    if any(value < -1 for value in series):
        raise ValueError(f"{field} must not contain a return below -100%")
    return series


def _sample_moments(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float]:
    """Sample variances and covariance, using the ddof=1 convention of performance_metrics."""
    count = len(left)
    mean_left = sum(left) / count
    mean_right = sum(right) / count
    return (
        sum((value - mean_left) ** 2 for value in left) / (count - 1),
        sum((value - mean_right) ** 2 for value in right) / (count - 1),
        sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right)) / (count - 1),
    )


def _mixture_variance(
    weight: float, variance_btc: float, variance_cash: float, covariance: float
) -> float:
    """Variance of the constant mix holding ``weight`` in BTC and the rest in cash."""
    return (
        weight * weight * variance_btc
        + (1.0 - weight) ** 2 * variance_cash
        + 2.0 * weight * (1.0 - weight) * covariance
    )


def vol_matched_cash_weight(
    *,
    btc_returns: Sequence[float],
    target_volatility: float,
    cash_returns: Sequence[float] | None = None,
    periods_per_year: float = 365.25,
    tolerance: float = 1e-4,
) -> float:
    """Return the fixed BTC weight whose constant mix matches a target volatility.

    This sizes the "would the same risk have paid better as a static BTC/cash
    mix" benchmark, so it must be reproducible: the weight is solved in closed
    form from sample variances and never searched for.

    The reachable range of a constant mix is a closed interval, and a target
    outside it is capped at the nearest end instead of rejected, because either
    endpoint is still a meaningful benchmark:

    - a target at or above the riskiest leg returns ``1.0`` when that leg is
      BTC and ``0.0`` when it is cash; exposure cannot leave ``[0, 1]``;
    - a target at or below the least volatile mix returns the weight that
      achieves that minimum, which for a riskless cash leg is ``0.0``;
    - when two weights reach the target, the smaller one wins, so the answer is
      the least risky mix that does, not the most risky.

    Identical legs are rejected rather than capped: when both legs carry the
    same risk no weight can change the volatility, so no answer is correct.

    ``cash_returns`` defaults to a flat zero series, matching the engine's
    convention that the USD leg earns nothing.  ``periods_per_year`` must match
    the annualization behind ``target_volatility`` (``365.25 /
    mean_period_days`` for historical runs).  The realized volatility of the
    solved mix is re-derived and checked against ``tolerance`` so a silent miss
    cannot reach a report.
    """
    btc = _return_series(btc_returns, "btc_returns")
    if len(btc) < 2:
        raise ValueError("btc_returns needs at least two observations")
    cash = [0.0] * len(btc) if cash_returns is None else _return_series(cash_returns, "cash_returns")
    if len(cash) != len(btc):
        raise ValueError("cash_returns and btc_returns must have equal lengths")
    target = _finite(target_volatility, "target_volatility")
    if target < 0:
        raise ValueError("target_volatility must be non-negative")
    horizon = _finite(periods_per_year, "periods_per_year")
    if horizon <= 0:
        raise ValueError("periods_per_year must be > 0")
    slack = _finite(tolerance, "tolerance")
    if slack < 0:
        raise ValueError("tolerance must be non-negative")

    variance_btc, variance_cash, covariance = _sample_moments(btc, cash)
    # mixture variance(w) = quadratic*w^2 + linear*w + variance_cash.
    quadratic = variance_btc + variance_cash - 2.0 * covariance
    linear = 2.0 * (covariance - variance_cash)
    scale = max(variance_btc, variance_cash)
    if abs(quadratic) <= 1e-15 * scale and abs(linear) <= 1e-15 * scale:
        raise ValueError("volatility matching is undefined when both legs carry identical risk")

    # A convex parabola reaches its minimum at its turning point, clamped into
    # the domain, and its maximum at one of the two endpoints.
    turning = min(max(-linear / (2.0 * quadratic), 0.0), 1.0) if quadratic > 0.0 else 0.0
    quietest = math.sqrt(max(_mixture_variance(turning, variance_btc, variance_cash, covariance), 0.0) * horizon)
    loudest = max(math.sqrt(variance_btc * horizon), math.sqrt(variance_cash * horizon))
    if target <= quietest:
        return turning
    if target >= loudest:
        return 1.0 if variance_btc >= variance_cash else 0.0

    constant = variance_cash - target * target / horizon
    if quadratic <= 0.0:
        weight = -constant / linear
    else:
        discriminant = max(linear * linear - 4.0 * quadratic * constant, 0.0)
        offset = math.sqrt(discriminant)
        reachable = sorted(
            value
            for value in (
                (-linear - offset) / (2.0 * quadratic),
                (-linear + offset) / (2.0 * quadratic),
            )
            if 0.0 <= value <= 1.0
        )
        if not reachable:
            raise ValueError("no whole-portfolio BTC weight reaches the target volatility")
        weight = reachable[0]
    realized = math.sqrt(
        max(_mixture_variance(weight, variance_btc, variance_cash, covariance), 0.0) * horizon
    )
    if abs(realized - target) > slack:
        raise ValueError(
            f"cannot reach {target:.6f} annualized volatility; the closest achievable is {realized:.6f}"
        )
    return weight


def exposure_timing_contribution(
    risky_weights: Sequence[float],
    prices_by_time: Sequence[tuple[Any, Mapping[str, float]]],
    *,
    leg_weights: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Signed exposure-timing contribution of a realized risky-weight path.

    Compounds the same static leg (default 100% BTC) twice over the same
    period returns: once on the strategy's realized risky weight entering
    each period, once on the constant weight equal to that path's average.
    The difference is a signed number for "did holding risk when the leg
    moved beat holding the average exposure constantly":

        path_total     = prod(1 + w_t * r_leg_t) - 1
        constant_total = prod(1 + wbar * r_leg_t) - 1
        contribution   = path_total - constant_total

    ``risky_weights`` holds the exposure known at each period's decision
    boundary (pre-trade); ``prices_by_time`` carries one price map per
    boundary plus one at the final period end, so its length is exactly one
    greater. Zero costs on both legs by construction — this isolates the
    timing covariance, it is not an investable benchmark.
    """
    weights = [_finite(weight, f"risky_weights[{index}]") for index, weight in enumerate(risky_weights)]
    if not weights:
        raise ValueError("risky_weights needs at least one observation")
    if any(weight < 0 or weight > 1 for weight in weights):
        raise ValueError("risky_weights must be in [0, 1]")
    if len(prices_by_time) != len(weights) + 1:
        raise ValueError("prices_by_time must contain exactly one more boundary than risky_weights")
    leg = {"BTC": 1.0} if leg_weights is None else {
        str(symbol).strip().upper(): _finite(weight, f"leg_weights for {symbol}")
        for symbol, weight in leg_weights.items()
    }
    if not leg or any(weight < 0 for weight in leg.values()):
        raise ValueError("leg_weights must be non-negative and non-empty")
    if not math.isclose(sum(leg.values()), 1.0, abs_tol=1e-9):
        raise ValueError("leg_weights must sum to 1")
    leg_returns: list[float] = []
    for index in range(len(weights)):
        previous = prices_by_time[index][1]
        current = prices_by_time[index + 1][1]
        period = 0.0
        for symbol, weight in leg.items():
            if symbol not in previous or symbol not in current:
                raise ValueError(f"prices are missing leg symbol {symbol}")
            before = _finite(previous[symbol], f"price for {symbol}")
            after = _finite(current[symbol], f"price for {symbol}")
            if before <= 0 or after <= 0:
                raise ValueError(f"price for {symbol} must be positive")
            period += weight * (after / before - 1.0)
        leg_returns.append(period)
    average = sum(weights) / len(weights)
    path_total = 1.0
    constant_total = 1.0
    for weight, period in zip(weights, leg_returns):
        path_total *= 1.0 + weight * period
        constant_total *= 1.0 + average * period
    return {
        "realized_average_risky_weight": average,
        "path_total_return": path_total - 1.0,
        "constant_total_return": constant_total - 1.0,
        "timing_contribution": (path_total - 1.0) - (constant_total - 1.0),
        "periods": len(weights),
    }


__all__ = [
    "benchmark_return",
    "benchmark_return_from_prices",
    "benchmark_return_with_cash_flows",
    "compare_portfolio_to_benchmark",
    "exposure_timing_contribution",
    "primary_benchmark_return",
    "require_aligned_period",
    "secondary_benchmark_return",
    "benchmark_70_30",
    "build_aligned_benchmark_result",
    "vol_matched_cash_weight",
]
