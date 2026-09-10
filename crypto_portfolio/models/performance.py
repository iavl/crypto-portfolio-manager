"""Validated position-level unrealized performance models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence


PNL_STATUSES = frozenset(
    {
        "AVAILABLE",
        "COST_UNKNOWN",
        "ZERO_COST",
        "INSUFFICIENT_DATA",
        "CROSSCHECK_WARNING",
        "MATERIAL_MISMATCH",
    }
)
VALIDATION_STATUSES = frozenset(
    {"PASS", "ROUNDING_WARNING", "MATERIAL_MISMATCH", "INSUFFICIENT_DATA"}
)
NAV_HISTORY_STATUSES = frozenset({"AVAILABLE", "PROVISIONAL", "UNAVAILABLE"})
PERFORMANCE_FINALITIES = frozenset({"FINAL", "PROVISIONAL", "UNAVAILABLE"})


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return value


def _optional_number(value: Any, field: str, *, minimum: float | None = None) -> float | None:
    return None if value is None else _number(value, field, minimum=minimum)


@dataclass(frozen=True)
class PositionPerformance:
    """Deterministic unrealized performance for one remaining position."""

    symbol: str
    quantity: float | None
    current_price_usd: float | None
    average_cost_price_usd: float | None
    current_value_usd: float
    cost_basis_usd: float | None
    unrealized_pnl_usd: float | None
    unrealized_return: float | None
    portfolio_weight: float
    pnl_status: str
    validation_status: str = "PASS"
    validation_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("performance.symbol must be a non-empty string")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(
            self,
            "quantity",
            _optional_number(self.quantity, f"performance {self.symbol}.quantity", minimum=0),
        )
        object.__setattr__(
            self,
            "current_price_usd",
            _optional_number(
                self.current_price_usd,
                f"performance {self.symbol}.current_price_usd",
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "average_cost_price_usd",
            _optional_number(
                self.average_cost_price_usd,
                f"performance {self.symbol}.average_cost_price_usd",
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "current_value_usd",
            _number(self.current_value_usd, f"performance {self.symbol}.current_value_usd", minimum=0),
        )
        object.__setattr__(
            self,
            "cost_basis_usd",
            _optional_number(
                self.cost_basis_usd,
                f"performance {self.symbol}.cost_basis_usd",
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "unrealized_pnl_usd",
            _optional_number(self.unrealized_pnl_usd, f"performance {self.symbol}.unrealized_pnl_usd"),
        )
        object.__setattr__(
            self,
            "unrealized_return",
            _optional_number(
                self.unrealized_return,
                f"performance {self.symbol}.unrealized_return",
            ),
        )
        object.__setattr__(
            self,
            "portfolio_weight",
            _number(self.portfolio_weight, f"performance {self.symbol}.portfolio_weight", minimum=0),
        )
        if self.portfolio_weight > 1:
            raise ValueError(f"performance {self.symbol}.portfolio_weight must be <= 1")
        if not isinstance(self.pnl_status, str):
            raise ValueError("pnl_status must be a string")
        object.__setattr__(self, "pnl_status", self.pnl_status.upper())
        if self.pnl_status not in PNL_STATUSES:
            raise ValueError(f"pnl_status must be one of {sorted(PNL_STATUSES)}")
        if not isinstance(self.validation_status, str):
            raise ValueError("validation_status must be a string")
        object.__setattr__(self, "validation_status", self.validation_status.upper())
        if self.validation_status not in VALIDATION_STATUSES:
            raise ValueError(f"validation_status must be one of {sorted(VALIDATION_STATUSES)}")
        if self.unrealized_return is not None and (
            self.cost_basis_usd is None or self.cost_basis_usd <= 0 or self.unrealized_pnl_usd is None
        ):
            raise ValueError("unrealized_return requires positive cost_basis_usd and unrealized_pnl_usd")
        notes = tuple(self.validation_notes)
        if any(not isinstance(note, str) or not note.strip() for note in notes):
            raise ValueError("validation_notes must contain non-empty strings")
        object.__setattr__(self, "validation_notes", notes)

    @property
    def has_usable_cost(self) -> bool:
        return (
            self.cost_basis_usd is not None
            and self.cost_basis_usd > 0
            and self.unrealized_pnl_usd is not None
            and self.pnl_status != "MATERIAL_MISMATCH"
            and self.validation_status != "MATERIAL_MISMATCH"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "current_price_usd": self.current_price_usd,
            "average_cost_price_usd": self.average_cost_price_usd,
            "current_value_usd": self.current_value_usd,
            "cost_basis_usd": self.cost_basis_usd,
            "unrealized_pnl_usd": self.unrealized_pnl_usd,
            "unrealized_return": self.unrealized_return,
            "portfolio_weight": self.portfolio_weight,
            "pnl_status": self.pnl_status,
            "validation_status": self.validation_status,
            "validation_notes": list(self.validation_notes),
        }


@dataclass(frozen=True)
class PortfolioPerformanceSummary:
    total_portfolio_value_usd: float
    cost_known_current_value_usd: float
    cost_known_cost_basis_usd: float
    total_unrealized_pnl_known_usd: float | None
    aggregate_unrealized_return: float | None
    pnl_value_coverage_ratio: float
    positions: tuple[PositionPerformance, ...]
    validation_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "total_portfolio_value_usd",
            _number(self.total_portfolio_value_usd, "total_portfolio_value_usd", minimum=0),
        )
        if self.total_portfolio_value_usd <= 0:
            raise ValueError("total_portfolio_value_usd must be > 0")
        for field in (
            "cost_known_current_value_usd",
            "cost_known_cost_basis_usd",
        ):
            object.__setattr__(
                self,
                field,
                _number(getattr(self, field), field, minimum=0),
            )
        object.__setattr__(
            self,
            "total_unrealized_pnl_known_usd",
            _optional_number(self.total_unrealized_pnl_known_usd, "total_unrealized_pnl_known_usd"),
        )
        object.__setattr__(
            self,
            "aggregate_unrealized_return",
            _optional_number(self.aggregate_unrealized_return, "aggregate_unrealized_return"),
        )
        object.__setattr__(
            self,
            "pnl_value_coverage_ratio",
            _number(self.pnl_value_coverage_ratio, "pnl_value_coverage_ratio", minimum=0),
        )
        if self.pnl_value_coverage_ratio > 1:
            raise ValueError("pnl_value_coverage_ratio must be <= 1")
        positions = tuple(self.positions)
        if any(not isinstance(position, PositionPerformance) for position in positions):
            raise ValueError("positions must contain PositionPerformance objects")
        object.__setattr__(self, "positions", positions)
        notes = tuple(self.validation_notes)
        if any(not isinstance(note, str) or not note.strip() for note in notes):
            raise ValueError("validation_notes must contain non-empty strings")
        object.__setattr__(self, "validation_notes", notes)

    def by_symbol(self) -> dict[str, PositionPerformance]:
        return {position.symbol: position for position in self.positions}

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_portfolio_value_usd": self.total_portfolio_value_usd,
            "cost_known_current_value_usd": self.cost_known_current_value_usd,
            "cost_known_cost_basis_usd": self.cost_known_cost_basis_usd,
            "total_unrealized_pnl_known_usd": self.total_unrealized_pnl_known_usd,
            "aggregate_unrealized_return": self.aggregate_unrealized_return,
            "pnl_value_coverage_ratio": self.pnl_value_coverage_ratio,
            "positions": [position.as_dict() for position in self.positions],
            "validation_notes": list(self.validation_notes),
        }


@dataclass(frozen=True)
class NAVHistoryResult:
    """Status-bearing cash-flow-adjusted history; unknown flows stay unknown."""

    status: str
    states: Sequence[Any] = ()
    segments: Sequence[Any] = ()
    unresolved_cash_flows: Sequence[Any] = ()
    assumed_cash_flows: Sequence[Any] = ()
    cash_flow_adjusted_return: float | None = None
    nav_return: float | None = None
    current_drawdown: float | None = None
    max_drawdown: float | None = None
    benchmark_status: str = "UNAVAILABLE"
    performance_finality: str | None = None
    btc_return: float | None = None
    btc_excess_return: float | None = None
    secondary_benchmark_return: float | None = None
    explanations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        status = str(self.status).strip().upper()
        if status not in NAV_HISTORY_STATUSES:
            raise ValueError("NAV history status is unsupported")
        benchmark_status = str(self.benchmark_status).strip().upper()
        if benchmark_status not in NAV_HISTORY_STATUSES:
            raise ValueError("benchmark_status is unsupported")
        finality = (
            ("FINAL" if status == "AVAILABLE" else status)
            if self.performance_finality is None
            else str(self.performance_finality).strip().upper()
        )
        if finality not in PERFORMANCE_FINALITIES:
            raise ValueError("performance_finality is unsupported")
        if status == "AVAILABLE" and finality != "FINAL":
            raise ValueError("available NAV history must be FINAL")
        if status == "UNAVAILABLE" and finality != "UNAVAILABLE":
            raise ValueError("unavailable NAV history must be UNAVAILABLE")
        for field_name in (
            "cash_flow_adjusted_return", "nav_return", "current_drawdown", "max_drawdown",
            "btc_return", "btc_excess_return", "secondary_benchmark_return",
        ):
            value = getattr(self, field_name)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise ValueError(f"{field_name} must be finite or null")
                object.__setattr__(self, field_name, float(value))
        states = tuple(self.states)
        segments = tuple(self.segments)
        unresolved = tuple(self.unresolved_cash_flows)
        assumed = tuple(self.assumed_cash_flows)
        explanations = tuple(str(item).strip() for item in self.explanations)
        if any(not item for item in explanations):
            raise ValueError("explanations must contain non-empty strings")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "benchmark_status", benchmark_status)
        object.__setattr__(self, "performance_finality", finality)
        object.__setattr__(self, "states", states)
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "unresolved_cash_flows", unresolved)
        object.__setattr__(self, "assumed_cash_flows", assumed)
        object.__setattr__(self, "explanations", explanations)

    def as_dict(self) -> dict[str, Any]:
        def render(value: Any) -> Any:
            if hasattr(value, "__dict__"):
                return dict(value.__dict__)
            if hasattr(value, "as_dict"):
                return value.as_dict()
            return value

        return {
            "status": self.status,
            "states": [render(item) for item in self.states],
            "segments": [render(item) for item in self.segments],
            "unresolved_cash_flows": [render(item) for item in self.unresolved_cash_flows],
            "assumed_cash_flows": [render(item) for item in self.assumed_cash_flows],
            "cash_flow_adjusted_return": self.cash_flow_adjusted_return,
            "nav_return": self.nav_return,
            "current_drawdown": self.current_drawdown,
            "max_drawdown": self.max_drawdown,
            "benchmark_status": self.benchmark_status,
            "performance_finality": self.performance_finality,
            "btc_return": self.btc_return,
            "btc_excess_return": self.btc_excess_return,
            "secondary_benchmark_return": self.secondary_benchmark_return,
            "explanations": list(self.explanations),
        }


__all__ = [
    "PNL_STATUSES",
    "VALIDATION_STATUSES",
    "PortfolioPerformanceSummary",
    "NAVHistoryResult",
    "NAV_HISTORY_STATUSES",
    "PERFORMANCE_FINALITIES",
    "PositionPerformance",
]
