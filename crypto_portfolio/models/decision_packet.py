"""Compact finalized inputs for high-impact decision review."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .factor_packet import freeze_packet_value, thaw_packet_value


_REVIEW_TYPES = {"SNAPSHOT_REVIEW", "FULL_REVIEW", "EVENT_REVIEW"}
_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_ACTIONS = {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}


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
    severe_event: bool = False
    portfolio_constraint: str = ""

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
        for field_name in ("thesis_broken", "severe_event"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be boolean")
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
            "severe_event": self.severe_event,
            "portfolio_constraint": self.portfolio_constraint,
        }
        return result

    @property
    def weighted_score(self) -> float | None:
        return self.score


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
        for field_name in ("major_event_risk", "risk_budget_breach", "risk_escalation", "recommendation_reversal"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be boolean")

    @property
    def asset_summaries(self) -> tuple[AssetDecisionSummary, ...]:
        return self.assets

    @property
    def high_impact_flags(self) -> tuple[str, ...]:
        flags = list(self.risk_flags) + list(self.critical_missing_data) + list(self.major_conflicts)
        if self.major_event_risk:
            flags.append("MAJOR_EVENT")
        if self.risk_budget_breach:
            flags.append("RISK_BUDGET_BREACH")
        return tuple(dict.fromkeys(flags))

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
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DecisionReviewPacket":
        if not isinstance(value, Mapping):
            raise ValueError("decision review packet must be an object")
        data = dict(value)
        raw_assets = data.get("assets", data.get("asset_summaries", ()))
        if isinstance(raw_assets, Mapping):
            raw_assets = [dict(item, symbol=symbol) for symbol, item in raw_assets.items()]
        data["assets"] = tuple(
            item if isinstance(item, AssetDecisionSummary) else AssetDecisionSummary.from_mapping(item)
            for item in raw_assets
        )
        data.pop("asset_summaries", None)
        return cls(**data)


__all__ = ["AssetDecisionSummary", "DecisionReviewPacket", "SolReview"]
