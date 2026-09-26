"""Canonical resting-order records observed by a read-only exchange view."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .time import normalize_timestamp


_SIDES = {"BUY", "SELL"}
# Order types that rest on the book at a chosen price; anything else
# (MARKET, STOP variants, trailing stops) is not a plan-equivalent order.
RESTING_ORDER_TYPES = frozenset({"LIMIT", "LIMIT_MAKER"})


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _number(value: Any, field: str, *, minimum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{field} is invalid")
    return result


@dataclass(frozen=True)
class OpenOrderRecord:
    """One currently resting spot order with price and remaining quantity.

    Open orders are point-in-time observations of the exchange order book, not
    append-only history: each read-only fetch replaces the previous snapshot.
    ``executed_quantity`` is the venue-reported cumulative fill, so the resting
    remainder is ``orig_quantity - executed_quantity``.
    """

    order_id: str
    symbol: str
    side: str
    order_type: str
    price: float
    orig_quantity: float
    executed_quantity: float
    status: str
    time_in_force: str
    created_at: str
    source: str = "binance_api_account"
    fetched_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_id", _text(self.order_id, "order_id"))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        side = _text(self.side, "side").upper()
        if side not in _SIDES:
            raise ValueError(f"side must be one of {sorted(_SIDES)}")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "order_type", _text(self.order_type, "order_type").upper())
        object.__setattr__(self, "status", _text(self.status, "status").upper())
        object.__setattr__(self, "time_in_force", _text(self.time_in_force, "time_in_force").upper())
        price = _number(self.price, "price", minimum=0.0)
        orig = _number(self.orig_quantity, "orig_quantity", minimum=0.0)
        executed = _number(self.executed_quantity, "executed_quantity", minimum=0.0)
        if price <= 0 or orig <= 0:
            raise ValueError("price and orig_quantity must be > 0")
        if executed > orig:
            raise ValueError("executed_quantity must not exceed orig_quantity")
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "orig_quantity", orig)
        object.__setattr__(self, "executed_quantity", executed)
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "source", _text(self.source, "source"))
        if self.fetched_at is not None:
            object.__setattr__(
                self, "fetched_at", normalize_timestamp(self.fetched_at, "fetched_at")
            )

    @property
    def is_resting_limit(self) -> bool:
        return self.order_type in RESTING_ORDER_TYPES

    @property
    def remaining_quantity(self) -> float:
        return self.orig_quantity - self.executed_quantity

    @property
    def remaining_notional_usd(self) -> float:
        return self.price * self.remaining_quantity

    def as_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "price": self.price,
            "orig_quantity": self.orig_quantity,
            "executed_quantity": self.executed_quantity,
            "status": self.status,
            "time_in_force": self.time_in_force,
            "created_at": self.created_at,
            "source": self.source,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OpenOrderRecord":
        if not isinstance(value, Mapping):
            raise ValueError("open order must be an object")
        required = {
            "order_id", "symbol", "side", "order_type", "price", "orig_quantity",
            "executed_quantity", "status", "time_in_force", "created_at",
        }
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(f"open order is missing fields: {', '.join(missing)}")
        allowed = required | {"source", "fetched_at"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"open order contains unknown fields: {', '.join(unknown)}")
        return cls(**{field: value[field] for field in sorted(allowed) if field in value})

    @classmethod
    def from_account_order(cls, order: Any, *, fetched_at: str, source: str = "binance_api_account") -> "OpenOrderRecord":
        created_at = datetime.fromtimestamp(
            order.timestamp_ms / 1000.0, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return cls(
            order_id=str(order.order_id),
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            price=order.price,
            orig_quantity=order.orig_quantity,
            executed_quantity=order.executed_quantity,
            status=order.status,
            time_in_force=order.time_in_force,
            created_at=created_at,
            source=source,
            fetched_at=fetched_at,
        )


__all__ = ["OpenOrderRecord", "RESTING_ORDER_TYPES"]
