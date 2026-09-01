"""Portfolio-level allocation risk gate."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.evidence import AssetAssessment
from ..models.policy import Policy, resolve_policy


_SEVERITIES = {"ERROR", "WARNING", "INFO"}


@dataclass(frozen=True)
class RiskViolation:
    severity: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.severity, str):
            raise ValueError("severity must be a string")
        severity = self.severity.upper()
        if severity not in _SEVERITIES:
            raise ValueError(f"severity must be one of {sorted(_SEVERITIES)}")
        object.__setattr__(self, "severity", severity)

    def as_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class RiskCheckResult:
    violations: tuple[RiskViolation, ...]

    @property
    def errors(self) -> tuple[RiskViolation, ...]:
        return tuple(item for item in self.violations if item.severity == "ERROR")

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "violations": [item.as_dict() for item in self.violations],
        }


def _weights(value: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("target_weights must be an object")
    result: dict[str, float] = {}
    for symbol, raw_weight in value.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("target_weights contains an invalid symbol")
        symbol = symbol.strip().upper()
        if symbol in result:
            raise ValueError(f"target_weights contains duplicate symbol {symbol}")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError(f"target_weights.{symbol} must be a number")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0 or weight > 1:
            raise ValueError(f"target_weights.{symbol} must be finite and in [0, 1]")
        result[symbol] = weight
    return result


def _assessment(value: Any) -> tuple[str, bool, str]:
    if isinstance(value, AssetAssessment):
        return value.confidence, value.severe_event, value.risk_tier
    if isinstance(value, Mapping):
        confidence = str(value.get("confidence", "LOW")).upper()
        if confidence not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("assessment confidence must be HIGH, MEDIUM, or LOW")
        severe_value = value.get("severe_event", False) or value.get("thesis_broken", False)
        if not isinstance(severe_value, bool):
            raise ValueError("assessment severe_event and thesis_broken must be boolean")
        return (
            confidence,
            severe_value,
            str(value.get("risk_tier", "normal")).lower(),
        )
    return "LOW", False, "normal"


def run_risk_gate(
    target_weights: Mapping[str, float] | Any,
    *,
    policy: Policy | None = None,
    regime: str = "NORMAL",
    assessments: Mapping[str, AssetAssessment | Mapping[str, Any]] | None = None,
    current_drawdown: float | None = None,
) -> RiskCheckResult:
    resolved = policy or resolve_policy()
    if hasattr(target_weights, "target_weights"):
        target_weights = target_weights.target_weights
    try:
        weights = _weights(target_weights)
    except ValueError as exc:
        return RiskCheckResult((RiskViolation("ERROR", "INVALID_WEIGHTS", str(exc)),))
    violations: list[RiskViolation] = []
    total = sum(weights.values())
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        violations.append(RiskViolation("ERROR", "TOTAL_NOT_ONE", "target weights must sum to 1"))

    stable_weight = sum(weights.get(symbol, 0.0) for symbol in resolved.stable_symbols)
    if stable_weight + 1e-9 < resolved.min_stablecoin_weight:
        violations.append(
            RiskViolation(
                "ERROR",
                "STABLECOIN_FLOOR",
                f"stablecoin weight {stable_weight:.2%} is below {resolved.min_stablecoin_weight:.2%}",
            )
        )

    regime_name = regime.regime if hasattr(regime, "regime") else str(regime).upper()
    limits = resolved.regime(regime_name)
    satellite_weight = sum(weights.get(symbol, 0.0) for symbol in resolved.satellite_symbols)
    if satellite_weight > limits.satellite_max + 1e-9:
        violations.append(
            RiskViolation(
                "ERROR",
                "SATELLITE_CAP",
                f"satellite weight {satellite_weight:.2%} exceeds {limits.satellite_max:.2%}",
            )
        )
    for symbol, weight in weights.items():
        if symbol in resolved.stable_symbols:
            continue
        if weight > limits.single_asset_max + 1e-9:
            violations.append(
                RiskViolation(
                    "ERROR",
                    "SINGLE_ASSET_CAP",
                    f"{symbol} weight {weight:.2%} exceeds {limits.single_asset_max:.2%}",
                )
            )

    risky_weight = max(0.0, total - stable_weight)
    core_weight = sum(weights.get(symbol, 0.0) for symbol in resolved.core_symbols)
    required_core = limits.core_risky_min * risky_weight
    if core_weight + 1e-9 < required_core:
        violations.append(
            RiskViolation(
                "WARNING",
                "CORE_MINIMUM",
                f"core weight {core_weight:.2%} is below the regime minimum {required_core:.2%}",
            )
        )

    if current_drawdown is not None:
        try:
            drawdown = float(current_drawdown)
        except (TypeError, ValueError) as exc:
            raise ValueError("current_drawdown must be numeric") from exc
        if not math.isfinite(drawdown) or drawdown > 0:
            raise ValueError("current_drawdown must be finite and <= 0")
        if drawdown < -resolved.max_portfolio_drawdown:
            violations.append(
                RiskViolation(
                    "ERROR",
                    "DRAWDOWN_BREACH",
                    f"current drawdown {drawdown:.2%} breaches the configured risk budget",
                )
            )
        elif drawdown <= -0.8 * resolved.max_portfolio_drawdown:
            violations.append(
                RiskViolation(
                    "WARNING",
                    "DRAWDOWN_GUARD",
                    f"current drawdown {drawdown:.2%} is near the configured risk budget",
                )
            )

    assessments = assessments or {}
    severe_symbols: list[str] = []
    high_beta_symbols: list[str] = []
    for raw_symbol, assessment in assessments.items():
        symbol = str(raw_symbol).strip().upper()
        confidence, severe_event, risk_tier = _assessment(assessment)
        if severe_event and weights.get(symbol, 0.0) > 0:
            severe_symbols.append(symbol)
        if symbol in resolved.satellite_symbols and weights.get(symbol, 0.0) > 0:
            if confidence == "LOW":
                violations.append(
                    RiskViolation(
                        "WARNING",
                        "LOW_CONFIDENCE_EXPOSURE",
                        f"low-confidence satellite {symbol} has non-zero exposure",
                    )
                )
            if severe_event:
                violations.append(
                    RiskViolation(
                        "ERROR",
                        "SEVERE_EVENT_EXPOSURE",
                        f"satellite {symbol} has a severe event flag but remains allocated",
                    )
                )
            if risk_tier in {"high", "high_beta", "high-beta"}:
                high_beta_symbols.append(symbol)
                violations.append(
                    RiskViolation(
                        "INFO",
                        "HIGH_BETA_EXPOSURE",
                        f"high-beta satellite {symbol} contributes to portfolio risk",
                    )
                )
    if high_beta_symbols:
        high_beta_weight = sum(weights.get(symbol, 0.0) for symbol in high_beta_symbols)
        violations.append(
            RiskViolation(
                "INFO",
                "AGGREGATE_HIGH_BETA",
                f"aggregate high-beta exposure is {high_beta_weight:.2%}",
            )
        )
    for symbol in severe_symbols:
        if symbol not in resolved.satellite_symbols:
            violations.append(
                RiskViolation(
                    "ERROR",
                    "SEVERE_EVENT_EXPOSURE",
                    f"{symbol} has a severe event flag but remains allocated",
                )
            )
    if regime_name == "CAPITAL_PRESERVATION" and satellite_weight > limits.satellite_max + 1e-9:
        violations.append(
            RiskViolation(
                "ERROR",
                "REGIME_MISMATCH",
                "capital-preservation allocation exceeds its satellite envelope",
            )
        )
    return RiskCheckResult(tuple(violations))


def risk_gate(
    target_weights: Mapping[str, float] | Any,
    *,
    policy: Policy | None = None,
    regime: str = "NORMAL",
    assessments: Mapping[str, AssetAssessment | Mapping[str, Any]] | None = None,
    current_drawdown: float | None = None,
) -> RiskCheckResult:
    return run_risk_gate(
        target_weights,
        policy=policy,
        regime=regime,
        assessments=assessments,
        current_drawdown=current_drawdown,
    )


__all__ = ["RiskCheckResult", "RiskViolation", "risk_gate", "run_risk_gate"]
