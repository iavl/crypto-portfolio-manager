"""Explicit cash-flow classification around portfolio performance."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .ledger import PortfolioSnapshot as LedgerSnapshot
from .ledger import build_nav_history, nav_return
from ..models.portfolio import PortfolioSnapshot


def _total_value(value: PortfolioSnapshot | Mapping[str, Any]) -> float:
    if isinstance(value, PortfolioSnapshot):
        return value.total_value_usd
    if not isinstance(value, Mapping):
        raise ValueError("snapshot must be a PortfolioSnapshot or mapping")
    for field in ("total_value_usd", "total_value", "reported_total_value"):
        if field in value and value[field] is not None:
            total = value[field]
            break
    else:
        positions = value.get("positions")
        if not isinstance(positions, Sequence) or isinstance(positions, (str, bytes)):
            raise ValueError("snapshot is missing a portfolio total")
        total = sum(float(item["value_usd"]) for item in positions if isinstance(item, Mapping) and "value_usd" in item)
    if isinstance(total, bool) or not isinstance(total, (int, float)) or not math.isfinite(float(total)) or float(total) < 0:
        raise ValueError("snapshot total must be finite and non-negative")
    return float(total)


def _flow(value: PortfolioSnapshot | Mapping[str, Any]) -> tuple[float, str, bool]:
    if isinstance(value, PortfolioSnapshot):
        amount = float(value.external_cash_flow)
        kind = value.external_cash_flow_type
        return amount, kind, kind != "UNRESOLVED"
    if not isinstance(value, Mapping):
        raise ValueError("snapshot must be a PortfolioSnapshot or mapping")
    if "external_cash_flow" in value and "external_cash_flow_usd" in value and value["external_cash_flow"] != value["external_cash_flow_usd"]:
        raise ValueError("external_cash_flow and external_cash_flow_usd disagree")
    amount_supplied = "external_cash_flow" in value or "external_cash_flow_usd" in value
    raw_amount = value.get("external_cash_flow", value.get("external_cash_flow_usd", 0.0))
    if isinstance(raw_amount, bool) or not isinstance(raw_amount, (int, float)) or not math.isfinite(float(raw_amount)):
        raise ValueError("external cash flow must be finite numeric")
    amount = float(raw_amount)
    raw_kind = value.get("external_cash_flow_type")
    if raw_kind is None:
        if not amount_supplied:
            return amount, "UNRESOLVED", False
        raw_kind = "DEPOSIT" if amount > 0 else "WITHDRAWAL" if amount < 0 else "NONE"
    kind = str(raw_kind).strip().upper()
    if kind not in {"NONE", "DEPOSIT", "WITHDRAWAL", "UNRESOLVED"}:
        raise ValueError("external_cash_flow_type is unsupported")
    if kind == "NONE" and amount != 0:
        raise ValueError("external_cash_flow_type NONE requires zero flow")
    if kind == "DEPOSIT" and amount <= 0:
        raise ValueError("external cash flow DEPOSIT requires a positive amount")
    if kind == "WITHDRAWAL" and amount >= 0:
        raise ValueError("external cash flow WITHDRAWAL requires a negative amount")
    return amount, kind, kind != "UNRESOLVED"


def detect_external_cash_flow(
    previous: PortfolioSnapshot | Mapping[str, Any],
    current: PortfolioSnapshot | Mapping[str, Any],
    *,
    material_usd: float = 100.0,
    material_fraction: float = 0.01,
) -> dict[str, Any]:
    """Flag material unclassified snapshot changes without guessing their cause."""
    previous_total = _total_value(previous)
    current_total = _total_value(current)
    delta = current_total - previous_total
    if isinstance(material_usd, bool) or not isinstance(material_usd, (int, float)) or not math.isfinite(float(material_usd)) or material_usd < 0:
        raise ValueError("material_usd must be finite and non-negative")
    if isinstance(material_fraction, bool) or not isinstance(material_fraction, (int, float)) or not math.isfinite(float(material_fraction)) or not 0 <= material_fraction <= 1:
        raise ValueError("material_fraction must be a fraction in [0, 1]")
    threshold = max(float(material_usd), max(previous_total, current_total) * float(material_fraction))
    amount, kind, confirmed = _flow(current)
    material = abs(delta) >= threshold and threshold > 0
    if not material:
        return {
            "status": "NO_MATERIAL_CHANGE",
            "performance_status": "AVAILABLE",
            "requires_confirmation": False,
            "delta_usd": delta,
            "external_cash_flow": amount,
            "external_cash_flow_type": kind,
        }
    if confirmed:
        return {
            "status": "CONFIRMED",
            "performance_status": "AVAILABLE",
            "requires_confirmation": False,
            "delta_usd": delta,
            "external_cash_flow": amount,
            "external_cash_flow_type": kind,
        }
    return {
        "status": "UNRESOLVED",
        "performance_status": "PROVISIONAL",
        "requires_confirmation": True,
        "delta_usd": delta,
        "external_cash_flow": amount,
        "external_cash_flow_type": "UNRESOLVED",
        "reason": "material snapshot change has no explicit external cash-flow classification",
    }


def cash_flow_adjusted_performance(
    snapshots: Sequence[PortfolioSnapshot | Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a NAV result only when every material change is classified."""
    if not snapshots:
        raise ValueError("at least one snapshot is required")
    values = tuple(snapshots)
    unresolved = [
        detect_external_cash_flow(previous, current)
        for previous, current in zip(values, values[1:])
    ]
    if any(item["requires_confirmation"] for item in unresolved):
        return {
            "status": "PROVISIONAL",
            "return": None,
            "transitions": unresolved,
            "reason": "external cash-flow classification is required before reporting NAV performance",
        }
    ledger = []
    for value in values:
        if isinstance(value, PortfolioSnapshot):
            timestamp = value.timestamp
            total = value.total_value_usd
            amount = value.external_cash_flow
        else:
            timestamp = value["timestamp"]
            total = _total_value(value)
            amount, _, _ = _flow(value)
        ledger.append(LedgerSnapshot(timestamp, total, amount))
    states = build_nav_history(ledger)
    return {
        "status": "AVAILABLE",
        "return": nav_return(states),
        "transitions": unresolved,
        "states": [state.__dict__.copy() for state in states],
    }


__all__ = ["cash_flow_adjusted_performance", "detect_external_cash_flow"]
