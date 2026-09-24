"""Canonical executed-trade records confirmed by a read-only exchange view."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .time import normalize_timestamp


_SIDES = {"BUY", "SELL"}


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
class TradeFill:
    """One exchange-confirmed spot execution with price, quantity, and fee.

    ``notional`` is the executed quote amount (quantity x price as reported by
    the venue) in the snapshot quote currency USD. Trade ids are unique per
    symbol, so the persisted dedup key is ``symbol:fill_id``.
    """

    fill_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    notional: float
    fee: float
    fee_asset: str
    executed_at: str
    order_id: str | None = None
    source: str = "binance_api_account"
    fetched_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fill_id", _text(self.fill_id, "fill_id"))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        side = _text(self.side, "side").upper()
        if side not in _SIDES:
            raise ValueError(f"side must be one of {sorted(_SIDES)}")
        object.__setattr__(self, "side", side)
        quantity = _number(self.quantity, "quantity", minimum=0.0)
        price = _number(self.price, "price", minimum=0.0)
        notional = _number(self.notional, "notional", minimum=0.0)
        fee = _number(self.fee, "fee", minimum=0.0)
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity and price must be > 0")
        if not math.isclose(notional, quantity * price, rel_tol=1e-3, abs_tol=1e-9):
            raise ValueError("notional must equal quantity x price")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "notional", notional)
        object.__setattr__(self, "fee", fee)
        object.__setattr__(self, "fee_asset", _text(self.fee_asset, "fee_asset").upper())
        object.__setattr__(self, "executed_at", normalize_timestamp(self.executed_at, "executed_at"))
        if self.order_id is not None:
            object.__setattr__(self, "order_id", _text(self.order_id, "order_id"))
        object.__setattr__(self, "source", _text(self.source, "source"))
        if self.fetched_at is not None:
            object.__setattr__(
                self, "fetched_at", normalize_timestamp(self.fetched_at, "fetched_at")
            )

    @property
    def dedup_key(self) -> str:
        return f"{self.symbol}:{self.fill_id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "notional": self.notional,
            "fee": self.fee,
            "fee_asset": self.fee_asset,
            "executed_at": self.executed_at,
            "order_id": self.order_id,
            "source": self.source,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TradeFill":
        if not isinstance(value, Mapping):
            raise ValueError("trade fill must be an object")
        required = {
            "fill_id", "symbol", "side", "quantity", "price", "notional",
            "fee", "fee_asset", "executed_at",
        }
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(f"trade fill is missing fields: {', '.join(missing)}")
        allowed = required | {"order_id", "source", "fetched_at"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"trade fill contains unknown fields: {', '.join(unknown)}")
        return cls(**{field: value[field] for field in sorted(allowed) if field in value})

    @classmethod
    def from_account_trade(cls, trade: Any, *, fetched_at: str, source: str = "binance_api_account") -> "TradeFill":
        executed_at = datetime.fromtimestamp(
            trade.timestamp_ms / 1000.0, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return cls(
            fill_id=str(trade.trade_id),
            symbol=trade.symbol,
            side=trade.side,
            quantity=trade.quantity,
            price=trade.price,
            notional=trade.quote_quantity,
            fee=trade.commission,
            fee_asset=trade.commission_asset,
            executed_at=executed_at,
            order_id=str(trade.order_id),
            source=source,
            fetched_at=fetched_at,
        )


__all__ = ["TradeFill"]
