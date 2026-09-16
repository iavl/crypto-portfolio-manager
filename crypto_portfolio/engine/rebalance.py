"""Threshold-based rebalance and execution-plan validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..models.decision_packet import NoTradeAttribution
from ..models.confidence import DEFAULT_HIGH_MIN, DEFAULT_MEDIUM_MIN
from ..models.policy import Policy, resolve_policy
from .confidence import compose_deployment_factors, confidence_deployment_factor


_ACTIONS = {"INCREASE", "REDUCE", "HOLD", "EXIT", "WAIT", "NO_TRADE"}
_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_ACTION_REASONS = {
    "THESIS_BROKEN",
    "EVENT_RISK",
    "HARD_EXIT_SCORE",
    "REGIME_DERISK",
    "ALLOCATION_OVERWEIGHT",
    "ALLOCATION_UNDERWEIGHT",
    "CONFIDENCE_LIMIT",
    "RISK_BUDGET_BREACH",
}
# Reasons a caller may attach to a risk-reducing action that rebalance
# itself cannot observe (they mark hard exits that bypass staging).
_CALLER_HARD_REASONS = {"EVENT_RISK", "RISK_BUDGET_BREACH"}


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
            thresholds = resolved.allocation.get("relative_strength") or {
                "increase_min_score": 50.0, "hard_block_below_score": 30.0,
            }
            if relative is None:
                relative_state = "UNKNOWN"
            elif isinstance(relative, str):
                relative_state = relative.strip().upper()
            else:
                # Canonical 0-100 score unit read against the same two
                # thresholds the allocation engine uses.
                try:
                    score = float(relative)
                except (TypeError, ValueError) as exc:
                    raise ValueError("relative_strength_vs_btc must be numeric or a supported state") from exc
                if score < float(thresholds["hard_block_below_score"]):
                    relative_state = "MATERIALLY_WEAK"
                elif score < float(thresholds["increase_min_score"]):
                    relative_state = "UNDERPERFORM"
                else:
                    relative_state = "OUTPERFORM"
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
class ExecutionSizingAttribution:
    """Explicit single-pass sizing chain from strategic target to approved amount.

    Every layer that can shrink an executable trade records its own entry
    exactly once: staging closes at most ``staging_gap_close_fraction`` of the
    strategic gap; the composed deployment allowance scales the staged gap;
    funding competition can only cut the executable amount further, never
    enlarge it. ``effective_strategic_gap_close`` is the final
    ``|execution_target - current| / |strategic_gap|`` a report may quote, so
    a "closes 50%" claim can never silently mask a 35% outcome.
    """

    current_weight: float
    strategic_target_weight: float
    strategic_gap: float
    staging_enabled: bool
    staging_gap_close_fraction: float
    staging_max_step_pp: float | None
    staged_gap: float
    deployment_factors: Mapping[str, float]
    deployment_composition_mode: str
    effective_deployment_factor: float
    executable_gap: float
    executable_amount_usd: float
    funding_available_usd: float | None
    funding_shortfall_usd: float
    approved_amount_usd: float
    execution_target_weight: float
    effective_strategic_gap_close: float

    def __post_init__(self) -> None:
        fraction_fields = (
            "current_weight", "strategic_target_weight", "staging_gap_close_fraction",
            "effective_deployment_factor", "execution_target_weight",
        )
        for field in ("strategic_gap", "staged_gap", "executable_gap", *fraction_fields):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"sizing attribution {field} must be a number")
            if not math.isfinite(float(value)) or abs(float(value)) > 1:
                raise ValueError(f"sizing attribution {field} must be finite and within +/-1")
        # A hard exit can legitimately close (and overshoot) a tiny strategic
        # gap by selling the entire position, so the final close fraction is
        # bounded only by the position itself, not by 1.
        raw_close = getattr(self, "effective_strategic_gap_close")
        if isinstance(raw_close, bool) or not isinstance(raw_close, (int, float)):
            raise ValueError("sizing attribution effective_strategic_gap_close must be a number")
        close = float(raw_close)
        if not math.isfinite(close) or close < 0:
            raise ValueError("sizing attribution effective_strategic_gap_close must be finite and >= 0")
        object.__setattr__(self, "effective_strategic_gap_close", close)
        for field in (
            "strategic_gap", "staged_gap", "executable_gap", "effective_strategic_gap_close",
        ):
            object.__setattr__(self, field, float(getattr(self, field)))
        for field in fraction_fields:
            object.__setattr__(self, field, float(getattr(self, field)))
        for field in ("current_weight", "strategic_target_weight", "staging_gap_close_fraction",
                      "effective_deployment_factor", "execution_target_weight", "effective_strategic_gap_close"):
            if getattr(self, field) < 0:
                raise ValueError(f"sizing attribution {field} must be >= 0")
        for field in ("executable_amount_usd", "approved_amount_usd", "funding_shortfall_usd"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"sizing attribution {field} must be a number")
            value = float(value)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"sizing attribution {field} must be finite and >= 0")
            object.__setattr__(self, field, value)
        if self.funding_available_usd is not None:
            available = self.funding_available_usd
            if isinstance(available, bool) or not isinstance(available, (int, float)):
                raise ValueError("sizing attribution funding_available_usd must be a number or null")
            available = float(available)
            if not math.isfinite(available) or available < 0:
                raise ValueError("sizing attribution funding_available_usd must be finite and >= 0")
            object.__setattr__(self, "funding_available_usd", available)
        if self.staging_max_step_pp is not None:
            step = self.staging_max_step_pp
            if isinstance(step, bool) or not isinstance(step, (int, float)):
                raise ValueError("sizing attribution staging_max_step_pp must be a number or null")
            step = float(step)
            if not math.isfinite(step) or step <= 0:
                raise ValueError("sizing attribution staging_max_step_pp must be > 0")
            object.__setattr__(self, "staging_max_step_pp", step)
        if not isinstance(self.staging_enabled, bool):
            raise ValueError("sizing attribution staging_enabled must be boolean")
        if not isinstance(self.deployment_factors, Mapping):
            raise ValueError("sizing attribution deployment_factors must be an object")
        factors: dict[str, float] = {}
        for raw_name, raw_value in self.deployment_factors.items():
            name = str(raw_name).strip().lower()
            if not name:
                raise ValueError("sizing attribution deployment factor names must be non-empty")
            if name in factors:
                raise ValueError(f"sizing attribution deployment factor {name} is supplied twice")
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ValueError(f"sizing attribution deployment factor {name} must be a number")
            value = float(raw_value)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"sizing attribution deployment factor {name} must be in [0, 1]")
            factors[name] = value
        object.__setattr__(self, "deployment_factors", factors)
        mode = str(self.deployment_composition_mode).strip().lower()
        if mode not in {"minimum_cap", "multiplicative"}:
            raise ValueError("sizing attribution deployment_composition_mode is unsupported")
        object.__setattr__(self, "deployment_composition_mode", mode)

    def as_dict(self) -> dict[str, Any]:
        return {
            "current_weight": self.current_weight,
            "strategic_target_weight": self.strategic_target_weight,
            "strategic_gap": self.strategic_gap,
            "staging_enabled": self.staging_enabled,
            "staging_gap_close_fraction": self.staging_gap_close_fraction,
            "staging_max_step_pp": self.staging_max_step_pp,
            "staged_gap": self.staged_gap,
            "deployment_factors": dict(self.deployment_factors),
            "deployment_composition_mode": self.deployment_composition_mode,
            "effective_deployment_factor": self.effective_deployment_factor,
            "executable_gap": self.executable_gap,
            "executable_amount_usd": self.executable_amount_usd,
            "funding_available_usd": self.funding_available_usd,
            "funding_shortfall_usd": self.funding_shortfall_usd,
            "approved_amount_usd": self.approved_amount_usd,
            "execution_target_weight": self.execution_target_weight,
            "effective_strategic_gap_close": self.effective_strategic_gap_close,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExecutionSizingAttribution":
        if not isinstance(value, Mapping):
            raise ValueError("sizing attribution must be an object")
        allowed = {
            "current_weight", "strategic_target_weight", "strategic_gap", "staging_enabled",
            "staging_gap_close_fraction", "staging_max_step_pp", "staged_gap",
            "deployment_factors", "deployment_composition_mode", "effective_deployment_factor",
            "executable_gap", "executable_amount_usd", "funding_available_usd",
            "funding_shortfall_usd", "approved_amount_usd", "execution_target_weight",
            "effective_strategic_gap_close",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"sizing attribution contains unknown fields: {', '.join(sorted(unknown))}")
        missing = [field for field in allowed if field not in value]
        if missing:
            raise ValueError(f"sizing attribution is missing fields: {', '.join(missing)}")
        return cls(**{field: value[field] for field in allowed})


@dataclass(frozen=True)
class RebalanceAction:
    """One recommended per-asset action.

    ``strategic_target_weight`` is the long-run allocation-engine target;
    ``target_weight``/``execution_target_weight`` are where THIS review's
    action actually moves (staging can deliberately stop short of the
    strategic target); ``remaining_gap_after_action`` is what is left.
    """

    symbol: str
    action: str
    current_weight: float
    target_weight: float
    amount_usd: float
    priority: str
    rationale: str = ""
    strategic_target_weight: float | None = None
    execution_target_weight: float | None = None
    action_reason: str = ""
    staging_applied: bool = False
    remaining_gap_after_action: float | None = None
    sizing_attribution: ExecutionSizingAttribution | Mapping[str, Any] | None = None

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
        for field in (
            "strategic_target_weight", "execution_target_weight", "remaining_gap_after_action",
        ):
            raw_value = getattr(self, field)
            if raw_value is None:
                continue
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ValueError(f"{field} must be a number or null")
            value = float(raw_value)
            if not math.isfinite(value) or abs(value) > 1:
                raise ValueError(f"{field} must be finite and within +/-1")
            object.__setattr__(self, field, value)
        if not isinstance(self.action_reason, str):
            raise ValueError("action_reason must be a string")
        reason = self.action_reason.strip().upper()
        if reason and reason not in _ACTION_REASONS:
            raise ValueError(f"action_reason must be one of {sorted(_ACTION_REASONS)}")
        object.__setattr__(self, "action_reason", reason)
        if not isinstance(self.staging_applied, bool):
            raise ValueError("staging_applied must be boolean")
        executable = {"INCREASE", "REDUCE", "EXIT"}
        if action in executable and self.amount_usd <= 0:
            raise ValueError(f"{action} requires a positive executable amount")
        if action not in executable and self.amount_usd != 0:
            raise ValueError(f"{action} must have zero executable amount")
        if not isinstance(self.priority, str) or not self.priority.strip():
            raise ValueError("priority must be a non-empty string")
        if not isinstance(self.rationale, str):
            raise ValueError("rationale must be a string")
        if self.sizing_attribution is None:
            object.__setattr__(self, "sizing_attribution", None)
        else:
            attribution = self.sizing_attribution
            if not isinstance(attribution, ExecutionSizingAttribution):
                attribution = ExecutionSizingAttribution.from_mapping(attribution)
            object.__setattr__(self, "sizing_attribution", attribution)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "current_weight": self.current_weight,
            "target_weight": self.target_weight,
            "amount_usd": self.amount_usd,
            "priority": self.priority,
            "rationale": self.rationale,
            "strategic_target_weight": self.strategic_target_weight,
            "execution_target_weight": self.execution_target_weight,
            "action_reason": self.action_reason,
            "staging_applied": self.staging_applied,
            "remaining_gap_after_action": self.remaining_gap_after_action,
            "sizing_attribution": self.sizing_attribution.as_dict() if self.sizing_attribution else None,
        }


@dataclass(frozen=True)
class RebalanceResult:
    actions: tuple[RebalanceAction, ...]
    decision: str
    post_cash_total: float = 0.0
    reconciliation: Mapping[str, float | bool] | None = None
    no_trade_attribution: NoTradeAttribution | None = None
    post_action_projection: Mapping[str, Any] | None = None

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
            "post_action_projection": dict(self.post_action_projection or {}) or None,
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
    deployment_caps: Mapping[str, float] | None = None,
    hard_action_reasons: Mapping[str, str] | None = None,
    hard_exposure_caps: Mapping[str, float] | None = None,
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
    if decision_confidence is not None and deployment_caps:
        # Both parameters can express the same deployment cap on the same
        # dollar basis: per-symbol allowances from allocation already fold
        # the decision-confidence factor in (min of all applicable caps), so
        # supplying both would consume it twice. Express each constraint
        # once, composed by the caller when sources differ.
        raise ValueError(
            "decision_confidence and deployment_caps are mutually exclusive; "
            "compose different cap sources into one per-symbol factor instead"
        )
    normalized_deployment_caps: dict[str, float] = {}
    for raw_symbol, raw_factor in (deployment_caps or {}).items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError("deployment_caps contains an empty symbol")
        if symbol in normalized_deployment_caps:
            raise ValueError(f"deployment_caps contains duplicate symbol {symbol}")
        if isinstance(raw_factor, bool) or not isinstance(raw_factor, (int, float)):
            raise ValueError("deployment_caps values must be numbers")
        factor = float(raw_factor)
        if not math.isfinite(factor) or not 0 <= factor <= 1:
            raise ValueError("deployment_caps values must be finite and in [0, 1]")
        normalized_deployment_caps[symbol] = factor

    normalized_hard_reasons: dict[str, str] = {}
    for raw_symbol, raw_reason in (hard_action_reasons or {}).items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError("hard_action_reasons contains an empty symbol")
        if symbol in normalized_hard_reasons:
            raise ValueError(f"hard_action_reasons contains duplicate symbol {symbol}")
        reason = str(raw_reason).strip().upper()
        if reason not in _CALLER_HARD_REASONS:
            raise ValueError(
                "hard_action_reasons values must be one of "
                + ", ".join(sorted(_CALLER_HARD_REASONS))
            )
        normalized_hard_reasons[symbol] = reason

    # Hard exposure caps are a genuine risk ceiling distinct from strategic
    # target caps: a current weight above the cap forces a REDUCE regardless
    # of ordinary rebalance bands (and bypasses staging via the
    # RISK_BUDGET_BREACH bypass reason). Small strategic overshoots inside
    # the buffer stay governed by the normal bands.
    normalized_hard_caps: dict[str, float] = {}
    for raw_symbol, raw_cap in (hard_exposure_caps or {}).items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError("hard_exposure_caps contains an empty symbol")
        if symbol in normalized_hard_caps:
            raise ValueError(f"hard_exposure_caps contains duplicate symbol {symbol}")
        if isinstance(raw_cap, bool) or not isinstance(raw_cap, (int, float)):
            raise ValueError("hard_exposure_caps values must be numbers")
        cap = float(raw_cap)
        if not math.isfinite(cap) or not 0 < cap <= 1:
            raise ValueError("hard_exposure_caps values must be finite and in (0, 1]")
        normalized_hard_caps[symbol] = cap

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
    stable_symbols_set = frozenset(stable_symbols)
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
    for symbol, cap in normalized_hard_caps.items():
        if symbol in normalized_hard_reasons or symbol in broken:
            continue
        if effective_current.get(symbol, 0.0) / post_cash_total > cap + 1e-12:
            normalized_hard_reasons[symbol] = "RISK_BUDGET_BREACH"
    effective_target = {symbol: weight for symbol, weight in target.items() if symbol not in stable_symbols}
    effective_target.update(stable_target)
    stable_current_amount = sum(effective_current.get(symbol, 0.0) for symbol in stable_symbols)
    stable_current_weight = stable_current_amount / post_cash_total
    stable_pool_difference = target_stable_total - stable_current_weight
    stable_pool_deviation_pp = abs(stable_pool_difference) * 100.0
    stable_pool_relative_deviation = abs(stable_pool_difference) / max(
        target_stable_total, float(resolved.rebalance["relative_target_floor"])
    )
    stable_pool_below_hold = (
        stable_pool_deviation_pp < resolved.rebalance["hold_below_pp"]
        and stable_pool_relative_deviation < resolved.rebalance["relative_watch"]
    )
    stable_pool_high_priority = (
        stable_pool_deviation_pp > resolved.rebalance["high_priority_above_pp"]
        or stable_pool_relative_deviation > resolved.rebalance["relative_high"]
    )
    stable_pool_watch = (
        not stable_pool_below_hold
        and not stable_pool_high_priority
        and stable_pool_deviation_pp <= resolved.rebalance["watch_below_pp"]
        and stable_pool_relative_deviation <= float(resolved.rebalance["relative_high"])
    )
    symbols = sorted(
        symbol
        for symbol in set(effective_current) | set(effective_target) | broken
        if not resolved.is_excluded(symbol)
    )
    # The sizing pipeline is explicit and single-pass per asset:
    #   strategic gap -> staged gap -> composed deployment allowance
    #   -> executable amount -> funding-constrained approved amount.
    # Deployment allowances are maximum caps composed under the policy mode
    # (minimum_cap by default) and are consumed exactly once, here; the
    # pullback/entry planner downstream only decides when/where approved
    # dollars execute and can never rescale them.
    composition_mode = str(resolved.execution.get("deployment_factor_composition", "minimum_cap"))
    if composition_mode not in {"minimum_cap", "multiplicative"}:
        raise ValueError("execution.deployment_factor_composition is unsupported")
    decision_confidence_factor: float | None = None
    if decision_confidence is not None:
        confidence_score = getattr(decision_confidence, "score", None) if not isinstance(decision_confidence, Mapping) else decision_confidence.get("score", decision_confidence.get("confidence_score"))
        if confidence_score is not None:
            confidence_score = float(confidence_score)
            if not math.isfinite(confidence_score) or not 0 <= confidence_score <= 1:
                raise ValueError("decision_confidence score must be finite and in [0, 1]")
            decision_confidence_factor = confidence_deployment_factor(confidence_score, resolved)
    candidates: list[dict[str, Any]] = []
    staging_config = resolved.rebalance["staging"]
    bypass_reasons = frozenset(staging_config["bypass_reasons"])
    relative_floor = float(resolved.rebalance["relative_target_floor"])
    relative_watch = float(resolved.rebalance["relative_watch"])
    relative_high = float(resolved.rebalance["relative_high"])
    for symbol in symbols:
        current_amount = effective_current.get(symbol, 0.0)
        current_weight = current_amount / post_cash_total
        strategic_weight = effective_target.get(symbol, 0.0)
        difference = strategic_weight - current_weight
        deviation_pp = abs(difference) * 100.0
        # Relative deviation answers "how far off the plan is this position";
        # the floor keeps dust targets from manufacturing extreme priority
        # purely through a tiny denominator.
        relative_deviation = abs(difference) / max(strategic_weight, relative_floor)
        below_hold = deviation_pp < resolved.rebalance["hold_below_pp"] and relative_deviation < relative_watch
        high_priority = (
            deviation_pp > resolved.rebalance["high_priority_above_pp"]
            or relative_deviation > relative_high
        )
        watch_band = not below_hold and not high_priority and (
            deviation_pp <= resolved.rebalance["watch_below_pp"]
            and relative_deviation <= relative_high
        )
        caller_hard_reason = normalized_hard_reasons.get(symbol)
        if difference > 0:
            base_reason = "ALLOCATION_UNDERWEIGHT"
        else:
            base_reason = "REGIME_DERISK" if regime_name != "NORMAL" else "ALLOCATION_OVERWEIGHT"
        if symbol in stable_symbols_set:
            # Stable assets are one economic sleeve. Thresholds and staging
            # apply once to the sleeve, then the executable sale is allocated
            # across the currently held stable symbols.
            if stable_pool_difference < 0 and not stable_pool_below_hold:
                if stable_pool_watch:
                    action = "WAIT"
                    priority = "WATCH"
                    rationale = (
                        f"stable sleeve deviation {stable_pool_deviation_pp:.2f}pp "
                        "is in the watch band"
                    )
                else:
                    action = "REDUCE"
                    priority = "HIGH" if stable_pool_high_priority else "NORMAL"
                    rationale = (
                        f"stable sleeve overweight {stable_pool_deviation_pp:.2f}pp "
                        "exceeds the active rebalance threshold"
                    )
                action_reason = "ALLOCATION_OVERWEIGHT"
            else:
                action = "HOLD"
                action_reason = base_reason
                priority = "LOW"
                rationale = "stable sleeve is handled as one pooled funding leg"
        elif symbol in broken and current.get(symbol, 0.0) > 0:
            action = "EXIT"
            action_reason = "THESIS_BROKEN"
            priority = "HIGH"
            rationale = "investment thesis is marked broken"
        elif caller_hard_reason is not None and difference <= 0 and (current.get(symbol, 0.0) > 0 or strategic_weight == 0):
            action = "EXIT" if strategic_weight == 0 else "REDUCE"
            action_reason = caller_hard_reason
            priority = "HIGH"
            rationale = f"hard risk reason {caller_hard_reason} forces risk reduction"
            if symbol in normalized_hard_caps:
                rationale += (
                    f"; current weight exceeds the {normalized_hard_caps[symbol]:.2%} hard exposure cap"
                )
        elif strategic_weight == 0 and difference < 0:
            action = "EXIT"
            action_reason = "HARD_EXIT_SCORE"
            priority = "HIGH" if high_priority else "NORMAL"
            rationale = "strategic target is zero; the position exits"
        elif below_hold:
            action = "HOLD"
            action_reason = base_reason
            priority = "LOW"
            rationale = (
                f"deviation {deviation_pp:.2f}pp (relative {relative_deviation:.0%}) "
                "is below the hold threshold"
            )
        elif watch_band:
            action = "WAIT"
            action_reason = base_reason
            priority = "WATCH"
            rationale = (
                f"deviation {deviation_pp:.2f}pp (relative {relative_deviation:.0%}) "
                "is in the watch band"
            )
        elif difference > 0:
            action = "INCREASE"
            action_reason = base_reason
            priority = "HIGH" if high_priority else "NORMAL"
            rationale = "underweight exceeds the active rebalance threshold"
        else:
            action = "REDUCE"
            action_reason = base_reason
            priority = "HIGH" if high_priority else "NORMAL"
            rationale = "overweight exceeds the active rebalance threshold"
        executable = action in {"INCREASE", "REDUCE", "EXIT"}
        staging_active = executable and staging_config["enabled"] and action_reason not in bypass_reasons
        stable_share = (
            current_amount / stable_current_amount
            if symbol in stable_symbols_set and stable_current_amount > 0
            else 0.0
        )
        gap_for_staging = (
            stable_pool_difference * stable_share
            if symbol in stable_symbols_set
            else difference
        )
        if not executable:
            staged_gap = 0.0
        elif staging_active:
            # Ordinary allocation corrections move at most
            # max_gap_close_fraction of the remaining gap, capped by an
            # absolute per-review step, so one review never forces a healthy
            # position all the way to its long-run target.
            max_step = float(staging_config["max_step_pp"]) / 100.0
            if symbol in stable_symbols_set:
                # The cap belongs to the pooled sleeve, then follows the
                # same stable-symbol composition as the sale.
                max_step *= stable_share
            step = min(
                abs(gap_for_staging) * float(staging_config["max_gap_close_fraction"]),
                max_step,
            )
            staged_gap = (1.0 if gap_for_staging > 0 else -1.0) * step
        else:
            staged_gap = gap_for_staging
        staging_applied = executable and staging_active and abs(staged_gap) < abs(gap_for_staging) - 1e-12
        # Deployment allowance: a single named factor from the one configured
        # source (decision_confidence and deployment_caps are mutually
        # exclusive), composed under the policy mode. Only new increases are
        # scaled; hard risk-reducing moves are never delayed by confidence.
        named_factors: dict[str, float] = {}
        if action == "INCREASE":
            if decision_confidence_factor is not None:
                named_factors["decision_confidence"] = decision_confidence_factor
            elif symbol in normalized_deployment_caps:
                named_factors["deployment_allowance"] = normalized_deployment_caps[symbol]
        effective_deployment_factor = (
            compose_deployment_factors(named_factors, policy=resolved)
            if named_factors else 1.0
        )
        if action == "INCREASE" and effective_deployment_factor == 0.0:
            action = "WAIT"
            executable = False
            priority = "WATCH"
            action_reason = "CONFIDENCE_LIMIT"
            rationale = (
                "new increase is blocked by LOW decision confidence"
                if "decision_confidence" in named_factors
                else "new increase is blocked by the deployment allowance"
            )
            executable_gap = 0.0
        elif executable:
            executable_gap = (
                staged_gap * effective_deployment_factor if action == "INCREASE" else staged_gap
            )
        else:
            executable_gap = 0.0
        if staging_applied:
            rationale += (
                f"; staged execution closes {abs(staged_gap) / abs(difference):.0%} "
                f"of the {abs(difference) * 100:.2f}pp gap this review"
            )
        if action == "INCREASE" and 0.0 < effective_deployment_factor < 1.0:
            if "decision_confidence" in named_factors:
                rationale += f"; decision confidence caps deployment at {effective_deployment_factor:.0%}"
            else:
                rationale += f"; deployment allowance caps immediate increase at {effective_deployment_factor:.0%}"
        amount = abs(executable_gap) * post_cash_total if executable else 0.0
        if symbol in broken and current.get(symbol, 0.0) > 0:
            amount = current.get(symbol, 0.0) * portfolio_value
            executable_gap = -current_weight
        candidates.append({
            "symbol": symbol,
            "action": action,
            "current_weight": current_weight,
            "target_weight": strategic_weight,
            "execution_target": current_weight if not executable else None,
            "amount": max(0.0, amount),
            "priority": priority,
            "rationale": rationale,
            "action_reason": action_reason,
            "staging_applied": staging_applied,
            "staging_active": staging_active,
            "staged_gap": staged_gap,
            "named_factors": named_factors,
            "effective_deployment_factor": effective_deployment_factor,
            "executable_gap": executable_gap,
            "executable_amount": max(0.0, abs(executable_gap) * post_cash_total),
            "funding_available": None,
            "funding_shortfall": 0.0,
            "remaining_gap": strategic_weight - current_weight,
        })

    # Caller-supplied hard reasons must mark risk-reducing actions only;
    # an INCREASE can never be a hard exit.
    for symbol, reason in normalized_hard_reasons.items():
        item = next((entry for entry in candidates if entry["symbol"] == symbol), None)
        if item is not None and item["action"] not in {"REDUCE", "EXIT"}:
            raise ValueError(
                f"hard_action_reasons.{symbol} ({reason}) requires a REDUCE/EXIT outcome, "
                f"not {item['action']}"
            )

    # Funding competition: executable sales (plus any new cash already folded
    # into effective_current) are the only dollars buys may draw on. When the
    # pool cannot cover an executable amount the shortfall is recorded and
    # explained, never silently reported as a fully staged move.
    available_funding = sum(
        item["amount"] for item in candidates if item["action"] in {"REDUCE", "EXIT"}
    )
    for item in sorted(
        (item for item in candidates if item["action"] == "INCREASE"),
        key=lambda value: (-value["executable_amount"], value["symbol"]),
    ):
        item["funding_available"] = available_funding
        approved = min(item["executable_amount"], available_funding)
        item["funding_shortfall"] = item["executable_amount"] - approved
        item["amount"] = approved
        available_funding -= approved
        if approved <= 1e-9:
            item["action"] = "WAIT"
            item["executable_gap"] = 0.0
            item["priority"] = "WATCH"
            item["rationale"] = "underweight is not funded by available cash or executable sales"
        elif item["funding_shortfall"] > 1e-9:
            item["rationale"] += (
                f"; executable sales and cash fund {approved:,.2f} USD of the "
                f"{item['executable_amount']:,.2f} USD executable amount this review"
            )

    # Normalize execution fields against the final (possibly capped or
    # unfunded) amounts so target_weight/remaining_gap always describe what
    # this review's action actually moves toward, and attach the explicit
    # sizing attribution each action was produced by.
    for item in candidates:
        if item["action"] in {"INCREASE", "REDUCE", "EXIT"}:
            # Direction follows the executable gap, not target-vs-current: a
            # broken-thesis EXIT sells the whole position even when the
            # strategic target still sits above the current weight.
            direction = 1.0 if item["executable_gap"] > 0 else -1.0
            item["execution_target"] = item["current_weight"] + direction * (
                item["amount"] / post_cash_total
            )
        else:
            item["execution_target"] = item["current_weight"]
            item["staging_applied"] = False
        item["execution_target"] = min(1.0, max(0.0, item["execution_target"]))
        item["remaining_gap"] = item["target_weight"] - item["execution_target"]
        strategic_gap = item["target_weight"] - item["current_weight"]
        item["sizing_attribution"] = ExecutionSizingAttribution(
            current_weight=item["current_weight"],
            strategic_target_weight=item["target_weight"],
            strategic_gap=strategic_gap,
            staging_enabled=bool(item["staging_active"]),
            staging_gap_close_fraction=(
                abs(item["staged_gap"]) / abs(strategic_gap)
                if abs(strategic_gap) > 1e-12 else 0.0
            ),
            staging_max_step_pp=float(staging_config["max_step_pp"]),
            staged_gap=item["staged_gap"],
            deployment_factors=item["named_factors"],
            deployment_composition_mode=composition_mode,
            effective_deployment_factor=item["effective_deployment_factor"],
            executable_gap=item["executable_gap"],
            executable_amount_usd=item["executable_amount"],
            funding_available_usd=item["funding_available"],
            funding_shortfall_usd=item["funding_shortfall"],
            approved_amount_usd=item["amount"],
            execution_target_weight=item["execution_target"],
            effective_strategic_gap_close=(
                abs(item["execution_target"] - item["current_weight"]) / abs(strategic_gap)
                if abs(strategic_gap) > 1e-12 else 0.0
            ),
        )

    actions = [
        RebalanceAction(
            symbol=item["symbol"],
            action=item["action"],
            current_weight=item["current_weight"],
            target_weight=item["execution_target"],
            amount_usd=item["amount"] if item["action"] in {"INCREASE", "REDUCE", "EXIT"} else 0.0,
            priority=item["priority"],
            rationale=item["rationale"],
            strategic_target_weight=item["target_weight"],
            execution_target_weight=item["execution_target"],
            action_reason=item["action_reason"],
            staging_applied=item["staging_applied"],
            remaining_gap_after_action=item["remaining_gap"],
            sizing_attribution=item["sizing_attribution"],
        )
        for item in candidates
    ]
    active = {"INCREASE", "REDUCE", "EXIT"}
    decision = "REBALANCE" if any(action.action in active for action in actions) else "NO_TRADE"
    # Project the portfolio that the recommended actions would actually leave
    # behind: hard-constraint compliance is judged on this projection, not on
    # the strategic target. Shortfalls the actions cannot repair stay visible
    # instead of being swallowed by the turnover thresholds.
    projected_dollars: dict[str, float] = {}
    for action in actions:
        if action.symbol in stable_symbols_set:
            # Stable legs are settlement plumbing: sells exist to fund buys,
            # and any buy a cap shrank leaves its dollars undeployed. The
            # stable sleeve absorbs the remainder so dollars are conserved
            # instead of silently destroyed by capped increases.
            continue
        delta = action.amount_usd if action.action == "INCREASE" else -action.amount_usd
        if action.action in {"INCREASE", "REDUCE", "EXIT"}:
            projected_dollars[action.symbol] = (
                effective_current.get(action.symbol, 0.0) + delta
            )
        else:
            projected_dollars[action.symbol] = effective_current.get(action.symbol, 0.0)
    for symbol, dollars in effective_current.items():
        projected_dollars.setdefault(symbol, dollars)
    risky_total = sum(
        dollars for symbol, dollars in projected_dollars.items() if symbol not in stable_symbols_set
    )
    stable_total = max(0.0, post_cash_total - risky_total)
    current_stable = {
        symbol: dollars
        for symbol, dollars in effective_current.items()
        if symbol in stable_symbols_set and dollars > 0
    }
    current_stable_total = sum(current_stable.values())
    if current_stable_total > 0:
        for symbol, dollars in current_stable.items():
            projected_dollars[symbol] = stable_total * dollars / current_stable_total
    else:
        projected_dollars[stable_symbol] = projected_dollars.get(stable_symbol, 0.0) + stable_total
    projected_weights = {
        symbol: dollars / post_cash_total for symbol, dollars in projected_dollars.items()
    }
    projected_stable = sum(projected_weights.get(symbol, 0.0) for symbol in stable_symbols)
    unresolved: list[str] = []
    if projected_stable + 1e-9 < required_stable:
        unresolved.append(
            f"STABLECOIN_FLOOR_UNRESOLVED: projected stable sleeve {projected_stable:.2%} "
            f"is below required {required_stable:.2%}"
        )
    limits = resolved.regime(regime_name)
    for symbol, weight in projected_weights.items():
        if symbol in resolved.stable_symbols or weight <= 0:
            continue
        if weight > limits.single_asset_max + 1e-9:
            unresolved.append(
                f"SINGLE_ASSET_CAP_UNRESOLVED: projected {symbol} {weight:.2%} "
                f"exceeds {limits.single_asset_max:.2%}"
            )
    projected_satellite = sum(
        projected_weights.get(symbol, 0.0) for symbol in resolved.satellite_symbols
    )
    if projected_satellite > limits.satellite_max + 1e-9:
        unresolved.append(
            f"SATELLITE_CAP_UNRESOLVED: projected satellite sleeve {projected_satellite:.2%} "
            f"exceeds {limits.satellite_max:.2%}"
        )
    projection: dict[str, Any] = {
        "basis": "recommended actions applied to current dollars plus new cash",
        "post_action_total_usd": post_cash_total,
        "projected_weights": {symbol: max(0.0, weight) for symbol, weight in sorted(projected_weights.items())},
        "projected_stable_weight": projected_stable,
        "required_stable_weight": required_stable,
        "unresolved_constraints": tuple(unresolved),
    }
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
            risk_flags=unresolved,
        ),
        projection,
    )


def format_execution_sizing_chain(
    attribution: ExecutionSizingAttribution | Mapping[str, Any],
) -> str:
    """Render one trade's explicit sizing chain for review reports.

    The plan-required layout: every layer between the strategic target and
    the approved amount is shown with its own numbers, so a 50% staging rule
    can never be quoted alone when deployment caps or funding competition
    reduced the effective close.
    """
    value = (
        attribution if isinstance(attribution, ExecutionSizingAttribution)
        else ExecutionSizingAttribution.from_mapping(attribution)
    )
    def _pp(gap: float) -> str:
        return f"{gap * 100:+.2f}pp"

    def _usd(amount: float) -> str:
        return f"${amount:,.2f}"

    lines = [
        f"Current weight                  {value.current_weight:.2%}",
        f"Strategic target                {value.strategic_target_weight:.2%}",
        f"Strategic gap                   {_pp(value.strategic_gap)}",
        "",
        "Staging:",
        (
            f"  configured gap-close          {value.staging_gap_close_fraction:.0%}"
            if value.staging_enabled
            else "  disabled (hard exit or staging bypass)"
        ),
        f"  post-staging gap              {_pp(value.staged_gap)}",
        "",
        "Deployment constraints:",
    ]
    if value.deployment_factors:
        for name, factor in sorted(value.deployment_factors.items()):
            lines.append(f"  {name:<30}{factor:.2f}")
    else:
        lines.append("  none                           1.00")
    lines.extend([
        f"  composition                   {value.deployment_composition_mode}",
        f"  effective factor              {value.effective_deployment_factor:.2f}",
        "",
        "Executable:",
        f"  executable gap                {_pp(value.executable_gap)}",
        f"  execution target              {value.execution_target_weight:.2%}",
        f"  executable amount             {_usd(value.executable_amount_usd)}",
    ])
    if value.funding_available_usd is not None:
        lines.append(f"  funding available             {_usd(value.funding_available_usd)}")
        lines.append(f"  funding shortfall             {_usd(value.funding_shortfall_usd)}")
    lines.extend([
        f"  approved amount               {_usd(value.approved_amount_usd)}",
        f"  effective strategic-gap close {value.effective_strategic_gap_close:.1%}",
    ])
    return "\n".join(lines)


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
    deployment_caps: Mapping[str, float] | None = None,
    hard_action_reasons: Mapping[str, str] | None = None,
    hard_exposure_caps: Mapping[str, float] | None = None,
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
        deployment_caps=deployment_caps,
        hard_action_reasons=hard_action_reasons,
        hard_exposure_caps=hard_exposure_caps,
    )




__all__ = [
    "ExecutionSizingAttribution",
    "RebalanceAction",
    "RebalanceResult",
    "build_no_trade_attribution",
    "format_execution_sizing_chain",
    "rebalance",
    "reconcile_trade_dollars",
    "recommend_rebalance",
]
