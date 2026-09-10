"""Append-only user-confirmed historical cash-flow resolutions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .time import normalize_timestamp


_FLOW_TYPES = {"DEPOSIT", "WITHDRAWAL", "NONE"}
CASH_FLOW_RESOLUTION_STATUSES = {
    "ASSUMED_NONE",
    "CONFIRMED_NONE",
    "CONFIRMED_AMOUNT",
    "UNRESOLVED",
    "BASELINE_RESET",
}
EXPLICIT_CASH_FLOW_RESOLUTION_STATUSES = {
    "CONFIRMED_NONE",
    "CONFIRMED_AMOUNT",
    "UNRESOLVED",
    "BASELINE_RESET",
}
CASH_FLOW_CLASSIFICATION_SOURCES = {"DEFAULT_ASSUMPTION", "USER_EXPLICIT", "LEGACY"}


@dataclass(frozen=True)
class CashFlowResolution:
    resolution_id: str
    snapshot_id: str
    timestamp: str
    cash_flow_resolution_status: str
    external_cash_flow: float | None
    external_cash_flow_type: str | None
    rationale: str

    def __post_init__(self) -> None:
        for field_name in ("resolution_id", "snapshot_id", "rationale"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp, "timestamp"))
        status = str(self.cash_flow_resolution_status).strip().upper()
        if status not in EXPLICIT_CASH_FLOW_RESOLUTION_STATUSES:
            raise ValueError("cash_flow_resolution_status is unsupported")
        object.__setattr__(self, "cash_flow_resolution_status", status)
        if self.external_cash_flow is not None:
            if (
                isinstance(self.external_cash_flow, bool)
                or not isinstance(self.external_cash_flow, (int, float))
                or not math.isfinite(float(self.external_cash_flow))
            ):
                raise ValueError("external_cash_flow must be finite or null")
            object.__setattr__(self, "external_cash_flow", float(self.external_cash_flow))
        flow_type = self.external_cash_flow_type
        if flow_type is not None:
            flow_type = str(flow_type).strip().upper()
            if flow_type not in _FLOW_TYPES:
                raise ValueError("external_cash_flow_type must be DEPOSIT, WITHDRAWAL, or NONE")
        object.__setattr__(self, "external_cash_flow_type", flow_type)
        if status == "UNRESOLVED":
            if self.external_cash_flow is not None or flow_type is not None:
                raise ValueError("UNRESOLVED cash flow requires null amount and type")
        elif status in {"CONFIRMED_NONE", "BASELINE_RESET"}:
            if self.external_cash_flow != 0 or flow_type != "NONE":
                raise ValueError(f"{status} requires external_cash_flow 0 and type NONE")
        elif status == "CONFIRMED_AMOUNT":
            if self.external_cash_flow is None or self.external_cash_flow == 0:
                raise ValueError("CONFIRMED_AMOUNT requires a non-zero external_cash_flow")
            if flow_type == "DEPOSIT" and self.external_cash_flow <= 0:
                raise ValueError("DEPOSIT external_cash_flow must be positive")
            if flow_type == "WITHDRAWAL" and self.external_cash_flow >= 0:
                raise ValueError("WITHDRAWAL external_cash_flow must be negative")
            if flow_type not in {"DEPOSIT", "WITHDRAWAL"}:
                raise ValueError("CONFIRMED_AMOUNT requires DEPOSIT or WITHDRAWAL")

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "CashFlowResolution":
        if not isinstance(value, dict):
            raise ValueError("cash-flow resolution must be an object")
        allowed = {
            "resolution_id", "snapshot_id", "timestamp", "cash_flow_resolution_status",
            "external_cash_flow", "external_cash_flow_type", "rationale",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("cash-flow resolution contains unknown fields: " + ", ".join(sorted(unknown)))
        missing = allowed - set(value)
        if missing:
            raise ValueError("cash-flow resolution is missing fields: " + ", ".join(sorted(missing)))
        return cls(**value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "cash_flow_resolution_status": self.cash_flow_resolution_status,
            "external_cash_flow": self.external_cash_flow,
            "external_cash_flow_type": self.external_cash_flow_type,
            "rationale": self.rationale,
        }


__all__ = [
    "CASH_FLOW_CLASSIFICATION_SOURCES",
    "CASH_FLOW_RESOLUTION_STATUSES",
    "EXPLICIT_CASH_FLOW_RESOLUTION_STATUSES",
    "CashFlowResolution",
]
