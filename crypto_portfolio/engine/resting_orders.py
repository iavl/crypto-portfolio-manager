"""Deterministic matching of resting exchange orders to execution-plan zones.

Pure functions only. An open order counts toward a tranche when its side
matches the plan direction, its type rests on the book at a chosen price
(LIMIT family), and its price lies inside the tranche zone or within the
configured relative tolerance of a zone edge. A near-zone match still counts
as coverage — the zones themselves move slightly every review — and the
deviation is reported so the human can decide whether to adjust the order.

The matcher never proposes, places, or cancels orders; it only states what is
already resting so a report can mark covered tranches as no-action.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from ..models.order import OpenOrderRecord, RESTING_ORDER_TYPES


_RESTING_FULL = "RESTING_FULL"
_RESTING_PARTIAL = "RESTING_PARTIAL"
_NOT_RESTING = "NOT_RESTING"


def _plan_value(plan: Any, name: str, default=None):
    if isinstance(plan, Mapping):
        return plan.get(name, default)
    return getattr(plan, name, default)


def _tranche_number(tranche: Any, name: str) -> float:
    value = _plan_value(tranche, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"execution tranche {name} must be a finite number")
    return float(value)


def _match_kind(price: float, low: float, high: float, tolerance: float) -> str | None:
    if low <= price <= high:
        return "IN_ZONE"
    if low * (1.0 - tolerance) <= price < low:
        return "NEAR_ZONE_BELOW"
    if high < price <= high * (1.0 + tolerance):
        return "NEAR_ZONE_ABOVE"
    return None


def match_plan_resting_orders(
    plan: Any,
    open_orders: Any,
    *,
    tolerance: float,
    full_fraction: float,
    partial_fraction: float,
) -> dict[str, Any]:
    """Map one execution plan's tranches to already-resting exchange orders.

    ``open_orders`` is the sequence of resting-order records fetched for the
    plan's symbol (``OpenOrderRecord`` objects or strict mappings). Each
    tranche independently asks whether resting limit orders on the plan's side
    sit inside its zone widened by ``tolerance``. Matching is deliberately
    non-exclusive: plan zones may overlap, and one resting order legitimately
    serves every zone that contains its price — claiming it for only the
    first zone would wrongly mark the others as unplaced. Orders matched by
    more than one tranche are counted toward each tranche's coverage; the
    plan-level fraction caps every tranche at its own amount so the aggregate
    stays bounded.
    """
    action = str(_plan_value(plan, "action", "") or "").strip().upper()
    required_side = "BUY" if action == "INCREASE" else "SELL"
    tranches = _plan_value(plan, "tranches") or ()
    normalized_tranches = tuple(
        t.as_dict() if hasattr(t, "as_dict") else t for t in tranches
    )
    normalized_tranches = tuple(
        sorted(normalized_tranches, key=lambda t: _plan_value(t, "sequence", 0))
    )
    zones = [
        (_tranche_number(t, "price_low"), _tranche_number(t, "price_high"))
        for t in normalized_tranches
    ]

    unmatched: list[dict[str, Any]] = []
    matched: list[list[dict[str, Any]]] = [[] for _ in normalized_tranches]
    resting_notional = [0.0] * len(normalized_tranches)
    for order in open_orders:
        record = order if isinstance(order, OpenOrderRecord) else OpenOrderRecord.from_mapping(order)
        summary = {
            "order_id": record.order_id,
            "side": record.side,
            "order_type": record.order_type,
            "price": record.price,
            "remaining_quantity": record.remaining_quantity,
            "remaining_notional_usd": record.remaining_notional_usd,
        }
        if record.remaining_quantity <= 0.0:
            unmatched.append({**summary, "reason": "fully executed; nothing rests"})
            continue
        if record.side != required_side:
            unmatched.append({**summary, "reason": "opposite side of the plan"})
            continue
        if record.order_type not in RESTING_ORDER_TYPES:
            unmatched.append({**summary, "reason": "non-resting order type"})
            continue
        entry = {
            "order_id": record.order_id,
            "price": record.price,
            "remaining_quantity": record.remaining_quantity,
            "remaining_notional_usd": record.remaining_notional_usd,
        }
        joined_any = False
        for index, (low, high) in enumerate(zones):
            kind = _match_kind(record.price, low, high, tolerance)
            if kind is None:
                continue
            joined_any = True
            matched[index].append({**entry, "match_kind": kind})
            resting_notional[index] += record.remaining_notional_usd
        if not joined_any:
            unmatched.append({
                **summary,
                "reason": "outside every planned zone (within tolerance)",
            })

    tranche_states = []
    planned_total = 0.0
    capped_total = 0.0
    for index, tranche in enumerate(normalized_tranches):
        amount = _tranche_number(tranche, "amount_usd")
        planned_total += amount
        fraction = min(1.0, resting_notional[index] / amount) if amount > 0 else 0.0
        capped_total += min(resting_notional[index], amount)
        if fraction >= full_fraction:
            state = _RESTING_FULL
        elif fraction >= partial_fraction:
            state = _RESTING_PARTIAL
        else:
            state = _NOT_RESTING
        tranche_states.append({
            "sequence": _plan_value(tranche, "sequence"),
            "zone_low": zones[index][0],
            "zone_high": zones[index][1],
            "amount_usd": amount,
            "resting_state": state,
            "covered_fraction": fraction,
            "resting_notional_usd": resting_notional[index],
            "matched_orders": matched[index],
        })

    states = {t["resting_state"] for t in tranche_states}
    if states and states <= {_RESTING_FULL}:
        plan_state = _RESTING_FULL
    elif _RESTING_FULL in states or _RESTING_PARTIAL in states:
        plan_state = _RESTING_PARTIAL
    else:
        plan_state = _NOT_RESTING
    planned_amount = float(_plan_value(plan, "planned_amount_usd", 0.0) or 0.0)
    covered = min(1.0, capped_total / planned_amount) if planned_amount > 0 else 0.0
    return {
        "action": action,
        "resting_state": plan_state,
        "covered_fraction": covered,
        "tranches": tranche_states,
        "unmatched_orders": unmatched,
    }


__all__ = ["match_plan_resting_orders"]
