"""Unitized NAV accounting for portfolio history."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..models.cash_flow import CASH_FLOW_RESOLUTION_STATUSES
from ..models.time import normalize_timestamp, parse_timestamp
from ..models.performance import NAVHistoryResult


def _finite(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: str
    portfolio_value: float
    external_cash_flow: float | ExternalCashFlow | None = 0.0
    cash_flow_resolution_status: str | None = None
    snapshot_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp))
        object.__setattr__(
            self,
            "portfolio_value",
            _finite(self.portfolio_value, "portfolio_value", minimum=0),
        )
        status = self.cash_flow_resolution_status
        if status is not None:
            status = str(status).strip().upper()
            if status not in CASH_FLOW_RESOLUTION_STATUSES:
                raise ValueError("cash_flow_resolution_status is unsupported")
        flow_amount = None
        if isinstance(self.external_cash_flow, ExternalCashFlow):
            if parse_timestamp(self.external_cash_flow.timestamp) > parse_timestamp(self.timestamp):
                raise ValueError("cash flow timestamp must be <= its snapshot timestamp")
            object.__setattr__(self, "external_cash_flow", self.external_cash_flow)
            flow_amount = self.external_cash_flow.amount
        else:
            if self.external_cash_flow is not None:
                flow_amount = _finite(self.external_cash_flow, "external_cash_flow")
            object.__setattr__(self, "external_cash_flow", flow_amount)
        if status is None:
            # Internal benchmark/ledger callers pass an explicit numeric flow;
            # persisted portfolio snapshots use the stricter model contract.
            status = "ASSUMED_NONE" if flow_amount in (None, 0) else "CONFIRMED_AMOUNT"
        if status == "ASSUMED_NONE" and flow_amount is None:
            flow_amount = 0.0
            object.__setattr__(self, "external_cash_flow", flow_amount)
        if status == "UNRESOLVED":
            if flow_amount is not None:
                raise ValueError("UNRESOLVED cash flow requires null amount")
        elif status in {"ASSUMED_NONE", "CONFIRMED_NONE", "BASELINE_RESET"}:
            if flow_amount != 0:
                raise ValueError(f"{status} requires zero external_cash_flow")
        elif status == "CONFIRMED_AMOUNT" and (flow_amount is None or flow_amount == 0):
            raise ValueError("CONFIRMED_AMOUNT requires a non-zero external_cash_flow")
        if self.snapshot_id is not None and (
            not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip()
        ):
            raise ValueError("snapshot_id must be a non-empty string or null")
        object.__setattr__(self, "cash_flow_resolution_status", status)
        if self.snapshot_id is not None:
            object.__setattr__(self, "snapshot_id", self.snapshot_id.strip())
        if status == "BASELINE_RESET" and self.snapshot_id is None:
            raise ValueError("BASELINE_RESET requires snapshot_id")


@dataclass(frozen=True)
class ExternalCashFlow:
    timestamp: str
    amount: float
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp, "cash flow timestamp"))
        object.__setattr__(self, "amount", _finite(self.amount, "cash flow amount"))
        if self.description is not None and not isinstance(self.description, str):
            raise ValueError("cash flow description must be a string or null")


@dataclass(frozen=True)
class NAVState:
    timestamp: str
    portfolio_value: float
    external_cash_flow: float
    units: float
    nav_per_unit: float
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0
    cash_flow_resolution_status: str = "CONFIRMED_NONE"

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp))
        for field in ("portfolio_value", "units", "nav_per_unit"):
            _finite(getattr(self, field), field, minimum=0)
        _finite(self.external_cash_flow, "external_cash_flow")
        current_drawdown = _finite(self.current_drawdown, "current_drawdown")
        max_drawdown = _finite(self.max_drawdown, "max_drawdown")
        if current_drawdown > 0 or max_drawdown > 0:
            raise ValueError("drawdown values must be <= 0")
        status = str(self.cash_flow_resolution_status).strip().upper()
        if status not in CASH_FLOW_RESOLUTION_STATUSES:
            raise ValueError("cash_flow_resolution_status is unsupported")
        object.__setattr__(self, "cash_flow_resolution_status", status)


def _coerce_snapshot(value: PortfolioSnapshot | Mapping[str, Any] | Any) -> PortfolioSnapshot:
    if isinstance(value, PortfolioSnapshot):
        return value
    if isinstance(value, Mapping):
        if "portfolio_value" in value:
            portfolio_value = value["portfolio_value"]
        elif "total_value_usd" in value:
            portfolio_value = value["total_value_usd"]
        elif "total_value" in value:
            portfolio_value = value["total_value"]
        else:
            raise ValueError("snapshot is missing portfolio_value")
        if str(value.get("external_cash_flow_type", "")).strip().upper() == "UNRESOLVED":
            raise ValueError("external_cash_flow_type UNRESOLVED is unsupported; use cash_flow_resolution_status")
        cash_flow = value.get("external_cash_flow")
        if isinstance(cash_flow, Mapping):
            cash_flow = ExternalCashFlow(**cash_flow)
        return PortfolioSnapshot(
            timestamp=value.get("timestamp", ""),
            portfolio_value=portfolio_value,
            external_cash_flow=cash_flow,
            cash_flow_resolution_status=value.get("cash_flow_resolution_status"),
            snapshot_id=value.get("snapshot_id"),
        )
    if hasattr(value, "timestamp") and hasattr(value, "total_value_usd"):
        return PortfolioSnapshot(
            timestamp=value.timestamp,
            portfolio_value=value.total_value_usd,
            external_cash_flow=getattr(value, "external_cash_flow", 0.0),
            cash_flow_resolution_status=getattr(value, "cash_flow_resolution_status", None),
            snapshot_id=getattr(value, "snapshot_id", None),
        )
    raise ValueError("snapshots must contain PortfolioSnapshot objects or mappings")


def build_nav_history(
    snapshots: Sequence[PortfolioSnapshot | Mapping[str, Any] | Any],
) -> list[NAVState]:
    """Build NAV states assuming each cash flow occurs before that snapshot's valuation."""
    if not snapshots:
        raise ValueError("at least one snapshot is required")
    values = [_coerce_snapshot(snapshot) for snapshot in snapshots]
    if any(
        parse_timestamp(current.timestamp) <= parse_timestamp(previous.timestamp)
        for previous, current in zip(values, values[1:])
    ):
        raise ValueError("snapshot timestamps must be strictly increasing")
    if values[0].portfolio_value <= 0:
        raise ValueError("initial portfolio_value must be > 0")

    first_flow = values[0].external_cash_flow
    first_amount = first_flow.amount if isinstance(first_flow, ExternalCashFlow) else first_flow
    if first_amount is None:
        first_amount = 0.0
    if first_amount != 0:
        raise ValueError("initial snapshot external_cash_flow must be 0")

    units = values[0].portfolio_value
    nav = 1.0
    peak_nav = nav
    worst_drawdown = 0.0
    result = [
        NAVState(
            timestamp=values[0].timestamp,
            portfolio_value=values[0].portfolio_value,
            external_cash_flow=first_amount,
            units=units,
            nav_per_unit=nav,
            current_drawdown=0.0,
            max_drawdown=0.0,
            cash_flow_resolution_status=values[0].cash_flow_resolution_status,
        )
    ]
    for snapshot in values[1:]:
        if snapshot.cash_flow_resolution_status == "UNRESOLVED":
            raise ValueError("cash-flow resolution is required before building NAV history")
        flow = snapshot.external_cash_flow
        flow_amount = flow.amount if isinstance(flow, ExternalCashFlow) else flow
        if flow_amount is None:
            raise ValueError("resolved cash flow must include an amount")
        if snapshot.cash_flow_resolution_status == "BASELINE_RESET":
            units = snapshot.portfolio_value
            nav = 1.0
            peak_nav = nav
            worst_drawdown = 0.0
            result.append(
                NAVState(
                    timestamp=snapshot.timestamp,
                    portfolio_value=snapshot.portfolio_value,
                    external_cash_flow=0.0,
                    units=units,
                    nav_per_unit=nav,
                    current_drawdown=0.0,
                    max_drawdown=0.0,
                    cash_flow_resolution_status=snapshot.cash_flow_resolution_status,
                )
            )
            continue
        if isinstance(flow, ExternalCashFlow):
            if parse_timestamp(flow.timestamp) > parse_timestamp(snapshot.timestamp):
                raise ValueError("cash flow timestamp must be <= its snapshot timestamp")
        pre_flow_value = snapshot.portfolio_value - flow_amount
        if pre_flow_value <= 0 or not math.isfinite(pre_flow_value):
            raise ValueError("cash flow must leave a positive pre-flow portfolio value")
        pre_flow_nav = pre_flow_value / units
        if pre_flow_nav <= 0 or not math.isfinite(pre_flow_nav):
            raise ValueError("cash flow must have a positive pre-flow NAV")
        units += flow_amount / pre_flow_nav
        if units <= 0:
            raise ValueError("external cash flow leaves no positive NAV units")
        if snapshot.portfolio_value < 0:
            raise ValueError("portfolio_value must be >= 0")
        nav = snapshot.portfolio_value / units
        if nav <= 0 or not math.isfinite(nav):
            raise ValueError("portfolio_value must produce a positive finite NAV")
        peak_nav = max(peak_nav, nav)
        drawdown = nav / peak_nav - 1.0
        worst_drawdown = min(worst_drawdown, drawdown)
        result.append(
            NAVState(
                timestamp=snapshot.timestamp,
                portfolio_value=snapshot.portfolio_value,
                external_cash_flow=flow_amount,
                units=units,
                nav_per_unit=nav,
                current_drawdown=drawdown,
                max_drawdown=worst_drawdown,
                cash_flow_resolution_status=snapshot.cash_flow_resolution_status,
            )
        )
    return result


def nav_return(states: Sequence[NAVState]) -> float:
    if not states:
        raise ValueError("at least one NAV state is required")
    return states[-1].nav_per_unit / states[0].nav_per_unit - 1.0


def current_drawdown(states: Sequence[NAVState]) -> float:
    if not states:
        raise ValueError("at least one NAV state is required")
    return states[-1].current_drawdown


def max_drawdown(states: Sequence[NAVState]) -> float:
    if not states:
        raise ValueError("at least one NAV state is required")
    return min(state.max_drawdown for state in states)


def cash_flow_adjusted_return(
    snapshots: Sequence[PortfolioSnapshot | Mapping[str, Any] | Any],
) -> float:
    return nav_return(build_nav_history(snapshots))


def _status(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("cash_flow_resolution_status")
        if raw is None:
            amount = value.get("external_cash_flow")
        else:
            return str(raw).strip().upper()
    else:
        raw = getattr(value, "cash_flow_resolution_status", None)
        if raw is not None:
            return str(raw).strip().upper()
        amount = getattr(value, "external_cash_flow", 0.0)
    if isinstance(amount, ExternalCashFlow):
        amount = amount.amount
    return "ASSUMED_NONE" if amount is None or amount == 0 else "CONFIRMED_AMOUNT"


def _unresolved_flow(value: Any, index: int) -> bool:
    return _status(value) == "UNRESOLVED"


def _snapshot_id(value: Any) -> str | None:
    return value.get("snapshot_id") if isinstance(value, Mapping) else getattr(value, "snapshot_id", None)


def _segment_result(values: Sequence[PortfolioSnapshot | Mapping[str, Any] | Any]) -> tuple[tuple[NAVState, ...], tuple[dict[str, Any], ...], tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """Return confirmed states, segment metadata, and unresolved current gaps."""
    unresolved_indices = [index for index, value in enumerate(values) if _unresolved_flow(value, index)]
    first_gap = unresolved_indices[0] if unresolved_indices else len(values)
    confirmed = values[:first_gap]
    states = tuple(build_nav_history(confirmed)) if confirmed else ()
    gaps = tuple(
        {
            "index": index,
            "snapshot_id": _snapshot_id(values[index]),
            "amount": None,
            "cash_flow_resolution_status": "UNRESOLVED",
            "timestamp": (
                values[index].get("timestamp")
                if isinstance(values[index], Mapping)
                else getattr(values[index], "timestamp", None)
            ),
            "reason": "cash_flow_resolution_status is UNRESOLVED",
        }
        for index in unresolved_indices
    )
    assumed = tuple(
        {
            "index": index,
            "snapshot_id": _snapshot_id(value),
            "timestamp": value.get("timestamp") if isinstance(value, Mapping) else getattr(value, "timestamp", None),
            "cash_flow_resolution_status": "ASSUMED_NONE",
            "external_cash_flow": 0.0,
        }
        for index, value in enumerate(confirmed)
        if _status(value) == "ASSUMED_NONE"
    )
    status = "PROVISIONAL" if gaps else "FINAL"
    segment = ({
        "status": "AVAILABLE" if states else "UNAVAILABLE",
        "performance_finality": status,
        "start": states[0].timestamp if states else None,
        "end": states[-1].timestamp if states else None,
    },)
    return states, segment, gaps, assumed


def build_nav_history_result(
    snapshots: Sequence[PortfolioSnapshot | Mapping[str, Any] | Any],
) -> NAVHistoryResult:
    """Build a status-bearing NAV history without guessing unresolved flows."""
    if not snapshots:
        return NAVHistoryResult("UNAVAILABLE", explanations=("no portfolio snapshots are available",))
    values = tuple(_coerce_snapshot(snapshot) for snapshot in snapshots)
    reset_indices = [index for index, value in enumerate(values) if index > 0 and value.cash_flow_resolution_status == "BASELINE_RESET"]
    current_start = reset_indices[-1] if reset_indices else 0
    prior_segments: list[dict[str, Any]] = []
    assumed_cash_flows: list[dict[str, Any]] = []
    for start, end in zip((0, *reset_indices), (*reset_indices, len(values))):
        segment_values = values[start:end]
        if not segment_values:
            continue
        segment_states, segment_meta, segment_gaps, segment_assumed = _segment_result(segment_values)
        if start != current_start:
            assumed_cash_flows.extend(segment_assumed)
            prior_segments.append({**segment_meta[0], "archived": True, "unresolved_count": len(segment_gaps)})
    current_values = values[current_start:]
    states, current_segment, unresolved, current_assumed = _segment_result(current_values)
    assumed_cash_flows.extend(current_assumed)
    if unresolved:
        return NAVHistoryResult(
            "PROVISIONAL",
            states=states,
            segments=tuple(prior_segments) + current_segment,
            unresolved_cash_flows=unresolved,
            assumed_cash_flows=tuple(assumed_cash_flows),
            benchmark_status="PROVISIONAL",
            performance_finality="PROVISIONAL",
            explanations=("unresolved cash-flow resolution blocks current NAV and benchmark performance",),
        )
    result = nav_return(states)
    return NAVHistoryResult(
        "AVAILABLE",
        states=states,
        segments=tuple(prior_segments) + current_segment,
        cash_flow_adjusted_return=result,
        nav_return=result,
        current_drawdown=states[-1].current_drawdown,
        max_drawdown=min(state.max_drawdown for state in states),
        assumed_cash_flows=tuple(assumed_cash_flows),
        benchmark_status="UNAVAILABLE",
        performance_finality="FINAL",
    )


__all__ = [
    "ExternalCashFlow",
    "NAVState",
    "PortfolioSnapshot",
    "build_nav_history",
    "cash_flow_adjusted_return",
    "current_drawdown",
    "max_drawdown",
    "nav_return",
    "build_nav_history_result",
]
