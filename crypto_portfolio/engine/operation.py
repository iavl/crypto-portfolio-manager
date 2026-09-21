"""Reconcile entry plans with approved funding without changing approvals."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from ..models.operation import FinalOperation, OperationLine, amount


def build_final_operation(actions: Iterable[Any], execution_plans: Mapping[str, Any], *, stable_symbols: Iterable[str] = ()) -> FinalOperation:
    stable = {s.upper() for s in stable_symbols}
    plans = {s.upper(): p.as_dict() if hasattr(p, "as_dict") else dict(p) for s, p in execution_plans.items()}
    rows = []
    seen = set()
    for raw in actions:
        a = raw.as_dict() if hasattr(raw, "as_dict") else dict(raw)
        symbol = str(a.get("symbol", "")).upper()
        if not symbol or symbol in seen:
            raise ValueError("operation actions require unique symbols")
        seen.add(symbol)
        action = a["action"]
        approved = amount(a.get("amount_usd", a.get("approved_amount_usd", 0.0)), "approved amount")
        if action not in {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}:
            raise ValueError("unsupported operation action")
        if action in {"HOLD", "WAIT", "NO_TRADE"} and approved:
            raise ValueError("non-executable action has nonzero amount")
        proposed, reserve, purpose, reserve_policy = approved, 0.0, "NONE", None
        reason = str(a.get("rationale", a.get("portfolio_constraint", "")))
        status = "PROPOSED_NOT_CONFIRMED" if approved else "NO_EXECUTABLE_CHANGE"
        if action == "INCREASE" and symbol not in stable and approved:
            if symbol not in plans:
                raise ValueError(f"execution plan is required for approved increase {symbol}")
            plan = plans[symbol]
            if plan.get("symbol") is not None and str(plan["symbol"]).upper() != symbol:
                raise ValueError("execution plan symbol does not match approval")
            if abs(amount(plan.get("approved_amount_usd", approved), "plan approval") - approved) > 1e-7:
                raise ValueError("execution plan does not match approval")
            proposed = amount(plan["planned_amount_usd"], "planned amount")
            reserve = amount(plan["reserve_amount_usd"], "reserve amount")
            if abs(proposed + reserve - approved) > 1e-7:
                raise ValueError("planned amount and reserve must reconcile approval")
            if plan["action"] == "WAIT" and proposed:
                raise ValueError("WAIT plan cannot deploy funds")
            if plan["action"] not in {"WAIT", "INCREASE"}:
                raise ValueError("unsupported increase plan")
            status = "CONDITIONAL_LIMIT_PROPOSAL" if proposed else "GATE_HOLD"
            reason = str(plan.get("rationale", reason))
            reserve_policy = plan.get("reserve_policy")
            purpose = "RISK_INCREASE"
        elif action in {"REDUCE", "EXIT"} and approved:
            # Only an explicit ordinary overweight reason identifies a funding
            # leg. Stable-specific risk exits must survive a gated purchase.
            purpose = "BUY_FUNDING" if symbol in stable and action == "REDUCE" and a.get("action_reason") == "ALLOCATION_OVERWEIGHT" else "INDEPENDENT_REDUCTION"
        elif action == "INCREASE" and symbol in stable:
            purpose = "STABLE_RECEIPT"
        rows.append(OperationLine(symbol, action, approved, proposed, reserve, status, purpose, reason, reserve_policy))
    if set(plans) - seen:
        raise ValueError("execution plan has no approved action")
    buys = sum(r.proposed_amount_usd for r in rows if r.purpose == "RISK_INCREASE")
    sales = sum(r.proposed_amount_usd for r in rows if r.purpose == "INDEPENDENT_REDUCTION" and r.symbol not in stable)
    funding = sum(r.proposed_amount_usd for r in rows if r.purpose == "BUY_FUNDING")
    required = max(0.0, buys - sales)
    scale = min(1.0, required / funding) if funding else 0.0
    if funding * scale + sales + 1e-7 < buys:
        raise ValueError("final buy proposals exceed their matched funding")
    # New cash already lives in the post-cash stable sleeve in rebalance;
    # subtracting it again here would count that funding source twice.
    rows = [replace(r, proposed_amount_usd=r.proposed_amount_usd * scale,
                    execution_status="FUNDING_DEFERRED" if scale == 0 else "CONDITIONAL_FUNDING",
                    reason=r.reason + "; funding limited to entry-approved proposals")
            if r.purpose == "BUY_FUNDING" else r for r in rows]
    active = any(r.proposed_amount_usd for r in rows if r.purpose not in {"STABLE_RECEIPT", "NONE"})
    decision = "PROPOSED" if active else "WAIT" if any(r.execution_status == "GATE_HOLD" for r in rows) else "NO_TRADE"
    return FinalOperation(tuple(sorted(rows, key=lambda r: r.symbol)), decision, buys, sales, funding * scale, sales - buys)
