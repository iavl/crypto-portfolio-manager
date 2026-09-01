"""Unitized NAV accounting for portfolio history."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


def _finite(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: str
    portfolio_value: float
    external_cash_flow: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, str) or not self.timestamp.strip():
            raise ValueError("timestamp must be a non-empty string")
        object.__setattr__(self, "timestamp", self.timestamp.strip())
        object.__setattr__(
            self,
            "portfolio_value",
            _finite(self.portfolio_value, "portfolio_value", minimum=0),
        )
        cash_flow = (
            self.external_cash_flow.amount
            if isinstance(self.external_cash_flow, ExternalCashFlow)
            else self.external_cash_flow
        )
        object.__setattr__(self, "external_cash_flow", _finite(cash_flow, "external_cash_flow"))


@dataclass(frozen=True)
class ExternalCashFlow:
    timestamp: str
    amount: float
    description: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, str) or not self.timestamp.strip():
            raise ValueError("cash flow timestamp must be a non-empty string")
        object.__setattr__(self, "timestamp", self.timestamp.strip())
        object.__setattr__(self, "amount", _finite(self.amount, "cash flow amount"))
        if self.description is not None and not isinstance(self.description, str):
            raise ValueError("cash flow description must be a string or null")


@dataclass(frozen=True)
class NAVState:
    timestamp: str
    portfolio_value: float
    external_cash_flow: float
    units: float
    nav_per_unit: float
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, str) or not self.timestamp.strip():
            raise ValueError("timestamp must be a non-empty string")
        for field in ("portfolio_value", "units", "nav_per_unit"):
            _finite(getattr(self, field), field, minimum=0)
        _finite(self.external_cash_flow, "external_cash_flow")
        current_drawdown = _finite(self.current_drawdown, "current_drawdown")
        max_drawdown = _finite(self.max_drawdown, "max_drawdown")
        if current_drawdown > 0 or max_drawdown > 0:
            raise ValueError("drawdown values must be <= 0")


def _coerce_snapshot(value: PortfolioSnapshot | Mapping[str, Any] | Any) -> PortfolioSnapshot:
    if isinstance(value, PortfolioSnapshot):
        return value
    if isinstance(value, Mapping):
        if "portfolio_value" in value:
            portfolio_value = value["portfolio_value"]
        elif "total_value_usd" in value:
            portfolio_value = value["total_value_usd"]
        elif "total_value" in value:
            portfolio_value = value["total_value"]
        else:
            raise ValueError("snapshot is missing portfolio_value")
        cash_flow = value.get("external_cash_flow", 0.0)
        if isinstance(cash_flow, ExternalCashFlow):
            cash_flow = cash_flow.amount
        return PortfolioSnapshot(
            timestamp=value.get("timestamp", ""),
            portfolio_value=portfolio_value,
            external_cash_flow=value.get("external_cash_flow", 0.0),
        )
    if hasattr(value, "timestamp") and hasattr(value, "total_value_usd"):
        return PortfolioSnapshot(
            timestamp=value.timestamp,
            portfolio_value=value.total_value_usd,
            external_cash_flow=getattr(value, "external_cash_flow", 0.0),
        )
    raise ValueError("snapshots must contain PortfolioSnapshot objects or mappings")


def build_nav_history(
    snapshots: Sequence[PortfolioSnapshot | Mapping[str, Any] | Any],
) -> list[NAVState]:
    """Build NAV states assuming each cash flow occurs before that snapshot's valuation."""
    if not snapshots:
        raise ValueError("at least one snapshot is required")
    values = [_coerce_snapshot(snapshot) for snapshot in snapshots]
    if any(current.timestamp <= previous.timestamp for previous, current in zip(values, values[1:])):
        raise ValueError("snapshot timestamps must be strictly increasing")
    if values[0].portfolio_value <= 0:
        raise ValueError("initial portfolio_value must be > 0")

    units = values[0].portfolio_value
    nav = 1.0
    peak_nav = nav
    worst_drawdown = 0.0
    result = [
        NAVState(
            timestamp=values[0].timestamp,
            portfolio_value=values[0].portfolio_value,
            external_cash_flow=values[0].external_cash_flow,
            units=units,
            nav_per_unit=nav,
            current_drawdown=0.0,
            max_drawdown=0.0,
        )
    ]
    for snapshot in values[1:]:
        units += snapshot.external_cash_flow / nav
        if units <= 0:
            raise ValueError("external cash flow leaves no positive NAV units")
        if snapshot.portfolio_value < 0:
            raise ValueError("portfolio_value must be >= 0")
        nav = snapshot.portfolio_value / units
        if nav <= 0 or not math.isfinite(nav):
            raise ValueError("portfolio_value must produce a positive finite NAV")
        peak_nav = max(peak_nav, nav)
        drawdown = nav / peak_nav - 1.0
        worst_drawdown = min(worst_drawdown, drawdown)
        result.append(
            NAVState(
                timestamp=snapshot.timestamp,
                portfolio_value=snapshot.portfolio_value,
                external_cash_flow=snapshot.external_cash_flow,
                units=units,
                nav_per_unit=nav,
                current_drawdown=drawdown,
                max_drawdown=worst_drawdown,
            )
        )
    return result


def nav_return(states: Sequence[NAVState]) -> float:
    if not states:
        raise ValueError("at least one NAV state is required")
    return states[-1].nav_per_unit / states[0].nav_per_unit - 1.0


def current_drawdown(states: Sequence[NAVState]) -> float:
    if not states:
        raise ValueError("at least one NAV state is required")
    return states[-1].current_drawdown


def max_drawdown(states: Sequence[NAVState]) -> float:
    if not states:
        raise ValueError("at least one NAV state is required")
    return min(state.max_drawdown for state in states)


def cash_flow_adjusted_return(
    snapshots: Sequence[PortfolioSnapshot | Mapping[str, Any] | Any],
) -> float:
    return nav_return(build_nav_history(snapshots))


calculate_nav = build_nav_history
nav_history = build_nav_history


__all__ = [
    "ExternalCashFlow",
    "NAVState",
    "PortfolioSnapshot",
    "build_nav_history",
    "cash_flow_adjusted_return",
    "current_drawdown",
    "max_drawdown",
    "nav_return",
]
