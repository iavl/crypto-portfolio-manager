"""Deterministic core-asset eligibility gates for policy v3."""

from __future__ import annotations

import math
from typing import Any, Mapping

from ..models.evidence import AssetAssessment, EventRiskAssessment
from ..models.policy import Policy, resolve_policy


CORE_ELIGIBILITY_STATES = (
    "ELIGIBLE_INCREASE",
    "HOLD_ONLY",
    "UNDERWEIGHT",
    "REDUCE",
    "INELIGIBLE",
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be boolean")


def _score(assessment: Any) -> float:
    raw = _field(assessment, "weighted_score", 50.0)
    if raw is None:
        raw = 50.0
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("core assessment score must be numeric")
    result = float(raw)
    if not math.isfinite(result) or not 0 <= result <= 100:
        raise ValueError("core assessment score must be finite and in [0, 100]")
    return result


def _confidence(assessment: Any) -> str:
    result = str(_field(assessment, "confidence", "LOW")).strip().upper()
    if result not in {"HIGH", "MEDIUM", "LOW"}:
        raise ValueError("core assessment confidence must be HIGH, MEDIUM, or LOW")
    return result


def _event_state(assessment: Any) -> str:
    raw = _field(assessment, "event_risk")
    if isinstance(raw, EventRiskAssessment):
        state = raw.state
    elif isinstance(raw, Mapping):
        state = raw.get("state")
    else:
        state = raw
    state = str(state or "NORMAL").strip().upper()
    if state not in {"NORMAL", "ELEVATED", "HIGH", "SEVERE", "CRITICAL"}:
        raise ValueError("core assessment event_risk is unsupported")
    return state


def relative_strength_score(assessment: Any) -> float | None:
    """Normalize the explicit ETH/BTC opportunity score to 0..100."""
    raw = _field(assessment, "relative_strength_vs_btc")
    if raw is None and isinstance(assessment, AssetAssessment):
        factor = assessment.factor_scores.get("relative_strength_btc")
        raw = factor.score if hasattr(factor, "score") else factor
    if raw is None and isinstance(assessment, Mapping):
        factors = assessment.get("factor_scores")
        if isinstance(factors, Mapping):
            factor = factors.get("relative_strength_btc")
            raw = factor.get("score") if isinstance(factor, Mapping) else getattr(factor, "score", factor)
    if raw is None:
        return None
    if isinstance(raw, str):
        mapping = {
            "OUTPERFORM": 70.0,
            "NEUTRAL": 50.0,
            "UNDERPERFORM": 30.0,
            "MATERIALLY_WEAK": 20.0,
            "UNKNOWN": None,
        }
        if raw.strip().upper() not in mapping:
            raise ValueError("relative_strength_vs_btc is unsupported")
        return mapping[raw.strip().upper()]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("relative_strength_vs_btc must be numeric or a supported state")
    result = float(raw)
    if not math.isfinite(result):
        raise ValueError("relative_strength_vs_btc must be finite")
    if 0 <= result <= 1:
        result *= 100
    if not 0 <= result <= 100:
        raise ValueError("relative_strength_vs_btc score must be in [0, 100]")
    return result


def _structural_state(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, Mapping):
        value = value.get("state")
    elif hasattr(value, "state"):
        value = value.state
    state = str(value).strip().upper()
    if state in {"SEVERE", "CRITICAL", "HALTED", "CONFLICT"}:
        return state
    return "NORMAL"


def eth_core_eligibility(
    assessment: AssetAssessment | Mapping[str, Any] | None,
    policy: Policy | None = None,
    *,
    current_weight: float = 0.0,
    relative_score: float | None = None,
    chain_liveness: str | None = None,
    structural_risk: Any = None,
) -> str:
    """Return the v3 ETH core state without changing the base score."""
    resolved = policy or resolve_policy()
    if resolved.policy_version != 3:
        raise ValueError("ETH core eligibility requires policy v3")
    if isinstance(current_weight, bool) or not isinstance(current_weight, (int, float)) or not math.isfinite(float(current_weight)) or current_weight < 0:
        raise ValueError("current_weight must be finite and >= 0")
    assessment = assessment or {}
    if _bool(_field(assessment, "thesis_broken", False), "thesis_broken"):
        return "INELIGIBLE"
    event = _event_state(assessment)
    if event in {"SEVERE", "CRITICAL"}:
        return "INELIGIBLE"
    liveness = chain_liveness
    if isinstance(liveness, Mapping):
        liveness = liveness.get("status")
    if liveness is not None and str(liveness).strip().upper() == "HALTED":
        return "INELIGIBLE"
    if _structural_state(structural_risk or _field(assessment, "structural_risk")) in {"SEVERE", "CRITICAL", "HALTED", "CONFLICT"}:
        return "INELIGIBLE"
    if not _bool(_field(assessment, "critical_data_complete", True), "critical_data_complete"):
        return "HOLD_ONLY"
    score = _score(assessment)
    config = resolved.core_allocation
    eth = config["eth"]
    if score < eth["hold_min_score"]:
        return "REDUCE"
    relative = relative_strength_score(assessment) if relative_score is None else relative_score
    if relative is not None:
        relative = float(relative)
        if 0 <= relative <= 1:
            relative *= 100
        if not math.isfinite(relative) or not 0 <= relative <= 100:
            raise ValueError("relative_score must be finite and in [0, 100]")
    if relative is None:
        return "HOLD_ONLY"
    if relative < eth["relative_reduce_below_score"]:
        return "REDUCE"
    if relative < eth["relative_increase_min_score"]:
        return "UNDERWEIGHT"
    if score < eth["increase_min_score"]:
        return "HOLD_ONLY"
    if _confidence(assessment) == "LOW":
        return "HOLD_ONLY"
    return "ELIGIBLE_INCREASE"


__all__ = ["CORE_ELIGIBILITY_STATES", "eth_core_eligibility", "relative_strength_score"]
