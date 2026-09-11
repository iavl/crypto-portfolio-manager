"""Validated decision-record model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from .evidence import AssetAssessment, Evidence, FactorScore, ManualAssetContext
from .confidence import ConfidenceResult, DecisionConfidence
from .decision_packet import NoTradeAttribution
from .execution import ExecutionPlan
from .factor_packet import freeze_packet_value, thaw_packet_value
from .policy import policy_hash
from .time import normalize_timestamp


_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_STATUSES = {"PENDING", "CONFIRMED", "NOT_EXECUTED"}
_REVIEW_TYPES = {"SNAPSHOT_REVIEW", "FULL_REVIEW", "EVENT_REVIEW"}


def _weights(value: Mapping[str, Any], field: str, *, require_sum: bool = True) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result: dict[str, float] = {}
    for symbol, raw_weight in value.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"{field} contains an invalid symbol")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError(f"{field}.{symbol} must be a number")
        symbol = symbol.strip().upper()
        if symbol in result:
            raise ValueError(f"{field} contains duplicate symbol {symbol}")
        weight = float(raw_weight)
        if not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError(f"{field}.{symbol} must be finite and in [0, 1]")
        result[symbol] = weight
    if require_sum and result and not math.isclose(sum(result.values()), 1.0, abs_tol=1e-9):
        raise ValueError(f"{field} weights must sum to 1")
    return result


@dataclass(frozen=True)
class Decision:
    timestamp: str
    market_regime: str
    current_weights: Mapping[str, float]
    target_weights: Mapping[str, float]
    actions: tuple[Any, ...] = ()
    risk_checks: tuple[Any, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    factor_scores: Mapping[str, Any] = None
    status: str = "PENDING"
    constraints_applied: tuple[str, ...] = ()
    config: Mapping[str, Any] | None = None
    policy_hash: str | None = None
    resolved_policy: Mapping[str, Any] | None = None
    review_type: str = "SNAPSHOT_REVIEW"
    decision_id: str | None = None
    based_on_snapshot_id: str | None = None
    execution_plans: Mapping[str, ExecutionPlan | Mapping[str, Any]] | None = None
    market_overlays: Mapping[str, Any] | None = None
    regime_confidence: ConfidenceResult | Mapping[str, Any] | None = None
    decision_confidence: DecisionConfidence | Mapping[str, Any] | None = None
    nav_performance: Mapping[str, Any] | None = None
    benchmark_performance: Mapping[str, Any] | None = None
    event_scan_summary: Mapping[str, Any] | None = None
    manual_asset_contexts: tuple[ManualAssetContext, ...] = ()
    no_trade_attribution: NoTradeAttribution | Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp))
        if not isinstance(self.market_regime, str):
            raise ValueError("market_regime must be a string")
        object.__setattr__(self, "market_regime", self.market_regime.upper())
        if self.market_regime not in _REGIMES:
            raise ValueError(f"market_regime must be one of {sorted(_REGIMES)}")
        object.__setattr__(self, "current_weights", _weights(self.current_weights, "current_weights"))
        object.__setattr__(self, "target_weights", _weights(self.target_weights, "target_weights"))
        if not self.current_weights or not self.target_weights:
            raise ValueError("current_weights and target_weights must be non-empty")
        if not isinstance(self.factor_scores, Mapping) and self.factor_scores is not None:
            raise ValueError("factor_scores must be an object or null")
        from ..engine.rebalance import RebalanceAction

        parsed_actions = []
        for item in self.actions:
            if isinstance(item, RebalanceAction):
                parsed_actions.append(item)
                continue
            if not isinstance(item, Mapping):
                raise ValueError("actions must contain RebalanceAction objects or mappings")
            try:
                parsed_actions.append(RebalanceAction(**item))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid rebalance action: {exc}") from exc
        object.__setattr__(self, "actions", tuple(parsed_actions))
        object.__setattr__(self, "risk_checks", tuple(self.risk_checks))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        evidence_ids = []
        for item in self.evidence:
            if not isinstance(item, Evidence):
                raise ValueError("evidence must contain Evidence objects")
            evidence_ids.append(item.id)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("decision evidence IDs must be unique")
        parsed_factor_scores: dict[str, AssetAssessment] = {}
        for symbol, value in (self.factor_scores or {}).items():
            normalized_symbol = str(symbol).strip().upper()
            assessment = AssetAssessment.from_mapping(normalized_symbol, value)
            if assessment.symbol != normalized_symbol:
                raise ValueError(f"factor score symbol {normalized_symbol} does not match assessment")
            parsed_factor_scores[normalized_symbol] = assessment
        object.__setattr__(self, "factor_scores", parsed_factor_scores)
        object.__setattr__(self, "constraints_applied", tuple(self.constraints_applied))
        if self.market_overlays is not None:
            value = self.market_overlays.as_dict() if hasattr(self.market_overlays, "as_dict") else self.market_overlays
            if not isinstance(value, Mapping):
                raise ValueError("market_overlays must be an object or null")
            object.__setattr__(self, "market_overlays", freeze_packet_value(value, path="market_overlays"))
        if self.regime_confidence is not None:
            value = self.regime_confidence if isinstance(self.regime_confidence, ConfidenceResult) else ConfidenceResult.from_mapping(self.regime_confidence)
            object.__setattr__(self, "regime_confidence", value)
        if self.decision_confidence is not None:
            value = self.decision_confidence if isinstance(self.decision_confidence, DecisionConfidence) else DecisionConfidence.from_mapping(self.decision_confidence)
            object.__setattr__(self, "decision_confidence", value)
        for field_name in ("nav_performance", "benchmark_performance", "event_scan_summary"):
            value = getattr(self, field_name)
            if value is not None:
                if not isinstance(value, Mapping):
                    raise ValueError(f"{field_name} must be an object or null")
                object.__setattr__(self, field_name, freeze_packet_value(value, path=field_name))
        contexts = self.manual_asset_contexts
        if isinstance(contexts, ManualAssetContext) or isinstance(contexts, Mapping):
            contexts = (contexts,)
        if isinstance(contexts, (str, bytes)) or not isinstance(contexts, (list, tuple)):
            raise ValueError("manual_asset_contexts must be a sequence of objects")
        parsed_contexts = tuple(
            item if isinstance(item, ManualAssetContext) else ManualAssetContext.from_mapping(item)
            for item in contexts
        )
        if len({(item.asset, item.category, item.as_of) for item in parsed_contexts}) != len(parsed_contexts):
            raise ValueError("manual_asset_contexts must not contain duplicate contexts")
        object.__setattr__(self, "manual_asset_contexts", parsed_contexts)
        if self.no_trade_attribution is not None:
            value = (
                self.no_trade_attribution
                if isinstance(self.no_trade_attribution, NoTradeAttribution)
                else NoTradeAttribution.from_mapping(self.no_trade_attribution)
            )
            object.__setattr__(self, "no_trade_attribution", value)
        if self.config is not None:
            if not isinstance(self.config, Mapping):
                raise ValueError("config must be an object or null")
            object.__setattr__(self, "config", dict(self.config))
        if not isinstance(self.status, str):
            raise ValueError("status must be a string")
        status = self.status.upper()
        if status not in _STATUSES:
            raise ValueError(f"status must be one of {sorted(_STATUSES)}")
        object.__setattr__(self, "status", status)
        if not isinstance(self.review_type, str):
            raise ValueError("review_type must be a string")
        review_type = self.review_type.upper()
        if review_type not in _REVIEW_TYPES:
            raise ValueError(f"review_type must be one of {sorted(_REVIEW_TYPES)}")
        object.__setattr__(self, "review_type", review_type)
        for field in ("decision_id", "based_on_snapshot_id"):
            value = getattr(self, field)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{field} must be a non-empty string or null")
                object.__setattr__(self, field, value.strip())
        if self.execution_plans is not None:
            if not isinstance(self.execution_plans, Mapping):
                raise ValueError("execution_plans must be an object or null")
            parsed_plans: dict[str, ExecutionPlan] = {}
            for symbol, value in self.execution_plans.items():
                normalized_symbol = str(symbol).strip().upper()
                if not normalized_symbol:
                    raise ValueError("execution_plans contains an empty symbol")
                plan = value if isinstance(value, ExecutionPlan) else ExecutionPlan.from_mapping(value)
                if plan.symbol != normalized_symbol:
                    raise ValueError(f"execution plan symbol {normalized_symbol} does not match mapping key")
                parsed_plans[normalized_symbol] = plan
            object.__setattr__(self, "execution_plans", parsed_plans)
            increase_actions = [
                action for action in self.actions if action.action == "INCREASE"
            ]
            for plan in parsed_plans.values():
                if plan.action != "INCREASE" and not (
                    plan.action == "WAIT" and plan.approved_amount_usd > 0
                ):
                    continue
                matches = [
                    action
                    for action in increase_actions
                    if action.symbol == plan.symbol
                    and math.isclose(
                        action.amount_usd,
                        plan.approved_amount_usd,
                        rel_tol=1e-9,
                        abs_tol=1e-7,
                    )
                ]
                if len(matches) != 1:
                    raise ValueError(
                        "each INCREASE execution plan must match exactly one approved INCREASE action"
                    )
        if self.policy_hash is not None:
            if not isinstance(self.policy_hash, str) or len(self.policy_hash) != 64:
                raise ValueError("policy_hash must be a SHA-256 hex digest")
            try:
                int(self.policy_hash, 16)
            except ValueError as exc:
                raise ValueError("policy_hash must be a SHA-256 hex digest") from exc
            object.__setattr__(self, "policy_hash", self.policy_hash.lower())
        if self.resolved_policy is not None:
            if not isinstance(self.resolved_policy, Mapping):
                raise ValueError("resolved_policy must be an object or null")
            object.__setattr__(self, "resolved_policy", dict(self.resolved_policy))
            if self.policy_hash is not None and policy_hash(self.resolved_policy) != self.policy_hash:
                raise ValueError("policy_hash does not match resolved_policy")

        evidence_by_id = {
            item.id: item for item in self.evidence if isinstance(item, Evidence)
        }
        for symbol, assessment in self.factor_scores.items():
            validate_factor_evidence_binding(
                assessment.factor_scores,
                evidence_by_id,
                symbol=symbol,
            )
            if assessment.event_risk is not None:
                for evidence_id in assessment.event_risk.evidence_ids:
                    evidence = evidence_by_id.get(evidence_id)
                    if evidence is None:
                        raise ValueError(f"event_risk references missing evidence {evidence_id}")
                    if evidence.asset != symbol or evidence.factor != "event_risk":
                        raise ValueError(f"event_risk references incompatible evidence {evidence_id}")
        for plan in (self.execution_plans or {}).values():
            if plan.technical_summary is None:
                continue
            linked = [
                evidence
                for evidence in self.evidence
                if isinstance(evidence, Evidence)
                and evidence.asset == plan.symbol
                and evidence.factor == "execution_technical"
                and isinstance(evidence.value, Mapping)
                and evidence.value.get("ohlcv_hash") == plan.ohlcv_hash
                and (
                    not plan.volume_profile_hash
                    or evidence.value.get("volume_profile_hash") == plan.volume_profile_hash
                )
                and evidence.value.get("technical_summary") == plan.technical_summary
            ]
            if len(linked) != 1:
                raise ValueError(
                    "each execution plan with a technical summary must have one matching execution_technical evidence record"
                )
    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Decision":
        if not isinstance(value, Mapping):
            raise ValueError("decision must be an object")
        data = dict(value)
        allowed = {
            "timestamp", "market_regime", "current_weights", "target_weights", "actions",
            "risk_checks", "evidence", "evidence_ids", "factor_scores", "status", "constraints_applied",
            "config", "policy_hash", "resolved_policy", "review_type", "decision_id",
            "based_on_snapshot_id", "execution_plans", "market_overlays",
            "regime_confidence", "decision_confidence", "nav_performance",
            "benchmark_performance", "event_scan_summary",
            "manual_asset_contexts",
            "no_trade_attribution",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError("decision contains unknown fields: " + ", ".join(sorted(unknown)))
        required = ("timestamp", "market_regime", "current_weights", "target_weights")
        missing = [field for field in required if field not in data]
        if missing:
            raise ValueError(f"decision is missing fields: {', '.join(missing)}")

        def _evidence_item(item: Any) -> Any:
            if not isinstance(item, Mapping):
                raise ValueError("decision evidence must be an object")
            return Evidence(**item)

        evidence = tuple(_evidence_item(item) for item in data.get("evidence", ()))
        data["evidence"] = evidence
        data["factor_scores"] = data.get("factor_scores") or {}
        return cls(
            timestamp=data["timestamp"],
            market_regime=data["market_regime"],
            current_weights=data["current_weights"],
            target_weights=data["target_weights"],
            actions=tuple(data.get("actions", ())),
            risk_checks=tuple(data.get("risk_checks", ())),
            evidence=evidence,
            factor_scores=data["factor_scores"],
            status=data.get("status", "PENDING"),
            constraints_applied=tuple(data.get("constraints_applied", ())),
            config=data.get("config"),
            policy_hash=data.get("policy_hash"),
            resolved_policy=data.get("resolved_policy"),
            review_type=data.get("review_type", "SNAPSHOT_REVIEW"),
            decision_id=data.get("decision_id"),
            based_on_snapshot_id=data.get("based_on_snapshot_id"),
            execution_plans=data.get("execution_plans"),
            market_overlays=data.get("market_overlays"),
            regime_confidence=data.get("regime_confidence"),
            decision_confidence=data.get("decision_confidence"),
            nav_performance=data.get("nav_performance"),
            benchmark_performance=data.get("benchmark_performance"),
            event_scan_summary=data.get("event_scan_summary"),
            manual_asset_contexts=tuple(data.get("manual_asset_contexts", ())),
            no_trade_attribution=data.get("no_trade_attribution"),
        )

    def as_dict(self) -> dict[str, Any]:
        evidence = [item.as_dict() for item in self.evidence]
        result = {
            "timestamp": self.timestamp,
            "market_regime": self.market_regime,
            "current_weights": dict(self.current_weights),
            "target_weights": dict(self.target_weights),
            "actions": [item.as_dict() if hasattr(item, "as_dict") else item for item in self.actions],
            "risk_checks": [item.as_dict() if hasattr(item, "as_dict") else item for item in self.risk_checks],
            "constraints_applied": list(self.constraints_applied),
            "evidence": evidence,
            "evidence_ids": [item.id for item in self.evidence],
            "factor_scores": {
                symbol: value.as_dict() if isinstance(value, AssetAssessment) else value
                for symbol, value in self.factor_scores.items()
            },
            "status": self.status,
            "review_type": self.review_type,
        }
        if self.policy_hash is not None:
            result["policy_hash"] = self.policy_hash
        if self.resolved_policy is not None:
            result["resolved_policy"] = dict(self.resolved_policy)
        if self.decision_id is not None:
            result["decision_id"] = self.decision_id
        if self.based_on_snapshot_id is not None:
            result["based_on_snapshot_id"] = self.based_on_snapshot_id
        if self.config is not None:
            result["config"] = dict(self.config)
        if self.execution_plans is not None:
            result["execution_plans"] = {
                symbol: plan.as_dict() for symbol, plan in self.execution_plans.items()
            }
        if self.market_overlays is not None:
            result["market_overlays"] = thaw_packet_value(self.market_overlays)
        if self.regime_confidence is not None:
            result["regime_confidence"] = self.regime_confidence.as_dict() if isinstance(self.regime_confidence, ConfidenceResult) else self.regime_confidence
        if self.decision_confidence is not None:
            result["decision_confidence"] = self.decision_confidence.as_dict() if isinstance(self.decision_confidence, DecisionConfidence) else self.decision_confidence
        for field_name in ("nav_performance", "benchmark_performance", "event_scan_summary"):
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = thaw_packet_value(value)
        if self.manual_asset_contexts:
            result["manual_asset_contexts"] = [item.as_dict() for item in self.manual_asset_contexts]
        if self.no_trade_attribution is not None:
            result["no_trade_attribution"] = self.no_trade_attribution.as_dict()
        return result


def validate_factor_evidence_binding(
    factor_scores: Mapping[str, Any],
    evidence_by_id: Mapping[str, Evidence],
    *,
    symbol: str,
) -> None:
    """Reject any factor whose evidence references cross factor or asset lines.

    ``factor_scores`` maps factor name to a value carrying ``evidence_ids``
    (``FactorScore`` or a mapping). ``evidence_by_id`` maps evidence id to its
    persisted ``Evidence``. A reference that is missing, belongs to another
    asset, or belongs to another factor is a lineage error, never a warning.
    """
    for factor, score in factor_scores.items():
        if not isinstance(score, FactorScore):
            continue
        if str(getattr(score, "factor", factor)).strip().lower() != str(factor).strip().lower():
            raise ValueError(f"factor score key {factor} does not match its factor {score.factor}")
        for evidence_id in score.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                raise ValueError(f"factor {factor} references missing evidence {evidence_id}")
            if evidence.asset != symbol:
                raise ValueError(f"factor {factor} references evidence for wrong asset {evidence_id}")
            if evidence.factor != factor:
                raise ValueError(f"factor {factor} references evidence for wrong factor {evidence_id}")


@dataclass(frozen=True)
class DecisionStatusEvent:
    decision_id: str
    timestamp: str
    status: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision_id, str) or not self.decision_id.strip():
            raise ValueError("decision_id must be a non-empty string")
        object.__setattr__(self, "decision_id", self.decision_id.strip())
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp))
        status = self.status.upper()
        if status not in _STATUSES:
            raise ValueError(f"status must be one of {sorted(_STATUSES)}")
        object.__setattr__(self, "status", status)
        if self.reason is not None:
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("reason must be a non-empty string or null")
            object.__setattr__(self, "reason", self.reason.strip())

    def as_dict(self) -> dict[str, Any]:
        result = {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp,
            "status": self.status,
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result


__all__ = ["Decision", "DecisionStatusEvent", "validate_factor_evidence_binding"]
