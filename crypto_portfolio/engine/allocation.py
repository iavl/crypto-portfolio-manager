"""Deterministic bounded target-allocation rules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.evidence import AssetAssessment, EventRiskAssessment
from ..models.market_overlays import MarketOverlays
from ..models.policy import Policy, RegimeLimits, resolve_policy
from .core_eligibility import eth_core_eligibility, relative_strength_score
from .scoring import score_assessment


@dataclass(frozen=True)
class AllocationResult:
    target_weights: Mapping[str, float]
    allocation_reasons: tuple[str, ...]
    constraints_applied: tuple[str, ...]
    stable_sleeve_target: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_weights": dict(self.target_weights),
            "allocation_reasons": list(self.allocation_reasons),
            "constraints_applied": list(self.constraints_applied),
            "stable_sleeve_target": self.stable_sleeve_target,
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
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("relative_strength_vs_btc must be numeric or a supported state") from exc
    if not math.isfinite(value):
        raise ValueError("relative_strength_vs_btc must be finite")
    return "ELIGIBLE" if value >= 0 else "INELIGIBLE"


def _relative_multiplier(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 0.5 + min(1.0, max(0.0, float(value))) * 0.5
    state = str(value).strip().upper()
    if state not in {"OUTPERFORM", "NEUTRAL", "UNDERPERFORM", "MATERIALLY_WEAK"}:
        raise ValueError("relative_strength_vs_btc is unsupported")
    return 1.0 if state == "OUTPERFORM" else 0.75


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


def _core_relative_multiplier(value: Any, policy: Policy) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        score = float(value)
        if 0 <= score <= 1:
            score *= 100.0
        if not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError("ETH relative score must be finite and in [0, 100]")
        return min(1.25, max(0.75, 1.0 + 0.25 * ((score - 50.0) / 50.0)))
    state = str(value or "neutral").strip().lower()
    if state not in policy.core_allocation["relative_multipliers"]:
        raise ValueError(f"unknown ETH relative-strength state: {state}")
    return float(policy.core_allocation["relative_multipliers"][state])


def _allocate_core_v3(
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
            raw_relative = _field(assessment, "relative_strength_vs_btc") if assessment is not None else None
            relative = relative_strength_score(assessment) if assessment is not None else None
            relative_multiplier = _core_relative_multiplier(
                raw_relative if isinstance(raw_relative, str) else relative,
                policy,
            ) if relative is not None else 1.0
        else:
            state = _btc_core_state(assessment)
            relative_multiplier = 1.0
        states[symbol] = state
        if state == "INELIGIBLE":
            continue
        raw[symbol] = (
            anchor.get(symbol, 0.0)
            * _core_quality_multiplier(score)
            * confidence_multipliers[confidence]
            * relative_multiplier
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


def satellite_eligibility(
    assessment: AssetAssessment | Mapping[str, Any] | None,
    policy: Policy | None = None,
    *,
    current_weight: float = 0.0,
) -> str:
    """Return ELIGIBLE, HOLD_ONLY, or INELIGIBLE for a satellite assessment."""
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
    if _event_risk_state(assessment) in {"SEVERE", "CRITICAL"} or _flag(
        _field(assessment, "thesis_broken", False), "thesis_broken"
    ):
        return "INELIGIBLE"
    if score < entry_score:
        if _relative_eligibility(relative) == "INELIGIBLE":
            return "INELIGIBLE"
        return "HOLD_ONLY" if current_weight > 0 and score >= exit_score else "INELIGIBLE"
    if not _flag(_field(assessment, "critical_data_complete", True), "critical_data_complete"):
        return "HOLD_ONLY"
    event_risk = _field(assessment, "event_risk", None)
    unresolved = (
        event_risk.unresolved
        if isinstance(event_risk, EventRiskAssessment)
        else _flag(event_risk.get("unresolved", False), "event_risk.unresolved")
        if isinstance(event_risk, Mapping)
        else False
    )
    if unresolved:
        return "HOLD_ONLY"
    return _relative_eligibility(relative)


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
    if overlays is not None:
        if not isinstance(overlays, MarketOverlays):
            MarketOverlays.from_mapping(overlays)
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
            decision_confidence_factor = 0.0 if score < 0.60 else 0.70 if score < 0.80 else 1.0
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

    satellite_raw: dict[str, float] = {}
    satellite_hold: dict[str, float] = {}
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
        risk_tier = str(_field(assessment, "risk_tier", "normal")).lower() if assessment is not None else "normal"
        relative = _field(assessment, "relative_strength_vs_btc") if assessment is not None else None
        if asset_type == "satellite":
            relative_status = satellite_eligibility(
                assessment,
                resolved,
                current_weight=normalized_current_weights.get(symbol, 0.0),
            )
            if relative_status == "HOLD_ONLY":
                if current_weights.get(symbol, 0.0) > 0:
                    satellite_hold[symbol] = current_weights[symbol]
                reason = (
                    "score is inside the entry/exit hysteresis band"
                    if score < resolved.allocation["satellite_entry_score"]
                    else "BTC-relative or critical evidence is incomplete"
                )
                reasons.append(f"{symbol} is HOLD_ONLY because {reason}")
            elif relative_status == "ELIGIBLE":
                entry_score = resolved.allocation["satellite_entry_score"]
                score_strength = min(
                    1.0,
                    max(0.0, (score - entry_score) / (
                        resolved.allocation["satellite_full_score"]
                        - entry_score
                    )),
                )
                confidence_multiplier = resolved.allocation["confidence_multipliers"][confidence]
                risk_multipliers = resolved.allocation["risk_multipliers"]
                risk_multiplier = risk_multipliers.get(
                    risk_tier, risk_multipliers.get(risk_tier.replace("-", "_"), 1.0)
                )
                satellite_raw[symbol] = (
                    satellite_cap
                    * score_strength
                    * confidence_multiplier
                    * risk_multiplier
                    * event_multiplier
                    * _relative_multiplier(relative)
                    * decision_confidence_factor
                )
                if event_multiplier < 1.0:
                    reasons.append(
                        f"{symbol} event-risk state {event_risk} limits new deployment to {event_multiplier:.0%}"
                    )
                if confidence_multiplier == 0:
                    reasons.append(f"{symbol} receives 0% satellite target because confidence is LOW")
                if decision_confidence_factor < 1.0:
                    reasons.append(f"{symbol} new deployment is capped by portfolio decision confidence at {decision_confidence_factor:.0%}")
            else:
                reasons.append(f"{symbol} receives 0% satellite target because eligibility failed")
        elif asset_type == "core":
            core_assessments[symbol] = assessment

    held_satellite_weights, _ = _bounded_allocate(
        satellite_hold, satellite_cap, limits.single_asset_max
    )
    eligible_satellite_budget = max(0.0, satellite_cap - sum(held_satellite_weights.values()))
    satellite_weights, _ = _bounded_allocate(
        satellite_raw, eligible_satellite_budget, limits.single_asset_max
    )
    satellite_weights = {
        symbol: held_satellite_weights.get(symbol, 0.0) + satellite_weights.get(symbol, 0.0)
        for symbol in set(held_satellite_weights) | set(satellite_weights)
    }
    actual_satellite_weight = sum(satellite_weights.values())
    core_budget = risky_budget - actual_satellite_weight
    core_weights, residual_core, core_reasons = _allocate_core_v3(
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
    reasons.append("satellites are optional and receive capital only after score/confidence/risk gates")
    if current_weights:
        reasons.append("current weights are inputs for later rebalance decisions, not allocation entitlement")
    return AllocationResult(target, tuple(reasons), tuple(constraints), stable_target)


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


__all__ = ["AllocationResult", "allocate", "build_target_allocation", "satellite_eligibility"]
