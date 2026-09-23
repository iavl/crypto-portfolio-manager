"""Quantity-and-cash accounting primitives for research-only backtests.

This module owns execution accounting and performance math.  It does not
choose assets, scores, targets, or policy parameters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from ..models.time import normalize_timestamp, parse_timestamp


def _number(value: Any, field_name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{field_name} must be finite" + (" and non-negative" if minimum == 0 else ""))
    return result


def _timestamp(value: Any, field_name: str = "timestamp") -> str:
    if isinstance(value, datetime):
        value = value.isoformat()
    return normalize_timestamp(value, field_name)


def _prices(value: Mapping[str, Any], field_name: str = "prices") -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    result: dict[str, float] = {}
    for raw_symbol, raw_price in value.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol or symbol in result:
            raise ValueError(f"{field_name} contains an empty or duplicate symbol")
        price = _number(raw_price, f"{field_name}.{symbol}", minimum=0)
        if price <= 0:
            raise ValueError(f"{field_name}.{symbol} must be > 0")
        result[symbol] = price
    return result


@dataclass(frozen=True)
class LedgerTrade:
    timestamp: str
    symbol: str
    side: str
    quantity: float
    reference_price: float
    execution_price: float
    gross_notional_usd: float
    fee_usd: float
    cash_change_usd: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ValuationPoint:
    timestamp: str
    total_value_usd: float
    cash_usd: float
    positions_usd: Mapping[str, float]
    weights: Mapping[str, float]
    drawdown: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp, "total_value_usd": self.total_value_usd,
            "cash_usd": self.cash_usd, "positions_usd": dict(self.positions_usd),
            "weights": dict(self.weights), "drawdown": self.drawdown,
        }


@dataclass
class QuantityLedger:
    """Mutable simulation state with an append-only audit trail."""

    cash_usd: float
    quantities: dict[str, float] = field(default_factory=dict)
    trades: list[LedgerTrade] = field(default_factory=list)
    valuations: list[ValuationPoint] = field(default_factory=list)
    peak_value_usd: float | None = None

    def __post_init__(self) -> None:
        self.cash_usd = _number(self.cash_usd, "cash_usd", minimum=0)
        normalized: dict[str, float] = {}
        for raw_symbol, raw_quantity in self.quantities.items():
            symbol = str(raw_symbol).strip().upper()
            if not symbol or symbol == "USD" or symbol in normalized:
                raise ValueError("quantities contains an empty, USD, or duplicate symbol")
            normalized[symbol] = _number(raw_quantity, f"quantities.{symbol}", minimum=0)
        self.quantities = normalized

    @classmethod
    def from_weights(
        cls,
        total_value_usd: float,
        weights: Mapping[str, Any],
        prices: Mapping[str, Any],
    ) -> "QuantityLedger":
        total = _number(total_value_usd, "total_value_usd", minimum=0)
        if total <= 0:
            raise ValueError("total_value_usd must be > 0")
        normalized_prices = _prices(prices)
        normalized_weights: dict[str, float] = {}
        for raw_symbol, raw_weight in weights.items():
            symbol = str(raw_symbol).strip().upper()
            if not symbol or symbol in normalized_weights:
                raise ValueError("weights contains an empty or duplicate symbol")
            normalized_weights[symbol] = _number(raw_weight, f"weights.{symbol}", minimum=0)
        if not math.isclose(sum(normalized_weights.values()), 1.0, abs_tol=1e-9):
            raise ValueError("weights must sum to 1")
        quantities: dict[str, float] = {}
        for symbol, weight in normalized_weights.items():
            if symbol == "USD" or weight == 0:
                continue
            if symbol not in normalized_prices:
                raise ValueError(f"initial price is missing for {symbol}")
            quantities[symbol] = total * weight / normalized_prices[symbol]
        return cls(total * normalized_weights.get("USD", 0.0), quantities)

    def mark(self, timestamp: str, prices: Mapping[str, Any]) -> ValuationPoint:
        moment = _timestamp(timestamp)
        if self.valuations and parse_timestamp(moment) <= parse_timestamp(self.valuations[-1].timestamp):
            raise ValueError("valuation timestamps must be strictly increasing")
        normalized_prices = _prices(prices)
        missing = sorted(symbol for symbol, quantity in self.quantities.items()
                         if quantity > 1e-15 and symbol not in normalized_prices)
        if missing:
            raise ValueError("valuation is missing held asset price(s): " + ", ".join(missing))
        positions = {symbol: quantity * normalized_prices[symbol]
                     for symbol, quantity in self.quantities.items() if quantity > 1e-15}
        total = self.cash_usd + sum(positions.values())
        if total <= 0:
            raise ValueError("portfolio value must remain positive")
        self.peak_value_usd = max(self.peak_value_usd or total, total)
        drawdown = total / self.peak_value_usd - 1.0
        weights = {symbol: amount / total for symbol, amount in positions.items()}
        weights["USD"] = self.cash_usd / total
        point = ValuationPoint(moment, total, self.cash_usd, positions, weights, drawdown)
        self.valuations.append(point)
        return point

    def execute(
        self,
        *,
        timestamp: str,
        symbol: str,
        side: str,
        amount_usd: float,
        reference_price: float,
        fee_bps: float,
        slippage_bps: float,
        reason: str,
    ) -> LedgerTrade | None:
        moment = _timestamp(timestamp)
        if self.trades and parse_timestamp(moment) < parse_timestamp(self.trades[-1].timestamp):
            raise ValueError("trades must be ordered by timestamp")
        normalized_symbol = str(symbol).strip().upper()
        if not normalized_symbol or normalized_symbol == "USD":
            raise ValueError("trade symbol must be a non-cash asset")
        normalized_side = str(side).strip().upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        requested = _number(amount_usd, "amount_usd", minimum=0)
        if requested <= 1e-12:
            return None
        price = _number(reference_price, "reference_price", minimum=0)
        if price <= 0:
            raise ValueError("reference_price must be > 0")
        fee_rate = _number(fee_bps, "fee_bps", minimum=0) / 10_000.0
        slip_rate = _number(slippage_bps, "slippage_bps", minimum=0) / 10_000.0
        execution_price = price * (1.0 + slip_rate if normalized_side == "BUY" else 1.0 - slip_rate)
        if execution_price <= 0:
            raise ValueError("slippage produces a non-positive execution price")
        if normalized_side == "BUY":
            gross = min(requested, self.cash_usd / (1.0 + fee_rate))
            if gross <= 1e-12:
                return None
            fee = gross * fee_rate
            quantity = gross / execution_price
            cash_change = -(gross + fee)
            self.cash_usd += cash_change
            self.quantities[normalized_symbol] = self.quantities.get(normalized_symbol, 0.0) + quantity
        else:
            available = self.quantities.get(normalized_symbol, 0.0)
            quantity = min(available, requested / execution_price)
            if quantity <= 1e-15:
                return None
            gross = quantity * execution_price
            fee = gross * fee_rate
            cash_change = gross - fee
            self.cash_usd += cash_change
            self.quantities[normalized_symbol] = max(0.0, available - quantity)
        if self.cash_usd < -1e-7:
            raise ValueError("trade accounting produced negative cash")
        self.cash_usd = max(0.0, self.cash_usd)
        trade = LedgerTrade(
            moment, normalized_symbol, normalized_side, quantity, price, execution_price,
            gross, fee, cash_change, str(reason).strip() or "UNSPECIFIED",
        )
        self.trades.append(trade)
        return trade


def _period_returns(values: Sequence[ValuationPoint]) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for left, right in zip(values, values[1:]):
        result.append((right.timestamp, right.total_value_usd / left.total_value_usd - 1.0))
    return result


def performance_metrics(values: Sequence[ValuationPoint]) -> dict[str, Any]:
    """Calculate unambiguous daily/hourly portfolio performance statistics."""
    ordered = tuple(values)
    if len(ordered) < 2:
        raise ValueError("at least two valuation points are required")
    moments = [parse_timestamp(item.timestamp) for item in ordered]
    if moments != sorted(moments) or len(moments) != len(set(moments)):
        raise ValueError("valuation points must be unique and ordered")
    elapsed_days = (moments[-1] - moments[0]).total_seconds() / 86_400.0
    if elapsed_days <= 0:
        raise ValueError("performance interval must be positive")
    total_return = ordered[-1].total_value_usd / ordered[0].total_value_usd - 1.0
    cagr = (1.0 + total_return) ** (365.25 / elapsed_days) - 1.0 if total_return > -1.0 else -1.0
    returns = [value for _, value in _period_returns(ordered)]
    intervals = [(right - left).total_seconds() / 86_400.0 for left, right in zip(moments, moments[1:])]
    mean_interval = sum(intervals) / len(intervals)
    periods_per_year = 365.25 / mean_interval
    mean_return = sum(returns) / len(returns)
    sample_variance = (
        sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
        if len(returns) > 1 else None
    )
    volatility = math.sqrt(sample_variance * periods_per_year) if sample_variance is not None else None
    sharpe = mean_return / math.sqrt(sample_variance) * math.sqrt(periods_per_year) \
        if sample_variance is not None and sample_variance > 0 else None
    downside_mean_square = sum(min(value, 0.0) ** 2 for value in returns) / len(returns)
    sortino = mean_return / math.sqrt(downside_mean_square) * math.sqrt(periods_per_year) \
        if downside_mean_square > 0 else None
    maximum_drawdown = min(item.drawdown for item in ordered)
    calmar = cagr / abs(maximum_drawdown) if maximum_drawdown < 0 else None
    worst_return = min(returns)

    deepest = min(range(len(ordered)), key=lambda index: ordered[index].drawdown)
    prior_peak_value = max(item.total_value_usd for item in ordered[: deepest + 1])
    recovery = next((item.timestamp for item in ordered[deepest + 1:]
                     if item.total_value_usd >= prior_peak_value), None)
    peak_index = max(range(deepest + 1), key=lambda index: ordered[index].total_value_usd)
    drawdown_duration_days = (moments[deepest] - moments[peak_index]).total_seconds() / 86_400.0
    recovery_days = ((parse_timestamp(recovery) - moments[deepest]).total_seconds() / 86_400.0
                     if recovery is not None else None)

    def _calendar_returns(unit: str) -> dict[str, float]:
        grouped: dict[str, list[ValuationPoint]] = {}
        for point in ordered:
            moment = parse_timestamp(point.timestamp)
            key = moment.strftime("%Y-%m" if unit == "month" else "%Y")
            grouped.setdefault(key, []).append(point)
        return {
            key: values[-1].total_value_usd / values[0].total_value_usd - 1.0
            for key, values in sorted(grouped.items()) if len(values) >= 2
        }

    return {
        "start_at": ordered[0].timestamp, "end_at": ordered[-1].timestamp,
        "elapsed_days": elapsed_days, "initial_value_usd": ordered[0].total_value_usd,
        "final_value_usd": ordered[-1].total_value_usd, "total_return": total_return,
        "cagr": cagr, "annualized_volatility": volatility, "sharpe_rf_zero": sharpe,
        "sortino_target_zero": sortino, "calmar": calmar,
        "maximum_drawdown": maximum_drawdown, "worst_period_return": worst_return,
        "deepest_drawdown_at": ordered[deepest].timestamp,
        "drawdown_duration_days": drawdown_duration_days,
        "recovered_at": recovery, "recovery_days_from_trough": recovery_days,
        "periods": len(returns), "mean_period_days": mean_interval,
        "calendar_returns": {"monthly": _calendar_returns("month"), "yearly": _calendar_returns("year")},
    }


def buy_and_hold_benchmark(
    *,
    prices_by_time: Sequence[tuple[str, Mapping[str, float]]],
    weights: Mapping[str, float],
    initial_value_usd: float,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> dict[str, Any]:
    """Initial buy-and-hold benchmark; it never rebalances."""
    if len(prices_by_time) < 2:
        raise ValueError("benchmark needs at least two aligned price points")
    first_timestamp, first_prices = prices_by_time[0]
    ledger = QuantityLedger(float(initial_value_usd), {})
    normalized_weights = {str(symbol).strip().upper(): float(weight) for symbol, weight in weights.items()}
    if not math.isclose(sum(normalized_weights.values()), 1.0, abs_tol=1e-9):
        raise ValueError("benchmark weights must sum to 1")
    ledger.mark(first_timestamp, first_prices)
    for symbol, weight in normalized_weights.items():
        if symbol == "USD" or weight <= 0:
            continue
        if symbol not in first_prices:
            raise ValueError(f"benchmark initial price is missing for {symbol}")
        ledger.execute(
            timestamp=first_timestamp, symbol=symbol, side="BUY",
            amount_usd=initial_value_usd * weight, reference_price=first_prices[symbol],
            fee_bps=fee_bps, slippage_bps=slippage_bps, reason="BENCHMARK_INITIAL_BUY",
        )
    # The initial pre-trade mark stays as the denominator so entry costs are
    # included in investable benchmark performance.
    for timestamp, prices in prices_by_time[1:]:
        ledger.mark(timestamp, prices)
    return {
        "metrics": performance_metrics(ledger.valuations),
        "trades": [item.as_dict() for item in ledger.trades],
        "valuations": [item.as_dict() for item in ledger.valuations],
        "methodology": "initial allocation followed by buy-and-hold; no daily rebalancing",
    }


def constant_weight_rebalanced_benchmark(
    *,
    prices_by_time: Sequence[tuple[str, Mapping[str, float]]],
    weights: Mapping[str, float],
    initial_value_usd: float,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    rebalance_every: int = 1,
) -> dict[str, Any]:
    """Fixed-weight benchmark that restores its target weights on a schedule.

    ``buy_and_hold_benchmark`` answers "what if nothing had been done after the
    initial allocation".  This answers "what if the starting weights had been
    mechanically maintained instead".  The gap between the two is what the
    strategy's own rebalancing has to justify, so both must be reported
    together.  ``USD`` is the uninvested cash leg and earns nothing, exactly as
    in ``buy_and_hold_benchmark``.

    ``rebalance_every`` counts aligned price points: 1 restores the weights at
    every mark, 2 at every second mark, and so on.  A rebalance smaller than a
    relative ``1e-9`` of the portfolio is skipped so float noise cannot
    manufacture trades.

    A mark always shows the state carried into it, so rebalancing costs land in
    the following valuation point.  This is the same treatment the initial
    entry costs already receive in ``buy_and_hold_benchmark``.
    """
    if len(prices_by_time) < 2:
        raise ValueError("benchmark needs at least two aligned price points")
    if isinstance(rebalance_every, bool) or not isinstance(rebalance_every, int) or rebalance_every < 1:
        raise ValueError("rebalance_every must be a positive integer count of aligned price points")
    normalized_weights = {
        str(symbol).strip().upper(): _number(weight, f"weights.{symbol}", minimum=0)
        for symbol, weight in weights.items()
    }
    if not math.isclose(sum(normalized_weights.values()), 1.0, abs_tol=1e-9):
        raise ValueError("benchmark weights must sum to 1")
    invested = sorted(symbol for symbol, weight in normalized_weights.items() if symbol != "USD" and weight > 0)

    initial_value = _number(initial_value_usd, "initial_value_usd", minimum=0)
    first_timestamp, first_prices = prices_by_time[0]
    ledger = QuantityLedger(initial_value, {})
    ledger.mark(first_timestamp, first_prices)
    for symbol in invested:
        if symbol not in first_prices:
            raise ValueError(f"benchmark initial price is missing for {symbol}")
        ledger.execute(
            timestamp=first_timestamp, symbol=symbol, side="BUY",
            amount_usd=initial_value * normalized_weights[symbol], reference_price=first_prices[symbol],
            fee_bps=fee_bps, slippage_bps=slippage_bps, reason="BENCHMARK_INITIAL_BUY",
        )

    for index, (timestamp, prices) in enumerate(prices_by_time[1:], start=1):
        ledger.mark(timestamp, prices)
        if index % rebalance_every:
            continue
        point = ledger.valuations[-1]
        normalized_prices = _prices(prices)
        dust = 1e-9 * point.total_value_usd
        targets: list[tuple[str, float, float]] = []
        for symbol in invested:
            if symbol not in normalized_prices:
                raise ValueError(f"benchmark rebalance is missing a price for {symbol}")
            delta = point.total_value_usd * normalized_weights[symbol] - point.positions_usd.get(symbol, 0.0)
            if abs(delta) <= dust:
                continue
            targets.append((symbol, delta, normalized_prices[symbol]))
        # Reductions run first so the following increases are funded by the
        # proceeds instead of being clipped by the available cash.
        for symbol, delta, price in targets:
            if delta >= 0:
                continue
            ledger.execute(
                timestamp=timestamp, symbol=symbol, side="SELL", amount_usd=-delta,
                reference_price=price, fee_bps=fee_bps, slippage_bps=slippage_bps,
                reason="BENCHMARK_REBALANCE",
            )
        for symbol, delta, price in targets:
            if delta <= 0:
                continue
            ledger.execute(
                timestamp=timestamp, symbol=symbol, side="BUY", amount_usd=delta,
                reference_price=price, fee_bps=fee_bps, slippage_bps=slippage_bps,
                reason="BENCHMARK_REBALANCE",
            )

    return {
        "metrics": performance_metrics(ledger.valuations),
        "trades": [item.as_dict() for item in ledger.trades],
        "valuations": [item.as_dict() for item in ledger.valuations],
        "methodology": (
            f"target weights restored every {rebalance_every} aligned price point(s); "
            "the USD leg is uninvested cash and earns nothing"
        ),
    }


__all__ = [
    "LedgerTrade", "QuantityLedger", "ValuationPoint", "buy_and_hold_benchmark",
    "constant_weight_rebalanced_benchmark", "performance_metrics",
]
