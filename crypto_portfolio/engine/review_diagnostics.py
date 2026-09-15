"""Static, diagnostic-only portfolio scenarios and ordered target attribution."""
from __future__ import annotations

import math
from typing import Any, Mapping

from ..models.decision import _weights
from ..models.execution import ExecutionPlan
from ..models.policy import Policy, policy_from_mapping, policy_hash, resolve_policy
from .risk import stress_diagnostic, scenario_portfolio_return


def _amount(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return float(value)


def portfolio_stress(weights: Mapping[str, float], *, policy: Policy,
                     drawdown: float | None = None) -> dict[str, Any]:
    weights = _weights(weights, "stress weights")
    scenario = {**policy.stress_scenario, **{s: 0.0 for s in policy.stable_symbols}}
    missing = sorted(s for s, w in weights.items() if w > 0 and s not in scenario)
    base = {"status": "DIAGNOSTIC_ONLY", "stable_assumption": "zero return, not risk-free",
            "scenario_returns": {s: scenario.get(s) for s, w in weights.items() if w > 0}}
    if missing:
        return {**base, "availability": "UNAVAILABLE", "reason": "MISSING_SCENARIO_RETURNS",
                "missing_assets": missing, "scenario_return": None, "projected_drawdown": None,
                "budget_breach": None}
    if drawdown is None:
        return {**base, "availability": "PARTIAL", "reason": "NAV_DRAWDOWN_UNAVAILABLE",
                "scenario_return": scenario_portfolio_return(weights, scenario),
                "asset_contributions": {s: w * scenario[s] for s, w in weights.items() if w > 0},
                "projected_drawdown": None, "budget_breach": None, "remaining_capacity": None}
    return {**base, "availability": "AVAILABLE",
            **stress_diagnostic(weights, scenario, current_drawdown=drawdown,
                                risk_budget=policy.max_portfolio_drawdown)}


def build_review_diagnostics(*, current_weights: Mapping[str, float], portfolio_value: float,
                             target_weights: Mapping[str, float], actions: Any,
                             execution_plans: Mapping[str, Any] | None = None,
                             new_cash: float = 0.0, drawdown: float | None = None,
                             regime: str = "NORMAL", policy: Policy | None = None) -> dict[str, Any]:
    from .rebalance import RebalanceAction

    policy = policy or resolve_policy()
    current = _weights(current_weights, "current_weights")
    target = _weights(target_weights, "target_weights")
    if not current or not target:
        raise ValueError("diagnostics require complete current and target weights")
    value = _amount(portfolio_value, "portfolio_value")
    cash = _amount(new_cash, "new_cash")
    if value <= 0:
        raise ValueError("diagnostics require positive existing portfolio value")
    if drawdown is not None and (isinstance(drawdown, bool) or not isinstance(drawdown, (int, float)) or not math.isfinite(drawdown) or not -1 <= drawdown <= 0):
        raise ValueError("drawdown must be in [-1, 0] or null")
    parsed = [a if isinstance(a, RebalanceAction) else RebalanceAction(**a) for a in actions]
    if len({a.symbol for a in parsed}) != len(parsed):
        raise ValueError("diagnostics actions contain duplicate symbols")
    plans = {s: p if isinstance(p, ExecutionPlan) else ExecutionPlan.from_mapping(p)
             for s, p in (execution_plans or {}).items()}
    action_by_symbol = {a.symbol: a for a in parsed}
    for symbol, plan in plans.items():
        action = action_by_symbol.get(symbol)
        if plan.symbol != symbol or action is None or not math.isclose(plan.approved_amount_usd, action.amount_usd, abs_tol=1e-7):
            raise ValueError("execution plan does not match approved action")
    stables = set(policy.stable_symbols)
    risky = [a for a in parsed if a.symbol not in stables]
    minimum = max(policy.min_stablecoin_weight, policy.regime(regime).stablecoin_target)
    plan_details = {}
    for symbol, plan in plans.items():
        pairs = [(a.sequence, b.sequence) for i, a in enumerate(plan.tranches)
                 for b in plan.tranches[i + 1:] if max(a.price_low, b.price_low) <= min(a.price_high, b.price_high)]
        plan_details[symbol] = {"reserve_amount_usd": plan.reserve_amount_usd,
                                "reserve_policy": plan.reserve_policy,
                                "invalidation": plan.invalidation.as_dict() if plan.invalidation else None,
                                "overlapping_tranches": pairs,
                                "status": "PROPOSED_NOT_CONFIRMED"}

    def project(name: str, fills: Mapping[str, float], *, include_cash: bool = True) -> dict[str, Any]:
        total = value + (cash if include_cash else 0.0)
        dollars = {s: w * value for s, w in current.items() if s not in stables}
        for symbol, delta in fills.items():
            dollars[symbol] = dollars.get(symbol, 0.0) + delta
        stable_dollars = total - sum(dollars.values())
        shortfall = max(0.0, -stable_dollars)
        issues = []
        if shortfall > 1e-7:
            issues.append("INSUFFICIENT_FUNDING")
        if any(d < -1e-7 for d in dollars.values()):
            issues.append("SALE_EXCEEDS_HOLDING")
        if stable_dollars / total < minimum - 1e-9:
            issues.append("STABLE_FLOOR")
        weights = None
        if not shortfall and not any(d < 0 for d in dollars.values()):
            stable_before = sum(current.get(s, 0.0) for s in stables)
            if stable_before:
                dollars.update({s: stable_dollars * current[s] / stable_before for s in current if s in stables})
            else:
                dollars[policy.stable_symbols[0]] = stable_dollars
            weights = {s: d / total for s, d in dollars.items()}
        return {"name": name, "feasible": not issues, "violations": issues,
                "portfolio_value_usd": total, "stable_balance_usd": max(0.0, stable_dollars),
                "funding_shortfall_usd": shortfall, "stable_floor_shortfall_usd": max(0.0, minimum * total - stable_dollars),
                "weights": weights, "fills_usd": dict(fills),
                "stress": portfolio_stress(weights, policy=policy, drawdown=drawdown) if weights else None}

    first, sales, full = {}, {}, {}
    missing_plans = []
    for a in risky:
        if a.action == "INCREASE":
            full[a.symbol] = a.amount_usd
            plan = plans.get(a.symbol)
            if plan is None:
                missing_plans.append(a.symbol)
            else:
                first[a.symbol] = min(plan.tranches, key=lambda t: t.sequence).amount_usd if plan.tranches else 0.0
        elif a.action in {"REDUCE", "EXIT"}:
            sales[a.symbol] = full[a.symbol] = -a.amount_usd
    rows = {"CURRENT": project("CURRENT", {}, include_cash=False),
            "FIRST_BUYS_ONLY": project("FIRST_BUYS_ONLY", first) if not missing_plans else {
                "name": "FIRST_BUYS_ONLY", "feasible": None, "availability": "UNAVAILABLE",
                "reason": "MISSING_EXECUTION_PLANS", "missing_assets": sorted(missing_plans)},
            "SALES_ONLY": project("SALES_ONLY", sales),
            "APPROVED_FULL": project("APPROVED_FULL", full)}
    original = rows["CURRENT"]["stress"]["scenario_return"]
    for row in rows.values():
        stress = row.get("stress")
        if stress:
            after = stress.get("scenario_return")
            stress["change_from_current"] = after - original if after is not None and original is not None else None
    return {"status": "DIAGNOSTIC_ONLY", "basis": "static current valuations; no fill-price or probability forecast; fees excluded",
            "policy_hash": policy_hash(policy), "required_stable_weight": minimum,
            "scenarios": rows, "strategic_stress": portfolio_stress(target, policy=policy, drawdown=drawdown),
            "execution_plans": plan_details}


def target_change_attribution(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """Four fixed-order counterfactuals; no old-contract dispatch or migrations.

    Inputs are frozen allocation inputs, plus portfolio_value and new_cash.
    Prior targets are replayed with the current engine only when their contract
    is current. A mismatch is unavailable, never a fabricated market effect.
    """
    from .allocation import build_target_allocation
    from .scoring import score_assessment
    from ..models.evidence import AssetAssessment

    hashes = {"previous_policy_hash": policy_hash(previous.get("resolved_policy", {})),
              "current_policy_hash": policy_hash(current.get("resolved_policy", {}))}
    try:
        old_policy = policy_from_mapping(dict(previous["resolved_policy"]))
        new_policy = policy_from_mapping(dict(current["resolved_policy"]))

        def run(market: Mapping[str, Any], holdings: Mapping[str, Any], policy: Policy):
            value = _amount(holdings["portfolio_value"], "portfolio_value")
            cash = _amount(holdings.get("new_cash", 0.0), "new_cash")
            if value <= 0:
                raise ValueError("portfolio_value must be positive")
            weights = _weights(holdings["current_weights"], "current_weights")
            weights = {s: w * value / (value + cash) for s, w in weights.items()}
            weights[policy.stable_symbols[0]] = weights.get(policy.stable_symbols[0], 0.0) + cash / (value + cash)
            assessments = {}
            for symbol, raw in market["assessments"].items():
                asset = AssetAssessment.from_mapping(symbol, raw)
                if not asset.factor_scores:
                    raise ValueError("missing frozen factor inputs")
                assessments[symbol] = score_assessment(asset, policy=policy)[0]
            result = build_target_allocation(policy=policy, regime=market["regime"],
                                             assessments=assessments, current_weights=weights,
                                             **{k: market[k] for k in ("overlays", "chain_liveness", "structural_risk", "decision_confidence") if k in market})
            return dict(result.target_weights)

        stages = [run(previous, previous, old_policy), run(previous, previous, new_policy),
                  run(current, previous, new_policy), run(current, current, new_policy)]
        for frozen, computed in ((previous, stages[0]), (current, stages[-1])):
            expected = frozen["target_weights"]
            if any(not math.isclose(computed.get(s, 0.0), expected.get(s, 0.0), abs_tol=1e-9) for s in set(computed) | set(expected)):
                raise ValueError("frozen target not reproducible from supplied inputs")
    except (KeyError, TypeError, ValueError) as exc:
        return {**hashes, "availability": "UNAVAILABLE", "reason": str(exc), "stages": None}
    symbols = sorted(set().union(*(set(s) for s in stages)))
    changes = {name: {s: stages[i + 1].get(s, 0.0) - stages[i].get(s, 0.0) for s in symbols}
               for i, name in enumerate(("policy", "market_and_assessment", "holdings_and_cash"))}
    return {**hashes, "availability": "AVAILABLE", "order": list(changes),
            "note": "fixed-order decomposition includes interactions", "stages": stages,
            "changes": changes, "total_change": {s: stages[-1].get(s, 0.0) - stages[0].get(s, 0.0) for s in symbols}}
