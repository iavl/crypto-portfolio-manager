"""Validated position-level unrealized performance models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


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
    """Deterministic unrealized performance for one remaining position.

    Return values are stored as decimal fractions, despite the historical
    ``*_pct`` field name.  For example, ``-0.1`` is a -10% return.
    """

    symbol: str
    quantity: float | None
    current_price_usd: float | None
    average_cost_price_usd: float | None
    current_value_usd: float
    cost_basis_usd: float | None
    unrealized_pnl_usd: float | None
    unrealized_return_pct: float | None
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
            "unrealized_return_pct",
            _optional_number(
                self.unrealized_return_pct,
                f"performance {self.symbol}.unrealized_return_pct",
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
        if self.unrealized_return_pct is not None and (
            self.cost_basis_usd is None or self.cost_basis_usd <= 0 or self.unrealized_pnl_usd is None
        ):
            raise ValueError("unrealized_return_pct requires positive cost_basis_usd and unrealized_pnl_usd")
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

    @property
    def computed_weight(self) -> float:
        return self.portfolio_weight

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "current_price_usd": self.current_price_usd,
            "average_cost_price_usd": self.average_cost_price_usd,
            "current_value_usd": self.current_value_usd,
            "cost_basis_usd": self.cost_basis_usd,
            "unrealized_pnl_usd": self.unrealized_pnl_usd,
            "unrealized_return_pct": self.unrealized_return_pct,
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
    aggregate_unrealized_return_pct: float | None
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
            "aggregate_unrealized_return_pct",
            _optional_number(self.aggregate_unrealized_return_pct, "aggregate_unrealized_return_pct"),
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

    @property
    def total_unrealized_pnl_usd(self) -> float | None:
        return self.total_unrealized_pnl_known_usd

    @property
    def cost_coverage_ratio(self) -> float:
        return self.pnl_value_coverage_ratio

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_portfolio_value_usd": self.total_portfolio_value_usd,
            "cost_known_current_value_usd": self.cost_known_current_value_usd,
            "cost_known_cost_basis_usd": self.cost_known_cost_basis_usd,
            "total_unrealized_pnl_known_usd": self.total_unrealized_pnl_known_usd,
            "aggregate_unrealized_return_pct": self.aggregate_unrealized_return_pct,
            "pnl_value_coverage_ratio": self.pnl_value_coverage_ratio,
            "positions": [position.as_dict() for position in self.positions],
            "validation_notes": list(self.validation_notes),
        }


__all__ = [
    "PNL_STATUSES",
    "VALIDATION_STATUSES",
    "PortfolioPerformanceSummary",
    "PositionPerformance",
]
