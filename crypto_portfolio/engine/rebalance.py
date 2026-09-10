"""Threshold-based rebalance and execution-plan validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..models.decision_packet import NoTradeAttribution
from ..models.confidence import DEFAULT_HIGH_MIN, DEFAULT_MEDIUM_MIN
from ..models.policy import Policy, resolve_policy
from .confidence import confidence_deployment_factor


_ACTIONS = {"INCREASE", "REDUCE", "HOLD", "EXIT", "WAIT", "NO_TRADE"}
_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}


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


def _value_field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _decision_score(value: Any) -> float | None:
    raw = _value_field(value, "score", _value_field(value, "confidence_score"))
    if raw is None:
        return None
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    return score if math.isfinite(score) and 0 <= score <= 1 else None


def build_no_trade_attribution(
    current_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
    actions: Iterable[RebalanceAction | Mapping[str, Any]] = (),
    *,
    policy: Policy | None = None,
    regime: str = "NORMAL",
    assessments: Mapping[str, Any] | None = None,
    decision_confidence: Any | None = None,
    risk_flags: Iterable[str] = (),
    critical_missing_data: Iterable[str] = (),
    execution: Any | None = None,
) -> NoTradeAttribution | None:
    """Explain a non-executable outcome using only deterministic gate inputs."""
    resolved = policy or resolve_policy()
    current = _weights(current_weights, "current_weights")
    target = _weights(target_weights, "target_weights")
    action_values = tuple(actions)
    if any(
        str(_value_field(item, "action", "")).strip().upper() in {"INCREASE", "REDUCE", "EXIT"}
        and float(_value_field(item, "amount_usd", 0.0)) > 0
        for item in action_values
    ):
        return None

    reasons: set[str] = set()
    flags = {str(item).strip().upper() for item in risk_flags}
    missing = {str(item).strip().upper() for item in critical_missing_data}
    assessments = {str(key).strip().upper(): value for key, value in (assessments or {}).items()}

    score_gate = "UNKNOWN"
    scored = False
    for symbol, assessment in assessments.items():
        if resolved.classify(symbol) != "satellite":
            continue
        score = _value_field(assessment, "weighted_score")
        if score is None:
            continue
        scored = True
        if float(score) < resolved.allocation["satellite_entry_score"]:
            score_gate = "BLOCKED"
            reasons.add("SCORE_BELOW_ENTRY")
            break
    if scored and score_gate != "BLOCKED":
        score_gate = "PASS"

    confidence_gate = "UNKNOWN"
    confidence_values = []
    for assessment in assessments.values():
        confidence = str(_value_field(assessment, "confidence", "")).strip().upper()
        if confidence in {"HIGH", "MEDIUM", "LOW"}:
            confidence_values.append(confidence)
        if confidence == "LOW" or _value_field(assessment, "critical_data_complete", True) is False:
            confidence_gate = "BLOCKED"
            reasons.add("CONFIDENCE_TOO_LOW")
    decision_score = _decision_score(decision_confidence)
    decision_band = str(_value_field(decision_confidence, "band", "")).strip().upper()
    medium, high = (
        resolved.confidence.get("band_thresholds", {}).get("medium_min", DEFAULT_MEDIUM_MIN),
        resolved.confidence.get("band_thresholds", {}).get("high_min", DEFAULT_HIGH_MIN),
    )
    if decision_score is not None and decision_score < medium or decision_band == "LOW":
        confidence_gate = "BLOCKED"
        reasons.add("CONFIDENCE_TOO_LOW")
    elif decision_score is not None and decision_score < high or decision_band == "MEDIUM":
        if confidence_gate != "BLOCKED":
            confidence_gate = "WATCH"
        reasons.add("DECISION_CONFIDENCE_MEDIUM")
    elif confidence_values:
        confidence_gate = "PASS" if all(item == "HIGH" for item in confidence_values) else "WATCH"
    if confidence_gate == "WATCH" and "DECISION_CONFIDENCE_MEDIUM" not in reasons and "CONFIDENCE_TOO_LOW" not in reasons:
        reasons.add("DECISION_CONFIDENCE_MEDIUM")

    regime_name = str(regime).strip().upper()
    if regime_name == "CAPITAL_PRESERVATION":
        regime_gate = "BLOCKED"
        reasons.add("REGIME_RISK_BUDGET_EXHAUSTED")
    elif regime_name == "DEFENSIVE":
        regime_gate = "WATCH"
    elif regime_name == "NORMAL":
        regime_gate = "PASS"
    else:
        regime_gate = "UNKNOWN"

    event_gate = "PASS"
    liveness_gate = "NOT_APPLICABLE"
    btc_relative_gate = "NOT_APPLICABLE"
    for symbol, assessment in assessments.items():
        event = _value_field(assessment, "event_risk")
        event_state = str(_value_field(event, "state", event or "NORMAL")).strip().upper()
        if event_state in {"SEVERE", "CRITICAL"}:
            event_gate = "BLOCKED"
            reasons.add("EVENT_RISK_BLOCK")
        elif event_state in {"ELEVATED", "HIGH"} and event_gate != "BLOCKED":
            event_gate = "WATCH"
        liveness = _value_field(assessment, "chain_liveness")
        if liveness is not None:
            liveness_gate = "PASS"
            liveness_state = str(_value_field(liveness, "status", liveness)).strip().upper()
            if liveness_state in {"HALTED", "UNKNOWN", "FAILED", "CONFLICT"}:
                liveness_gate = "BLOCKED"
                reasons.add("LIVENESS_BLOCK")
            elif liveness_state == "DEGRADED" and liveness_gate != "BLOCKED":
                liveness_gate = "WATCH"
        relative = _value_field(assessment, "relative_strength_vs_btc")
        if symbol == "ETH" or resolved.classify(symbol) == "satellite":
            btc_relative_gate = "PASS"
            relative_state = str(relative or "UNKNOWN").strip().upper()
            if relative_state in {"", "UNKNOWN", "UNDERPERFORM", "MATERIALLY_WEAK"}:
                btc_relative_gate = "BLOCKED"
                reasons.add("BTC_RELATIVE_WEAK")
    if any("LIVENESS" in flag or "CHAIN_" in flag for flag in flags | missing):
        liveness_gate = "BLOCKED"
        reasons.add("LIVENESS_BLOCK")
    if any("SEVERE_EVENT" in flag or "EVENT_RISK" in flag for flag in flags | missing):
        event_gate = "BLOCKED"
        reasons.add("EVENT_RISK_BLOCK")
    if any("STABLECOIN_FLOOR" in flag for flag in flags):
        reasons.add("STABLECOIN_FLOOR_CONSTRAINT")

    positive_deltas = [
        max(0.0, target.get(symbol, 0.0) - current.get(symbol, 0.0)) * 100.0
        for symbol in set(current) | set(target)
    ]
    max_delta = max(positive_deltas, default=0.0)
    hold = resolved.rebalance["hold_below_pp"]
    watch = resolved.rebalance["watch_below_pp"]
    if max_delta < hold:
        allocation_delta_gate = "BLOCKED"
        reasons.add("TARGET_DELTA_BELOW_HOLD_BAND")
    elif max_delta <= watch:
        allocation_delta_gate = "WATCH"
        reasons.add("TARGET_DELTA_WATCH_ONLY")
    else:
        allocation_delta_gate = "PASS"

    rebalance_gate = "BLOCKED"
    if any(str(_value_field(item, "action", "")).strip().upper() == "WAIT" for item in action_values):
        rebalance_gate = "WATCH"
    elif action_values and all(str(_value_field(item, "action", "")).strip().upper() == "HOLD" for item in action_values):
        rebalance_gate = "BLOCKED"
    reasons.add("NO_APPROVED_INCREASE")

    execution_gate = "NOT_APPLICABLE"
    execution_action = str(_value_field(execution, "action", "")).strip().upper()
    execution_mode = str(_value_field(execution, "entry_mode", "")).strip().upper()
    if execution_action or execution_mode:
        execution_gate = "PASS"
        if execution_action == "WAIT" or execution_mode == "WAIT":
            execution_gate = "BLOCKED"
            reasons.add("EXECUTION_WAIT")

    priority = (
        "LIVENESS_BLOCK",
        "EVENT_RISK_BLOCK",
        "REGIME_RISK_BUDGET_EXHAUSTED",
        "CONFIDENCE_TOO_LOW",
        "BTC_RELATIVE_WEAK",
        "SCORE_BELOW_ENTRY",
        "STABLECOIN_FLOOR_CONSTRAINT",
        "EXECUTION_WAIT",
        "TARGET_DELTA_BELOW_HOLD_BAND",
        "TARGET_DELTA_WATCH_ONLY",
        "DECISION_CONFIDENCE_MEDIUM",
        "NO_APPROVED_INCREASE",
    )
    primary = next((reason for reason in priority if reason in reasons), "NO_APPROVED_INCREASE")
    secondary = tuple(reason for reason in priority if reason in reasons and reason != primary)
    return NoTradeAttribution(
        score_gate=score_gate,
        confidence_gate=confidence_gate,
        regime_gate=regime_gate,
        event_gate=event_gate,
        liveness_gate=liveness_gate,
        btc_relative_gate=btc_relative_gate,
        allocation_delta_gate=allocation_delta_gate,
        rebalance_gate=rebalance_gate,
        execution_gate=execution_gate,
        primary_reason=primary,
        secondary_reasons=secondary,
    )


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
            raw_value = getattr(self, field)
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ValueError(f"{field} must be a number")
            value = float(raw_value)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{field} must be finite and >= 0")
            if field != "amount_usd" and value > 1:
                raise ValueError(f"{field} must be <= 1")
            object.__setattr__(self, field, value)
        executable = {"INCREASE", "REDUCE", "EXIT"}
        if action in executable and self.amount_usd <= 0:
            raise ValueError(f"{action} requires a positive executable amount")
        if action not in executable and self.amount_usd != 0:
            raise ValueError(f"{action} must have zero executable amount")
        if not isinstance(self.priority, str) or not self.priority.strip():
            raise ValueError("priority must be a non-empty string")
        if not isinstance(self.rationale, str):
            raise ValueError("rationale must be a string")

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
    no_trade_attribution: NoTradeAttribution | None = None

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
            "no_trade_attribution": self.no_trade_attribution.as_dict() if self.no_trade_attribution else None,
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
    regime: str = "NORMAL",
    decision_confidence: Any | None = None,
) -> RebalanceResult:
    resolved = policy or resolve_policy()
    current = _weights(current_weights, "current_weights")
    target = _weights(target_weights, "target_weights")
    excluded_targets = {
        symbol for symbol, weight in target.items()
        if weight > 0 and resolved.is_excluded(symbol)
    }
    if excluded_targets:
        raise ValueError(
            "excluded assets cannot be allocation targets: "
            + ", ".join(sorted(excluded_targets))
        )
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
    if not stable_symbols:
        raise ValueError("policy must define at least one stable symbol")
    stable_target = _stable_target_weights(current, target, stable_symbols)
    # The stablecoin/cash sleeve must satisfy the harder of the global floor
    # and the active regime target; allocation and the risk gate enforce it on
    # their paths, and rebalance validates the target it is asked to execute.
    regime_name = str(regime).strip().upper()
    if regime_name not in _REGIMES:
        raise ValueError(f"regime must be one of {sorted(_REGIMES)}")
    required_stable = max(
        resolved.min_stablecoin_weight,
        resolved.regime(regime_name).stablecoin_target,
    )
    target_stable_total = sum(target.get(symbol, 0.0) for symbol in stable_symbols)
    if target_stable_total + 1e-9 < required_stable:
        raise ValueError(
            f"target stablecoin sleeve {target_stable_total:.2%} is below "
            f"required {required_stable:.2%}"
        )
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
    symbols = sorted(
        symbol
        for symbol in set(effective_current) | set(effective_target) | broken
        if not resolved.is_excluded(symbol)
    )
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

    if decision_confidence is not None:
        confidence_score = getattr(decision_confidence, "score", None) if not isinstance(decision_confidence, Mapping) else decision_confidence.get("score", decision_confidence.get("confidence_score"))
        if confidence_score is not None:
            confidence_score = float(confidence_score)
            if not math.isfinite(confidence_score) or not 0 <= confidence_score <= 1:
                raise ValueError("decision_confidence score must be finite and in [0, 1]")
            factor = confidence_deployment_factor(confidence_score, resolved)
            if factor < 1.0:
                for item in candidates:
                    if item["action"] == "INCREASE":
                        if factor == 0.0:
                            item["action"] = "WAIT"
                            item["amount"] = 0.0
                            item["priority"] = "WATCH"
                            item["rationale"] = "new increase is blocked by LOW decision confidence"
                        else:
                            item["amount"] *= factor
                            item["rationale"] += f"; decision confidence caps deployment at {factor:.0%}"

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
        build_no_trade_attribution(
            current,
            target,
            actions,
            policy=resolved,
            regime=regime_name,
            decision_confidence=decision_confidence,
        ),
    )


def rebalance(
    current_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
    portfolio_value: float,
    *,
    new_cash_available: float = 0.0,
    thesis_broken: Iterable[str] | Mapping[str, bool] | None = None,
    policy: Policy | None = None,
    regime: str = "NORMAL",
    decision_confidence: Any | None = None,
) -> RebalanceResult:
    return recommend_rebalance(
        current_weights,
        target_weights,
        portfolio_value,
        new_cash_available=new_cash_available,
        thesis_broken=thesis_broken,
        policy=policy,
        regime=regime,
        decision_confidence=decision_confidence,
    )




__all__ = [
    "RebalanceAction",
    "RebalanceResult",
    "build_no_trade_attribution",
    "rebalance",
    "reconcile_trade_dollars",
    "recommend_rebalance",
]
