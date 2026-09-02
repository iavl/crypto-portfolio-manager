"""Builders and conditional routing for compact decision review packets."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from ..models.decision_packet import AssetDecisionSummary, DecisionReviewPacket
from ..models.evidence import AssetAssessment, FactorScore
from ..models.factor_packet import AssetFactorPacket, FactorJudgment, freeze_packet_value
from ..models.policy import Policy, resolve_policy
from ..model_routing import ModelRouting, validate_model_routing


_ACTIONS = {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}


def _routing(value: ModelRouting | Mapping[str, Any] | None) -> ModelRouting:
    if value is None:
        from ..model_routing import load_model_routing

        return load_model_routing()
    return value if isinstance(value, ModelRouting) else validate_model_routing(value)


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"weighted_score": value}
    raise ValueError("value must be a mapping or expose as_dict")


def _bool_flag(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, 0, "", "FALSE", "false"):
        return False
    if value in (1, "TRUE", "true"):
        return True
    raise ValueError(f"{field} must be boolean")


def _action_map(actions: Iterable[Any] | Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if actions is None:
        return {}
    if isinstance(actions, Mapping):
        if all(isinstance(value, (str, int, float, bool)) for value in actions.values()):
            return {}
        values = (
            dict(value, symbol=str(symbol).strip().upper())
            if isinstance(value, Mapping) and "symbol" not in value
            else value
            for symbol, value in actions.items()
        )
    else:
        values = actions
    result: dict[str, Mapping[str, Any]] = {}
    for value in values:
        item = _as_dict(value)
        symbol = str(item.get("symbol", "")).strip().upper()
        if symbol:
            result[symbol] = item
    return result


def _scores(assessment: Any) -> dict[str, float | None]:
    if isinstance(assessment, AssetAssessment):
        values = assessment.factor_scores
    elif isinstance(assessment, Mapping):
        values = assessment.get("factor_scores", {})
    else:
        values = {}
    result: dict[str, float | None] = {}
    for factor, value in values.items():
        if value is None:
            result[str(factor).strip().lower()] = None
        elif isinstance(value, FactorScore):
            result[str(factor).strip().lower()] = value.score
        elif isinstance(value, FactorJudgment):
            result[str(factor).strip().lower()] = value.score
        elif isinstance(value, Mapping):
            result[str(factor).strip().lower()] = float(value["score"]) if value.get("score") is not None else None
        else:
            result[str(factor).strip().lower()] = float(value)
    return result


def _asset_summary(
    symbol: str,
    assessment: Any,
    action: Mapping[str, Any] | None,
    *,
    current_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
    previous_assessment: Any = None,
    factor_packet: AssetFactorPacket | None = None,
) -> AssetDecisionSummary:
    assessment_dict = _as_dict(assessment) if assessment is not None else {}
    action_dict = dict(action or {})
    current_weights = {str(key).strip().upper(): value for key, value in current_weights.items()}
    target_weights = {str(key).strip().upper(): value for key, value in target_weights.items()}
    factor_scores = _scores(assessment)
    previous_score = None
    if previous_assessment is not None:
        previous_score = (
            previous_assessment.weighted_score
            if isinstance(previous_assessment, AssetAssessment)
            else _as_dict(previous_assessment).get("weighted_score")
        )
    key_facts: dict[str, Any] = {}
    historical: dict[str, Any] = {}
    evidence_ids: list[str] = []
    contrary_ids: list[str] = []
    raw_scores = assessment.factor_scores if isinstance(assessment, AssetAssessment) else assessment_dict.get("factor_scores", {})
    if isinstance(raw_scores, Mapping):
        for value in raw_scores.values():
            if isinstance(value, FactorScore):
                evidence_ids.extend(value.evidence_ids)
            elif isinstance(value, FactorJudgment):
                evidence_ids.extend(value.supporting_evidence_ids)
                contrary_ids.extend(value.contrary_evidence_ids)
            elif isinstance(value, Mapping):
                evidence_ids.extend(value.get("evidence_ids", ()))
    if factor_packet is not None:
        for name, fact in factor_packet.facts.items():
            if fact is None:
                continue
            if hasattr(fact, "current"):
                key_facts[name] = dict(fact.current)
                historical[name] = dict(fact.changes)
            elif isinstance(fact, Mapping):
                key_facts[name] = fact.get("current", {})
                historical[name] = fact.get("changes", {})
        evidence_ids.extend(factor_packet.evidence_ids)
    action_name = str(action_dict.get("action", "HOLD")).strip().upper()
    if action_name not in _ACTIONS:
        raise ValueError(f"unknown action {action_name}")
    amount = float(action_dict.get("amount_usd", action_dict.get("approved_amount_usd", 0.0)))
    if action_name in {"HOLD", "WAIT", "NO_TRADE"}:
        amount = 0.0
    return AssetDecisionSummary(
        symbol=symbol,
        factor_scores=factor_scores,
        score=(
            assessment.weighted_score
            if isinstance(assessment, AssetAssessment)
            else assessment_dict.get("weighted_score", assessment_dict.get("score"))
        ),
        confidence=str(assessment_dict.get("confidence", "LOW")),
        previous_score=previous_score,
        key_facts=key_facts,
        historical_changes=historical,
        supporting_evidence_ids=tuple(dict.fromkeys(evidence_ids)),
        contrary_evidence_ids=tuple(dict.fromkeys(contrary_ids)),
        current_weight=float(current_weights.get(symbol, action_dict.get("current_weight", 0.0))),
        target_weight=float(target_weights.get(symbol, action_dict.get("target_weight", 0.0))),
        action=action_name,
        approved_amount_usd=amount,
        thesis_broken=_bool_flag(assessment_dict.get("thesis_broken", False), "thesis_broken"),
        severe_event=_bool_flag(assessment_dict.get("severe_event", False), "severe_event"),
        portfolio_constraint=str(action_dict.get("rationale", "")),
    )


def _execution_summary(execution: Any) -> dict[str, Any]:
    if execution is None:
        return {}
    value = _as_dict(execution)
    summary = {
        key: value[key]
        for key in (
            "symbol", "action", "approved_amount_usd", "planned_amount_usd",
            "unallocated_amount_usd", "entry_mode", "technical_confidence",
            "current_price", "rationale", "ohlcv_hash", "volume_profile_hash",
        )
        if key in value
    }
    technical = value.get("technical_summary")
    if isinstance(technical, Mapping):
        summary["selected_zones"] = technical.get("selected_zones", [])
        summary["technical_facts"] = {
            key: technical[key]
            for key in (
                "ma20", "ma50", "ma100", "ma200", "atr14", "atr_percent",
                "trend_state", "data_confidence", "setup_quality",
                "volume_profile_confidence", "volume_profile_poc",
                "volume_profile_val", "volume_profile_vah",
                "volume_profile_hash",
            )
            if key in technical
        }
    return summary


def _risk_flags(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        value = value.values()
    result: list[str] = []
    for item in value:
        data = _as_dict(item)
        code = data.get("code") or data.get("message")
        if code and str(data.get("severity", "INFO")).upper() in {"ERROR", "WARNING"}:
            result.append(str(code))
    return tuple(dict.fromkeys(result))


def _factor_packet(value: Any) -> AssetFactorPacket | None:
    if value is None:
        return None
    if isinstance(value, AssetFactorPacket):
        return value
    if isinstance(value, Mapping):
        return AssetFactorPacket.from_mapping(value)
    raise ValueError("factor_packets must contain AssetFactorPacket objects or mappings")


def build_decision_review_packet(
    decision: Mapping[str, Any] | None = None,
    *,
    review_type: str | None = None,
    market_regime: str | None = None,
    portfolio_drawdown: float | None = None,
    current_weights: Mapping[str, float] | None = None,
    target_weights: Mapping[str, float] | None = None,
    previous_target_weights: Mapping[str, float] | None = None,
    assessments: Mapping[str, Any] | None = None,
    previous_assessments: Mapping[str, Any] | None = None,
    factor_packets: Mapping[str, AssetFactorPacket] | None = None,
    actions: Iterable[Any] | Mapping[str, Any] | None = None,
    execution: Any = None,
    risk_flags: Iterable[str] = (),
    critical_missing_data: Iterable[str] = (),
    major_conflicts: Iterable[str] = (),
    major_event_risk: bool = False,
    risk_budget_breach: bool = False,
    risk_escalation: bool = False,
    recommendation_reversal: bool = False,
) -> DecisionReviewPacket:
    source = _as_dict(decision) if decision is not None and not isinstance(decision, Mapping) else dict(decision or {})
    freeze_packet_value(source, path="decision")
    review = review_type or source.get("review_type", "SNAPSHOT_REVIEW")
    regime = market_regime or source.get("market_regime", "NORMAL")
    current = current_weights if current_weights is not None else source.get("current_weights", {})
    target = target_weights if target_weights is not None else source.get("target_weights", {})
    previous_target = previous_target_weights if previous_target_weights is not None else source.get("previous_target_weights", {})
    raw_assessments = assessments if assessments is not None else source.get("factor_scores", {})
    raw_previous = previous_assessments or source.get("previous_assessments", {})
    if not isinstance(raw_assessments, Mapping) or not isinstance(raw_previous, Mapping):
        raise ValueError("assessments and previous_assessments must be objects")
    raw_assessments = {str(key).strip().upper(): value for key, value in raw_assessments.items()}
    raw_previous = {str(key).strip().upper(): value for key, value in raw_previous.items()}
    action_by_symbol = _action_map(actions if actions is not None else source.get("actions"))
    symbols = list(dict.fromkeys([
        *(str(symbol).strip().upper() for symbol in current),
        *(str(symbol).strip().upper() for symbol in target),
        *(str(symbol).strip().upper() for symbol in raw_assessments),
        *action_by_symbol,
    ]))
    assets = tuple(
        _asset_summary(
            symbol,
            raw_assessments.get(symbol),
            action_by_symbol.get(symbol),
            current_weights=current,
            target_weights=target,
            previous_assessment=raw_previous.get(symbol),
            factor_packet=_factor_packet(next(
                (
                    value
                    for key, value in (factor_packets or {}).items()
                    if str(key).strip().upper() == symbol
                ),
                None,
            )),
        )
        for symbol in symbols
    )
    return DecisionReviewPacket(
        review_type=review,
        market_regime=regime,
        portfolio_drawdown=portfolio_drawdown if portfolio_drawdown is not None else source.get("portfolio_drawdown"),
        risk_flags=tuple(risk_flags) or tuple(source.get("risk_flags", ())) or _risk_flags(source.get("risk_checks")),
        current_weights=current,
        target_weights=target,
        previous_target_weights=previous_target,
        assets=assets,
        execution_summary=_execution_summary(execution if execution is not None else source.get("execution")),
        critical_missing_data=tuple(critical_missing_data) or tuple(source.get("critical_missing_data", ())),
        major_conflicts=tuple(major_conflicts) or tuple(source.get("major_conflicts", ())),
        major_event_risk=major_event_risk or bool(source.get("major_event_risk", False)),
        risk_budget_breach=risk_budget_breach or bool(source.get("risk_budget_breach", False)),
        risk_escalation=risk_escalation or bool(source.get("risk_escalation", False)),
        recommendation_reversal=recommendation_reversal or bool(source.get("recommendation_reversal", False)),
    )


def _target_change(packet: DecisionReviewPacket) -> float:
    if not packet.previous_target_weights:
        return 0.0
    symbols = set(packet.previous_target_weights) | set(packet.target_weights)
    return max(
        abs(packet.target_weights.get(symbol, 0.0) - packet.previous_target_weights.get(symbol, 0.0)) * 100
        for symbol in symbols
    )


def sol_final_review_reasons(
    packet: DecisionReviewPacket | Mapping[str, Any],
    *,
    policy: Policy | None = None,
    material_reduce_pp: float | None = None,
    material_target_change_pp: float | None = None,
    routing: ModelRouting | Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    packet = packet if isinstance(packet, DecisionReviewPacket) else DecisionReviewPacket.from_mapping(packet)
    resolved = policy or resolve_policy()
    if material_reduce_pp is None or material_target_change_pp is None:
        thresholds = _routing(routing).sol_thresholds
        material_reduce_pp = thresholds["material_reduce_pp"] if material_reduce_pp is None else material_reduce_pp
        material_target_change_pp = thresholds["material_target_change_pp"] if material_target_change_pp is None else material_target_change_pp
    for name, value in (("material_reduce_pp", material_reduce_pp), ("material_target_change_pp", material_target_change_pp)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
            raise ValueError(f"{name} must be finite and > 0")
    reasons: list[str] = []
    if any(item.action == "EXIT" for item in packet.assets):
        reasons.append("EXIT recommendation")
    if any(
        item.action == "REDUCE"
        and item.symbol in resolved.core_symbols
        and max(item.current_weight - item.target_weight, 0.0) * 100 >= material_reduce_pp
        for item in packet.assets
    ):
        reasons.append("material core-asset reduction")
    if packet.market_regime == "CAPITAL_PRESERVATION":
        reasons.append("capital-preservation regime")
    if packet.risk_budget_breach:
        reasons.append("risk-budget breach")
    if any(item.thesis_broken for item in packet.assets):
        reasons.append("thesis_broken")
    if any(item.severe_event for item in packet.assets):
        reasons.append("major event risk")
    if packet.major_event_risk:
        reasons.append("major event risk")
    event_words = ("EXPLOIT", "REGULATORY", "GOVERNANCE", "SOLVENCY", "SECURITY", "THESIS")
    if any(any(word in flag.upper() for word in event_words) for flag in packet.risk_flags):
        reasons.append("major event risk")
    if packet.critical_missing_data:
        reasons.append("critical data failure")
    if packet.major_conflicts:
        reasons.append("material source conflict")
    if packet.risk_escalation:
        reasons.append("risk escalation")
    if packet.recommendation_reversal:
        reasons.append("recommendation reversal")
    if _target_change(packet) > material_target_change_pp:
        reasons.append("material target-weight change")
    return tuple(dict.fromkeys(reasons))


def should_run_sol_final_review(
    packet: DecisionReviewPacket | Mapping[str, Any] | None = None,
    *,
    decision_review_packet: DecisionReviewPacket | Mapping[str, Any] | None = None,
    review_type: str | None = None,
    market_regime: str | None = None,
    actions: Iterable[Any] | None = None,
    thesis_broken: bool = False,
    major_event: bool = False,
    critical_failure: bool = False,
    source_conflict: bool = False,
    target_change_pp: float = 0.0,
    risk_budget_breach: bool = False,
    risk_escalation: bool = False,
    major_event_risk: bool = False,
    critical_data_failure: bool = False,
    target_weight_change: float | None = None,
    policy: Policy | None = None,
    material_target_change_pp: float | None = None,
    routing: ModelRouting | Mapping[str, Any] | None = None,
) -> bool:
    if packet is not None and decision_review_packet is not None:
        raise ValueError("provide only one of packet or decision_review_packet")
    packet = decision_review_packet if decision_review_packet is not None else packet
    if target_weight_change is not None:
        if target_change_pp != 0:
            raise ValueError("provide only one of target_change_pp or target_weight_change")
        target_change_pp = target_weight_change
    risk_escalation = risk_escalation or False
    major_event = major_event or major_event_risk
    critical_failure = critical_failure or critical_data_failure
    if isinstance(target_change_pp, bool) or not isinstance(target_change_pp, (int, float)) or not math.isfinite(float(target_change_pp)) or target_change_pp < 0:
        raise ValueError("target_change_pp must be finite and >= 0")
    if packet is None:
        packet = build_decision_review_packet(
            review_type=review_type or "SNAPSHOT_REVIEW",
            market_regime=market_regime or "NORMAL",
            target_weights={"USD": 1.0},
            actions=actions,
            major_event_risk=major_event,
            critical_missing_data=("critical",) if critical_failure else (),
            major_conflicts=("conflict",) if source_conflict else (),
            risk_budget_breach=risk_budget_breach,
            risk_escalation=risk_escalation,
        )
    elif not isinstance(packet, DecisionReviewPacket):
        packet = DecisionReviewPacket.from_mapping(packet)
    reasons = list(sol_final_review_reasons(
        packet,
        policy=policy,
        material_target_change_pp=material_target_change_pp,
        routing=routing,
    ))
    if thesis_broken:
        reasons.append("thesis_broken")
    if major_event:
        reasons.append("major event risk")
    if critical_failure:
        reasons.append("critical data failure")
    if source_conflict:
        reasons.append("material source conflict")
    if risk_budget_breach:
        reasons.append("risk-budget breach")
    if material_target_change_pp is None:
        material_target_change_pp = _routing(routing).sol_thresholds["material_target_change_pp"]
    if target_change_pp > material_target_change_pp:
        reasons.append("material target-weight change")
    if reasons:
        return True
    review = packet.review_type
    return not (
        review == "SNAPSHOT_REVIEW"
        and all(item.action not in {"EXIT", "REDUCE", "INCREASE"} for item in packet.assets)
        and not packet.major_event_risk
        and not packet.critical_missing_data
        and not packet.major_conflicts
        and not packet.risk_escalation
        and target_change_pp <= material_target_change_pp
    )


sol_review_required = should_run_sol_final_review


def validate_decision_review_packet(value: DecisionReviewPacket | Mapping[str, Any]) -> bool:
    DecisionReviewPacket.from_mapping(value) if not isinstance(value, DecisionReviewPacket) else value
    return True


__all__ = [
    "build_decision_review_packet",
    "should_run_sol_final_review",
    "sol_final_review_reasons",
    "sol_review_required",
    "validate_decision_review_packet",
]
