"""Explicit cash-flow classification around portfolio performance."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .ledger import PortfolioSnapshot as LedgerSnapshot
from .ledger import build_nav_history, nav_return
from ..models.cash_flow import CASH_FLOW_RESOLUTION_STATUSES
from ..models.portfolio import PortfolioSnapshot
from ..models.cash_flow import CashFlowResolution
from ..models.policy import resolve_policy


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
            # CONFIRMED_NONE reaches this branch with amount 0 when the market
            # moved materially; a zero amount must not be labeled a withdrawal.
            "external_cash_flow_type": "DEPOSIT" if amount > 0 else "WITHDRAWAL" if amount < 0 else "NONE",
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


def _persisted_flow_state(value: Any) -> tuple[str, Any, Any]:
    """Normalize a snapshot's persisted flow fields to the current contract.

    Pre-contract records carried no ``cash_flow_resolution_status`` and put
    the unresolved intent in ``external_cash_flow_type``; those normalize to
    ``UNRESOLVED`` with a null flow. Any other record without a status key
    defaults to ``ASSUMED_NONE / 0 / NONE``. Explicit statuses pass through.
    """
    status = _snapshot_field(value, "cash_flow_resolution_status")
    flow = _snapshot_field(value, "external_cash_flow")
    flow_type = _snapshot_field(value, "external_cash_flow_type")
    if status is None:
        if str(flow_type or "").strip().upper() == "UNRESOLVED":
            return "UNRESOLVED", None, None
        return "ASSUMED_NONE", 0.0, "NONE"
    return str(status).strip().upper(), flow, flow_type


def apply_cash_flow_resolutions(
    snapshots: Sequence[PortfolioSnapshot | Mapping[str, Any]],
    resolutions: Sequence[CashFlowResolution | Mapping[str, Any]] = (),
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Overlay explicit resolutions onto snapshots without mutating inputs.

    A resolution may override a persisted ``UNRESOLVED`` status or a merely
    derived ``ASSUMED_NONE`` (no user-explicit classification). A snapshot the
    user already classified explicitly is never silently rewritten. Snapshots
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
        original_status, persisted_flow, persisted_flow_type = _persisted_flow_state(value)
        classification_source = str(
            _snapshot_field(value, "cash_flow_classification_source", "") or ""
        ).strip().upper() or None
        record = {
            "timestamp": _snapshot_field(value, "timestamp"),
            "total_value_usd": _total_value(value),
            "external_cash_flow": persisted_flow,
            "external_cash_flow_type": persisted_flow_type,
            "cash_flow_resolution_status": original_status,
            "cash_flow_classification_source": classification_source,
            "snapshot_id": _snapshot_id_of(value),
        }
        snapshot_id = record["snapshot_id"]
        resolution = by_snapshot.get(snapshot_id) if snapshot_id else None
        if resolution is not None:
            overridable = original_status == "UNRESOLVED" or (
                original_status == "ASSUMED_NONE" and classification_source != "USER_EXPLICIT"
            )
            if not overridable:
                raise ValueError(
                    f"snapshot {snapshot_id} already has an explicit user classification "
                    f"({original_status}); it cannot be re-resolved"
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
    "INTERNAL_REALLOCATION_TYPES",
    "InternalReallocation",
    "apply_cash_flow_resolutions",
    "attribute_asset_changes",
    "cash_flow_adjusted_performance",
    "detect_external_cash_flow",
    "detect_internal_reallocation",
    "find_unresolved_cash_flow_snapshots",
    "resolve_cash_flow_issue",
]


# --- Internal stable-sleeve reallocation attribution -----------------------
#
# An internal stablecoin exchange (e.g. USDT -> U) is neither an external
# cash flow nor market P&L: the sleeve total is unchanged and no dollar left
# the portfolio. These helpers classify matched opposite-direction stable
# deltas so reports attribute them to internal reallocation instead of
# folding them into market performance.

INTERNAL_REALLOCATION_TYPES = {"INTERNAL_REALLOCATION", "INFERRED_INTERNAL_REALLOCATION"}


@dataclass(frozen=True)
class InternalReallocation:
    from_symbol: str
    to_symbol: str
    amount_usd: float
    attribution: str
    confidence: float
    source: str
    rationale: str

    def __post_init__(self) -> None:
        for field_name in ("from_symbol", "to_symbol"):
            value = str(getattr(self, field_name)).strip().upper()
            if not value:
                raise ValueError(f"{field_name} must be a non-empty symbol")
            object.__setattr__(self, field_name, value)
        if self.from_symbol == self.to_symbol:
            raise ValueError("internal reallocation requires two different symbols")
        if isinstance(self.amount_usd, bool) or not isinstance(self.amount_usd, (int, float)):
            raise ValueError("amount_usd must be a number")
        amount = float(self.amount_usd)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("amount_usd must be finite and > 0")
        object.__setattr__(self, "amount_usd", amount)
        attribution = str(self.attribution).strip().upper()
        if attribution not in INTERNAL_REALLOCATION_TYPES:
            raise ValueError("attribution must be INTERNAL_REALLOCATION or INFERRED_INTERNAL_REALLOCATION")
        object.__setattr__(self, "attribution", attribution)
        confidence = self.confidence
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("confidence must be a number")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be finite and in [0, 1]")
        object.__setattr__(self, "confidence", confidence)
        source = str(self.source).strip().upper()
        if source not in {"USER_EXPLICIT", "SNAPSHOT_INFERENCE"}:
            raise ValueError("source must be USER_EXPLICIT or SNAPSHOT_INFERENCE")
        object.__setattr__(self, "source", source)
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValueError("rationale must be a non-empty string")
        object.__setattr__(self, "rationale", self.rationale.strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "from_symbol": self.from_symbol,
            "to_symbol": self.to_symbol,
            "amount_usd": self.amount_usd,
            "attribution": self.attribution,
            "confidence": self.confidence,
            "source": self.source,
            "rationale": self.rationale,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "InternalReallocation":
        if not isinstance(value, Mapping):
            raise ValueError("internal reallocation must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(value) - allowed
        if unknown:
            raise ValueError("internal reallocation contains unknown fields: " + ", ".join(sorted(unknown)))
        missing = allowed - set(value)
        if missing:
            raise ValueError("internal reallocation is missing fields: " + ", ".join(sorted(missing)))
        return cls(**value)


def _positions_value(value: PortfolioSnapshot | Mapping[str, Any]) -> dict[str, float]:
    if isinstance(value, PortfolioSnapshot):
        return {position.symbol: position.value_usd for position in value.positions}
    if not isinstance(value, Mapping):
        raise ValueError("snapshot must be a PortfolioSnapshot or mapping")
    positions = value.get("positions")
    if not isinstance(positions, Sequence) or isinstance(positions, (str, bytes)):
        raise ValueError("snapshot positions must be a list")
    result: dict[str, float] = {}
    for item in positions:
        if not isinstance(item, Mapping) or "symbol" not in item or "value_usd" not in item:
            raise ValueError("each position must carry symbol and value_usd")
        symbol = str(item["symbol"]).strip().upper()
        if not symbol:
            raise ValueError("position symbol must be non-empty")
        if symbol in result:
            raise ValueError(f"snapshot contains duplicate position {symbol}")
        amount = item["value_usd"]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(float(amount)) or float(amount) < 0:
            raise ValueError(f"position {symbol} value_usd must be finite and >= 0")
        result[symbol] = float(amount)
    return result


def detect_internal_reallocation(
    previous: PortfolioSnapshot | Mapping[str, Any],
    current: PortfolioSnapshot | Mapping[str, Any],
    *,
    policy: Any | None = None,
    stable_symbols: Sequence[str] | None = None,
    tolerance: float = 0.01,
    minimum_amount_usd: float = 1.0,
) -> InternalReallocation | None:
    """Match opposite-direction stable-sleeve deltas with similar magnitude.

    Inference only claims "internal reallocation of about this amount
    between these two sleeve symbols": the matched magnitude is
    ``min(|decrease|, |increase|)`` and the mismatch (swap cost or slippage)
    stays visible in the caller's residual attribution. When no pair matches
    within tolerance the function returns None rather than guessing.
    """
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(float(tolerance)) or not 0 <= float(tolerance) < 1:
        raise ValueError("tolerance must be finite and in [0, 1)")
    if isinstance(minimum_amount_usd, bool) or not isinstance(minimum_amount_usd, (int, float)) or not math.isfinite(float(minimum_amount_usd)) or minimum_amount_usd <= 0:
        raise ValueError("minimum_amount_usd must be finite and > 0")
    if stable_symbols is not None:
        stables = {str(symbol).strip().upper() for symbol in stable_symbols if str(symbol).strip()}
    else:
        resolved = policy or resolve_policy()
        stables = set(resolved.stable_symbols)
    if not stables:
        raise ValueError("internal reallocation detection requires stable symbols")
    previous_values = _positions_value(previous)
    current_values = _positions_value(current)
    deltas = {
        symbol: current_values.get(symbol, 0.0) - previous_values.get(symbol, 0.0)
        for symbol in set(previous_values) | set(current_values)
        if symbol in stables
    }
    decreases = [(symbol, -delta) for symbol, delta in deltas.items() if -delta >= minimum_amount_usd]
    increases = [(symbol, delta) for symbol, delta in deltas.items() if delta >= minimum_amount_usd]
    best: tuple[str, str, float, float] | None = None
    for from_symbol, decrease in sorted(decreases):
        for to_symbol, increase in sorted(increases):
            if from_symbol == to_symbol:
                continue
            mismatch = abs(decrease - increase) / max(decrease, increase)
            if mismatch > tolerance:
                continue
            if best is None or mismatch < best[3]:
                best = (from_symbol, to_symbol, min(decrease, increase), mismatch)
    if best is None:
        return None
    from_symbol, to_symbol, amount, mismatch = best
    return InternalReallocation(
        from_symbol=from_symbol,
        to_symbol=to_symbol,
        amount_usd=amount,
        attribution="INFERRED_INTERNAL_REALLOCATION",
        confidence=1.0 - mismatch,
        source="SNAPSHOT_INFERENCE",
        rationale=(
            f"{from_symbol} decreased while {to_symbol} increased by a similar "
            f"amount with no external cash flow; inferred internal stable-sleeve "
            f"reallocation of about {amount:,.2f} USD"
        ),
    )


def attribute_asset_changes(
    previous: PortfolioSnapshot | Mapping[str, Any],
    current: PortfolioSnapshot | Mapping[str, Any],
    *,
    policy: Any | None = None,
    stable_symbols: Sequence[str] | None = None,
    explicit_reallocation: InternalReallocation | Mapping[str, Any] | None = None,
    tolerance: float = 0.01,
    minimum_amount_usd: float = 1.0,
) -> dict[str, Any]:
    """Split per-symbol dollar changes into market, flow, and internal legs.

    The external cash flow stays exactly what the snapshots classify; a
    matched stable-to-stable reallocation moves its principal out of the
    market-change attribution (and its magnitude mismatch surfaces as swap
    cost/slippage instead of P&L). Non-stable assets attribute their whole
    dollar change to market performance.
    """
    resolved = policy or resolve_policy()
    stables = (
        {str(symbol).strip().upper() for symbol in (stable_symbols or ()) if str(symbol).strip()}
        or set(resolved.stable_symbols)
    )
    reallocation = None
    if explicit_reallocation is not None:
        reallocation = (
            explicit_reallocation
            if isinstance(explicit_reallocation, InternalReallocation)
            else InternalReallocation.from_mapping(explicit_reallocation)
        )
    else:
        reallocation = detect_internal_reallocation(
            previous,
            current,
            policy=resolved,
            stable_symbols=sorted(stables),
            tolerance=tolerance,
            minimum_amount_usd=minimum_amount_usd,
        )
    previous_values = _positions_value(previous)
    current_values = _positions_value(current)
    total_change = {
        symbol: current_values.get(symbol, 0.0) - previous_values.get(symbol, 0.0)
        for symbol in sorted(set(previous_values) | set(current_values))
    }
    market_change = dict(total_change)
    swap_cost = 0.0
    if reallocation is not None:
        market_change[reallocation.from_symbol] = round(
            market_change.get(reallocation.from_symbol, 0.0) + reallocation.amount_usd, 10
        )
        market_change[reallocation.to_symbol] = round(
            market_change.get(reallocation.to_symbol, 0.0) - reallocation.amount_usd, 10
        )
        swap_cost = (
            abs(total_change.get(reallocation.from_symbol, 0.0))
            - abs(total_change.get(reallocation.to_symbol, 0.0))
        )
    return {
        "external_cash_flow": 0.0,
        "total_change_usd": total_change,
        "market_change_usd": market_change,
        "internal_reallocation": reallocation.as_dict() if reallocation is not None else None,
        "swap_cost_slippage_usd": swap_cost,
    }
