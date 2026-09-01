"""Threshold-based rebalance and execution-plan validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

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
        }


def _cash_repairs(
    underweights: Iterable[tuple[str, float]],
    target: Mapping[str, float],
    current: Mapping[str, float],
    portfolio_value: float,
    new_cash: float,
) -> dict[str, float]:
    remaining = new_cash
    total_after_cash = portfolio_value + new_cash
    repairs: dict[str, float] = {}
    for symbol, _ in sorted(underweights, key=lambda item: (-item[1], item[0])):
        needed = max(
            0.0,
            target.get(symbol, 0.0) * total_after_cash - current.get(symbol, 0.0) * portfolio_value,
        )
        amount = min(remaining, needed)
        repairs[symbol] = amount
        remaining -= amount
    return repairs


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
        broken = {str(symbol).strip().upper() for symbol, value in thesis_broken.items() if value}
    else:
        broken = {str(symbol).strip().upper() for symbol in (thesis_broken or ())}
    symbols = sorted(set(current) | set(target))
    deviations = [(symbol, abs(target.get(symbol, 0.0) - current.get(symbol, 0.0))) for symbol in symbols]
    repairs = _cash_repairs(
        [item for item in deviations if target.get(item[0], 0.0) > current.get(item[0], 0.0)],
        target,
        current,
        portfolio_value,
        new_cash_available,
    )
    actions: list[RebalanceAction] = []
    for symbol in symbols:
        current_weight = current.get(symbol, 0.0)
        target_weight = target.get(symbol, 0.0)
        difference = target_weight - current_weight
        deviation_pp = abs(difference) * 100.0
        small_target_watch = (
            0 < target_weight < 0.05
            and deviation_pp < resolved.rebalance["hold_below_pp"]
            and difference != 0
            and abs(difference) / target_weight >= 0.5
        )
        amount = 0.0
        if symbol in broken and current_weight > 0:
            action = "EXIT"
            priority = "HIGH"
            amount = current_weight * portfolio_value
            rationale = "investment thesis is marked broken"
        elif small_target_watch:
            action = "WAIT"
            priority = "WATCH"
            amount = repairs.get(symbol, 0.0)
            rationale = "small target has a material relative deviation"
        elif deviation_pp < resolved.rebalance["hold_below_pp"]:
            action = "HOLD"
            priority = "LOW"
            rationale = f"deviation {deviation_pp:.2f}pp is below the hold threshold"
        elif deviation_pp <= resolved.rebalance["watch_below_pp"]:
            action = "WAIT"
            priority = "WATCH"
            amount = repairs.get(symbol, 0.0)
            rationale = f"deviation {deviation_pp:.2f}pp is in the watch band"
        elif difference > 0:
            action = "INCREASE"
            priority = "HIGH" if deviation_pp > resolved.rebalance["high_priority_above_pp"] else "NORMAL"
            amount = repairs.get(symbol, difference * portfolio_value)
            rationale = "underweight exceeds the active rebalance threshold"
        else:
            action = "EXIT" if target_weight == 0 else "REDUCE"
            priority = "HIGH" if deviation_pp > resolved.rebalance["high_priority_above_pp"] else "NORMAL"
            amount = (current_weight - target_weight) * portfolio_value
            rationale = "overweight exceeds the active rebalance threshold"
        actions.append(
            RebalanceAction(
                symbol=symbol,
                action=action,
                current_weight=current_weight,
                target_weight=target_weight,
                amount_usd=max(0.0, amount),
                priority=priority,
                rationale=rationale,
            )
        )
    active = {"INCREASE", "REDUCE", "EXIT"}
    decision = "REBALANCE" if any(action.action in active for action in actions) else "NO_TRADE"
    return RebalanceResult(tuple(actions), decision)


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


def validate_execution_plan(plan: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> bool:
    """Validate staged execution zones without generating price zones."""
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
    "recommend_rebalance",
    "validate_execution_plan",
]
