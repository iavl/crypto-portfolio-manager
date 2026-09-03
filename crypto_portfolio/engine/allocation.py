"""Deterministic bounded target-allocation rules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.evidence import AssetAssessment, FactorScore
from ..models.market_overlays import MarketOverlays
from ..models.policy import Policy, RegimeLimits, resolve_policy


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
        values = [
            score.score if isinstance(score, FactorScore) else float(score)
            for score in value.factor_scores.values()
            if score is not None
        ]
        raw = sum(values) / len(values) if values else 50.0
    else:
        if isinstance(value, Mapping):
            raw = value.get("weighted_score", value.get("score", 50.0))
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


def _field(value: Any, name: str, default: Any) -> Any:
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
        if state in {"", "UNKNOWN", "UNAVAILABLE", "MISSING", "N/A"}:
            return "HOLD_ONLY"
        return "INELIGIBLE" if state in {"WEAK", "NEGATIVE", "BEARISH", "UNDERPERFORM"} else "ELIGIBLE"
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("relative_strength_vs_btc must be numeric or a known state") from exc
    if not math.isfinite(value):
        raise ValueError("relative_strength_vs_btc must be finite")
    return "ELIGIBLE" if value >= 0 else "INELIGIBLE"


def _relative_multiplier(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 0.5 + min(1.0, max(0.0, float(value))) * 0.5
    return 1.0 if str(value).strip().upper() in {"STRONG", "OUTPERFORM", "POSITIVE", "HEALTHY"} else 0.75


def satellite_eligibility(
    assessment: AssetAssessment | Mapping[str, Any] | None,
    policy: Policy | None = None,
) -> str:
    """Return ELIGIBLE, HOLD_ONLY, or INELIGIBLE for a satellite assessment."""
    resolved = policy or resolve_policy()
    score = _score(assessment, "satellite") if assessment is not None else 50.0
    relative = (
        _field(
            assessment,
            "relative_strength_vs_btc",
            _field(assessment, "relative_strength", None),
        )
        if assessment is not None
        else None
    )
    if score < resolved.allocation["satellite_min_score"]:
        return "INELIGIBLE"
    if _flag(_field(assessment, "severe_event", False), "severe_event") or _flag(
        _field(assessment, "thesis_broken", False), "thesis_broken"
    ):
        return "INELIGIBLE"
    if not _flag(_field(assessment, "critical_data_complete", True), "critical_data_complete"):
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
    core_raw: dict[str, float] = {}
    for symbol in candidates:
        asset_type = resolved.classify(symbol)
        assessment = normalized_assessments.get(symbol)
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
        severe_event = _flag(_field(assessment, "severe_event", False), "severe_event") if assessment is not None else False
        thesis_broken = _flag(_field(assessment, "thesis_broken", False), "thesis_broken") if assessment is not None else False
        risk_tier = str(_field(assessment, "risk_tier", "normal")).lower() if assessment is not None else "normal"
        relative = (
            _field(
                assessment,
                "relative_strength_vs_btc",
                _field(assessment, "relative_strength", None),
            )
            if assessment is not None
            else None
        )
        if asset_type == "satellite":
            relative_status = satellite_eligibility(assessment, resolved)
            if relative_status == "HOLD_ONLY":
                if current_weights.get(symbol, 0.0) > 0:
                    satellite_hold[symbol] = current_weights[symbol]
                reasons.append(f"{symbol} is HOLD_ONLY because BTC-relative evidence is incomplete")
            elif relative_status == "ELIGIBLE":
                score_strength = min(
                    1.0,
                    max(0.0, (score - resolved.allocation["satellite_min_score"]) / (
                        resolved.allocation["satellite_full_score"]
                        - resolved.allocation["satellite_min_score"]
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
                    * _relative_multiplier(relative)
                )
                if confidence_multiplier == 0:
                    reasons.append(f"{symbol} receives 0% satellite target because confidence is LOW")
            else:
                reasons.append(f"{symbol} receives 0% satellite target because eligibility failed")
        elif asset_type == "core" and not severe_event and not thesis_broken:
            core_raw[symbol] = max(score, resolved.allocation["core_min_score"])

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
    core_weights, residual_core = _bounded_allocate(core_raw, core_budget, limits.single_asset_max)
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
) -> AllocationResult:
    return build_target_allocation(policy, regime, assessments, current_weights, overlays=overlays)


__all__ = ["AllocationResult", "allocate", "build_target_allocation", "satellite_eligibility"]
