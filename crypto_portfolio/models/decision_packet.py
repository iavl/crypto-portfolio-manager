"""Compact finalized inputs for high-impact decision review."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .confidence import ConfidenceResult, DecisionConfidence
from .evidence import EventRiskAssessment, ManualAssetContext
from .factor_packet import freeze_packet_value, thaw_packet_value


_REVIEW_TYPES = {"SNAPSHOT_REVIEW", "FULL_REVIEW", "EVENT_REVIEW"}
_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_ACTIONS = {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_GATE_STATES = {"PASS", "WATCH", "BLOCKED", "NOT_APPLICABLE", "UNKNOWN"}
_NO_TRADE_REASONS = {
    "SCORE_BELOW_ENTRY",
    "CONFIDENCE_TOO_LOW",
    "DECISION_CONFIDENCE_MEDIUM",
    "REGIME_RISK_BUDGET_EXHAUSTED",
    "EVENT_RISK_BLOCK",
    "LIVENESS_BLOCK",
    "BTC_RELATIVE_WEAK",
    "TARGET_DELTA_BELOW_HOLD_BAND",
    "TARGET_DELTA_WATCH_ONLY",
    "STABLECOIN_FLOOR_CONSTRAINT",
    "EXECUTION_WAIT",
    "NO_APPROVED_INCREASE",
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _weights(value: Mapping[str, Any] | None, field: str, *, require_sum: bool = False) -> Mapping[str, float]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result: dict[str, float] = {}
    for raw_symbol, raw_weight in value.items():
        symbol = _text(raw_symbol, f"{field} symbol").upper()
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError(f"{field}.{symbol} must be a number")
        weight = float(raw_weight)
        if not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError(f"{field}.{symbol} must be finite and in [0, 1]")
        if symbol in result:
            raise ValueError(f"{field} contains duplicate symbol {symbol}")
        result[symbol] = weight
    total = sum(result.values())
    if total > 1.0 + 1e-9:
        raise ValueError(f"{field} weights must sum to no more than 1")
    if require_sum and result and not math.isclose(total, 1.0, abs_tol=1e-9):
        raise ValueError(f"{field} weights must sum to 1")
    return MappingProxyType(result)


def _ids(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a sequence of strings")
    result = tuple(_text(item, f"{field} item") for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must contain unique values")
    return result


def _manual_contexts(value: Any) -> tuple[ManualAssetContext, ...]:
    if value is None:
        return ()
    if isinstance(value, ManualAssetContext) or isinstance(value, Mapping):
        value = (value,)
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError("manual_asset_contexts must be a sequence of objects")
    result = tuple(
        item if isinstance(item, ManualAssetContext) else ManualAssetContext.from_mapping(item)
        for item in value
    )
    identities = [(item.asset, item.category, item.as_of) for item in result]
    if len(identities) != len(set(identities)):
        raise ValueError("manual_asset_contexts must not contain duplicate contexts")
    return result


@dataclass(frozen=True)
class NoTradeAttribution:
    score_gate: str
    confidence_gate: str
    regime_gate: str
    event_gate: str
    liveness_gate: str
    btc_relative_gate: str
    allocation_delta_gate: str
    rebalance_gate: str
    execution_gate: str
    primary_reason: str
    secondary_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "score_gate",
            "confidence_gate",
            "regime_gate",
            "event_gate",
            "liveness_gate",
            "btc_relative_gate",
            "allocation_delta_gate",
            "rebalance_gate",
            "execution_gate",
        ):
            state = _text(getattr(self, field_name), f"no_trade_attribution.{field_name}").upper()
            if state not in _GATE_STATES:
                raise ValueError(
                    f"no_trade_attribution.{field_name} must be one of {sorted(_GATE_STATES)}"
                )
            object.__setattr__(self, field_name, state)
        primary = _text(self.primary_reason, "no_trade_attribution.primary_reason").upper()
        if primary not in _NO_TRADE_REASONS:
            raise ValueError("no_trade_attribution.primary_reason is unsupported")
        secondary = tuple(item.upper() for item in _ids(self.secondary_reasons, "no_trade_attribution.secondary_reasons"))
        if any(item not in _NO_TRADE_REASONS for item in secondary):
            raise ValueError("no_trade_attribution.secondary_reasons contains an unsupported code")
        if primary in secondary:
            raise ValueError("no_trade_attribution.secondary_reasons must not repeat primary_reason")
        object.__setattr__(self, "primary_reason", primary)
        object.__setattr__(self, "secondary_reasons", secondary)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NoTradeAttribution":
        if not isinstance(value, Mapping):
            raise ValueError("no_trade_attribution must be an object")
        return cls(**dict(value))

    def as_dict(self) -> dict[str, Any]:
        return {
            "score_gate": self.score_gate,
            "confidence_gate": self.confidence_gate,
            "regime_gate": self.regime_gate,
            "event_gate": self.event_gate,
            "liveness_gate": self.liveness_gate,
            "btc_relative_gate": self.btc_relative_gate,
            "allocation_delta_gate": self.allocation_delta_gate,
            "rebalance_gate": self.rebalance_gate,
            "execution_gate": self.execution_gate,
            "primary_reason": self.primary_reason,
            "secondary_reasons": list(self.secondary_reasons),
        }


def _scores(value: Any) -> Mapping[str, float | None]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise ValueError("asset factor scores must be an object")
    result: dict[str, float | None] = {}
    for raw_factor, raw_score in value.items():
        factor = _text(raw_factor, "factor").lower()
        if factor in result:
            raise ValueError(f"factor scores contain duplicate factor {factor}")
        if raw_score is None:
            result[factor] = None
            continue
        if hasattr(raw_score, "score") and not isinstance(raw_score, (int, float)):
            raw_score = raw_score.score
        elif isinstance(raw_score, Mapping) and "score" in raw_score:
            raw_score = raw_score["score"]
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            raise ValueError(f"factor {factor} score must be a number or null")
        score = float(raw_score)
        if not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError(f"factor {factor} score must be finite and in [0, 100]")
        result[factor] = score
    return MappingProxyType(result)


@dataclass(frozen=True)
class AssetDecisionSummary:
    symbol: str
    factor_scores: Mapping[str, Any] = field(default_factory=dict)
    score: float | None = None
    confidence: str = "LOW"
    previous_score: float | None = None
    key_facts: Mapping[str, Any] = field(default_factory=dict)
    historical_changes: Mapping[str, Any] = field(default_factory=dict)
    supporting_evidence_ids: tuple[str, ...] = ()
    contrary_evidence_ids: tuple[str, ...] = ()
    current_weight: float = 0.0
    target_weight: float = 0.0
    action: str = "HOLD"
    approved_amount_usd: float = 0.0
    thesis_broken: bool = False
    portfolio_constraint: str = ""
    event_risk: Mapping[str, Any] | None = None
    confidence_score: float | None = None
    confidence_explanation: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "asset summary symbol").upper())
        object.__setattr__(self, "factor_scores", _scores(self.factor_scores))
        if self.score is not None:
            score = float(self.score)
            if not math.isfinite(score) or not 0 <= score <= 100:
                raise ValueError("asset summary score must be finite and in [0, 100]")
            object.__setattr__(self, "score", score)
        confidence = _text(self.confidence, "asset summary confidence").upper()
        if confidence not in _CONFIDENCE:
            raise ValueError("asset summary confidence must be HIGH, MEDIUM, or LOW")
        object.__setattr__(self, "confidence", confidence)
        if self.confidence_score is not None:
            score = float(self.confidence_score)
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("asset confidence_score must be finite and in [0, 1] or null")
            object.__setattr__(self, "confidence_score", score)
        if self.confidence_explanation is not None:
            if not isinstance(self.confidence_explanation, Mapping):
                raise ValueError("confidence_explanation must be an object or null")
            object.__setattr__(self, "confidence_explanation", freeze_packet_value(self.confidence_explanation, path="confidence_explanation"))
        if self.previous_score is not None:
            if isinstance(self.previous_score, bool) or not isinstance(self.previous_score, (int, float)):
                raise ValueError("previous_score must be a number or null")
            score = float(self.previous_score)
            if not math.isfinite(score) or not 0 <= score <= 100:
                raise ValueError("previous_score must be finite and in [0, 100]")
            object.__setattr__(self, "previous_score", score)
        if not isinstance(self.key_facts, Mapping) or not isinstance(self.historical_changes, Mapping):
            raise ValueError("key_facts and historical_changes must be objects")
        object.__setattr__(self, "key_facts", freeze_packet_value(self.key_facts, path="key_facts"))
        object.__setattr__(self, "historical_changes", freeze_packet_value(self.historical_changes, path="historical_changes"))
        object.__setattr__(self, "supporting_evidence_ids", _ids(self.supporting_evidence_ids, "supporting_evidence_ids"))
        object.__setattr__(self, "contrary_evidence_ids", _ids(self.contrary_evidence_ids, "contrary_evidence_ids"))
        for field_name in ("current_weight", "target_weight"):
            if isinstance(getattr(self, field_name), bool) or not isinstance(getattr(self, field_name), (int, float)):
                raise ValueError(f"{field_name} must be a number")
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{field_name} must be finite and in [0, 1]")
            object.__setattr__(self, field_name, value)
        action = _text(self.action, "asset summary action").upper()
        if action not in _ACTIONS:
            raise ValueError(f"asset summary action must be one of {sorted(_ACTIONS)}")
        object.__setattr__(self, "action", action)
        if isinstance(self.approved_amount_usd, bool) or not isinstance(self.approved_amount_usd, (int, float)):
            raise ValueError("approved_amount_usd must be a number")
        amount = float(self.approved_amount_usd)
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("approved_amount_usd must be finite and >= 0")
        if action in {"HOLD", "WAIT", "NO_TRADE"} and amount != 0:
            raise ValueError(f"{action} must have zero approved_amount_usd")
        object.__setattr__(self, "approved_amount_usd", amount)
        if action in {"INCREASE", "REDUCE", "EXIT"} and amount <= 0:
            raise ValueError(f"{action} requires a positive approved_amount_usd")
        if not isinstance(self.thesis_broken, bool):
            raise ValueError("thesis_broken must be boolean")
        if self.event_risk is not None:
            event_risk = (
                self.event_risk.as_dict()
                if isinstance(self.event_risk, EventRiskAssessment)
                else EventRiskAssessment.from_mapping(self.event_risk).as_dict()
            )
            object.__setattr__(self, "event_risk", freeze_packet_value(event_risk, path="event_risk"))
        if not isinstance(self.portfolio_constraint, str):
            raise ValueError("portfolio_constraint must be a string")
        object.__setattr__(self, "portfolio_constraint", self.portfolio_constraint.strip())

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], symbol: str | None = None) -> "AssetDecisionSummary":
        if not isinstance(value, Mapping):
            raise ValueError("asset decision summary must be an object")
        data = dict(value)
        if symbol is not None:
            data.setdefault("symbol", symbol)
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        result = {
            "symbol": self.symbol,
            "factor_scores": dict(self.factor_scores),
            "score": self.score,
            "confidence": self.confidence,
            "previous_score": self.previous_score,
            "key_facts": thaw_packet_value(self.key_facts),
            "historical_changes": thaw_packet_value(self.historical_changes),
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "contrary_evidence_ids": list(self.contrary_evidence_ids),
            "current_weight": self.current_weight,
            "target_weight": self.target_weight,
            "action": self.action,
            "approved_amount_usd": self.approved_amount_usd,
            "thesis_broken": self.thesis_broken,
            "portfolio_constraint": self.portfolio_constraint,
            "event_risk": thaw_packet_value(self.event_risk) if self.event_risk is not None else None,
            "confidence_score": self.confidence_score,
            "confidence_explanation": thaw_packet_value(self.confidence_explanation) if self.confidence_explanation is not None else None,
        }
        return result

@dataclass(frozen=True)
class SolReview:
    status: str
    rationale: str
    stage: str = "SOL"

    def __post_init__(self) -> None:
        status = _text(self.status, "Sol review status").upper()
        if status not in {"APPROVE", "CHALLENGE", "DOWNGRADE", "REQUIRE_REVIEW"}:
            raise ValueError("Sol review status is unsupported")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "rationale", _text(self.rationale, "Sol review rationale"))
        if _text(self.stage, "Sol review stage").upper() != "SOL":
            raise ValueError("Sol review stage must be SOL")
        object.__setattr__(self, "stage", "SOL")

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "rationale": self.rationale, "stage": self.stage}


@dataclass(frozen=True)
class DecisionReviewPacket:
    review_type: str
    market_regime: str
    portfolio_drawdown: float | None = None
    risk_flags: tuple[str, ...] = ()
    current_weights: Mapping[str, float] = field(default_factory=dict)
    target_weights: Mapping[str, float] = field(default_factory=dict)
    assets: tuple[AssetDecisionSummary, ...] = ()
    execution_summary: Mapping[str, Any] = field(default_factory=dict)
    critical_missing_data: tuple[str, ...] = ()
    major_conflicts: tuple[str, ...] = ()
    major_event_risk: bool = False
    risk_budget_breach: bool = False
    risk_escalation: bool = False
    recommendation_reversal: bool = False
    previous_target_weights: Mapping[str, float] = field(default_factory=dict)
    positioning_summaries: Mapping[str, Any] = field(default_factory=dict)
    btc_cycle_summary: Mapping[str, Any] | None = None
    overlay_confidence: str = "LOW"
    overlay_warnings: tuple[str, ...] = ()
    effective_deployment_caps: Mapping[str, float] = field(default_factory=dict)
    regime_confidence: ConfidenceResult | Mapping[str, Any] | None = None
    decision_confidence: DecisionConfidence | Mapping[str, Any] | None = None
    nav_performance: Mapping[str, Any] | None = None
    benchmark_performance: Mapping[str, Any] | None = None
    event_scan_summary: Mapping[str, Any] | None = None
    manual_asset_contexts: tuple[ManualAssetContext, ...] = ()
    no_trade_attribution: NoTradeAttribution | Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        review = _text(self.review_type, "review_type").upper()
        if review not in _REVIEW_TYPES:
            raise ValueError(f"review_type must be one of {sorted(_REVIEW_TYPES)}")
        regime = _text(self.market_regime, "market_regime").upper()
        if regime not in _REGIMES:
            raise ValueError(f"market_regime must be one of {sorted(_REGIMES)}")
        object.__setattr__(self, "review_type", review)
        object.__setattr__(self, "market_regime", regime)
        if self.portfolio_drawdown is not None:
            drawdown = float(self.portfolio_drawdown)
            if not math.isfinite(drawdown) or drawdown > 0:
                raise ValueError("portfolio_drawdown must be finite and <= 0")
            object.__setattr__(self, "portfolio_drawdown", drawdown)
        object.__setattr__(self, "risk_flags", _ids(self.risk_flags, "risk_flags"))
        object.__setattr__(self, "critical_missing_data", _ids(self.critical_missing_data, "critical_missing_data"))
        object.__setattr__(self, "major_conflicts", _ids(self.major_conflicts, "major_conflicts"))
        object.__setattr__(self, "current_weights", _weights(self.current_weights, "current_weights"))
        object.__setattr__(self, "target_weights", _weights(self.target_weights, "target_weights", require_sum=True))
        if not self.target_weights:
            raise ValueError("target_weights must be non-empty")
        object.__setattr__(self, "previous_target_weights", _weights(self.previous_target_weights, "previous_target_weights"))
        assets = tuple(
            item if isinstance(item, AssetDecisionSummary) else AssetDecisionSummary.from_mapping(item)
            for item in self.assets
        )
        symbols = [item.symbol for item in assets]
        if len(symbols) != len(set(symbols)):
            raise ValueError("assets must not contain duplicate symbols")
        object.__setattr__(self, "assets", assets)
        if not isinstance(self.execution_summary, Mapping):
            raise ValueError("execution_summary must be an object")
        object.__setattr__(self, "execution_summary", freeze_packet_value(self.execution_summary, path="execution_summary"))
        if not isinstance(self.positioning_summaries, Mapping):
            raise ValueError("positioning_summaries must be an object")
        summaries = {}
        for raw_symbol, summary in self.positioning_summaries.items():
            if not isinstance(raw_symbol, str) or not raw_symbol.strip():
                raise ValueError("positioning_summaries contains an invalid symbol")
            symbol = raw_symbol.strip().upper()
            if symbol in summaries:
                raise ValueError(f"positioning_summaries contains duplicate symbol {symbol}")
            if hasattr(summary, "as_dict"):
                summary = summary.as_dict()
            if not isinstance(summary, Mapping):
                raise ValueError(f"positioning_summaries.{symbol} must be an object")
            summaries[symbol] = freeze_packet_value(summary, path=f"positioning_summaries.{symbol}")
        object.__setattr__(self, "positioning_summaries", MappingProxyType(summaries))
        if self.btc_cycle_summary is not None:
            if hasattr(self.btc_cycle_summary, "as_dict"):
                object.__setattr__(self, "btc_cycle_summary", self.btc_cycle_summary.as_dict())
            if not isinstance(self.btc_cycle_summary, Mapping):
                raise ValueError("btc_cycle_summary must be an object or null")
            object.__setattr__(self, "btc_cycle_summary", freeze_packet_value(self.btc_cycle_summary, path="btc_cycle_summary"))
        overlay_confidence = _text(self.overlay_confidence, "overlay_confidence").upper()
        if overlay_confidence not in _CONFIDENCE:
            raise ValueError("overlay_confidence must be HIGH, MEDIUM, or LOW")
        object.__setattr__(self, "overlay_confidence", overlay_confidence)
        warnings = _ids(self.overlay_warnings, "overlay_warnings")
        object.__setattr__(self, "overlay_warnings", warnings)
        if not isinstance(self.effective_deployment_caps, Mapping):
            raise ValueError("effective_deployment_caps must be an object")
        caps = {}
        for raw_symbol, raw_factor in self.effective_deployment_caps.items():
            symbol = _text(raw_symbol, "effective_deployment_caps symbol").upper()
            if isinstance(raw_factor, bool) or not isinstance(raw_factor, (int, float)):
                raise ValueError("effective_deployment_caps values must be numbers")
            factor = float(raw_factor)
            if not math.isfinite(factor) or not 0 <= factor <= 1:
                raise ValueError("effective_deployment_caps values must be finite and in [0, 1]")
            if symbol in caps:
                raise ValueError(f"effective_deployment_caps contains duplicate symbol {symbol}")
            caps[symbol] = factor
        object.__setattr__(self, "effective_deployment_caps", MappingProxyType(caps))
        for field_name in ("major_event_risk", "risk_budget_breach", "risk_escalation", "recommendation_reversal"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be boolean")
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
        object.__setattr__(self, "manual_asset_contexts", _manual_contexts(self.manual_asset_contexts))
        if self.no_trade_attribution is not None:
            value = (
                self.no_trade_attribution
                if isinstance(self.no_trade_attribution, NoTradeAttribution)
                else NoTradeAttribution.from_mapping(self.no_trade_attribution)
            )
            object.__setattr__(self, "no_trade_attribution", value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "review_type": self.review_type,
            "market_regime": self.market_regime,
            "portfolio_drawdown": self.portfolio_drawdown,
            "risk_flags": list(self.risk_flags),
            "current_weights": dict(self.current_weights),
            "target_weights": dict(self.target_weights),
            "previous_target_weights": dict(self.previous_target_weights),
            "assets": [item.as_dict() for item in self.assets],
            "execution_summary": thaw_packet_value(self.execution_summary),
            "critical_missing_data": list(self.critical_missing_data),
            "major_conflicts": list(self.major_conflicts),
            "major_event_risk": self.major_event_risk,
            "risk_budget_breach": self.risk_budget_breach,
            "risk_escalation": self.risk_escalation,
            "recommendation_reversal": self.recommendation_reversal,
            "positioning_summaries": {
                symbol: thaw_packet_value(summary)
                for symbol, summary in self.positioning_summaries.items()
            },
            "btc_cycle_summary": thaw_packet_value(self.btc_cycle_summary) if self.btc_cycle_summary is not None else None,
            "overlay_confidence": self.overlay_confidence,
            "overlay_warnings": list(self.overlay_warnings),
            "effective_deployment_caps": dict(self.effective_deployment_caps),
            "regime_confidence": self.regime_confidence.as_dict() if isinstance(self.regime_confidence, ConfidenceResult) else self.regime_confidence,
            "decision_confidence": self.decision_confidence.as_dict() if isinstance(self.decision_confidence, DecisionConfidence) else self.decision_confidence,
            "nav_performance": thaw_packet_value(self.nav_performance) if self.nav_performance is not None else None,
            "benchmark_performance": thaw_packet_value(self.benchmark_performance) if self.benchmark_performance is not None else None,
            "event_scan_summary": thaw_packet_value(self.event_scan_summary) if self.event_scan_summary is not None else None,
            "manual_asset_contexts": [item.as_dict() for item in self.manual_asset_contexts],
            "no_trade_attribution": self.no_trade_attribution.as_dict() if self.no_trade_attribution else None,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DecisionReviewPacket":
        if not isinstance(value, Mapping):
            raise ValueError("decision review packet must be an object")
        data = dict(value)
        raw_assets = data.get("assets", ())
        data["assets"] = tuple(
            item if isinstance(item, AssetDecisionSummary) else AssetDecisionSummary.from_mapping(item)
            for item in raw_assets
        )
        return cls(**data)


__all__ = ["AssetDecisionSummary", "DecisionReviewPacket", "NoTradeAttribution", "SolReview"]
