"""Explicit cash-flow classification around portfolio performance."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .ledger import PortfolioSnapshot as LedgerSnapshot
from .ledger import build_nav_history, nav_return
from ..models.cash_flow import CASH_FLOW_RESOLUTION_STATUSES
from ..models.portfolio import PortfolioSnapshot
from ..models.cash_flow import CashFlowResolution


def _total_value(value: PortfolioSnapshot | Mapping[str, Any]) -> float:
    if isinstance(value, PortfolioSnapshot):
        return value.total_value_usd
    if not isinstance(value, Mapping):
        raise ValueError("snapshot must be a PortfolioSnapshot or mapping")
    for field in ("total_value_usd", "total_value"):
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
        amount = value.external_cash_flow
        kind = value.external_cash_flow_type
        return (0.0 if amount is None else float(amount)), value.cash_flow_resolution_status, value.cash_flow_resolution_status != "UNRESOLVED"
    if not isinstance(value, Mapping):
        raise ValueError("snapshot must be a PortfolioSnapshot or mapping")
    status = str(value.get("cash_flow_resolution_status", "ASSUMED_NONE")).strip().upper()
    if status not in CASH_FLOW_RESOLUTION_STATUSES:
        raise ValueError("cash_flow_resolution_status is unsupported")
    raw_amount = value.get("external_cash_flow")
    raw_kind = value.get("external_cash_flow_type")
    if raw_amount is not None and (
        isinstance(raw_amount, bool)
        or not isinstance(raw_amount, (int, float))
        or not math.isfinite(float(raw_amount))
    ):
        raise ValueError("external cash flow must be finite numeric or null")
    amount = None if raw_amount is None else float(raw_amount)
    kind = None if raw_kind is None else str(raw_kind).strip().upper()
    if status == "ASSUMED_NONE":
        if amount is None and kind is None:
            amount, kind = 0.0, "NONE"
        if amount != 0 or kind != "NONE":
            raise ValueError("ASSUMED_NONE cash flow requires zero amount and type NONE")
        return 0.0, status, True
    if status == "UNRESOLVED":
        if amount is not None or kind is not None:
            raise ValueError("UNRESOLVED cash flow requires null amount and type")
        return 0.0, status, False
    if status in {"CONFIRMED_NONE", "BASELINE_RESET"}:
        if amount != 0 or kind != "NONE":
            raise ValueError(f"{status} requires zero flow and type NONE")
        return 0.0, status, True
    if amount is None or amount == 0 or kind not in {"DEPOSIT", "WITHDRAWAL"}:
        raise ValueError("CONFIRMED_AMOUNT requires a non-zero amount and flow type")
    if kind == "DEPOSIT" and amount <= 0:
        raise ValueError("DEPOSIT external cash flow must be positive")
    if kind == "WITHDRAWAL" and amount >= 0:
        raise ValueError("WITHDRAWAL external cash flow must be negative")
    return amount, status, True


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
    amount, status, confirmed = _flow(current)
    material = abs(delta) >= threshold and threshold > 0
    if status == "UNRESOLVED":
        return {
            "status": "UNRESOLVED",
            "performance_status": "PROVISIONAL",
            "requires_confirmation": True,
            "delta_usd": delta,
            "external_cash_flow": None,
            "external_cash_flow_type": None,
            "cash_flow_resolution_status": status,
            "reason": "cash_flow_resolution_status is UNRESOLVED",
        }
    if status == "BASELINE_RESET":
        return {
            "status": "BASELINE_RESET",
            "performance_status": "AVAILABLE",
            "requires_confirmation": False,
            "delta_usd": delta,
            "external_cash_flow": 0.0,
            "external_cash_flow_type": "NONE",
            "cash_flow_resolution_status": status,
        }
    if status == "ASSUMED_NONE":
        return {
            "status": "ASSUMED_NONE",
            "performance_status": "AVAILABLE",
            "requires_confirmation": False,
            "delta_usd": delta,
            "external_cash_flow": 0.0,
            "external_cash_flow_type": "NONE",
            "cash_flow_resolution_status": status,
        }
    if not material:
        return {
            "status": "NO_MATERIAL_CHANGE",
            "performance_status": "AVAILABLE",
            "requires_confirmation": False,
            "delta_usd": delta,
            "external_cash_flow": amount,
            "external_cash_flow_type": "DEPOSIT" if amount > 0 else "WITHDRAWAL" if amount < 0 else "NONE",
            "cash_flow_resolution_status": status,
        }
    if confirmed:
        return {
            "status": "CONFIRMED",
            "performance_status": "AVAILABLE",
            "requires_confirmation": False,
            "delta_usd": delta,
            "external_cash_flow": amount,
            "external_cash_flow_type": "DEPOSIT" if amount > 0 else "WITHDRAWAL",
            "cash_flow_resolution_status": status,
        }
    raise ValueError("unsupported cash-flow resolution state")


def _snapshot_field(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _snapshot_id_of(value: Any) -> str | None:
    raw = _snapshot_field(value, "snapshot_id")
    return str(raw).strip() if raw is not None and str(raw).strip() else None


def apply_cash_flow_resolutions(
    snapshots: Sequence[PortfolioSnapshot | Mapping[str, Any]],
    resolutions: Sequence[CashFlowResolution | Mapping[str, Any]] = (),
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Overlay explicit resolutions onto snapshots without mutating inputs.

    Only snapshots whose status is ``UNRESOLVED`` may be overridden by a
    resolution; a user-explicit status is never silently rewritten. Snapshots
    without a matching resolution keep their persisted classification — this
    function never guesses a legacy unresolved flow.
    """
    parsed: list[tuple[str, CashFlowResolution]] = []
    seen: set[str] = set()
    for index, item in enumerate(resolutions):
        resolution = item if isinstance(item, CashFlowResolution) else CashFlowResolution.from_mapping(dict(item))
        if resolution.snapshot_id in seen:
            raise ValueError(f"duplicate cash-flow resolution for snapshot {resolution.snapshot_id}")
        seen.add(resolution.snapshot_id)
        parsed.append((f"resolutions[{index}]", resolution))
    known_ids = {_snapshot_id_of(value) for value in snapshots}
    unknown = [
        (label, resolution)
        for label, resolution in parsed
        if resolution.snapshot_id not in known_ids
    ]
    if unknown:
        label, resolution = unknown[0]
        raise ValueError(
            f"{label} references unknown snapshot_id {resolution.snapshot_id}"
        )
    by_snapshot = {resolution.snapshot_id: resolution for _, resolution in parsed}
    effective: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    for value in snapshots:
        original_status = str(
            _snapshot_field(value, "cash_flow_resolution_status", "ASSUMED_NONE")
        ).strip().upper()
        record = {
            "timestamp": _snapshot_field(value, "timestamp"),
            "total_value_usd": _total_value(value),
            "external_cash_flow": _snapshot_field(value, "external_cash_flow"),
            "external_cash_flow_type": _snapshot_field(value, "external_cash_flow_type"),
            "cash_flow_resolution_status": original_status,
            "snapshot_id": _snapshot_id_of(value),
        }
        snapshot_id = record["snapshot_id"]
        resolution = by_snapshot.get(snapshot_id) if snapshot_id else None
        if resolution is not None:
            if original_status != "UNRESOLVED":
                raise ValueError(
                    f"snapshot {snapshot_id} is {original_status}; only UNRESOLVED snapshots accept a resolution"
                )
            record.update(
                {
                    "external_cash_flow": resolution.external_cash_flow,
                    "external_cash_flow_type": resolution.external_cash_flow_type,
                    "cash_flow_resolution_status": resolution.cash_flow_resolution_status,
                    "cash_flow_classification_source": "USER_EXPLICIT",
                }
            )
            lineage.append(
                {
                    "snapshot_id": snapshot_id,
                    "original_status": original_status,
                    "effective_status": resolution.cash_flow_resolution_status,
                    "resolution_id": resolution.resolution_id,
                    "rationale": resolution.rationale,
                    "source": "USER_EXPLICIT",
                }
            )
        else:
            lineage.append(
                {
                    "snapshot_id": snapshot_id,
                    "original_status": original_status,
                    "effective_status": original_status,
                    "resolution_id": None,
                    "rationale": None,
                    "source": "PERSISTED",
                }
            )
        effective.append(record)
    return tuple(effective), {"lineage": tuple(lineage)}


def find_unresolved_cash_flow_snapshots(
    snapshots: Sequence[PortfolioSnapshot | Mapping[str, Any]],
    resolutions: Sequence[CashFlowResolution | Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], ...]:
    """List snapshots that still block final NAV after the resolution overlay."""
    effective, _ = apply_cash_flow_resolutions(snapshots, resolutions)
    return tuple(
        {
            "snapshot_id": record["snapshot_id"],
            "timestamp": record["timestamp"],
            "original_status": record["cash_flow_resolution_status"],
            "has_resolution": False,
        }
        for record in effective
        if record["cash_flow_resolution_status"] == "UNRESOLVED"
    )


def cash_flow_adjusted_performance(
    snapshots: Sequence[PortfolioSnapshot | Mapping[str, Any]],
    *,
    resolutions: Sequence[CashFlowResolution | Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return a NAV result only when every material change is classified."""
    if not snapshots:
        raise ValueError("at least one snapshot is required")
    values, resolution_diagnostics = apply_cash_flow_resolutions(snapshots, resolutions)
    unresolved = [
        detect_external_cash_flow(previous, current)
        for previous, current in zip(values, values[1:])
    ]
    if any(item["requires_confirmation"] for item in unresolved) or any(
        _flow(value)[1] == "UNRESOLVED" for value in values
    ):
        blocking = find_unresolved_cash_flow_snapshots(snapshots, resolutions)
        return {
            "status": "PROVISIONAL",
            "performance_finality": "PROVISIONAL",
            "return": None,
            "transitions": unresolved,
            "unresolved_snapshots": blocking,
            "resolution_lineage": resolution_diagnostics["lineage"],
            "reason": "external cash-flow classification is required before reporting NAV performance",
        }
    ledger = []
    for value in values:
        amount, _, _ = _flow(value)
        ledger.append(
            LedgerSnapshot(
                value["timestamp"],
                value["total_value_usd"],
                amount,
                value["cash_flow_resolution_status"],
                value.get("snapshot_id"),
            )
        )
    states = build_nav_history(ledger)
    return {
        "status": "AVAILABLE",
        "performance_finality": "FINAL",
        "return": nav_return(states),
        "transitions": unresolved,
        "unresolved_snapshots": (),
        "resolution_lineage": resolution_diagnostics["lineage"],
        "states": [state.__dict__.copy() for state in states],
    }


def resolve_cash_flow_issue(
    *,
    resolution_id: str,
    snapshot_id: str,
    timestamp: str,
    cash_flow_resolution_status: str,
    external_cash_flow: float | None,
    external_cash_flow_type: str | None,
    rationale: str,
) -> CashFlowResolution:
    """Validate an explicit user resolution; never infer amount or type."""
    return CashFlowResolution(
        resolution_id=resolution_id,
        snapshot_id=snapshot_id,
        timestamp=timestamp,
        cash_flow_resolution_status=cash_flow_resolution_status,
        external_cash_flow=external_cash_flow,
        external_cash_flow_type=external_cash_flow_type,
        rationale=rationale,
    )


__all__ = [
    "apply_cash_flow_resolutions",
    "cash_flow_adjusted_performance",
    "detect_external_cash_flow",
    "find_unresolved_cash_flow_snapshots",
    "resolve_cash_flow_issue",
]
