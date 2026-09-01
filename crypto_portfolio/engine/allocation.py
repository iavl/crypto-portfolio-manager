"""Deterministic bounded target-allocation rules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.evidence import AssetAssessment, FactorScore
from ..models.policy import Policy, RegimeLimits, resolve_policy


@dataclass(frozen=True)
class AllocationResult:
    target_weights: Mapping[str, float]
    allocation_reasons: tuple[str, ...]
    constraints_applied: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_weights": dict(self.target_weights),
            "allocation_reasons": list(self.allocation_reasons),
            "constraints_applied": list(self.constraints_applied),
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


def _relative_case_is_acceptable(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.upper() not in {"WEAK", "NEGATIVE", "BEARISH", "UNDERPERFORM"}
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("relative_strength_vs_btc must be numeric or a known state") from exc
    return math.isfinite(value) and value >= 0


def _bounded_allocate(
    raw: Mapping[str, float], budget: float, cap: float
) -> tuple[dict[str, float], float]:
    result: dict[str, float] = {}
    active = {symbol: weight for symbol, weight in raw.items() if weight > 0}
    remaining = budget
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
) -> AllocationResult:
    resolved = policy or resolve_policy()
    regime_name = regime.regime if hasattr(regime, "regime") else str(regime).upper()
    limits: RegimeLimits = resolved.regime(regime_name)
    if not resolved.stable_symbols:
        raise ValueError("policy must define at least one stable symbol")
    assessments = assessments or {}
    current_weights = current_weights or {}
    current_symbols = {str(symbol).strip().upper() for symbol in current_weights}
    stable_symbol = next(
        (symbol for symbol in resolved.stable_symbols if symbol in current_symbols),
        resolved.stable_symbols[0],
    )

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
            eligible = (
                score >= resolved.allocation["satellite_min_score"]
                and not severe_event
                and not thesis_broken
                and _relative_case_is_acceptable(relative)
            )
            if eligible:
                confidence_multiplier = (
                    1.0
                    if confidence == "HIGH"
                    else 0.5
                    if confidence == "MEDIUM"
                    else resolved.allocation["low_confidence_satellite_weight"]
                )
                risk_multiplier = {"high": 0.5, "high_beta": 0.5, "high-beta": 0.5}.get(risk_tier, 1.0)
                if confidence_multiplier > 0:
                    satellite_raw[symbol] = (
                        max(1.0, score - resolved.allocation["satellite_min_score"] + 1.0)
                        * confidence_multiplier
                        * risk_multiplier
                    )
                else:
                    reasons.append(f"{symbol} receives 0% satellite target because confidence is LOW")
            else:
                reasons.append(f"{symbol} receives 0% satellite target because eligibility failed")
        elif asset_type == "core" and not severe_event and not thesis_broken:
            core_raw[symbol] = max(score, resolved.allocation["core_min_score"])

    satellite_budget = satellite_cap if satellite_raw else 0.0
    satellite_weights, residual_satellite = _bounded_allocate(
        satellite_raw, satellite_budget, limits.single_asset_max
    )
    core_budget = risky_budget - satellite_budget + residual_satellite
    core_weights, residual_core = _bounded_allocate(core_raw, core_budget, limits.single_asset_max)
    stable_target += residual_core

    target: dict[str, float] = {stable_symbol: stable_target}
    for symbol, weight in core_weights.items():
        target[symbol] = weight
    for symbol, weight in satellite_weights.items():
        target[symbol] = weight

    total = sum(target.values())
    if total <= 0 or not math.isfinite(total):
        raise ValueError("allocation produced an invalid total")
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        target[stable_symbol] = target.get(stable_symbol, 0.0) + 1.0 - total
    if target[stable_symbol] < resolved.min_stablecoin_weight - 1e-9:
        raise ValueError("allocation cannot satisfy the stablecoin floor")
    if any(weight < -1e-9 or not math.isfinite(weight) for weight in target.values()):
        raise ValueError("allocation produced an invalid weight")
    target = {symbol: max(0.0, weight) for symbol, weight in target.items() if weight > 1e-12}
    reasons.append("satellites are optional and receive capital only after score/confidence/risk gates")
    if current_weights:
        reasons.append("current weights are inputs for later rebalance decisions, not allocation entitlement")
    return AllocationResult(target, tuple(reasons), tuple(constraints))


def allocate(
    policy: Policy | None = None,
    regime: str = "NORMAL",
    assessments: Mapping[str, AssetAssessment | Mapping[str, Any] | float] | None = None,
    current_weights: Mapping[str, float] | None = None,
) -> AllocationResult:
    return build_target_allocation(policy, regime, assessments, current_weights)


__all__ = ["AllocationResult", "allocate", "build_target_allocation"]
