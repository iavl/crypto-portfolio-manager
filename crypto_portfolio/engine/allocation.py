"""Deterministic bounded target-allocation rules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..models.evidence import AssetAssessment, EventRiskAssessment
from ..models.market_overlays import MarketOverlays
from ..models.policy import Policy, RegimeLimits, resolve_policy
from .confidence import compose_deployment_factors, confidence_deployment_factor
from .core_eligibility import eth_core_eligibility
from .portfolio_risk import (
    PortfolioRiskInputs,
    combined_risk_cap,
    effective_target_volatility,
    marginal_risk_contributions,
    portfolio_volatility,
    regime_target_volatility_multiplier,
    volatility_budget_scale,
)
from .risk import risk_overlay_floor
from .risk_tier import tier_strategic_fraction
from .scoring import low_evidence_contract, score_assessment
from .scoring_v3 import asset_conviction_state, comparison_score_space


@dataclass(frozen=True)
class AllocationResult:
    target_weights: Mapping[str, float]
    allocation_reasons: tuple[str, ...]
    constraints_applied: tuple[str, ...]
    stable_sleeve_target: float = 0.0
    deployment_allowances: Mapping[str, Mapping[str, Any]] | None = None
    strategic_stable_target: float = 0.0
    risk_engine: Mapping[str, Any] | None = None

    @property
    def constraint_residual_cash(self) -> float:
        """Core budget no capped core asset could absorb; it lands in stable."""
        return max(0.0, self.stable_sleeve_target - self.strategic_stable_target)

    @property
    def effective_stable_target(self) -> float:
        return self.stable_sleeve_target

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
            "stable_targets": {
                "strategic_stable_target": self.strategic_stable_target,
                "constraint_residual_cash": self.constraint_residual_cash,
                "effective_stable_target": self.effective_stable_target,
            },
            "deployment_allowances": {
                symbol: dict(value)
                for symbol, value in (self.deployment_allowances or {}).items()
            },
            "risk_engine": dict(self.risk_engine) if self.risk_engine is not None else None,
        }


def _score(value: Any, symbol: str) -> float:
    """Comparison score for threshold decisions (Strategy V2 Phase 2).

    Thresholds live in the coverage-normalized space: when a normalized
    score is available it answers "how attractive is what we actually
    observed", and coverage separately gates deployment as evidence
    confidence. The effective (reliability-shrunk) score remains the
    diagnostic aggregate and the fallback when normalization is
    unavailable — the two spaces are never mixed inside one comparison.
    """
    if isinstance(value, AssetAssessment):
        if value.normalized_score is not None:
            raw = value.normalized_score
        elif value.weighted_score is not None:
            raw = value.weighted_score
        else:
            raise ValueError("typed assessment must be scored before allocation")
    else:
        if isinstance(value, Mapping):
            raw = value.get("normalized_score")
            if raw is None:
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


def _relative_eligibility(value: Any, policy: Policy | None = None) -> str:
    """Classify the BTC-relative comparison on the canonical 0-100 score unit.

    Two policy thresholds split the domain so moderate weakness is not
    double-counted as both a scoring penalty and a hard ineligibility:

    - at or above ``increase_min_score`` (OUTPERFORM/NEUTRAL): may increase;
    - ``hard_block_below_score`` .. ``increase_min_score`` (UNDERPERFORM):
      no new increase on that evidence, but a held position is reduced
      normally, never forced out by this signal alone;
    - below ``hard_block_below_score`` (MATERIALLY_WEAK): confirmed severe
      weakness, a hard eligibility block.

    ``None``/UNKNOWN means the comparison is missing and maps to HOLD_ONLY
    (fail-defensive: no new risk, no manufactured exit).
    """
    resolved = policy or resolve_policy()
    thresholds = resolved.allocation.get("relative_strength") or {"increase_min_score": 50.0, "hard_block_below_score": 30.0}
    increase_min = float(thresholds["increase_min_score"])
    hard_block = float(thresholds["hard_block_below_score"])
    if value is None:
        return "HOLD_ONLY"
    if isinstance(value, str):
        state = value.strip().upper()
        if state in {"", "UNKNOWN"}:
            return "HOLD_ONLY"
        if state == "MATERIALLY_WEAK":
            return "INELIGIBLE"
        if state == "UNDERPERFORM":
            return "HOLD_ONLY"
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
    if score >= increase_min:
        return "ELIGIBLE"
    if score < hard_block:
        return "INELIGIBLE"
    return "HOLD_ONLY"


def _core_quality_multiplier(score: float) -> float:
    """Unvalidated scores get narrow sizing authority (Strategy V2.1 Phase C).

    The composite score's ranking power is not yet demonstrated, so the core
    quality multiplier is preregistered to [0.85, 1.15] instead of the earlier
    [0.5, 1.5]: an unproven alpha signal must not double or halve core sizing.
    """
    return min(1.15, max(0.85, 0.85 + 0.30 * score / 100.0))


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
    reasons = tuple(f"{symbol} core state {state}" for symbol, state in states.items())
    if not positive or budget <= 0:
        return {}, max(0.0, budget), reasons
    # Per-asset ceilings: single-asset cap, the ETH sleeve share, and the
    # eligibility caps for gated states.  Only ELIGIBLE_INCREASE assets may
    # absorb budget freed by a capped peer; gated sleeves are frozen at
    # min(proportional share, cap) so a HOLD_ONLY/REDUCE state never grows
    # back through redistribution.
    caps: dict[str, float] = {}
    redistributable: set[str] = set()
    for symbol in positive:
        cap = single_asset_cap
        if symbol == "ETH":
            cap = min(cap, budget * config["eth"]["max_core_sleeve_share"])
        state = states.get(symbol)
        if state in {"HOLD_ONLY", "UNDERWEIGHT"}:
            cap = min(cap, current_weights.get(symbol, 0.0))
        elif state == "REDUCE":
            cap = min(cap, current_weights.get(symbol, 0.0) * 0.75)
        caps[symbol] = cap
        if state == "ELIGIBLE_INCREASE":
            redistributable.add(symbol)
    total_raw = sum(positive.values())
    desired = {symbol: budget * value / total_raw for symbol, value in positive.items()}
    result, residual = _bounded_allocate_with_caps(
        desired, budget, caps, redistributable=redistributable
    )
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


def _relative_missing(value: Any) -> bool:
    """True only when the BTC-relative comparison itself is absent.

    A confirmed moderate UNDERPERFORM reading is complete evidence that
    gates new increases; it is not missing data and must never route a held
    position into the fail-defensive preserve bucket.
    """
    if value is None:
        return True
    if isinstance(value, str) and value.strip().upper() in {"", "UNKNOWN"}:
        return True
    return False


def _incomplete_evidence(assessment: Any, policy: Policy | None = None) -> bool:
    """Missing relative/critical/factor evidence blocks new risk fail-defensively."""
    if assessment is None:
        return True
    return (
        _relative_missing(_field(assessment, "relative_strength_vs_btc", None))
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
    conviction: Mapping[str, Any] | None = None,
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

    Score thresholds are compared in the NORMALIZED space only (Strategy
    V2.1 Phase C): when the normalized score is unavailable the effective
    diagnostic score never substitutes for it — the conviction state decides
    between a conviction-driven entry and the fail-defensive preserve path.
    """
    resolved = policy or resolve_policy()
    if isinstance(assessment, AssetAssessment) and assessment.weighted_score is None:
        assessment, _ = score_assessment(assessment, policy=resolved)
    if conviction is None and assessment is not None:
        # Direct callers may pass a scored assessment without the
        # pre-computed conviction block; derive it when the symbol is known
        # so the missing-market guard works on every call path.
        symbol = (
            assessment.symbol if isinstance(assessment, AssetAssessment)
            else assessment.get("symbol") if isinstance(assessment, Mapping) else None
        )
        if symbol:
            conviction = asset_conviction_state(assessment, policy=resolved, symbol=symbol)
    normalized = _field(assessment, "normalized_score") if assessment is not None else None
    held = current_weight > 0
    conviction_state_value = (
        str(conviction.get("conviction_state")) if conviction is not None else None
    )
    if conviction_state_value == "HARD_EXIT":
        return "INELIGIBLE"
    relative = _field(assessment, "relative_strength_vs_btc") if assessment is not None else None
    if isinstance(current_weight, bool) or not isinstance(current_weight, (int, float)):
        raise ValueError("current_weight must be numeric")
    if not math.isfinite(float(current_weight)) or current_weight < 0:
        raise ValueError("current_weight must be finite and >= 0")
    entry_score = resolved.allocation["satellite_entry_score"]
    exit_score = resolved.allocation.get("satellite_exit_score", entry_score)
    soft_exit_score = resolved.allocation.get("satellite_soft_exit_score", exit_score)
    # Confirmed hard risk first: a broken thesis or severe event is never
    # masked by data availability in either direction.
    if _event_risk_state(assessment) in {"SEVERE", "CRITICAL"} or _flag(
        _field(assessment, "thesis_broken", False), "thesis_broken"
    ):
        return "INELIGIBLE"
    relative_status = _relative_eligibility(relative, resolved)
    # A confirmed materially-negative BTC-relative case is ineligible at any
    # score; missing evidence is the opposite signal and handled below.
    if relative_status == "INELIGIBLE":
        return "INELIGIBLE"
    # Entry-critical evidence gates every new-risk path: the BTC-relative
    # comparison itself, the critical-data flag, and unresolved events.
    # Missing STRUCTURAL factors no longer land here — they route through
    # the conviction state instead (TACTICAL_ONLY earns a small tactical
    # slice; FULL_CONVICTION needs the structural case evidenced).
    if (
        _relative_missing(relative)
        or not _flag(_field(assessment, "critical_data_complete", True), "critical_data_complete")
        or _unresolved_event(assessment)
    ):
        return "HOLD_OR_REDUCE" if held else "INELIGIBLE"
    missing_market = (
        tuple(conviction.get("missing_market_factors") or ())
        if conviction is not None and conviction.get("factor_detail_present") else ()
    )
    if missing_market:
        # A missing MARKET factor makes the tactical case itself unevidenced:
        # preserve a held position, never let the shrunk neutral score
        # manufacture a soft exit. Missing structural factors do not land
        # here — they cap conviction instead (TACTICAL_ONLY).
        return "HOLD_OR_REDUCE" if held else "INELIGIBLE"
    if conviction_state_value == "NO_NEW_RISK":
        return "HOLD_OR_REDUCE" if held else "INELIGIBLE"
    if normalized is None:
        # No normalized comparison score exists: the effective score is a
        # diagnostic and never enters threshold comparison. A conviction
        # strong enough to justify risk (FULL_CONVICTION/TACTICAL_ONLY) may
        # still enter; everything else is fail-defensive.
        if conviction_state_value in {"FULL_CONVICTION", "TACTICAL_ONLY"}:
            if relative_status == "HOLD_ONLY":
                return "HOLD_OR_REDUCE" if held else "INELIGIBLE"
            return "ELIGIBLE_INCREASE"
        return "HOLD_OR_REDUCE" if held else "INELIGIBLE"
    score = float(normalized)
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
        # Moderate BTC-relative weakness above the entry score: no new risk
        # on that evidence; a held position is reduced normally, and an
        # unheld one never gains a target.
        return "HOLD_OR_REDUCE" if held else "INELIGIBLE"
    return "ELIGIBLE_INCREASE"


def _bounded_allocate_with_caps(
    raw: Mapping[str, float],
    budget: float,
    caps: Mapping[str, float],
    *,
    redistributable: Iterable[str] | None = None,
) -> tuple[dict[str, float], float]:
    """Allocate absolute requested weights under a budget and per-asset caps.

    ``raw`` holds absolute requested weights; the total never exceeds
    ``min(budget, sum(requests))``.  Assets listed in ``redistributable``
    (default: all) compete for the budget proportionally to their request,
    and budget freed by a capped peer is water-filled into the remaining
    redistributable assets up to their caps.  Assets excluded from
    ``redistributable`` are frozen at ``min(request, cap)`` first, so a
    gated sleeve can never grow back through redistribution.  The returned
    residual is budget the capped field could not absorb.
    """
    requests = {symbol: float(value) for symbol, value in raw.items() if float(value) > 0}
    if not requests or budget <= 0:
        return {}, max(0.0, budget)
    result: dict[str, float] = {symbol: 0.0 for symbol in requests}
    remaining = min(budget, sum(requests.values()))
    frozen = (
        set() if redistributable is None
        else {symbol for symbol in requests if symbol not in redistributable}
    )
    for symbol in sorted(frozen):
        take = min(requests[symbol], float(caps.get(symbol, requests[symbol])))
        result[symbol] = take
        remaining -= take
    active = {symbol: request for symbol, request in requests.items() if symbol not in frozen}
    while active and remaining > 1e-12:
        total = sum(active.values())
        capped = [
            symbol for symbol, request in active.items()
            if remaining * request / total > float(caps.get(symbol, budget)) - result[symbol] + 1e-15
        ]
        if not capped:
            for symbol, request in active.items():
                result[symbol] += remaining * request / total
            remaining = 0.0
            break
        for symbol in sorted(capped):
            give = min(float(caps.get(symbol, budget)) - result[symbol], remaining)
            result[symbol] += give
            remaining -= give
            del active[symbol]
    return result, max(0.0, remaining)


def _bounded_allocate(
    raw: Mapping[str, float], budget: float, cap: float
) -> tuple[dict[str, float], float]:
    return _bounded_allocate_with_caps(
        raw, budget, {symbol: cap for symbol in raw}
    )


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
    portfolio_drawdown: float | None = None,
    market_recovery_streak: int = 0,
    risk_inputs: PortfolioRiskInputs | Mapping[str, Any] | None = None,
    recovery_state: Mapping[str, Any] | None = None,
) -> AllocationResult:
    resolved = policy or resolve_policy()
    risk_mode = (resolved.risk_engine or {}).get("mode", "legacy_drawdown")
    risk_metrics: PortfolioRiskInputs | None = None
    if risk_inputs is not None:
        risk_metrics = (
            risk_inputs
            if isinstance(risk_inputs, PortfolioRiskInputs)
            else PortfolioRiskInputs.from_mapping(risk_inputs)
        )
    if risk_mode == "volatility_budget" and risk_metrics is None:
        raise ValueError(
            "risk_engine.mode=volatility_budget requires portfolio risk inputs "
            "(asset volatilities and correlations); the drawdown ladder is not a "
            "fallback for normal sizing"
        )
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

    overlay_floor, overlay_reason, overlay_state = risk_overlay_floor(
        resolved, portfolio_drawdown, market_recovery_streak,
        recovery_state=recovery_state,
    )
    strategic_stable_only = max(resolved.min_stablecoin_weight, limits.stablecoin_target)
    # Regime authority over sizing differs by engine mode. Legacy keeps the
    # regime stable target as the primary dynamic risk control. The
    # volatility-budget engine lets the regime act on risk through the target
    # volatility multiplier only: the stable floor is the policy minimum and
    # drawdown keeps its authority solely in the emergency overlay.
    vol_multiplier = 1.0
    if risk_mode == "volatility_budget":
        emergency_floor = overlay_floor
        base_stable = resolved.min_stablecoin_weight
        stable_target = base_stable
        vol_multiplier = regime_target_volatility_multiplier(
            regime_name, resolved.risk_engine["regime_risk_scaling"]
        )
    else:
        emergency_floor = 0.0
        base_stable = strategic_stable_only
        stable_target = max(strategic_stable_only, overlay_floor)
    risky_budget = 1.0 - stable_target
    satellite_cap = min(limits.satellite_max, risky_budget)
    candidates = _assessment_symbols(assessments, resolved)
    normalized_assessments = {
        str(symbol).strip().upper(): value for symbol, value in assessments.items()
    }
    if risk_mode == "volatility_budget":
        reasons = [
            f"{regime_name} market regime scales the target volatility by {vol_multiplier:.2f}; "
            f"the stable floor is the policy minimum {base_stable:.2%}"
        ]
    else:
        reasons = [f"{regime_name} reserves {stable_target:.2%} for stablecoin/cash"]
    if overlay_reason is not None:
        reasons.append(overlay_reason)
    constraints = [
        f"stablecoin floor {resolved.min_stablecoin_weight:.2%}",
        f"satellite cap {satellite_cap:.2%}",
        f"single-asset cap {limits.single_asset_max:.2%}",
    ]
    if risk_mode != "volatility_budget" and overlay_floor > strategic_stable_only + 1e-12:
        constraints.append(
            f"drawdown budget overlay floor {overlay_floor:.2%} overrides the "
            f"{regime_name} stable target {strategic_stable_only:.2%}"
        )

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
            risk_tier_caps = resolved.allocation["risk_tier_caps"]
            tier_caps = (
                risk_tier_caps.get(risk_tier)
                or risk_tier_caps.get(risk_tier.replace("-", "_"))
                or risk_tier_caps["normal"]
            )
            hard_cap_buffer_pp = float(tier_caps["hard_cap_buffer_pp"])
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
            # Scoring V3 attribution (Strategy V2.1 Phase C): market and
            # structural family scores, the conviction state, and which score
            # space the threshold comparison is allowed to run in.
            conviction = (
                asset_conviction_state(assessment, policy=resolved, symbol=symbol)
                if assessment is not None else None
            )
            conviction_name = (
                str(conviction["conviction_state"]) if conviction is not None else "WATCH_ONLY"
            )
            score_space = (
                str(conviction["comparison_score_space"]) if conviction is not None
                else comparison_score_space(None, None)
            )
            evidence_class = (
                str(conviction["evidence_class"]) if conviction is not None else "ACTIONABLE"
            )
            held_weight = normalized_current_weights.get(symbol, 0.0)
            state = satellite_eligibility(
                assessment,
                resolved,
                current_weight=held_weight,
                conviction=conviction,
            )
            # Threshold comparisons run in the NORMALIZED space only. The
            # composite normalized score is the comparison score; when it is
            # unavailable but both families support full conviction, the
            # normalized market score (same normalized space) substitutes.
            normalized_value = (
                _field(assessment, "normalized_score") if assessment is not None else None
            )
            if normalized_value is not None:
                comparison_score = float(normalized_value)
                score_authority = "COMPOSITE_NORMALIZED"
            elif conviction_name == "FULL_CONVICTION" and conviction is not None and conviction["normalized_market_score"] is not None:
                comparison_score = float(conviction["normalized_market_score"])
                score_authority = "MARKET_NORMALIZED"
            else:
                comparison_score = None
                score_authority = "NONE"
            tactical_fraction = float(resolved.allocation["tactical_fraction"])
            if conviction_name == "TACTICAL_ONLY" and state == "ELIGIBLE_INCREASE":
                # The plan's key behavior: a strong market case without
                # structural evidence earns a small tactical slice of the
                # envelope instead of a zero-eligibility dead end.
                curve_fraction = tactical_fraction
                score_authority = "TACTICAL_FRACTION"
            elif comparison_score is not None:
                curve_fraction = satellite_target_fraction(
                    comparison_score,
                    soft_exit_score=resolved.allocation["satellite_soft_exit_score"],
                    exit_score=resolved.allocation["satellite_exit_score"],
                    entry_score=resolved.allocation["satellite_entry_score"],
                    full_score=resolved.allocation["satellite_full_score"],
                    curve=resolved.allocation["satellite_target_curve"],
                )
            else:
                curve_fraction = 0.0
            # A measured tier is a secondary constraint under the
            # volatility-budget engine (full envelope, hard caps still bound);
            # manual and policy-default tiers keep their configured fraction.
            risk_tier_cap_fraction = tier_strategic_fraction(
                risk_tier, source=risk_tier_source, policy=resolved,
            )
            # The risk tier defines the asset's risk envelope: the strategic
            # target is the score curve evaluated INSIDE that envelope (a
            # full score reaches exactly the envelope), so targets stay
            # score-sensitive everywhere below full_score. A hard exposure
            # cap (envelope plus the configured buffer) is a separate risk
            # ceiling for the rebalance layer, not a target-shaping input.
            risk_envelope_weight = satellite_cap * risk_tier_cap_fraction
            hard_exposure_cap = min(
                1.0, risk_envelope_weight + hard_cap_buffer_pp / 100.0
            )
            requested_strategic_weight = risk_envelope_weight * curve_fraction
            # Evidence permission is keyed on the low-evidence contract
            # classes, not on the data-confidence band: LIMITED evidence
            # scales deployment by its own factor instead of inheriting the
            # LOW-band zero (Phase C). The band label stays for attribution.
            evidence_permission_factor = float(
                resolved.execution["evidence_deployment_factor"][evidence_class]
            )
            deployment_factor = (
                compose_deployment_factors(
                    {
                        "evidence_permission": evidence_permission_factor,
                        "event_risk": event_multiplier,
                        "decision_confidence": decision_confidence_factor,
                        "execution_overlay": execution_overlay_factor,
                    },
                    policy=resolved,
                )
                if state == "ELIGIBLE_INCREASE"
                else 0.0
            )
            deployment_allowances[symbol] = {
                "profile_name": resolved.scoring_profile_name(symbol),
                "satellite_cap": satellite_cap,
                "score": score,
                "effective_score": (
                    float(_field(assessment, "weighted_score"))
                    if assessment is not None and _field(assessment, "weighted_score") is not None
                    else None
                ),
                "normalized_score": (
                    float(_field(assessment, "normalized_score"))
                    if assessment is not None and _field(assessment, "normalized_score") is not None
                    else None
                ),
                "evidence_class": low_evidence_contract(
                    coverage=(
                        float(_field(assessment, "score_coverage"))
                        if assessment is not None and _field(assessment, "score_coverage") is not None
                        else 1.0
                    ),
                    critical_data_complete=(
                        _flag(_field(assessment, "critical_data_complete", True), "critical_data_complete")
                        if assessment is not None else True
                    ),
                    policy=resolved,
                )["evidence_class"],
                "curve_fraction": curve_fraction,
                "current_weight": held_weight,
                "eligibility_state": state,
                "risk_tier": risk_tier,
                "risk_tier_source": risk_tier_source,
                "risk_tier_cap_fraction": risk_tier_cap_fraction,
                "hard_cap_buffer_pp": hard_cap_buffer_pp,
                "risk_envelope_weight": risk_envelope_weight,
                "hard_exposure_cap": hard_exposure_cap,
                "asset_confidence": confidence,
                "confidence_deployment_factor": asset_confidence_factor,
                "evidence_deployment_factor": evidence_permission_factor,
                "conviction_state": conviction_name,
                "market_score": conviction["market_score"] if conviction is not None else None,
                "market_coverage": conviction["market_coverage"] if conviction is not None else None,
                "normalized_market_score": (
                    conviction["normalized_market_score"] if conviction is not None else None
                ),
                "structural_score": conviction["structural_score"] if conviction is not None else None,
                "structural_coverage": conviction["structural_coverage"] if conviction is not None else None,
                "normalized_structural_score": (
                    conviction["normalized_structural_score"] if conviction is not None else None
                ),
                "comparison_score_space": score_space,
                "final_score_authority": score_authority,
                "tactical_fraction": (
                    tactical_fraction if score_authority == "TACTICAL_FRACTION" else None
                ),
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
            elif state == "ELIGIBLE_INCREASE":
                strategic_satellite_raw[symbol] = requested_strategic_weight
                if score_authority == "TACTICAL_FRACTION":
                    reasons.append(
                        f"{symbol} is TACTICAL_ONLY: market score {conviction['normalized_market_score']:.1f} "
                        f"is strong without usable structural evidence, so the strategic target rides "
                        f"{tactical_fraction:.0%} of the risk envelope instead of the score curve"
                    )
                else:
                    reasons.append(
                        f"{symbol} risk tier {risk_tier} bounds the strategic target by the "
                        f"{risk_envelope_weight:.2%} risk envelope; the score curve fills up to it "
                        f"(full score {resolved.allocation['satellite_full_score']:.0f} reaches the envelope)"
                    )
                if event_multiplier < 1.0:
                    reasons.append(
                        f"{symbol} event-risk state {event_risk} limits immediate deployment to {event_multiplier:.0%}"
                    )
                if evidence_permission_factor < 1.0:
                    reasons.append(
                        f"{symbol} immediate deployment is capped by {evidence_class} evidence at "
                        f"{evidence_permission_factor:.0%}"
                    )
                if decision_confidence_factor < 1.0:
                    reasons.append(f"{symbol} new deployment is capped by portfolio decision confidence at {decision_confidence_factor:.0%}")
            elif _incomplete_evidence(assessment, resolved) or _unresolved_event(assessment):
                # Fail-defensive preserve bucket: missing evidence blocks new
                # risk and never manufactures an exit for a held position.
                # A conviction-driven entry never lands here: its
                # entry-critical evidence was checked inside eligibility.
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
            elif held_weight > 0 and comparison_score is not None:
                # Evidence-complete sub-entry band: the strategic target rides
                # the same score curve, so crossing the entry score later
                # cannot jump the target, and deployment stays blocked.
                strategic_satellite_raw[symbol] = requested_strategic_weight
                band = "the soft-exit band" if state == "SOFT_EXIT" else "the sub-entry band"
                reasons.append(
                    f"{symbol} is {state}: the strategic target follows the score curve through "
                    f"{band} and no new risk may be added this review"
                )
            elif held_weight > 0:
                # Held without a normalized comparison score: the effective
                # score is diagnostic-only, so no curve position exists. The
                # fail-defensive preserve bucket keeps the position without
                # adding risk and without manufacturing an exit.
                satellite_hold[symbol] = held_weight
                deployment_allowances[symbol]["preserve_existing"] = True
                reasons.append(
                    f"{symbol} has no normalized comparison score this review; the existing "
                    "position is preserved without adding risk"
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
    if residual_core > 1e-12:
        reasons.append(
            f"{residual_core:.2%} of core budget stayed unabsorbed after every core cap; "
            "it becomes constraint residual cash in the stable sleeve"
        )

    risk_engine_block: dict[str, Any] | None = None
    if risk_mode == "volatility_budget":
        # Combined risk-cap minimum: the strategic risky sleeve is bounded by
        # the tightest of the volatility budget, the emergency drawdown brake,
        # and the policy/regime stable floor. Caps never multiply, so a
        # stressed book cannot be shrunk 0.7 x 0.75 x 0.5 by stacked layers.
        assert risk_metrics is not None  # guarded at function entry
        covariance = risk_metrics.covariance()
        portfolio_cfg = resolved.risk_engine["portfolio_risk"]
        base_target_volatility = float(portfolio_cfg["target_volatility"])
        effective_target = effective_target_volatility(base_target_volatility, vol_multiplier)
        risky_raw = {**core_weights, **satellite_weights}
        raw_risky_total = sum(risky_raw.values())
        scale_info: Mapping[str, Any] = {"risk_scaling_factor": 1.0, "binding": "none", "max_volatility_exceeded": False}
        sigma_raw = 0.0
        if raw_risky_total > 1e-12:
            sigma_raw = portfolio_volatility(risky_raw, covariance)
            scale_info = volatility_budget_scale(
                sigma_raw,
                target_volatility=effective_target,
                max_volatility=float(portfolio_cfg["max_volatility"]),
            )
        cap_candidates: dict[str, float | None] = {
            "base_stable_floor": 1.0 - base_stable if raw_risky_total > 0 else None,
            "emergency_overlay": 1.0 - emergency_floor if emergency_floor > 0 else None,
            "volatility_budget": (
                raw_risky_total * float(scale_info["risk_scaling_factor"])
                if raw_risky_total > 0 else None
            ),
        }
        combined = combined_risk_cap(cap_candidates)
        final_risky_total = min(raw_risky_total, float(combined["cap"]))
        if final_risky_total < raw_risky_total - 1e-12:
            binding = str(combined["binding_constraint"])
        else:
            binding = "strategic_target"
        final_scale = final_risky_total / raw_risky_total if raw_risky_total > 1e-12 else 0.0
        if final_scale < 1.0 - 1e-12:
            reasons.append(
                f"volatility-budget risk engine scales the risky sleeve to "
                f"{final_risky_total:.2%} (raw strategic {raw_risky_total:.2%}, "
                f"estimated portfolio volatility {sigma_raw:.2%}); binding constraint {binding}"
            )
        core_weights = {s: w * final_scale for s, w in core_weights.items()}
        satellite_weights = {s: w * final_scale for s, w in satellite_weights.items()}
        final_risky = {**core_weights, **satellite_weights}
        contributions = (
            marginal_risk_contributions(final_risky, covariance)
            if sum(final_risky.values()) > 1e-12 else {}
        )
        risk_engine_block = {
            "mode": risk_mode,
            "market_regime": regime_name,
            "drawdown_influenced_regime": False,
            "portfolio_volatility": sigma_raw,
            "target_volatility": effective_target,
            "base_target_volatility": base_target_volatility,
            "regime_volatility_multiplier": vol_multiplier,
            "effective_target_volatility": effective_target,
            "max_volatility": float(portfolio_cfg["max_volatility"]),
            "max_volatility_exceeded": bool(scale_info["max_volatility_exceeded"]),
            "risk_scaling_factor": final_scale,
            "volatility_budget_scale": dict(scale_info),
            "asset_risk_contributions": {
                symbol: {
                    "weight": details["weight"],
                    "risk_contribution_share": details["risk_contribution_share"],
                }
                for symbol, details in contributions.items()
            },
            "emergency_overlay_state": dict(overlay_state),
            "binding_risk_constraint": binding,
            "risk_caps": {
                name: (value if value is None else min(value, raw_risky_total if raw_risky_total > 0 else value))
                for name, value in cap_candidates.items()
            },
        }
        stable_target = 1.0 - sum(final_risky.values())
        for symbol, details in deployment_allowances.items():
            scaled = satellite_weights.get(symbol, 0.0)
            details["strategic_target_weight"] = scaled
            details["max_immediate_increase_weight"] = scaled * details["deployment_factor"]
    else:
        stable_target += residual_core
        risk_engine_block = {
            "mode": risk_mode,
            "drawdown_influenced_regime": True,
            "emergency_overlay_state": dict(overlay_state),
        }

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
    return AllocationResult(
        target, tuple(reasons), tuple(constraints), stable_target, deployment_allowances,
        strategic_stable_target=base_stable,
        risk_engine=risk_engine_block,
    )


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
    portfolio_drawdown: float | None = None,
    market_recovery_streak: int = 0,
    risk_inputs: PortfolioRiskInputs | Mapping[str, Any] | None = None,
    recovery_state: Mapping[str, Any] | None = None,
) -> AllocationResult:
    return build_target_allocation(
        policy, regime, assessments, current_weights,
        overlays=overlays, chain_liveness=chain_liveness, structural_risk=structural_risk,
        decision_confidence=decision_confidence,
        portfolio_drawdown=portfolio_drawdown,
        market_recovery_streak=market_recovery_streak,
        risk_inputs=risk_inputs,
        recovery_state=recovery_state,
    )


__all__ = [
    "AllocationResult",
    "allocate",
    "build_target_allocation",
    "satellite_eligibility",
    "satellite_target_fraction",
]
