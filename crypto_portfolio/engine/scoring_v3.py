"""Scoring V3: market/structural split, conviction states, score spaces.

Strategy V2.1 Phase C. The single composite score stays the diagnostic
aggregate; two family sub-scores answer distinct questions:

- **market score** — is this asset worth tactical risk right now?
  (trend, BTC-relative strength, capital flows, optionally on-chain demand)
- **structural score** — is there a long-hold conviction case?
  (valuation, fundamentals, on-chain activity)

Each family aggregates exactly like the composite (reliability shrinkage
toward neutral 50, coverage = weight x reliability, coverage-normalized
rescale above the minimum-normalization gate), so no new score semantics are
invented. The conviction state combines the two families with the evidence
permission contract, and the comparison-score-space contract keeps
normalized-space threshold comparisons from ever falling back onto the
effective diagnostic score.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from ..models.policy import Policy, resolve_policy
from .scoring import _extract, coverage_normalized_score, low_evidence_contract

CONVICTION_STATES = (
    "FULL_CONVICTION",
    "TACTICAL_ONLY",
    "WATCH_ONLY",
    "NO_NEW_RISK",
    "HARD_EXIT",
)
SCORE_SPACES = ("NORMALIZED", "EFFECTIVE_DIAGNOSTIC_ONLY", "UNAVAILABLE")
_FAMILY_NAMES = ("market", "structural")


def comparison_score_space(normalized_score: Any, weighted_score: Any) -> str:
    """Which score space a threshold comparison is allowed to run in.

    ``NORMALIZED`` when the coverage-normalized score exists;
    ``EFFECTIVE_DIAGNOSTIC_ONLY`` when only the reliability-shrunk aggregate
    does (it may be reported, never compared against normalized-space
    thresholds); ``UNAVAILABLE`` when neither score exists.
    """
    if normalized_score is not None:
        return "NORMALIZED"
    if weighted_score is not None:
        return "EFFECTIVE_DIAGNOSTIC_ONLY"
    return "UNAVAILABLE"


def scoring_v3_families(symbol: str, policy: Policy | None = None) -> Mapping[str, Mapping[str, float]]:
    """Resolved market/structural factor weights for one asset.

    An explicit per-asset override (``asset_families``) wins over the profile
    families; both were validated by the policy model to sum to 1 per family.
    """
    resolved = policy or resolve_policy()
    block = resolved.scoring_v3 or {}
    name = str(symbol).strip().upper()
    override = (block.get("asset_families") or {}).get(name)
    if isinstance(override, Mapping):
        return override
    profile = resolved.scoring_profile_name(symbol)
    families = (block.get("families") or {}).get(profile)
    if not isinstance(families, Mapping):
        raise ValueError(f"scoring_v3 families are not defined for profile {profile!r}")
    return families


def family_score(
    factor_scores: Mapping[str, Any] | None,
    family: Mapping[str, float],
    *,
    minimum_coverage: float,
    symbol: str = "ASSET",
) -> dict[str, Any]:
    """Reliability-aware aggregate of one family, composite semantics.

    Factors missing from ``factor_scores`` (or MISSING) shrink toward neutral
    50 exactly like the composite does; the family coverage is the
    reliability-weighted share of the family, and the family-normalized score
    rescales against that coverage above the minimum-normalization gate.
    """
    if not isinstance(family, Mapping) or not family:
        raise ValueError("family weights must be a non-empty object")
    weights = {str(key).strip().lower(): float(value) for key, value in family.items()}
    if not math.isclose(sum(weights.values()), 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError("family weights must sum to 1")
    available = dict(factor_scores or {})
    effective: dict[str, float] = {}
    reliability: dict[str, float] = {}
    availability: dict[str, str] = {}
    for factor, weight in weights.items():
        value = available.get(factor)
        score, state, factor_reliability = _extract(value, factor)
        if str(symbol).strip().upper() == "BTC" and factor == "relative_strength_btc":
            if state in {"MISSING", "NOT_APPLICABLE"}:
                # BTC's profile gives this factor zero composite weight; the
                # family definition inherits that and shrinks it away.
                raise ValueError(
                    "BTC market family must not weight relative_strength_btc"
                )
        # A factor the composite profile marks NOT_APPLICABLE (zero weight,
        # never harvested) still carries family weight when the family wants
        # it: it shrinks toward neutral exactly like MISSING data, so a family
        # can anticipate evidence the composite does not yet collect.
        availability[factor] = state
        reliability[factor] = factor_reliability
        effective[factor] = (
            50.0 if score is None else 50.0 + factor_reliability * (score - 50.0)
        )
    score_value = sum(weights[factor] * effective[factor] for factor in weights)
    coverage = sum(weights[factor] * reliability[factor] for factor in weights)
    normalization = coverage_normalized_score(
        score_value, coverage, minimum_coverage=minimum_coverage
    )
    return {
        "score": score_value,
        "coverage": coverage,
        "normalized_score": normalization["normalized"],
        "available": bool(normalization["available"]),
        "factor_availability": availability,
        "factor_reliability": reliability,
    }


def asset_family_scores(
    assessment_or_factors: Any,
    *,
    policy: Policy | None = None,
    symbol: str = "ASSET",
) -> dict[str, Any]:
    """Market/structural family results for one asset assessment.

    Accepts an assessment (typed or mapping) or a bare factor-score mapping.
    Returns both families' effective scores, coverages, and normalized scores;
    an unavailable family keeps ``normalized_score: None``/``available False``.
    """
    resolved = policy or resolve_policy()
    factors = assessment_or_factors
    if isinstance(factors, Mapping) and "factor_scores" in factors:
        factors = factors.get("factor_scores")
    elif not isinstance(factors, Mapping) and hasattr(assessment_or_factors, "factor_scores"):
        factors = assessment_or_factors.factor_scores
    if not isinstance(factors, Mapping):
        factors = {}
    families = scoring_v3_families(symbol, resolved)
    minimum = float(resolved.scoring.get("minimum_normalization_coverage", 0.0))
    result: dict[str, Any] = {}
    for name in _FAMILY_NAMES:
        family = families.get(name)
        if not isinstance(family, Mapping) or not family:
            result[name] = {
                "score": None, "coverage": 0.0, "normalized_score": None,
                "available": False, "factor_availability": {}, "factor_reliability": {},
            }
            continue
        result[name] = family_score(
            factors, family, minimum_coverage=minimum, symbol=symbol
        )
    return result


def conviction_state(
    *,
    market: Mapping[str, Any],
    structural: Mapping[str, Any],
    evidence_class: str,
    hard_risk: bool,
    market_entry_score: float,
    structural_conviction_score: float,
) -> str:
    """Conviction state from the two families and the evidence contract.

    Strategy V2.3 Phase 0 semantics: ``HARD_EXIT`` (broken thesis / severe
    event / hard risk) and ``NO_NEW_RISK`` (evidence not actionable) are
    gates. A strong normalized market score with a STRONG normalized
    structural score (at or above its own threshold — availability alone is
    not bullishness) is ``FULL_CONVICTION``; a strong market score with the
    structural case missing, weak, or neutral is ``TACTICAL_ONLY``; a weak
    market score is ``WATCH_ONLY`` regardless of structure.
    """
    if str(evidence_class) not in {"ACTIONABLE", "LIMITED", "NOT_ACTIONABLE"}:
        raise ValueError("evidence_class is unsupported")
    if hard_risk:
        return "HARD_EXIT"
    if evidence_class == "NOT_ACTIONABLE":
        return "NO_NEW_RISK"
    market_normalized = market.get("normalized_score")
    market_strong = (
        market_normalized is not None and float(market_normalized) >= float(market_entry_score)
    )
    if not market_strong:
        return "WATCH_ONLY"
    structural_normalized = structural.get("normalized_score")
    structural_strong = (
        bool(structural.get("available"))
        and structural_normalized is not None
        and float(structural_normalized) >= float(structural_conviction_score)
    )
    return "FULL_CONVICTION" if structural_strong else "TACTICAL_ONLY"


def asset_conviction_state(
    assessment: Any,
    *,
    policy: Policy | None = None,
    symbol: str = "ASSET",
) -> dict[str, Any]:
    """Full Scoring V3 attribution block for one assessment.

    Combines the family scores, the evidence-permission class, and the hard
    risk gates into the conviction state plus every attribution field the
    allocation layer and reports need. All inputs come from the assessment
    itself, so replay and production derive identical output.
    """
    resolved = policy or resolve_policy()

    def field(name: str, default=None):
        if isinstance(assessment, Mapping):
            return assessment.get(name, default)
        return getattr(assessment, name, default)

    factor_detail = field("factor_scores")
    factor_detail_present = isinstance(factor_detail, Mapping) and bool(factor_detail)
    families = asset_family_scores(assessment, policy=resolved, symbol=symbol)
    coverage = field("score_coverage")
    coverage = 1.0 if coverage is None else float(coverage)
    critical_complete = field("critical_data_complete", True)
    critical_complete = True if critical_complete is None else bool(critical_complete)
    # Evidence permission evaluates the evidence the decision actually
    # depends on. With structural evidence available that is the composite
    # coverage; with the structural case missing it is the market family's
    # own coverage — a complete tactical case must not be classified
    # NOT_ACTIONABLE merely because structural factors are absent.
    permission_coverage = coverage
    if factor_detail_present and not families["structural"]["available"]:
        permission_coverage = float(families["market"]["coverage"])
    contract = low_evidence_contract(
        coverage=permission_coverage, critical_data_complete=critical_complete, policy=resolved
    )
    event_risk = field("event_risk")
    if isinstance(event_risk, Mapping):
        event_risk = event_risk.get("state")
    event_state = str(event_risk or "NORMAL").strip().upper()
    thesis_broken = bool(field("thesis_broken", False))
    hard_risk = event_state in {"SEVERE", "CRITICAL"} or thesis_broken
    conviction_thresholds = (
        (resolved.scoring_v3 or {}).get("conviction")
        if isinstance(resolved.scoring_v3, Mapping) else None
    ) or {}
    market_entry_score = float(conviction_thresholds["market_entry_threshold"])
    structural_conviction_score = float(conviction_thresholds["structural_conviction_threshold"])
    state = conviction_state(
        market=families["market"],
        structural=families["structural"],
        evidence_class=contract["evidence_class"],
        hard_risk=hard_risk,
        market_entry_score=market_entry_score,
        structural_conviction_score=structural_conviction_score,
    )
    normalized = field("normalized_score")
    weighted = field("weighted_score")
    structural_normalized = families["structural"]["normalized_score"]
    return {
        "conviction_state": state,
        "market_score": families["market"]["score"],
        "market_coverage": families["market"]["coverage"],
        "normalized_market_score": families["market"]["normalized_score"],
        "structural_score": families["structural"]["score"],
        "structural_coverage": families["structural"]["coverage"],
        "normalized_structural_score": structural_normalized,
        # V2.3 Phase 0: structural strength is its own condition — structural
        # availability is never read as structural bullishness.
        "structural_strong": (
            families["structural"]["available"]
            and structural_normalized is not None
            and float(structural_normalized) >= structural_conviction_score
        ),
        # Entry-critical evidence on the market side: a MISSING market factor
        # means the tactical case itself is unevidenced (fail-defensive
        # preserve), while MISSING structural factors only cap conviction.
        "missing_market_factors": tuple(
            factor for factor, availability in families["market"]["factor_availability"].items()
            if availability == "MISSING"
        ) if factor_detail_present else (),
        "factor_detail_present": factor_detail_present,
        "evidence_class": contract["evidence_class"],
        "comparison_score_space": comparison_score_space(normalized, weighted),
        "market_entry_score": market_entry_score,
        "structural_conviction_score": structural_conviction_score,
    }


__all__ = [
    "CONVICTION_STATES",
    "SCORE_SPACES",
    "asset_conviction_state",
    "asset_family_scores",
    "comparison_score_space",
    "conviction_state",
    "family_score",
    "scoring_v3_families",
]
