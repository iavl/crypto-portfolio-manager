"""Deterministic reliability-aware asset scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.evidence import AVAILABILITY_STATES, AssetAssessment, FactorScore
from ..models.policy import Policy, SCORING_FACTORS, resolve_policy


_CONFIDENCE_ORDER = ("LOW", "MEDIUM", "HIGH")


def calculate_factor_reliability(
    completeness: float,
    freshness: str,
    source_confidence: str,
) -> float:
    """Derive a bounded reliability multiplier from structured metadata."""
    if isinstance(completeness, bool) or not isinstance(completeness, (int, float)):
        raise ValueError("completeness must be numeric")
    completeness = float(completeness)
    if not math.isfinite(completeness) or not 0 <= completeness <= 1:
        raise ValueError("completeness must be finite and in [0, 1]")
    freshness_quality = {"CURRENT": 1.0, "STALE": 0.5, "UNKNOWN": 0.0}.get(
        str(freshness).strip().upper()
    )
    if freshness_quality is None:
        raise ValueError("freshness must be CURRENT, STALE, or UNKNOWN")
    source_quality = {"HIGH": 1.0, "MEDIUM": 0.75, "LOW": 0.5}.get(
        str(source_confidence).strip().upper()
    )
    if source_quality is None:
        raise ValueError("source_confidence must be HIGH, MEDIUM, or LOW")
    return completeness * freshness_quality * source_quality


def ensure_acquisition_ready(acquisition: Any) -> None:
    """Stop scoring until hard-critical external evidence is resolved."""
    if hasattr(acquisition, "require_scoring_ready"):
        acquisition.require_scoring_ready()
        return
    if isinstance(acquisition, Mapping):
        pending = acquisition.get("pending_external_resolution")
        if pending is None:
            pending = len(acquisition.get("pending_event_scans", ())) + len(
                acquisition.get("pending_web_fallbacks", ())
            )
        if acquisition.get("finalized") is False or pending or acquisition.get("ready_for_scoring") is False:
            from ..acquisition import AcquisitionResolutionRequired

            raise AcquisitionResolutionRequired("hard-critical event scan resolution required")
    raise ValueError("acquisition must expose ready_for_scoring")


@dataclass(frozen=True)
class ScoreResult:
    score: float
    effective_weights: Mapping[str, float]
    missing_factors: tuple[str, ...]
    confidence: str
    confidence_adjustment: float
    coverage: float = 1.0
    critical_data_complete: bool = True
    profile_name: str = "default"
    effective_factor_scores: Mapping[str, float] = None
    factor_reliability: Mapping[str, float] = None
    factor_availability: Mapping[str, str] = None
    not_applicable_factors: tuple[str, ...] = ()
    scoring_model_version: int = 2

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "profile_name": self.profile_name,
            "effective_weights": dict(self.effective_weights),
            "effective_factor_scores": dict(self.effective_factor_scores or {}),
            "factor_reliability": dict(self.factor_reliability or {}),
            "factor_availability": dict(self.factor_availability or {}),
            "missing_factors": list(self.missing_factors),
            "not_applicable_factors": list(self.not_applicable_factors),
            "confidence": self.confidence,
            "confidence_adjustment": self.confidence_adjustment,
            "coverage": self.coverage,
            "critical_data_complete": self.critical_data_complete,
            "scoring_model_version": self.scoring_model_version,
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


def _reliability(value: Any, factor: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"factor {factor}.reliability must be a number")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"factor {factor}.reliability must be finite and in [0, 1]")
    return value


def _extract(value: Any, factor: str) -> tuple[float | None, str, float]:
    """Return raw score, availability, and reliability from a factor value."""
    if isinstance(value, FactorScore):
        if value.factor != factor:
            raise ValueError(f"factor score key {factor!r} does not match {value.factor!r}")
        return value.score, value.availability, value.reliability
    if value is None:
        return None, "MISSING", 0.0
    if not isinstance(value, Mapping) and hasattr(value, "as_dict") and hasattr(value, "facts"):
        value = value.as_dict()
    if isinstance(value, Mapping) and isinstance(value.get("facts"), Mapping):
        return _extract(FactorScore.from_result(factor, value), factor)
    if isinstance(value, Mapping):
        if "score" not in value and "availability" not in value:
            raise ValueError(f"factor {factor} must contain score or availability")
        default_availability = "MISSING" if value.get("score") is None and "availability" not in value else "AVAILABLE"
        availability = str(value.get("availability", default_availability)).strip().upper()
        if availability not in AVAILABILITY_STATES:
            raise ValueError(f"factor {factor}.availability is unsupported")
        raw_score = value.get("score")
        if value.get("state") in {"UNKNOWN", "NOT_APPLICABLE"}:
            availability = "MISSING" if value["state"] == "UNKNOWN" else "NOT_APPLICABLE"
            raw_score = None
        score = None if raw_score is None else _score(raw_score, f"factor {factor}.score")
        reliability = _reliability(
            value.get("reliability", 1.0 if availability == "AVAILABLE" else 0.0), factor
        )
    elif hasattr(value, "score") and not isinstance(value, (str, bytes, int, float, bool)):
        default_availability = "MISSING" if getattr(value, "score", None) is None and not hasattr(value, "availability") else "AVAILABLE"
        availability = str(getattr(value, "availability", default_availability)).strip().upper()
        if availability not in AVAILABILITY_STATES:
            raise ValueError(f"factor {factor}.availability is unsupported")
        raw_score = value.score
        score = None if raw_score is None else _score(raw_score, f"factor {factor}.score")
        reliability = _reliability(
            getattr(value, "reliability", 1.0 if availability == "AVAILABLE" else 0.0), factor
        )
    else:
        return _score(value, f"factor {factor}.score"), "AVAILABLE", 1.0
    if availability == "AVAILABLE" and score is None:
        raise ValueError(f"factor {factor}.score is required when available")
    if availability != "AVAILABLE":
        if score is not None:
            raise ValueError(f"factor {factor}.score must be null when {availability}")
        if reliability != 0.0:
            raise ValueError(f"factor {factor}.reliability must be 0 when {availability}")
    return score, availability, reliability


def _confidence(value: str | None) -> str:
    if value is None:
        return "HIGH"
    if not isinstance(value, str):
        raise ValueError("confidence must be HIGH, MEDIUM, or LOW")
    value = value.upper()
    if value not in _CONFIDENCE_ORDER:
        raise ValueError("confidence must be HIGH, MEDIUM, or LOW")
    return value


def _factor_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("factor_scores must be an object")
    result: dict[str, Any] = {}
    for raw_factor, raw_value in value.items():
        if not isinstance(raw_factor, str) or not raw_factor.strip():
            raise ValueError("factor score keys must be non-empty strings")
        factor = raw_factor.strip().lower()
        if factor in result:
            raise ValueError(f"factor_scores contains duplicate key {factor}")
        result[factor] = raw_value
    return result


def _weight_mapping(value: Mapping[str, float]) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("scoring weights must be an object")
    result: dict[str, float] = {}
    for raw_factor, raw_weight in value.items():
        if not isinstance(raw_factor, str) or not raw_factor.strip():
            raise ValueError("scoring weight keys must be non-empty strings")
        factor = raw_factor.strip().lower()
        if factor in result:
            raise ValueError(f"scoring weights contain duplicate key {factor}")
        weight = math.nan if isinstance(raw_weight, bool) else float(raw_weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"scoring weight {factor!r} must be finite and >= 0")
        if factor not in SCORING_FACTORS:
            raise ValueError(f"unknown scoring factor {factor}")
        if factor == "event_risk":
            raise ValueError("event_risk is not a v2 scoring factor")
        result[factor] = weight
    if not result or sum(result.values()) <= 0:
        raise ValueError("scoring weights must sum to > 0")
    return result


def _confidence_for_coverage(
    confidence: str | None, coverage: float, critical_data_complete: bool, policy: Policy
) -> str:
    result = _confidence(confidence)
    index = _CONFIDENCE_ORDER.index(result)
    thresholds = policy.scoring
    if not critical_data_complete or coverage < thresholds["minimum_investable_coverage"]:
        index = 0
    elif coverage < thresholds["medium_confidence_min_coverage"]:
        index = min(index, 0)
    elif coverage < thresholds["high_confidence_min_coverage"]:
        index = min(index, 1)
    return _CONFIDENCE_ORDER[index]




def _score_factors_v2(
    factor_scores: Mapping[str, Any],
    weights: Mapping[str, float],
    *,
    confidence: str | None,
    critical_data_complete: bool,
    policy: Policy,
    profile_name: str,
    symbol: str,
) -> ScoreResult:
    resolved_weights = _weight_mapping(weights)
    if not math.isclose(sum(resolved_weights.values()), 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError("v2 scoring weights must sum to 1")
    unknown = sorted(set(factor_scores) - set(resolved_weights))
    if unknown:
        raise ValueError(f"unknown scoring factor(s): {', '.join(unknown)}")
    normalized_symbol = symbol.strip().upper()
    effective_scores: dict[str, float] = {}
    reliabilities: dict[str, float] = {}
    availability: dict[str, str] = {}
    missing: list[str] = []
    not_applicable: list[str] = []
    for factor, weight in resolved_weights.items():
        if factor not in factor_scores:
            state = "NOT_APPLICABLE" if weight == 0 else "MISSING"
            score = None
            factor_reliability = 0.0
        else:
            score, state, factor_reliability = _extract(factor_scores[factor], factor)
        if normalized_symbol == "BTC" and factor == "relative_strength_btc":
            if state in {"MISSING", "NOT_APPLICABLE"}:
                state = "NOT_APPLICABLE"
                score = None
                factor_reliability = 0.0
            else:
                raise ValueError("BTC relative_strength_btc must be NOT_APPLICABLE")
        if state == "NOT_APPLICABLE":
            if weight > 0:
                raise ValueError(
                    f"factor {factor} is NOT_APPLICABLE but has positive profile weight"
                )
            if factor == "relative_strength_btc":
                not_applicable.append(factor)
        elif state == "MISSING":
            if weight > 0:
                missing.append(factor)
                effective_scores[factor] = 50.0
        else:
            effective_scores[factor] = 50.0 + factor_reliability * (score - 50.0)  # type: ignore[operator]
        availability[factor] = state
        reliabilities[factor] = factor_reliability
    coverage = sum(
        resolved_weights[factor] * reliabilities[factor] for factor in resolved_weights
    )
    result_score = sum(
        resolved_weights[factor] * effective_scores.get(factor, 0.0)
        for factor in resolved_weights
    )
    return ScoreResult(
        score=result_score,
        effective_weights={factor: weight for factor, weight in resolved_weights.items() if weight > 0},
        effective_factor_scores=effective_scores,
        factor_reliability=reliabilities,
        factor_availability=availability,
        missing_factors=tuple(missing),
        not_applicable_factors=tuple(not_applicable),
        confidence=_confidence_for_coverage(confidence, coverage, critical_data_complete, policy),
        confidence_adjustment=coverage,
        coverage=coverage,
        critical_data_complete=critical_data_complete,
        profile_name=profile_name,
        scoring_model_version=2,
    )


def score_factors(
    factor_scores: Mapping[str, Any],
    weights: Mapping[str, float] | None = None,
    *,
    confidence: str | None = None,
    critical_data_complete: bool = True,
    policy: Policy | None = None,
    acquisition: Any | None = None,
    symbol: str = "ASSET",
) -> ScoreResult:
    if acquisition is not None:
        ensure_acquisition_ready(acquisition)
    resolved_policy = policy or resolve_policy()
    if resolved_policy.is_excluded(symbol):
        raise ValueError(f"excluded asset {symbol.strip().upper()} cannot be scored")
    factors = _factor_mapping(factor_scores)
    if not isinstance(critical_data_complete, bool):
        raise ValueError("critical_data_complete must be boolean")
    if weights is None:
        profile_name = resolved_policy.scoring_profile_name(symbol)
        raw_weights = resolved_policy.scoring_profile(symbol)
    else:
        profile_name = "custom"
        raw_weights = weights
    return _score_factors_v2(
        factors,
        raw_weights,
        confidence=confidence,
        critical_data_complete=critical_data_complete,
        policy=resolved_policy,
        profile_name=profile_name,
        symbol=symbol,
    )


def score_assessment(
    assessment: AssetAssessment, *, policy: Policy | None = None, acquisition: Any | None = None
) -> tuple[AssetAssessment, ScoreResult]:
    result = score_factors(
        assessment.factor_scores,
        policy=policy,
        confidence=assessment.confidence,
        critical_data_complete=assessment.critical_data_complete,
        acquisition=acquisition,
        symbol=assessment.symbol,
    )
    scored_factors = dict(assessment.factor_scores)
    for factor, state in (result.factor_availability or {}).items():
        if factor not in scored_factors and state == "MISSING":
            scored_factors[factor] = FactorScore(factor, None, availability="MISSING")
        elif state == "NOT_APPLICABLE":
            scored_factors[factor] = FactorScore(factor, None, availability="NOT_APPLICABLE")
    updated = AssetAssessment(
        symbol=assessment.symbol,
        factor_scores=scored_factors,
        weighted_score=result.score,
        confidence=result.confidence,
        asset_type=assessment.asset_type,
        relative_strength_vs_btc=assessment.relative_strength_vs_btc,
        thesis_broken=assessment.thesis_broken,
        critical_data_complete=assessment.critical_data_complete,
        risk_tier=assessment.risk_tier,
        event_risk=assessment.event_risk,
        scoring_profile_name=result.profile_name,
        scoring_model_version=result.scoring_model_version,
        score_coverage=result.coverage,
    )
    return updated, result


def weighted_score(
    factor_scores: Mapping[str, Any],
    weights: Mapping[str, float] | None = None,
    *,
    confidence: str | None = None,
    critical_data_complete: bool = True,
    policy: Policy | None = None,
    acquisition: Any | None = None,
    symbol: str = "ASSET",
) -> ScoreResult:
    return score_factors(
        factor_scores,
        weights,
        confidence=confidence,
        critical_data_complete=critical_data_complete,
        policy=policy,
        acquisition=acquisition,
        symbol=symbol,
    )


__all__ = [
    "ScoreResult",
    "calculate_factor_reliability",
    "ensure_acquisition_ready",
    "score_assessment",
    "score_factors",
    "weighted_score",
]
