"""Normalize structured fields extracted from a Binance wallet screenshot.

Image recognition is intentionally outside this module.  The Agent reads the
visible table and supplies these observations; the portfolio engine performs
all financial calculations afterward.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.policy import Policy
from ..models.portfolio import PortfolioSnapshot, normalize_snapshot, snapshot_from_mapping
from ..models.time import normalize_timestamp


BINANCE_WALLET_SCREENSHOT_SOURCE = "binance_wallet_overview_screenshot"


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return value


def _optional_display_number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
) -> float | None:
    if value is None or value == "--":
        return None
    return _number(value, field, minimum=minimum)


@dataclass(frozen=True)
class BinancePositionObservation:
    symbol: str
    quantity: float | None
    displayed_value_usd: float
    displayed_current_price_usd: float | None = None
    displayed_average_cost_price_usd: float | None = None
    displayed_unrealized_pnl_usd: float | None = None
    cost_available: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("Binance position symbol must be a non-empty string")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if self.quantity is not None:
            object.__setattr__(
                self,
                "quantity",
                _number(self.quantity, f"Binance position {self.symbol}.quantity", minimum=0),
            )
        object.__setattr__(
            self,
            "displayed_value_usd",
            _number(self.displayed_value_usd, f"Binance position {self.symbol}.value_usd", minimum=0),
        )
        for field in (
            "displayed_current_price_usd",
            "displayed_average_cost_price_usd",
        ):
            object.__setattr__(
                self,
                field,
                _optional_display_number(
                    getattr(self, field),
                    f"Binance position {self.symbol}.{field}",
                    minimum=0,
                ),
            )
        object.__setattr__(
            self,
            "displayed_unrealized_pnl_usd",
            _optional_display_number(
                self.displayed_unrealized_pnl_usd,
                f"Binance position {self.symbol}.displayed_unrealized_pnl_usd",
            ),
        )
        if self.cost_available is not None and not isinstance(self.cost_available, bool):
            raise ValueError("Binance position cost_available must be a boolean or null")
        inferred_cost_available = self.displayed_average_cost_price_usd is not None
        if self.cost_available is not None and self.cost_available != inferred_cost_available:
            raise ValueError("cost_available does not match displayed average cost price")
        object.__setattr__(self, "cost_available", inferred_cost_available)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BinancePositionObservation":
        if not isinstance(value, Mapping):
            raise ValueError("Binance position observation must be an object")
        if "displayed_value_usd" in value:
            displayed_value = value["displayed_value_usd"]
        elif "value_usd" in value:
            displayed_value = value["value_usd"]
        else:
            raise ValueError("Binance position observation is missing displayed_value_usd")
        return cls(
            symbol=value.get("symbol", ""),
            quantity=value.get("quantity"),
            displayed_value_usd=displayed_value,
            displayed_current_price_usd=value.get(
                "displayed_current_price_usd", value.get("current_price_usd")
            ),
            displayed_average_cost_price_usd=value.get(
                "displayed_average_cost_price_usd", value.get("average_cost_price_usd")
            ),
            displayed_unrealized_pnl_usd=value.get(
                "displayed_unrealized_pnl_usd", value.get("exchange_unrealized_pnl_usd")
            ),
            cost_available=value.get("cost_available"),
        )

    def to_position_mapping(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "value_usd": self.displayed_value_usd,
            "current_price_usd": self.displayed_current_price_usd,
            "average_cost_price_usd": self.displayed_average_cost_price_usd,
            "exchange_unrealized_pnl_usd": self.displayed_unrealized_pnl_usd,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "displayed_value_usd": self.displayed_value_usd,
            "displayed_current_price_usd": self.displayed_current_price_usd,
            "displayed_average_cost_price_usd": self.displayed_average_cost_price_usd,
            "displayed_unrealized_pnl_usd": self.displayed_unrealized_pnl_usd,
            "cost_available": self.cost_available,
        }


@dataclass(frozen=True)
class BinancePortfolioObservation:
    captured_at: str
    display_currency: str = "USD"
    reported_total_value: float | None = None
    positions: tuple[BinancePositionObservation, ...] = ()
    source: str = BINANCE_WALLET_SCREENSHOT_SOURCE

    def __post_init__(self) -> None:
        object.__setattr__(self, "captured_at", normalize_timestamp(self.captured_at, "captured_at"))
        if not isinstance(self.display_currency, str) or not self.display_currency.strip():
            raise ValueError("display_currency must be a non-empty string")
        currency = self.display_currency.strip().upper()
        if currency != "USD":
            raise ValueError("Binance screenshot P&L import requires display_currency USD")
        object.__setattr__(self, "display_currency", currency)
        if self.reported_total_value is not None:
            object.__setattr__(
                self,
                "reported_total_value",
                _optional_display_number(
                    self.reported_total_value,
                    "reported_total_value",
                    minimum=0,
                ),
            )
        positions = tuple(
            position
            if isinstance(position, BinancePositionObservation)
            else BinancePositionObservation.from_mapping(position)
            for position in self.positions
        )
        if not positions:
            raise ValueError("Binance screenshot must contain at least one position")
        symbols = [position.symbol for position in positions]
        if len(symbols) != len(set(symbols)):
            raise ValueError("Binance screenshot contains duplicate position symbols")
        object.__setattr__(self, "positions", positions)
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")
        object.__setattr__(self, "source", self.source.strip())

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BinancePortfolioObservation":
        if not isinstance(value, Mapping):
            raise ValueError("Binance portfolio observation must be an object")
        raw_positions = value.get("positions")
        if not isinstance(raw_positions, (list, tuple)):
            raise ValueError("Binance portfolio observation positions must be a list")
        captured_at = value.get("captured_at", value.get("timestamp"))
        if captured_at is None:
            raise ValueError("Binance portfolio observation is missing captured_at")
        return cls(
            captured_at=captured_at,
            display_currency=value.get("display_currency", value.get("base_currency", "USD")),
            reported_total_value=value.get(
                "reported_total_value", value.get("reported_total_value_usd")
            ),
            positions=tuple(BinancePositionObservation.from_mapping(item) for item in raw_positions),
            source=value.get("source", BINANCE_WALLET_SCREENSHOT_SOURCE),
        )

    def to_snapshot_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "timestamp": self.captured_at,
            "source": self.source,
            "base_currency": "USD",
            "positions": [position.to_position_mapping() for position in self.positions],
        }
        if self.reported_total_value is not None:
            result["reported_total_value"] = self.reported_total_value
        return result

    def to_snapshot(self, *, policy: Policy | None = None) -> PortfolioSnapshot:
        return snapshot_from_mapping(self.to_snapshot_mapping(), policy=policy)[0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at,
            "display_currency": self.display_currency,
            "reported_total_value": self.reported_total_value,
            "positions": [position.as_dict() for position in self.positions],
            "source": self.source,
        }


def snapshot_from_binance_observation(
    observation: BinancePortfolioObservation | Mapping[str, Any],
    *,
    policy: Policy | None = None,
) -> tuple[PortfolioSnapshot, Policy, list[str]]:
    if not isinstance(observation, BinancePortfolioObservation):
        observation = BinancePortfolioObservation.from_mapping(observation)
    return snapshot_from_mapping(observation.to_snapshot_mapping(), policy=policy)


def normalize_binance_observation(
    observation: BinancePortfolioObservation | Mapping[str, Any],
    *,
    policy: Policy | None = None,
) -> dict[str, Any]:
    snapshot, _, _ = snapshot_from_binance_observation(observation, policy=policy)
    return normalize_snapshot(snapshot.as_dict(), policy=policy)


__all__ = [
    "BINANCE_WALLET_SCREENSHOT_SOURCE",
    "BinancePortfolioObservation",
    "BinancePositionObservation",
    "normalize_binance_observation",
    "snapshot_from_binance_observation",
]
