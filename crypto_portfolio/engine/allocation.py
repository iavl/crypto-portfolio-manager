"""Deterministic bounded target-allocation rules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.evidence import AssetAssessment, EventRiskAssessment
from ..models.market_overlays import MarketOverlays
from ..models.policy import Policy, RegimeLimits, resolve_policy
from .confidence import confidence_deployment_factor
from .core_eligibility import eth_core_eligibility
from .scoring import score_assessment


@dataclass(frozen=True)
class AllocationResult:
    target_weights: Mapping[str, float]
    allocation_reasons: tuple[str, ...]
    constraints_applied: tuple[str, ...]
    stable_sleeve_target: float = 0.0
    deployment_allowances: Mapping[str, Mapping[str, Any]] | None = None

    @property
    def strategic_target_weights(self) -> Mapping[str, float]:
        return self.target_weights

    @property
    def deployment_factors(self) -> Mapping[str, float]:
        return {
            symbol: float(value["deployment_factor"])
            for symbol, value in (self.deployment_allowances or {}).items()
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_weights": dict(self.target_weights),
            "allocation_reasons": list(self.allocation_reasons),
            "constraints_applied": list(self.constraints_applied),
            "stable_sleeve_target": self.stable_sleeve_target,
            "strategic_target_weights": dict(self.strategic_target_weights),
            "deployment_allowances": {
                symbol: dict(value)
                for symbol, value in (self.deployment_allowances or {}).items()
            },
        }


def _score(value: Any, symbol: str) -> float:
    if isinstance(value, AssetAssessment):
        if value.weighted_score is not None:
            return value.weighted_score
        raise ValueError("typed assessment must be scored before allocation")
    else:
        if isinstance(value, Mapping):
            raw = value.get("weighted_score", 50.0)
            if raw is None:
                raw = 50.0
        else:
            raw = value
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"assessment score for {symbol} must be a number")
    raw = float(raw)
    if not math.isfinite(raw) or not 0 <= raw <= 100:
        raise ValueError(f"assessment score for {symbol} must be finite and in [0, 100]")
    return raw


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _flag(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", "FALSE", "false", 0):
        return False
    if value in ("TRUE", "true", 1):
        return True
    raise ValueError(f"{field} must be boolean")


def _confidence(value: Any) -> str:
    result = str(value).upper()
    if result not in {"HIGH", "MEDIUM", "LOW"}:
        raise ValueError("assessment confidence must be HIGH, MEDIUM, or LOW")
    return result


def _relative_eligibility(value: Any) -> str:
    """Classify the BTC-relative comparison on the canonical 0-100 score unit.

    Numeric values are factor scores: below 50 is the confirmed
    UNDERPERFORM case (no new risk), at or above 50 is OUTPERFORM/NEUTRAL.
    Excess returns are never read here; they are horizon-scoped fraction
    facts on the relative-strength result. ``None``/UNKNOWN means the
    comparison is missing and maps to HOLD_ONLY.
    """
    if value is None:
        return "HOLD_ONLY"
    if isinstance(value, str):
        state = value.strip().upper()
        if state in {"", "UNKNOWN"}:
            return "HOLD_ONLY"
        if state in {"UNDERPERFORM", "MATERIALLY_WEAK"}:
            return "INELIGIBLE"
        if state in {"OUTPERFORM", "NEUTRAL"}:
            return "ELIGIBLE"
        raise ValueError("relative_strength_vs_btc is unsupported")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("relative_strength_vs_btc must be numeric or a supported state")
    score = float(value)
    if not math.isfinite(score):
        raise ValueError("relative_strength_vs_btc must be finite")
    if not 0 <= score <= 100:
        raise ValueError("relative_strength_vs_btc score must be in [0, 100]")
    return "ELIGIBLE" if score >= 50 else "INELIGIBLE"


def _core_quality_multiplier(score: float) -> float:
    return min(1.5, max(0.5, 0.5 + score / 100.0))


def _btc_core_state(assessment: Any) -> str:
    if _flag(_field(assessment, "thesis_broken", False), "thesis_broken"):
        return "INELIGIBLE"
    event = _event_risk_state(assessment)
    if event in {"SEVERE", "CRITICAL"}:
        return "INELIGIBLE"
    liveness = _field(assessment, "chain_liveness")
    if isinstance(liveness, Mapping):
        liveness = liveness.get("status")
    if liveness is not None and str(liveness).strip().upper() in {"HALTED", "UNKNOWN", "FAILED", "CONFLICT"}:
        return "HOLD_ONLY"
    if not _flag(_field(assessment, "critical_data_complete", True), "critical_data_complete"):
        return "HOLD_ONLY"
    return "HOLD_ONLY" if _confidence(_field(assessment, "confidence", "MEDIUM")) == "LOW" else "ELIGIBLE_INCREASE"


def _allocate_core(
    policy: Policy,
    budget: float,
    assessments: Mapping[str, Any],
    current_weights: Mapping[str, float],
    single_asset_cap: float,
    chain_liveness: Mapping[str, Any] | None = None,
    structural_risk: Mapping[str, Any] | None = None,
) -> tuple[dict[str, float], float, tuple[str, ...]]:
    config = policy.core_allocation
    confidence_multipliers = config["confidence_multipliers"]
    anchor = config["anchor"]
    raw: dict[str, float] = {}
    states: dict[str, str] = {}
    for symbol in policy.core_symbols:
        assessment = assessments.get(symbol)
        score = _score(assessment, symbol) if assessment is not None else 50.0
        confidence = _confidence(_field(assessment, "confidence", "MEDIUM")) if assessment is not None else "MEDIUM"
        event_state = _event_risk_state(assessment) if assessment is not None else "NORMAL"
        event_multiplier = _event_risk_multiplier(event_state, policy)
        if symbol == "ETH":
            supplied_structural = (structural_risk or {}).get(symbol)
            if supplied_structural is None and assessment is not None:
                supplied_structural = _field(assessment, "structural_risk", None)
            state = eth_core_eligibility(
                assessment,
                policy,
                current_weight=current_weights.get(symbol, 0.0),
                chain_liveness=(chain_liveness or {}).get(symbol),
                structural_risk=supplied_structural,
            )
        else:
            state = _btc_core_state(assessment)
        states[symbol] = state
        if state == "INELIGIBLE":
            continue
        raw[symbol] = (
            anchor.get(symbol, 0.0)
            * _core_quality_multiplier(score)
            * confidence_multipliers[confidence]
            * event_multiplier
        )
    positive = {symbol: value for symbol, value in raw.items() if value > 0}
    if not positive or budget <= 0:
        return {}, max(0.0, budget), tuple(f"{symbol} core state {state}" for symbol, state in states.items())
    total_raw = sum(positive.values())
    desired = {symbol: budget * value / total_raw for symbol, value in positive.items()}
    caps = {symbol: single_asset_cap for symbol in desired}
    if "BTC" in caps and states.get("BTC") == "HOLD_ONLY":
        caps["BTC"] = min(caps["BTC"], current_weights.get("BTC", 0.0))
    if "ETH" in caps:
        caps["ETH"] = min(caps["ETH"], budget * config["eth"]["max_core_sleeve_share"])
        if states.get("ETH") == "HOLD_ONLY":
            caps["ETH"] = min(caps["ETH"], current_weights.get("ETH", 0.0))
        elif states.get("ETH") == "UNDERWEIGHT":
            caps["ETH"] = min(caps["ETH"], current_weights.get("ETH", 0.0))
        elif states.get("ETH") == "REDUCE":
            caps["ETH"] = min(caps["ETH"], current_weights.get("ETH", 0.0) * 0.75)
    result = {symbol: min(desired[symbol], caps[symbol]) for symbol in desired}
    residual = max(0.0, budget - sum(result.values()))
    if residual > 1e-12 and states.get("ETH") in {"HOLD_ONLY", "UNDERWEIGHT", "REDUCE", "INELIGIBLE"} and states.get("BTC") == "ELIGIBLE_INCREASE":
        btc_capacity = max(0.0, caps.get("BTC", single_asset_cap) - result.get("BTC", 0.0))
        add = min(residual, btc_capacity)
        result["BTC"] = result.get("BTC", 0.0) + add
        residual -= add
    reasons = tuple(f"{symbol} core state {state}" for symbol, state in states.items())
    return {symbol: weight for symbol, weight in result.items() if weight > 1e-12}, residual, reasons


def _event_risk_state(value: Any) -> str:
    if isinstance(value, Mapping) and "severe_event" in value:
        raise ValueError("severe_event is unsupported; use event_risk.state")
    raw = _field(value, "event_risk", None)
    if isinstance(raw, EventRiskAssessment):
        raw = raw.state
    if isinstance(raw, Mapping):
        raw = raw.get("state")
    if raw is not None:
        state = str(raw).strip().upper()
        if state not in {"NORMAL", "ELEVATED", "HIGH", "SEVERE", "CRITICAL"}:
            raise ValueError("event_risk.state is unsupported")
        return state
    return "NORMAL"


def _event_risk_multiplier(state: str, policy: Policy) -> float:
    multipliers = policy.event_risk_multipliers
    try:
        return float(multipliers[state])
    except KeyError as exc:
        raise ValueError(f"unknown event risk state {state}") from exc


def _missing_factor_evidence(assessment: Any) -> bool:
    """True when the assessment itself reports a MISSING scoring factor.

    A scored assessment whose profile marks a positive-weight factor MISSING
    has incomplete evidence even when the caller left the coarse
    ``critical_data_complete`` flag at its default; the shrunk neutral score
    must not be mistaken for fully evidenced weakness. Raw assessments that
    carry no factor detail stay trusted on their flag.
    """
    if assessment is None:
        return False
    factors = _field(assessment, "factor_scores", None)
    if not isinstance(factors, Mapping):
        return False
    for value in factors.values():
        if value is None:
            return True
        availability = None
        if isinstance(value, Mapping):
            if str(value.get("state", "")).strip().upper() == "UNKNOWN":
                return True
            availability = value.get("availability")
        elif hasattr(value, "availability"):
            availability = value.availability
        if str(availability or "").strip().upper() == "MISSING":
            return True
    return False


def _incomplete_evidence(assessment: Any) -> bool:
    """Missing relative/critical/factor evidence blocks new risk fail-defensively."""
    if assessment is None:
        return True
    return (
        _relative_eligibility(_field(assessment, "relative_strength_vs_btc", None)) != "ELIGIBLE"
        or not _flag(_field(assessment, "critical_data_complete", True), "critical_data_complete")
        or _missing_factor_evidence(assessment)
    )


def _unresolved_event(assessment: Any) -> bool:
    event_risk = _field(assessment, "event_risk", None)
    if isinstance(event_risk, EventRiskAssessment):
        return bool(event_risk.unresolved)
    if isinstance(event_risk, Mapping):
        return _flag(event_risk.get("unresolved", False), "event_risk.unresolved")
    return False


_SATELLITE_STATES = {"ELIGIBLE_INCREASE", "HOLD_OR_REDUCE", "SOFT_EXIT", "INELIGIBLE"}


def satellite_target_fraction(
    score: float,
    *,
    soft_exit_score: float,
    exit_score: float,
    entry_score: float,
    full_score: float,
    curve: Mapping[str, float],
) -> float:
    """Piecewise-linear score-to-target fraction for satellite sizing.

    The curve is bounded to ``[0, 1]``, monotonic non-decreasing in score,
    continuous at every configured breakpoint, and independent of the
    current holding: it answers "what fraction of the satellite envelope
    does this score deserve", never "what do we currently own".  This is
    what removes the entry cliff: a held asset crossing
    ``satellite_entry_score`` stays on the same curve instead of dropping
    from preserve-current to a score-strength of zero.
    """
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("score must be a number")
    score = float(score)
    if not math.isfinite(score):
        raise ValueError("score must be finite")
    breakpoints = (soft_exit_score, exit_score, entry_score, full_score)
    if not all(
        isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item))
        for item in breakpoints
    ):
        raise ValueError("curve score breakpoints must be finite numbers")
    if not (float(soft_exit_score) < float(exit_score) < float(entry_score) < float(full_score)):
        raise ValueError("curve breakpoints must satisfy soft_exit < exit < entry < full")
    fractions = {
        name: curve.get(name)
        for name in ("soft_exit_fraction", "exit_fraction", "entry_fraction", "full_fraction")
    }
    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
        for value in fractions.values()
    ):
        raise ValueError("curve fractions must be finite numbers")
    values = [float(fractions[name]) for name in ("soft_exit_fraction", "exit_fraction", "entry_fraction", "full_fraction")]
    if any(value < 0.0 or value > 1.0 for value in values):
        raise ValueError("curve fractions must be in [0, 1]")
    if not (values[0] <= values[1] <= values[2] <= values[3]):
        raise ValueError("curve fractions must be non-decreasing from soft_exit to full")
    if values[3] != 1.0:
        raise ValueError("curve full_fraction must be 1.0")
    if score <= soft_exit_score:
        return values[0]
    if score >= full_score:
        return values[3]
    bounds = (float(soft_exit_score), float(exit_score), float(entry_score), float(full_score))
    for lower, upper, base, top in zip(bounds, bounds[1:], values, values[1:]):
        if score <= upper:
            span = upper - lower
            if span <= 0:
                return top
            return base + (score - lower) / span * (top - base)
    return values[3]


def satellite_eligibility(
    assessment: AssetAssessment | Mapping[str, Any] | None,
    policy: Policy | None = None,
    *,
    current_weight: float = 0.0,
) -> str:
    """Classify whether a satellite may receive new risk this review.

    Returns ``ELIGIBLE_INCREASE`` (new exposure may be added up to the
    strategic/deployment target), ``HOLD_OR_REDUCE`` (no new risk; an
    existing overweight may be reduced toward the strategic target),
    ``SOFT_EXIT`` (reduce existing exposure gradually along the target
    curve), or ``INELIGIBLE`` (target zero / exit according to hard-risk
    rules).  Eligibility gates deployment, never the shape of the target
    curve, so a state transition at the entry score cannot collapse target
    sizing.
    """
    resolved = policy or resolve_policy()
    if isinstance(assessment, AssetAssessment) and assessment.weighted_score is None:
        assessment, _ = score_assessment(assessment, policy=resolved)
    score = _score(assessment, "satellite") if assessment is not None else 50.0
    relative = _field(assessment, "relative_strength_vs_btc") if assessment is not None else None
    if isinstance(current_weight, bool) or not isinstance(current_weight, (int, float)):
        raise ValueError("current_weight must be numeric")
    if not math.isfinite(float(current_weight)) or current_weight < 0:
        raise ValueError("current_weight must be finite and >= 0")
    entry_score = resolved.allocation["satellite_entry_score"]
    exit_score = resolved.allocation.get("satellite_exit_score", entry_score)
    soft_exit_score = resolved.allocation.get("satellite_soft_exit_score", exit_score)
    held = current_weight > 0
    # Confirmed hard risk first: a broken thesis or severe event is never
    # masked by data availability in either direction.
    if _event_risk_state(assessment) in {"SEVERE", "CRITICAL"} or _flag(
        _field(assessment, "thesis_broken", False), "thesis_broken"
    ):
        return "INELIGIBLE"
    relative_status = _relative_eligibility(relative)
    # A confirmed materially-negative BTC-relative case is ineligible at any
    # score; missing evidence is the opposite signal and handled below.
    if relative_status == "INELIGIBLE":
        return "INELIGIBLE"
    if _incomplete_evidence(assessment) or _unresolved_event(assessment):
        # Missing factors shrink the score toward neutral; the neutral score
        # alone must not manufacture an exit for a held position, and an
        # unheld one receives no target at all.
        return "HOLD_OR_REDUCE" if held else "INELIGIBLE"
    if score < soft_exit_score:
        # Evidence-complete hard score floor: the exit is supported by the
        # score itself, not by missing data.
        return "INELIGIBLE"
    if score < entry_score:
        if relative_status == "HOLD_ONLY":
            # Moderate BTC-relative weakness: no new risk on that evidence,
            # but a held position is reduced normally, not forced out.
            return "HOLD_OR_REDUCE" if held else "INELIGIBLE"
        if score < exit_score:
            # A held satellite inside the soft-exit band de-risks gradually
            # along the curve instead of facing the full-exit cliff.
            return "SOFT_EXIT" if held else "INELIGIBLE"
        return "HOLD_OR_REDUCE" if held else "INELIGIBLE"
    if relative_status == "HOLD_ONLY":
        return "HOLD_OR_REDUCE"
    return "ELIGIBLE_INCREASE"


def _bounded_allocate(
    raw: Mapping[str, float], budget: float, cap: float
) -> tuple[dict[str, float], float]:
    result: dict[str, float] = {}
    active = {symbol: weight for symbol, weight in raw.items() if weight > 0}
    remaining = min(budget, sum(active.values()))
    while active and remaining > 1e-12:
        total_raw = sum(active.values())
        capped = [symbol for symbol, weight in active.items() if remaining * weight / total_raw > cap]
        if not capped:
            for symbol, weight in active.items():
                result[symbol] = result.get(symbol, 0.0) + remaining * weight / total_raw
            remaining = 0.0
            break
        for symbol in capped:
            result[symbol] = result.get(symbol, 0.0) + cap
            remaining -= cap
            del active[symbol]
    return result, max(0.0, remaining)


def _stable_targets(
    stable_symbols: tuple[str, ...], target: float, current_weights: Mapping[str, float]
) -> dict[str, float]:
    current = {
        symbol: max(0.0, float(current_weights.get(symbol, 0.0)))
        for symbol in stable_symbols
        if float(current_weights.get(symbol, 0.0)) > 0
    }
    if current:
        total = sum(current.values())
        return {symbol: target * weight / total for symbol, weight in current.items()}
    return {stable_symbols[0]: target}


def _assessment_symbols(
    assessments: Mapping[str, AssetAssessment | Mapping[str, Any] | float], policy: Policy
) -> list[str]:
    symbols = list(policy.core_symbols) + list(policy.satellite_symbols)
    for raw_symbol in assessments:
        symbol = str(raw_symbol).strip().upper()
        if policy.is_excluded(symbol):
            continue
        if symbol not in symbols and policy.classify(symbol) in {"core", "satellite"}:
            symbols.append(symbol)
    return symbols


def build_target_allocation(
    policy: Policy | None = None,
    regime: str = "NORMAL",
    assessments: Mapping[str, AssetAssessment | Mapping[str, Any] | float] | None = None,
    current_weights: Mapping[str, float] | None = None,
    *,
    overlays: MarketOverlays | Mapping[str, Any] | None = None,
    chain_liveness: Mapping[str, Any] | None = None,
    structural_risk: Mapping[str, Any] | None = None,
    decision_confidence: Any | None = None,
) -> AllocationResult:
    resolved = policy or resolve_policy()
    parsed_overlays = None
    if overlays is not None:
        parsed_overlays = overlays if isinstance(overlays, MarketOverlays) else MarketOverlays.from_mapping(overlays)
    regime_name = regime.regime if hasattr(regime, "regime") else str(regime).upper()
    limits: RegimeLimits = resolved.regime(regime_name)
    if not resolved.stable_symbols:
        raise ValueError("policy must define at least one stable symbol")
    assessments = assessments or {}
    decision_confidence_factor = 1.0
    if decision_confidence is not None:
        score = getattr(decision_confidence, "score", None) if not isinstance(decision_confidence, Mapping) else decision_confidence.get("score", decision_confidence.get("confidence_score"))
        if score is not None:
            score = float(score)
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("decision_confidence score must be finite and in [0, 1]")
            decision_confidence_factor = confidence_deployment_factor(score, resolved)
    current_weights = current_weights or {}
    normalized_current_weights: dict[str, float] = {}
    for raw_symbol, raw_weight in current_weights.items():
        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            raise ValueError("current_weights contains an invalid symbol")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError("current_weights must contain numbers")
        weight = float(raw_weight)
        symbol = raw_symbol.strip().upper()
        if symbol in normalized_current_weights:
            raise ValueError(f"current_weights contains duplicate symbol {symbol}")
        normalized_current_weights[symbol] = weight
    current_weights = normalized_current_weights
    if any(not math.isfinite(weight) or weight < 0 or weight > 1 for weight in current_weights.values()):
        raise ValueError("current_weights must contain finite values in [0, 1]")
    if sum(current_weights.values()) > 1.0 + 1e-9:
        raise ValueError("current_weights must sum to no more than 1")

    stable_target = max(resolved.min_stablecoin_weight, limits.stablecoin_target)
    risky_budget = 1.0 - stable_target
    satellite_cap = min(limits.satellite_max, risky_budget)
    candidates = _assessment_symbols(assessments, resolved)
    normalized_assessments = {
        str(symbol).strip().upper(): value for symbol, value in assessments.items()
    }
    reasons = [f"{regime_name} reserves {stable_target:.2%} for stablecoin/cash"]
    constraints = [
        f"stablecoin floor {resolved.min_stablecoin_weight:.2%}",
        f"satellite cap {satellite_cap:.2%}",
        f"single-asset cap {limits.single_asset_max:.2%}",
    ]

    strategic_satellite_raw: dict[str, float] = {}
    # Preserve-existing bucket: HOLD_ONLY keeps the full current weight while
    # SOFT_EXIT keeps the configured fraction of it.
    satellite_hold: dict[str, float] = {}
    deployment_allowances: dict[str, dict[str, Any]] = {}
    core_assessments: dict[str, Any] = {}
    for symbol in candidates:
        asset_type = resolved.classify(symbol)
        assessment = normalized_assessments.get(symbol)
        if isinstance(assessment, AssetAssessment) and assessment.weighted_score is None:
            assessment, _ = score_assessment(assessment, policy=resolved)
        supplied_type = _field(assessment, "asset_type", None) if assessment is not None else None
        if supplied_type is not None and not isinstance(assessment, AssetAssessment):
            supplied_type = str(supplied_type).lower()
            if supplied_type != asset_type:
                raise ValueError(
                    f"assessment {symbol} asset_type {supplied_type!r} conflicts with policy {asset_type!r}"
                )
        elif isinstance(assessment, AssetAssessment) and assessment.asset_type != "other" and assessment.asset_type != asset_type:
            raise ValueError(
                f"assessment {symbol} asset_type {assessment.asset_type!r} conflicts with policy {asset_type!r}"
            )
        score = _score(assessment, symbol) if assessment is not None else 50.0
        confidence = _confidence(_field(assessment, "confidence", "MEDIUM")) if assessment is not None else "MEDIUM"
        event_risk = _event_risk_state(assessment) if assessment is not None else "NORMAL"
        event_multiplier = _event_risk_multiplier(event_risk, resolved)
        if asset_type == "satellite":
            # Distinguish where the tier came from, independent of whether
            # the assessment arrived typed or as a mapping: an explicit
            # source wins; otherwise a non-default tier implies a human
            # assessment and the default "normal" tier is the policy's own.
            supplied_tier = _field(assessment, "risk_tier", None) if assessment is not None else None
            risk_tier = str(supplied_tier).lower() if supplied_tier is not None else "normal"
            risk_multipliers = resolved.allocation["risk_multipliers"]
            risk_multiplier = risk_multipliers.get(
                risk_tier, risk_multipliers.get(risk_tier.replace("-", "_"), 1.0)
            )
            asset_confidence_factor = float(
                resolved.execution["confidence_deployment_factor"].get(confidence, 1.0)
            )
            execution_overlay_factor = (
                float(parsed_overlays.effective_deployment_caps.get(symbol, 1.0))
                if parsed_overlays is not None else 1.0
            )
            risk_tier_source = _field(assessment, "risk_tier_source", None) if assessment is not None else None
            if risk_tier_source is None:
                risk_tier_source = (
                    "MANUAL_ASSESSMENT" if risk_tier not in {"", "normal"} else "POLICY_DEFAULT"
                )
            risk_tier_source = str(risk_tier_source).strip().upper()
            if risk_tier_source not in {"POLICY_DEFAULT", "MANUAL_ASSESSMENT", "DETERMINISTIC_ESTIMATE"}:
                raise ValueError(
                    "risk_tier_source must be POLICY_DEFAULT, MANUAL_ASSESSMENT, or DETERMINISTIC_ESTIMATE"
                )
            held_weight = normalized_current_weights.get(symbol, 0.0)
            state = satellite_eligibility(
                assessment,
                resolved,
                current_weight=held_weight,
            )
            curve_fraction = satellite_target_fraction(
                score,
                soft_exit_score=resolved.allocation["satellite_soft_exit_score"],
                exit_score=resolved.allocation["satellite_exit_score"],
                entry_score=resolved.allocation["satellite_entry_score"],
                full_score=resolved.allocation["satellite_full_score"],
                curve=resolved.allocation["satellite_target_curve"],
            )
            requested_strategic_weight = satellite_cap * curve_fraction * risk_multiplier
            deployment_factor = (
                min(
                    asset_confidence_factor,
                    event_multiplier,
                    decision_confidence_factor,
                    execution_overlay_factor,
                )
                if state == "ELIGIBLE_INCREASE"
                else 0.0
            )
            deployment_allowances[symbol] = {
                "profile_name": resolved.scoring_profile_name(symbol),
                "satellite_cap": satellite_cap,
                "score": score,
                "curve_fraction": curve_fraction,
                "current_weight": held_weight,
                "eligibility_state": state,
                "risk_tier": risk_tier,
                "risk_tier_source": risk_tier_source,
                "risk_multiplier": risk_multiplier,
                "asset_confidence": confidence,
                "confidence_deployment_factor": asset_confidence_factor,
                "event_risk": event_risk,
                "event_risk_deployment_factor": event_multiplier,
                "decision_confidence_factor": decision_confidence_factor,
                "execution_overlay_factor": execution_overlay_factor,
                "deployment_factor": deployment_factor,
                "requested_strategic_weight": requested_strategic_weight,
            }
            if state == "INELIGIBLE":
                reasons.append(
                    f"{symbol} receives 0% satellite target because hard eligibility failed "
                    "(score floor, broken thesis, severe event, or confirmed severe BTC-relative weakness)"
                )
            elif _incomplete_evidence(assessment) or _unresolved_event(assessment):
                # Fail-defensive preserve bucket: missing evidence blocks new
                # risk and never manufactures an exit for a held position.
                if held_weight > 0:
                    satellite_hold[symbol] = held_weight
                    deployment_allowances[symbol]["preserve_existing"] = True
                    reasons.append(
                        f"{symbol} is HOLD_OR_REDUCE because BTC-relative or critical evidence is "
                        "incomplete; the existing position is preserved without adding risk"
                    )
                else:
                    reasons.append(
                        f"{symbol} has incomplete evidence and no position; no satellite target is created"
                    )
            elif state == "ELIGIBLE_INCREASE":
                strategic_satellite_raw[symbol] = requested_strategic_weight
                if event_multiplier < 1.0:
                    reasons.append(
                        f"{symbol} event-risk state {event_risk} limits immediate deployment to {event_multiplier:.0%}"
                    )
                if asset_confidence_factor < 1.0:
                    reasons.append(f"{symbol} immediate deployment is capped by asset confidence at {asset_confidence_factor:.0%}")
                if decision_confidence_factor < 1.0:
                    reasons.append(f"{symbol} new deployment is capped by portfolio decision confidence at {decision_confidence_factor:.0%}")
            elif held_weight > 0:
                # Evidence-complete sub-entry band: the strategic target rides
                # the same score curve, so crossing the entry score later
                # cannot jump the target, and deployment stays blocked.
                strategic_satellite_raw[symbol] = requested_strategic_weight
                band = "the soft-exit band" if state == "SOFT_EXIT" else "the sub-entry band"
                reasons.append(
                    f"{symbol} is {state}: the strategic target follows the score curve through "
                    f"{band} and no new risk may be added this review"
                )
            else:
                reasons.append(
                    f"{symbol} is {state} without a position; no satellite target is created below the entry score"
                )
        elif asset_type == "core":
            core_assessments[symbol] = assessment

    held_satellite_weights, _ = _bounded_allocate(
        satellite_hold, satellite_cap, limits.single_asset_max
    )
    eligible_satellite_budget = max(0.0, satellite_cap - sum(held_satellite_weights.values()))
    satellite_weights, _ = _bounded_allocate(
        strategic_satellite_raw, eligible_satellite_budget, limits.single_asset_max
    )
    satellite_weights = {
        symbol: held_satellite_weights.get(symbol, 0.0) + satellite_weights.get(symbol, 0.0)
        for symbol in set(held_satellite_weights) | set(satellite_weights)
    }
    for symbol, details in deployment_allowances.items():
        strategic_weight = satellite_weights.get(symbol, 0.0)
        details["strategic_target_weight"] = strategic_weight
        # Capacity competition context: the envelope this satellite competed
        # in, how much preserve-existing buckets already consumed, and the
        # pre-competition strategic weight it requested.
        details["satellite_envelope"] = satellite_cap
        details["held_satellite_weight"] = sum(held_satellite_weights.values())
        details["eligible_satellite_budget"] = eligible_satellite_budget
        details["envelope_competition_weight"] = strategic_satellite_raw.get(symbol, 0.0)
        details["max_immediate_increase_weight"] = strategic_weight * details["deployment_factor"]
    actual_satellite_weight = sum(satellite_weights.values())
    core_budget = risky_budget - actual_satellite_weight
    core_weights, residual_core, core_reasons = _allocate_core(
        resolved,
        core_budget,
        core_assessments,
        current_weights,
        limits.single_asset_max,
        chain_liveness,
        structural_risk,
    )
    reasons.extend(core_reasons)
    constraints.append("v3 core sleeve uses configurable BTC/ETH anchor and ETH gates")
    stable_target += residual_core

    target: dict[str, float] = _stable_targets(
        resolved.stable_symbols, stable_target, current_weights
    )
    for symbol, weight in core_weights.items():
        target[symbol] = weight
    for symbol, weight in satellite_weights.items():
        target[symbol] = weight

    total = sum(target.values())
    if total <= 0 or not math.isfinite(total):
        raise ValueError("allocation produced an invalid total")
    stable_symbol = next(iter(_stable_targets(resolved.stable_symbols, 1.0, current_weights)))
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        target[stable_symbol] = target.get(stable_symbol, 0.0) + 1.0 - total
    stable_weight = sum(target.get(symbol, 0.0) for symbol in resolved.stable_symbols)
    if stable_weight < resolved.min_stablecoin_weight - 1e-9:
        raise ValueError("allocation cannot satisfy the stablecoin floor")
    if any(weight < -1e-9 or not math.isfinite(weight) for weight in target.values()):
        raise ValueError("allocation produced an invalid weight")
    target = {symbol: max(0.0, weight) for symbol, weight in target.items() if weight > 1e-12}
    reasons.append("satellite strategic targets are separated from immediate deployment allowances")
    if current_weights:
        reasons.append("current weights are inputs for later rebalance decisions, not allocation entitlement")
    return AllocationResult(target, tuple(reasons), tuple(constraints), stable_target, deployment_allowances)


def allocate(
    policy: Policy | None = None,
    regime: str = "NORMAL",
    assessments: Mapping[str, AssetAssessment | Mapping[str, Any] | float] | None = None,
    current_weights: Mapping[str, float] | None = None,
    *,
    overlays: MarketOverlays | Mapping[str, Any] | None = None,
    chain_liveness: Mapping[str, Any] | None = None,
    structural_risk: Mapping[str, Any] | None = None,
    decision_confidence: Any | None = None,
) -> AllocationResult:
    return build_target_allocation(
        policy, regime, assessments, current_weights,
        overlays=overlays, chain_liveness=chain_liveness, structural_risk=structural_risk,
        decision_confidence=decision_confidence,
    )


__all__ = [
    "AllocationResult",
    "allocate",
    "build_target_allocation",
    "satellite_eligibility",
    "satellite_target_fraction",
]
