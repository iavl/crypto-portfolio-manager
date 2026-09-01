"""Deterministic weighted asset scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.evidence import AssetAssessment, FactorScore
from ..models.policy import Policy, resolve_policy


_CONFIDENCE_ORDER = ("LOW", "MEDIUM", "HIGH")


@dataclass(frozen=True)
class ScoreResult:
    score: float
    effective_weights: Mapping[str, float]
    missing_factors: tuple[str, ...]
    confidence: str
    confidence_adjustment: float
    coverage: float = 1.0
    critical_data_complete: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "effective_weights": dict(self.effective_weights),
            "missing_factors": list(self.missing_factors),
            "confidence": self.confidence,
            "confidence_adjustment": self.confidence_adjustment,
            "coverage": self.coverage,
            "critical_data_complete": self.critical_data_complete,
        }

    def __float__(self) -> float:
        return self.score


def _score(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(f"{field} must be finite and in [0, 100]")
    return value


def _extract(value: Any, factor: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, FactorScore):
        if value.factor != factor:
            raise ValueError(f"factor score key {factor!r} does not match {value.factor!r}")
        return value.score
    if isinstance(value, Mapping) and "score" in value:
        return _score(value["score"], f"factor {factor}.score")
    return _score(value, f"factor {factor}.score")


def _confidence(value: str | None) -> str:
    if value is None:
        return "HIGH"
    if not isinstance(value, str):
        raise ValueError("confidence must be HIGH, MEDIUM, or LOW")
    value = value.upper()
    if value not in _CONFIDENCE_ORDER:
        raise ValueError("confidence must be HIGH, MEDIUM, or LOW")
    return value


def score_factors(
    factor_scores: Mapping[str, Any],
    weights: Mapping[str, float] | None = None,
    *,
    confidence: str | None = None,
    critical_data_complete: bool = True,
    policy: Policy | None = None,
) -> ScoreResult:
    raw_weights = (policy or resolve_policy()).scoring_weights if weights is None else weights
    if not isinstance(raw_weights, Mapping):
        raise ValueError("scoring weights must be an object")
    resolved_weights: dict[str, float] = {}
    for factor, raw_weight in raw_weights.items():
        if not isinstance(factor, str) or not factor.strip():
            raise ValueError("scoring weight keys must be non-empty strings")
        weight = float(raw_weight) if not isinstance(raw_weight, bool) else math.nan
        resolved_weights[factor] = weight
    if not resolved_weights:
        raise ValueError("scoring weights are required")
    for factor, weight in resolved_weights.items():
        weight = float(weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"scoring weight {factor!r} must be finite and >= 0")
    if sum(resolved_weights.values()) <= 0:
        raise ValueError("scoring weights must sum to > 0")
    if not isinstance(factor_scores, Mapping):
        raise ValueError("factor_scores must be an object")
    unknown = sorted(set(factor_scores) - set(resolved_weights))
    if unknown:
        raise ValueError(f"unknown scoring factor(s): {', '.join(unknown)}")
    if not isinstance(critical_data_complete, bool):
        raise ValueError("critical_data_complete must be boolean")

    available: dict[str, float] = {}
    missing: list[str] = []
    for factor, weight in resolved_weights.items():
        if factor not in factor_scores:
            missing.append(factor)
            continue
        value = _extract(factor_scores[factor], factor)
        if value is None:
            missing.append(factor)
            continue
        if weight > 0:
            available[factor] = value
    total_weight = sum(resolved_weights.values())
    available_weight = sum(resolved_weights[factor] for factor in available)
    if available_weight <= 0:
        raise ValueError("no scored factors available")
    effective_weights = {
        factor: resolved_weights[factor] / available_weight for factor in available
    }
    score = sum(available[factor] * effective_weights[factor] for factor in available)
    coverage = available_weight / total_weight
    base_confidence = _confidence(confidence)
    confidence_index = _CONFIDENCE_ORDER.index(base_confidence)
    scoring_policy = (policy or resolve_policy()).scoring
    if not critical_data_complete or coverage < scoring_policy["minimum_investable_coverage"]:
        confidence_index = 0
    elif coverage < scoring_policy["medium_confidence_min_coverage"]:
        confidence_index = min(confidence_index, 0)
    elif coverage < scoring_policy["high_confidence_min_coverage"]:
        confidence_index = min(confidence_index, 1)
    adjusted_confidence = _CONFIDENCE_ORDER[confidence_index]
    return ScoreResult(
        score=score,
        effective_weights=effective_weights,
        missing_factors=tuple(missing),
        confidence=adjusted_confidence,
        confidence_adjustment=coverage,
        coverage=coverage,
        critical_data_complete=critical_data_complete,
    )


def score_assessment(
    assessment: AssetAssessment, *, policy: Policy | None = None
) -> tuple[AssetAssessment, ScoreResult]:
    result = score_factors(
        assessment.factor_scores,
        policy=policy,
        confidence=assessment.confidence,
        critical_data_complete=assessment.critical_data_complete,
    )
    updated = AssetAssessment(
        symbol=assessment.symbol,
        factor_scores=assessment.factor_scores,
        weighted_score=result.score,
        confidence=result.confidence,
        asset_type=assessment.asset_type,
        relative_strength_vs_btc=assessment.relative_strength_vs_btc,
        severe_event=assessment.severe_event,
        thesis_broken=assessment.thesis_broken,
        critical_data_complete=assessment.critical_data_complete,
        risk_tier=assessment.risk_tier,
    )
    return updated, result


def weighted_score(
    factor_scores: Mapping[str, Any],
    weights: Mapping[str, float] | None = None,
    *,
    confidence: str | None = None,
    critical_data_complete: bool = True,
    policy: Policy | None = None,
) -> ScoreResult:
    return score_factors(
        factor_scores,
        weights,
        confidence=confidence,
        critical_data_complete=critical_data_complete,
        policy=policy,
    )


__all__ = ["ScoreResult", "score_assessment", "score_factors", "weighted_score"]
