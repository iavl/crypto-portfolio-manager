"""Threshold-based rebalance and execution-plan validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..models.execution import ExecutionPlan
from ..models.policy import Policy, resolve_policy


_ACTIONS = {"INCREASE", "REDUCE", "HOLD", "EXIT", "WAIT", "NO_TRADE"}


def _weights(value: Mapping[str, Any], field: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result: dict[str, float] = {}
    for raw_symbol, raw_weight in value.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError(f"{field} contains an empty symbol")
        if symbol in result:
            raise ValueError(f"{field} contains duplicate symbol {symbol}")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError(f"{field}.{symbol} must be a number")
        weight = float(raw_weight)
        if not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError(f"{field}.{symbol} must be finite and in [0, 1]")
        result[symbol] = weight
    return result


def _truthy_flag(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, "", "FALSE", "false", None):
        return False
    if value in (1, "TRUE", "true"):
        return True
    raise ValueError(f"{field} values must be boolean")


@dataclass(frozen=True)
class RebalanceAction:
    symbol: str
    action: str
    current_weight: float
    target_weight: float
    amount_usd: float
    priority: str
    rationale: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if not isinstance(self.action, str):
            raise ValueError("action must be a string")
        action = self.action.upper()
        if action not in _ACTIONS:
            raise ValueError(f"action must be one of {sorted(_ACTIONS)}")
        object.__setattr__(self, "action", action)
        for field in ("current_weight", "target_weight", "amount_usd"):
            value = float(getattr(self, field))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{field} must be finite and >= 0")
            object.__setattr__(self, field, value)
        executable = {"INCREASE", "REDUCE", "EXIT"}
        if action in executable and self.amount_usd <= 0:
            raise ValueError(f"{action} requires a positive executable amount")
        if action not in executable and self.amount_usd != 0:
            raise ValueError(f"{action} must have zero executable amount")

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "current_weight": self.current_weight,
            "target_weight": self.target_weight,
            "amount_usd": self.amount_usd,
            "priority": self.priority,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class RebalanceResult:
    actions: tuple[RebalanceAction, ...]
    decision: str
    post_cash_total: float = 0.0
    reconciliation: Mapping[str, float | bool] | None = None

    @property
    def no_trade(self) -> bool:
        return self.decision == "NO_TRADE"

    def __iter__(self):
        return iter(self.actions)

    def __len__(self) -> int:
        return len(self.actions)

    def __getitem__(self, index: int) -> RebalanceAction:
        return self.actions[index]

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "no_trade": self.no_trade,
            "actions": [action.as_dict() for action in self.actions],
            "post_cash_total": self.post_cash_total,
            "reconciliation": dict(self.reconciliation or {}),
        }


def _stable_target_weights(
    current: Mapping[str, float], target: Mapping[str, float], stable_symbols: Iterable[str]
) -> dict[str, float]:
    symbols = tuple(stable_symbols)
    target_total = sum(target.get(symbol, 0.0) for symbol in symbols)
    current_by_symbol = {
        symbol: current.get(symbol, 0.0) for symbol in symbols if current.get(symbol, 0.0) > 0
    }
    if not current_by_symbol:
        selected = next((symbol for symbol in symbols if target.get(symbol, 0.0) > 0), symbols[0])
        return {selected: target_total}
    current_total = sum(current_by_symbol.values())
    return {
        symbol: target_total * weight / current_total
        for symbol, weight in current_by_symbol.items()
    }


def reconcile_trade_dollars(
    actions: Iterable[RebalanceAction],
    new_cash_available: float = 0.0,
    stable_symbols: Iterable[str] = (),
) -> dict[str, float | bool]:
    """Reconcile executable risky-asset buys and sells into stable/cash change."""
    new_cash = float(new_cash_available)
    if not math.isfinite(new_cash) or new_cash < 0:
        raise ValueError("new_cash_available must be finite and >= 0")
    stable = {str(symbol).strip().upper() for symbol in stable_symbols}
    sells = sum(
        action.amount_usd
        for action in actions
        if action.action in {"REDUCE", "EXIT"} and action.symbol not in stable
    )
    buys = sum(
        action.amount_usd
        for action in actions
        if action.action == "INCREASE" and action.symbol not in stable
    )
    residual = new_cash + sells - buys
    return {
        "external_new_cash": new_cash,
        "planned_sells": sells,
        "planned_buys": buys,
        "residual_stablecoin_change": residual,
        "balanced": math.isfinite(residual),
    }


def recommend_rebalance(
    current_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
    portfolio_value: float,
    *,
    new_cash_available: float = 0.0,
    thesis_broken: Iterable[str] | Mapping[str, bool] | None = None,
    policy: Policy | None = None,
) -> RebalanceResult:
    resolved = policy or resolve_policy()
    current = _weights(current_weights, "current_weights")
    target = _weights(target_weights, "target_weights")
    if not math.isclose(sum(target.values()), 1.0, abs_tol=1e-9):
        raise ValueError("target_weights must sum to 1")
    portfolio_value = float(portfolio_value)
    new_cash_available = float(new_cash_available)
    if not math.isfinite(portfolio_value) or portfolio_value < 0:
        raise ValueError("portfolio_value must be finite and >= 0")
    if not math.isfinite(new_cash_available) or new_cash_available < 0:
        raise ValueError("new_cash_available must be finite and >= 0")

    if isinstance(thesis_broken, Mapping):
        broken = {
            str(symbol).strip().upper()
            for symbol, value in thesis_broken.items()
            if _truthy_flag(value, "thesis_broken")
        }
    else:
        broken = {str(symbol).strip().upper() for symbol in (thesis_broken or ())}

    current_total = sum(current.values())
    if current_total > 1.0 + 1e-9:
        raise ValueError("current_weights must sum to no more than 1")
    post_cash_total = portfolio_value + new_cash_available
    if post_cash_total <= 0:
        raise ValueError("portfolio_value plus new_cash_available must be > 0")
    stable_symbols = tuple(resolved.stable_symbols)
    stable_target = _stable_target_weights(current, target, stable_symbols)
    effective_current: dict[str, float] = {
        symbol: weight * portfolio_value for symbol, weight in current.items()
    }
    stable_symbol = next(
        (symbol for symbol in stable_symbols if current.get(symbol, 0.0) > 0),
        next(iter(stable_target)),
    )
    unallocated = max(0.0, 1.0 - current_total) * portfolio_value
    effective_current[stable_symbol] = effective_current.get(stable_symbol, 0.0) + unallocated + new_cash_available
    effective_target = {symbol: weight for symbol, weight in target.items() if symbol not in stable_symbols}
    effective_target.update(stable_target)
    symbols = sorted(set(effective_current) | set(effective_target) | broken)
    candidates: list[dict[str, Any]] = []
    for symbol in symbols:
        current_amount = effective_current.get(symbol, 0.0)
        current_weight = current_amount / post_cash_total
        target_weight = effective_target.get(symbol, 0.0)
        difference = target_weight - current_weight
        deviation_pp = abs(difference) * 100.0
        small_target_watch = (
            0 < target_weight < 0.05
            and deviation_pp < resolved.rebalance["hold_below_pp"]
            and difference != 0
            and abs(difference) / target_weight >= 0.5
        )
        if symbol in broken and current.get(symbol, 0.0) > 0:
            action = "EXIT"
            priority = "HIGH"
            amount = current.get(symbol, 0.0) * portfolio_value
            rationale = "investment thesis is marked broken"
        elif small_target_watch:
            action = "WAIT"
            priority = "WATCH"
            amount = 0.0
            rationale = "small target has a material relative deviation"
        elif deviation_pp < resolved.rebalance["hold_below_pp"]:
            action = "HOLD"
            priority = "LOW"
            amount = 0.0
            rationale = f"deviation {deviation_pp:.2f}pp is below the hold threshold"
        elif deviation_pp <= resolved.rebalance["watch_below_pp"]:
            action = "WAIT"
            priority = "WATCH"
            amount = 0.0
            rationale = f"deviation {deviation_pp:.2f}pp is in the watch band"
        elif difference > 0:
            action = "INCREASE"
            priority = "HIGH" if deviation_pp > resolved.rebalance["high_priority_above_pp"] else "NORMAL"
            amount = difference * post_cash_total
            rationale = "underweight exceeds the active rebalance threshold"
        else:
            action = "EXIT" if target_weight == 0 else "REDUCE"
            priority = "HIGH" if deviation_pp > resolved.rebalance["high_priority_above_pp"] else "NORMAL"
            amount = -difference * post_cash_total
            rationale = "overweight exceeds the active rebalance threshold"
        candidates.append({
            "symbol": symbol,
            "action": action,
            "current_weight": current_weight,
            "target_weight": target_weight,
            "amount": max(0.0, amount),
            "priority": priority,
            "rationale": rationale,
        })

    available_funding = sum(
        item["amount"] for item in candidates if item["action"] in {"REDUCE", "EXIT"}
    )
    for item in sorted(
        (item for item in candidates if item["action"] == "INCREASE"),
        key=lambda value: (-value["amount"], value["symbol"]),
    ):
        item["amount"] = min(item["amount"], available_funding)
        available_funding -= item["amount"]
        if item["amount"] <= 1e-9:
            item["action"] = "WAIT"
            item["priority"] = "WATCH"
            item["rationale"] = "underweight is not funded by available cash or executable sales"

    actions = [
        RebalanceAction(
            symbol=item["symbol"],
            action=item["action"],
            current_weight=item["current_weight"],
            target_weight=item["target_weight"],
            amount_usd=item["amount"] if item["action"] in {"INCREASE", "REDUCE", "EXIT"} else 0.0,
            priority=item["priority"],
            rationale=item["rationale"],
        )
        for item in candidates
    ]
    active = {"INCREASE", "REDUCE", "EXIT"}
    decision = "REBALANCE" if any(action.action in active for action in actions) else "NO_TRADE"
    return RebalanceResult(
        tuple(actions),
        decision,
        post_cash_total,
        reconcile_trade_dollars(actions, new_cash_available, stable_symbols),
    )


def rebalance(
    current_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
    portfolio_value: float,
    *,
    new_cash_available: float = 0.0,
    thesis_broken: Iterable[str] | Mapping[str, bool] | None = None,
    policy: Policy | None = None,
) -> RebalanceResult:
    return recommend_rebalance(
        current_weights,
        target_weights,
        portfolio_value,
        new_cash_available=new_cash_available,
        thesis_broken=thesis_broken,
        policy=policy,
    )


def validate_execution_plan(plan: ExecutionPlan | Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> bool:
    """Validate staged execution zones without generating price zones."""
    if isinstance(plan, ExecutionPlan) or (isinstance(plan, Mapping) and ({"tranches", "execution_plan_version"} & set(plan))):
        from .execution import validate_execution_plan as validate_typed_execution_plan

        return validate_typed_execution_plan(plan)
    zones = plan.get("execution_zones") if isinstance(plan, Mapping) else plan
    if not isinstance(zones, (list, tuple)) or not zones:
        raise ValueError("execution plan must contain a non-empty execution_zones list")
    total = 0.0
    for index, zone in enumerate(zones):
        if not isinstance(zone, Mapping):
            raise ValueError(f"execution zone {index} must be an object")
        fraction = zone.get("allocation_fraction")
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
            raise ValueError(f"execution zone {index}.allocation_fraction must be a number")
        fraction = float(fraction)
        if not math.isfinite(fraction) or not 0 < fraction <= 1:
            raise ValueError(f"execution zone {index}.allocation_fraction must be in (0, 1]")
        total += fraction
        description = zone.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"execution zone {index}.description must be non-empty")
        low = zone.get("price_low")
        high = zone.get("price_high")
        for name, value in (("price_low", low), ("price_high", high)):
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"execution zone {index}.{name} must be a number or null")
                value = float(value)
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"execution zone {index}.{name} must be finite and >= 0")
        if low is not None and high is not None and float(low) > float(high):
            raise ValueError(f"execution zone {index} has price_low above price_high")
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        raise ValueError("execution allocation fractions must sum to 1")
    return True


__all__ = [
    "RebalanceAction",
    "RebalanceResult",
    "rebalance",
    "reconcile_trade_dollars",
    "recommend_rebalance",
    "validate_execution_plan",
]
