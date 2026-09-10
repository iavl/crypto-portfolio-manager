"""Deterministic reliability-aware asset scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.confidence import (
    ConfidenceCap,
    ConfidenceDimension,
    ConfidenceResult,
    DEFAULT_HIGH_MIN,
    DEFAULT_MEDIUM_MIN,
    confidence_band,
)
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
    factor_data_confidence: ConfidenceResult | None = None
    data_confidence_score: float | None = None
    data_confidence_band: str | None = None
    confidence_reason_codes: tuple[str, ...] = ()
    source_groups: tuple[str, ...] = ()
    conflict_ids: tuple[str, ...] = ()
    fallback_used: bool = False

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
            "factor_data_confidence": self.factor_data_confidence.as_dict() if self.factor_data_confidence else None,
            "data_confidence_score": self.data_confidence_score,
            "data_confidence_band": self.data_confidence_band,
            "confidence_reason_codes": list(self.confidence_reason_codes),
            "source_groups": list(self.source_groups),
            "conflict_ids": list(self.conflict_ids),
            "fallback_used": self.fallback_used,
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


@dataclass(frozen=True)
class _FactorMetadata:
    freshness: float | None = None
    source_quality: float | None = None
    redundancy: float | None = None
    evidence_ids: tuple[str, ...] = ()


def _quality_score(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        value = value.get("mean", value.get("score"))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    return _reliability(value, field)


def _freshness_quality(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _reliability(value, "freshness score")
    result = {"CURRENT": 1.0, "STALE": 0.5, "UNKNOWN": 0.0}.get(str(value).strip().upper())
    if result is None:
        raise ValueError("freshness must be CURRENT, STALE, or UNKNOWN")
    return result


def _source_quality(value: Mapping[str, Any]) -> float | None:
    raw = value.get("source_quality")
    if raw is not None:
        return _quality_score(raw, "source_quality")
    raw = value.get("source_confidence")
    if raw is not None:
        result = {"HIGH": 1.0, "MEDIUM": 0.75, "LOW": 0.5}.get(str(raw).strip().upper())
        if result is None:
            raise ValueError("source_confidence must be HIGH, MEDIUM, or LOW")
        return result
    tier = value.get("authority_tier", value.get("tier"))
    if tier is not None:
        if isinstance(tier, bool) or not isinstance(tier, int) or tier not in {1, 2, 3}:
            raise ValueError("authority_tier must be 1, 2, or 3")
        return {1: 1.0, 2: 0.75, 3: 0.5}[tier]
    return None


def _factor_mapping_for_metadata(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "as_dict") and not isinstance(value, (str, bytes, int, float, bool)):
        result = value.as_dict()
        return result if isinstance(result, Mapping) else None
    return None


def _redundancy_from_records(records: Any) -> float | None:
    if isinstance(records, (str, bytes)) or not isinstance(records, (list, tuple)) or not records:
        return None
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for raw in records:
        if not isinstance(raw, Mapping):
            return None
        metric = raw.get("metric_key", raw.get("fact_key"))
        if metric is None:
            return None
        identity = (
            str(raw.get("asset", "")).strip().upper(),
            str(metric).strip().lower(),
            str(raw.get("window", raw.get("horizon", raw.get("period", "")))).strip().lower(),
        )
        grouped.setdefault(identity, []).append(raw)
    values = []
    for rows in grouped.values():
        groups = [row.get("source_group", row.get("source")) for row in rows]
        count = len({str(group).strip().lower() for group in groups if group is not None and str(group).strip()})
        values.append((count, all(row.get("redundancy_expected", True) is not False for row in rows)))
    if not values:
        return None
    return sum(
        0.0 if count == 0 else 0.5 if count == 1 and expected else 1.0 if count == 1 else 0.8 if count == 2 else 1.0
        for count, expected in values
    ) / len(values)


def _factor_metadata(value: Any) -> _FactorMetadata:
    record = _factor_mapping_for_metadata(value)
    if record is None:
        return _FactorMetadata()
    facts = record.get("facts")
    facts_record = _factor_mapping_for_metadata(facts) or {}
    metadata_record = _factor_mapping_for_metadata(record.get("metadata")) or {}
    freshness = _freshness_quality(record.get("freshness_score", record.get("freshness")))
    if freshness is None:
        freshness = _freshness_quality(facts_record.get("freshness_score", facts_record.get("freshness")))
    if freshness is None:
        freshness = _freshness_quality(metadata_record.get("freshness_score", metadata_record.get("freshness")))
    source_quality = _source_quality(record)
    if source_quality is None:
        source_quality = _source_quality(facts_record)
    if source_quality is None:
        source_quality = _source_quality(metadata_record)
    redundancy = _quality_score(record.get("redundancy_score", record.get("redundancy")), "redundancy")
    if redundancy is None:
        redundancy = _redundancy_from_records(
            record.get("evidence", record.get("observations", metadata_record.get("evidence")))
        )
    evidence_ids = []
    for item in (record, facts_record, metadata_record):
        ids = item.get("evidence_ids", item.get("source_ids", ()))
        if isinstance(ids, (list, tuple)):
            evidence_ids.extend(str(identifier) for identifier in ids if str(identifier).strip())
    return _FactorMetadata(
        freshness=freshness,
        source_quality=source_quality,
        redundancy=redundancy,
        evidence_ids=tuple(dict.fromkeys(evidence_ids)),
    )


def _metadata_reliability(value: Any, factor: str, metadata: _FactorMetadata) -> float | None:
    record = _factor_mapping_for_metadata(value)
    if record is None or "facts" in record or "reliability" in record:
        return None
    if metadata.freshness is None and metadata.source_quality is None:
        return None
    completeness = record.get("coverage", 1.0)
    completeness = _reliability(completeness, f"factor {factor}.coverage")
    return completeness * (metadata.freshness if metadata.freshness is not None else 1.0) * (
        metadata.source_quality if metadata.source_quality is not None else 1.0
    )


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
        metadata = _factor_metadata(value)
        metadata_reliability = _metadata_reliability(value, factor, metadata)
        reliability = _reliability(
            value.get(
                "reliability",
                metadata_reliability
                if availability == "AVAILABLE" and metadata_reliability is not None
                else 1.0 if availability == "AVAILABLE" else 0.0,
            ),
            factor,
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
            raise ValueError("event_risk is not a base scoring factor")
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




def _score_factors(
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
        raise ValueError("scoring weights must sum to 1")
    unknown = sorted(set(factor_scores) - set(resolved_weights))
    if unknown:
        raise ValueError(f"unknown scoring factor(s): {', '.join(unknown)}")
    normalized_symbol = symbol.strip().upper()
    effective_scores: dict[str, float] = {}
    reliabilities: dict[str, float] = {}
    availability: dict[str, str] = {}
    metadata_by_factor: dict[str, _FactorMetadata] = {}
    missing: list[str] = []
    not_applicable: list[str] = []
    for factor, weight in resolved_weights.items():
        if weight == 0.0:
            state = "NOT_APPLICABLE"
            score = None
            factor_reliability = 0.0
        elif factor not in factor_scores:
            state = "NOT_APPLICABLE" if weight == 0 else "MISSING"
            score = None
            factor_reliability = 0.0
        else:
            score, state, factor_reliability = _extract(factor_scores[factor], factor)
        metadata_by_factor[factor] = _factor_metadata(factor_scores.get(factor))
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
    data_score = coverage
    confidence_caps: tuple[ConfidenceCap, ...] = ()
    reason_codes = set(f"MISSING_FACTOR:{factor}" for factor in missing)
    if not critical_data_complete:
        cap = policy.confidence.get("caps", {}).get("hard_critical_missing", math.nextafter(DEFAULT_MEDIUM_MIN, 0.0))
        confidence_caps = (ConfidenceCap("HARD_CRITICAL_DATA_INCOMPLETE", cap, "ASSET", "critical factor data is incomplete"),)
        data_score = min(data_score, cap)
        reason_codes.add("HARD_CRITICAL_DATA_INCOMPLETE")
    configured_dimensions = policy.confidence.get("data_dimension_weights", {}) if policy.confidence else {}
    configured_dimensions = configured_dimensions or {
        "coverage": 0.30,
        "freshness": 0.20,
        "source_quality": 0.20,
        "redundancy": 0.30,
    }
    dimension_values: dict[str, list[tuple[float, float]]] = {"freshness": [], "source_quality": [], "redundancy": []}
    dimension_evidence: dict[str, list[str]] = {"freshness": [], "source_quality": [], "redundancy": []}
    for factor, weight in resolved_weights.items():
        if weight <= 0:
            continue
        metadata = metadata_by_factor[factor]
        for name, value in (
            ("freshness", metadata.freshness),
            ("source_quality", metadata.source_quality),
            ("redundancy", metadata.redundancy),
        ):
            if value is not None:
                dimension_values[name].append((value, weight))
                dimension_evidence[name].extend(metadata.evidence_ids)
    dimension_scores = {"coverage": coverage}
    for name, values in dimension_values.items():
        if values:
            dimension_scores[name] = sum(value * weight for value, weight in values) / sum(weight for _, weight in values)
    included = tuple(name for name in configured_dimensions if name in dimension_scores)
    dimension_weight_total = sum(float(configured_dimensions[name]) for name in included)
    if dimension_weight_total <= 0:
        raise ValueError("data dimension weights must include an applicable dimension")
    data_dimensions = {
        name: ConfidenceDimension(
            name,
            dimension_scores[name],
            float(configured_dimensions[name]) / dimension_weight_total,
            tuple(sorted(reason_codes)) if name == "coverage" else (),
            tuple(dict.fromkeys(dimension_evidence.get(name, ()))),
        )
        for name in included
    }
    raw_data_score = sum(item.score * item.weight for item in data_dimensions.values())
    data_score = min(raw_data_score, confidence_caps[0].ceiling) if confidence_caps else raw_data_score
    medium = policy.confidence.get("band_thresholds", {}).get("medium_min", DEFAULT_MEDIUM_MIN) if policy.confidence else DEFAULT_MEDIUM_MIN
    high = policy.confidence.get("band_thresholds", {}).get("high_min", DEFAULT_HIGH_MIN) if policy.confidence else DEFAULT_HIGH_MIN
    factor_data_confidence = ConfidenceResult(
        raw_score=raw_data_score,
        score=data_score,
        band=confidence_band(data_score, medium_min=medium, high_min=high),
        dimensions=data_dimensions,
        caps=confidence_caps,
        reasons=tuple(sorted(reason_codes)),
        status="BLOCKED" if confidence_caps else "PROVISIONAL" if reason_codes else "AVAILABLE",
        medium_min=medium,
        high_min=high,
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
        factor_data_confidence=factor_data_confidence,
        data_confidence_score=data_score,
        data_confidence_band=confidence_band(data_score, medium_min=medium, high_min=high),
        confidence_reason_codes=tuple(sorted(reason_codes)),
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
    return _score_factors(
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
        score_coverage=result.coverage,
        confidence_score=result.data_confidence_score,
        confidence_explanation=result.factor_data_confidence.as_dict() if result.factor_data_confidence else None,
        data_confidence=result.factor_data_confidence,
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
