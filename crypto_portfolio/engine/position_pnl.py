"""Deterministic unrealized P&L calculations for remaining positions."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..models.performance import PortfolioPerformanceSummary, PositionPerformance
from ..models.portfolio import Position, PortfolioSnapshot, snapshot_from_mapping


# Binance displays prices and P&L rounded to a small number of decimals; a
# comparison passes within max($0.05, 0.5% of the expected value).
DISPLAY_ABSOLUTE_TOLERANCE_USD = 0.05
DISPLAY_RELATIVE_TOLERANCE = 0.005


def _finite(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return value


def _comparison(actual: float, expected: float) -> str:
    if actual == expected:
        return "PASS"
    if math.isclose(
        actual,
        expected,
        rel_tol=DISPLAY_RELATIVE_TOLERANCE,
        abs_tol=DISPLAY_ABSOLUTE_TOLERANCE_USD,
    ):
        return "ROUNDING_WARNING"
    return "MATERIAL_MISMATCH"


def _worst_status(current: str, candidate: str) -> str:
    order = {"PASS": 0, "ROUNDING_WARNING": 1, "MATERIAL_MISMATCH": 2, "INSUFFICIENT_DATA": 1}
    return candidate if order[candidate] > order[current] else current


def calculate_position_performance(
    position: Position,
    *,
    portfolio_total_usd: float,
) -> PositionPerformance:
    """Calculate one position's unrealized P&L from its current value and cost."""
    if not isinstance(position, Position):
        raise ValueError("position must be a Position")
    portfolio_total_usd = _finite(portfolio_total_usd, "portfolio_total_usd", minimum=0)
    if portfolio_total_usd <= 0:
        raise ValueError("portfolio_total_usd must be > 0")

    quantity = position.quantity
    source_price = position.current_price_usd
    current_price = source_price
    notes: list[str] = []
    validation_status = "PASS"

    if quantity == 0 and position.value_usd > 0:
        validation_status = _worst_status(validation_status, "MATERIAL_MISMATCH")
        notes.append("quantity is zero while value_usd is positive")
    elif quantity is not None and quantity > 0:
        if source_price is None:
            if position.value_usd > 0:
                current_price = position.value_usd / quantity
                notes.append("current price derived from value_usd / quantity")
        elif source_price == 0 and position.value_usd > 0:
            current_price = position.value_usd / quantity
            notes.append("displayed current price was $0.00; used value_usd / quantity")
            validation_status = _worst_status(validation_status, "ROUNDING_WARNING")
        else:
            check = _comparison(position.value_usd, quantity * source_price)
            if check != "PASS":
                validation_status = _worst_status(validation_status, check)
                notes.append(
                    "value_usd differs from quantity * current_price_usd "
                    f"({position.value_usd:.8g} vs {quantity * source_price:.8g})"
                )

    average_cost = position.average_cost_price_usd
    cost_basis = position.cost_basis_usd
    if quantity is not None and average_cost is not None:
        expected_cost_basis = quantity * average_cost
        cost_basis = expected_cost_basis
        if position.cost_basis_usd is not None:
            check = _comparison(position.cost_basis_usd, expected_cost_basis)
            if check != "PASS":
                validation_status = _worst_status(validation_status, check)
                notes.append(
                    "cost_basis_usd differs from quantity * average_cost_price_usd "
                    f"({position.cost_basis_usd:.8g} vs {expected_cost_basis:.8g})"
                )
    elif cost_basis is not None and quantity is not None and quantity > 0:
        average_cost = cost_basis / quantity
        notes.append("average cost price derived from cost_basis_usd / quantity")

    if cost_basis is None:
        pnl_status = "INSUFFICIENT_DATA" if average_cost is not None else "COST_UNKNOWN"
        validation_status = _worst_status(validation_status, "INSUFFICIENT_DATA")
    elif cost_basis == 0:
        pnl_status = "ZERO_COST"
    else:
        pnl_status = "AVAILABLE"

    unrealized_pnl = None if cost_basis is None else position.value_usd - cost_basis
    unrealized_return = (
        None
        if cost_basis is None or cost_basis <= 0 or unrealized_pnl is None
        else unrealized_pnl / cost_basis
    )

    if (
        position.exchange_unrealized_pnl_usd is not None
        and unrealized_pnl is not None
    ):
        check = _comparison(position.exchange_unrealized_pnl_usd, unrealized_pnl)
        if check != "PASS":
            validation_status = _worst_status(validation_status, check)
            notes.append(
                "exchange_unrealized_pnl_usd differs from value_usd - cost_basis_usd "
                f"({position.exchange_unrealized_pnl_usd:.8g} vs {unrealized_pnl:.8g})"
            )

    if validation_status == "MATERIAL_MISMATCH":
        pnl_status = "MATERIAL_MISMATCH"
        unrealized_pnl = None
        unrealized_return = None
    elif validation_status == "ROUNDING_WARNING" and pnl_status == "AVAILABLE":
        pnl_status = "CROSSCHECK_WARNING"

    return PositionPerformance(
        symbol=position.symbol,
        quantity=quantity,
        current_price_usd=current_price,
        average_cost_price_usd=average_cost,
        current_value_usd=position.value_usd,
        cost_basis_usd=cost_basis,
        unrealized_pnl_usd=unrealized_pnl,
        unrealized_return_pct=unrealized_return,
        portfolio_weight=position.value_usd / portfolio_total_usd,
        pnl_status=pnl_status,
        validation_status=validation_status,
        validation_notes=tuple(notes),
    )


def _coerce_snapshot(snapshot: PortfolioSnapshot | Mapping[str, Any]) -> PortfolioSnapshot:
    if isinstance(snapshot, PortfolioSnapshot):
        return snapshot
    if isinstance(snapshot, Mapping):
        return snapshot_from_mapping(snapshot)[0]
    raise ValueError("snapshot must be a PortfolioSnapshot or mapping")


def calculate_portfolio_position_performance(
    snapshot: PortfolioSnapshot | Mapping[str, Any],
) -> PortfolioPerformanceSummary:
    """Calculate position performance and aggregate only usable cost data."""
    snapshot = _coerce_snapshot(snapshot)
    total = snapshot.total_value_usd
    if total <= 0:
        raise ValueError("portfolio total must be > 0")
    positions = tuple(
        calculate_position_performance(position, portfolio_total_usd=total)
        for position in snapshot.positions
    )
    known = tuple(position for position in positions if position.has_usable_cost)
    cost_known_value = sum(position.current_value_usd for position in known)
    cost_known_basis = sum(position.cost_basis_usd or 0.0 for position in known)
    total_pnl = (
        sum(position.unrealized_pnl_usd or 0.0 for position in known) if known else None
    )
    aggregate_return = (
        total_pnl / cost_known_basis
        if total_pnl is not None and cost_known_basis > 0
        else None
    )
    notes = [
        f"{position.symbol}: {note}"
        for position in positions
        for note in position.validation_notes
        if position.validation_status in {"ROUNDING_WARNING", "MATERIAL_MISMATCH"}
    ]
    return PortfolioPerformanceSummary(
        total_portfolio_value_usd=total,
        cost_known_current_value_usd=cost_known_value,
        cost_known_cost_basis_usd=cost_known_basis,
        total_unrealized_pnl_known_usd=total_pnl,
        aggregate_unrealized_return_pct=aggregate_return,
        pnl_value_coverage_ratio=cost_known_value / total,
        positions=positions,
        validation_notes=tuple(notes),
    )


def position_performance_record(
    position: Position,
    performance: PositionPerformance,
) -> dict[str, Any]:
    """Serialize source observations and derived performance together."""
    record = position.as_dict()
    record.update(
        {
            "current_price_usd": performance.current_price_usd,
            "average_cost_price_usd": performance.average_cost_price_usd,
            "cost_basis_usd": performance.cost_basis_usd,
            "unrealized_pnl_usd": performance.unrealized_pnl_usd,
            "unrealized_return_pct": performance.unrealized_return_pct,
            "pnl_status": performance.pnl_status,
            "validation_status": performance.validation_status,
            "validation_notes": list(performance.validation_notes),
            "computed_weight": performance.portfolio_weight,
            "performance": {
                "unrealized_pnl_usd": performance.unrealized_pnl_usd,
                "unrealized_return_pct": performance.unrealized_return_pct,
                "pnl_status": performance.pnl_status,
                "validation_status": performance.validation_status,
                "validation_notes": list(performance.validation_notes),
            },
        }
    )
    if position.current_price_usd != performance.current_price_usd:
        record["displayed_current_price_usd"] = position.current_price_usd
    if position.average_cost_price_usd != performance.average_cost_price_usd:
        record["displayed_average_cost_price_usd"] = position.average_cost_price_usd
    return record


__all__ = [
    "DISPLAY_ABSOLUTE_TOLERANCE_USD",
    "DISPLAY_RELATIVE_TOLERANCE",
    "calculate_portfolio_position_performance",
    "calculate_position_performance",
    "position_performance_record",
]
