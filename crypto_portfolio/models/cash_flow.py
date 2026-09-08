"""Append-only user-confirmed historical cash-flow resolutions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .time import normalize_timestamp


_FLOW_TYPES = {"DEPOSIT", "WITHDRAWAL", "NONE"}


@dataclass(frozen=True)
class CashFlowResolution:
    resolution_id: str
    snapshot_id: str
    timestamp: str
    cash_flow_type: str
    amount: float
    rationale: str

    def __post_init__(self) -> None:
        for field_name in ("resolution_id", "snapshot_id", "rationale"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp, "timestamp"))
        flow_type = str(self.cash_flow_type).strip().upper()
        if flow_type not in _FLOW_TYPES:
            raise ValueError("cash_flow_type must be DEPOSIT, WITHDRAWAL, or NONE")
        object.__setattr__(self, "cash_flow_type", flow_type)
        if isinstance(self.amount, bool) or not isinstance(self.amount, (int, float)) or not math.isfinite(float(self.amount)):
            raise ValueError("cash-flow amount must be finite")
        amount = float(self.amount)
        if flow_type == "NONE" and amount != 0:
            raise ValueError("NONE cash flow must have amount 0")
        if flow_type == "DEPOSIT" and amount <= 0:
            raise ValueError("DEPOSIT amount must be positive")
        if flow_type == "WITHDRAWAL" and amount >= 0:
            raise ValueError("WITHDRAWAL amount must be negative")
        object.__setattr__(self, "amount", amount)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "CashFlowResolution":
        if not isinstance(value, dict):
            raise ValueError("cash-flow resolution must be an object")
        allowed = {"resolution_id", "snapshot_id", "timestamp", "cash_flow_type", "amount", "rationale"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("cash-flow resolution contains unknown fields: " + ", ".join(sorted(unknown)))
        return cls(**value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "cash_flow_type": self.cash_flow_type,
            "amount": self.amount,
            "rationale": self.rationale,
        }


__all__ = ["CashFlowResolution"]
