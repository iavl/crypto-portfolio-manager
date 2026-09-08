"""Portfolio-level allocation risk gate."""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Iterable, Mapping

from ..models.evidence import AssetAssessment, EventRiskAssessment
from ..models.market_overlays import MarketOverlays
from ..models.policy import Policy, resolve_policy


_SEVERITIES = {"ERROR", "WARNING", "INFO"}
_CHAIN_LIVENESS_STATUSES = {"HEALTHY", "DEGRADED", "HALTED", "UNKNOWN", "FAILED", "CONFLICT"}
_EVENT_RISK_STATES = {"NORMAL", "ELEVATED", "HIGH", "SEVERE", "CRITICAL"}


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
    deployment_caps: Mapping[str, float] = dataclass_field(default_factory=dict)
    blocked_symbols: tuple[str, ...] = ()

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
            "deployment_caps": dict(self.deployment_caps),
            "blocked_symbols": list(self.blocked_symbols),
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


def _event_risk_state(value: Any) -> str:
    raw = value.event_risk if isinstance(value, AssetAssessment) else (
        value.get("event_risk") if isinstance(value, Mapping) else None
    )
    if isinstance(raw, EventRiskAssessment):
        state = raw.state
    else:
        if isinstance(raw, Mapping):
            raw = raw.get("state")
        state = None
    if state is None and raw is not None:
        state = str(raw).strip().upper()
        if state not in _EVENT_RISK_STATES:
            raise ValueError("assessment event_risk must be a recognized state")
    if isinstance(value, Mapping) and "severe_event" in value:
        raise ValueError("severe_event is unsupported; use event_risk.state")
    thesis = value.thesis_broken if isinstance(value, AssetAssessment) else (
        value.get("thesis_broken", False) if isinstance(value, Mapping) else False
    )
    if not isinstance(thesis, bool):
        raise ValueError("assessment thesis_broken must be boolean")
    return "SEVERE" if thesis and state != "CRITICAL" else state or "NORMAL"


def event_risk_deployment_factor(state: str, *, policy: Policy | None = None) -> float:
    normalized = str(state).strip().upper()
    if normalized not in _EVENT_RISK_STATES:
        raise ValueError("event risk state is unsupported")
    resolved = policy or resolve_policy()
    value = float(resolved.event_risk_multipliers[normalized])
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("event risk multiplier must be finite and in [0, 1]")
    return value


def _assessment(value: Any) -> tuple[str, str, str]:
    if isinstance(value, AssetAssessment):
        return value.confidence, _event_risk_state(value), value.risk_tier
    if isinstance(value, Mapping):
        confidence = str(value.get("confidence", "LOW")).upper()
        if confidence not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("assessment confidence must be HIGH, MEDIUM, or LOW")
        return (
            confidence,
            _event_risk_state(value),
            str(value.get("risk_tier", "normal")).lower(),
        )
    return "LOW", "NORMAL", "normal"


def _liveness_status(value: Any, field: str) -> str:
    if isinstance(value, Mapping):
        value = value.get("status")
    elif hasattr(value, "status"):
        value = value.status
    if not isinstance(value, str) or value.strip().upper() not in _CHAIN_LIVENESS_STATUSES:
        raise ValueError(f"{field} must be a recognized chain liveness status")
    return value.strip().upper()


def _liveness_values(
    value: Any | None,
    assessments: Mapping[str, Any],
) -> dict[str, str]:
    result: dict[str, str] = {}
    if value is not None:
        if hasattr(value, "asset") and hasattr(value, "status"):
            items = ((value.asset, value),)
        elif not isinstance(value, Mapping):
            raise ValueError("chain_liveness must be an object mapping assets to statuses")
        else:
            items = value.items()
        for raw_symbol, raw_status in items:
            symbol = str(raw_symbol).strip().upper()
            if not symbol:
                raise ValueError("chain_liveness contains an empty asset")
            if symbol not in {"BTC", "ETH", "SOL", "BNB"}:
                raise ValueError(f"chain liveness is not applicable to {symbol}")
            result[symbol] = _liveness_status(raw_status, f"chain_liveness.{symbol}")
    for raw_symbol, assessment in assessments.items():
        symbol = str(raw_symbol).strip().upper()
        if not isinstance(assessment, Mapping):
            continue
        for key in ("chain_liveness",):
            if key in assessment:
                if symbol not in {"BTC", "ETH", "SOL", "BNB"}:
                    raise ValueError(f"chain liveness is not applicable to {symbol}")
                result.setdefault(symbol, _liveness_status(assessment[key], f"assessments.{symbol}.{key}"))
                break
    return result


def _increase_symbols(actions: Iterable[Any] | None) -> set[str]:
    result: set[str] = set()
    for action in actions or ():
        if isinstance(action, Mapping):
            symbol = action.get("symbol")
            name = action.get("action")
            amount = action.get("amount_usd", 0)
        else:
            symbol = getattr(action, "symbol", None)
            name = getattr(action, "action", None)
            amount = getattr(action, "amount_usd", 0)
        if str(name).strip().upper() == "INCREASE" and float(amount) > 0:
            result.add(str(symbol).strip().upper())
    return result


def chain_liveness_deployment_factor(
    status: str,
    *,
    policy: Policy | None = None,
) -> float:
    """Return the maximum immediate deployment factor for one liveness state."""
    normalized = _liveness_status(status, "status")
    if normalized == "HEALTHY":
        return 1.0
    if normalized == "DEGRADED":
        resolved = policy or resolve_policy()
        configured = resolved.chain_liveness["degraded_deployment_factor"]
        if isinstance(configured, bool) or not isinstance(configured, (int, float)):
            raise ValueError("chain_liveness.degraded_deployment_factor must be numeric")
        configured = float(configured)
        if not math.isfinite(configured) or not 0 < configured <= 1:
            raise ValueError("chain_liveness.degraded_deployment_factor must be in (0, 1]")
        return configured
    return 0.0


def apply_chain_liveness_deployment_cap(
    amount_usd: float,
    status: str,
    *,
    policy: Policy | None = None,
) -> float:
    if isinstance(amount_usd, bool) or not isinstance(amount_usd, (int, float)):
        raise ValueError("amount_usd must be numeric")
    amount = float(amount_usd)
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("amount_usd must be finite and >= 0")
    return amount * chain_liveness_deployment_factor(status, policy=policy)


def run_risk_gate(
    target_weights: Mapping[str, float] | Any,
    *,
    policy: Policy | None = None,
    regime: str = "NORMAL",
    assessments: Mapping[str, AssetAssessment | Mapping[str, Any]] | None = None,
    current_drawdown: float | None = None,
    overlays: MarketOverlays | Mapping[str, Any] | None = None,
    chain_liveness: Mapping[str, Any] | None = None,
    actions: Iterable[Any] | None = None,
    current_weights: Mapping[str, float] | None = None,
    decision_confidence: Any | None = None,
) -> RiskCheckResult:
    resolved = policy or resolve_policy()
    if hasattr(target_weights, "target_weights"):
        target_weights = target_weights.target_weights
    try:
        weights = _weights(target_weights)
    except ValueError as exc:
        return RiskCheckResult((RiskViolation("ERROR", "INVALID_WEIGHTS", str(exc)),))
    violations: list[RiskViolation] = []
    excluded_targets = {
        symbol for symbol, weight in weights.items()
        if weight > 0 and resolved.is_excluded(symbol)
    }
    if excluded_targets:
        violations.append(
            RiskViolation(
                "ERROR",
                "EXCLUDED_ASSET_TARGET",
                "excluded assets cannot receive target allocation: "
                + ", ".join(sorted(excluded_targets)),
            )
        )
    total = sum(weights.values())
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        violations.append(RiskViolation("ERROR", "TOTAL_NOT_ONE", "target weights must sum to 1"))

    regime_name = regime.regime if hasattr(regime, "regime") else str(regime).upper()
    limits = resolved.regime(regime_name)
    stable_weight = sum(weights.get(symbol, 0.0) for symbol in resolved.stable_symbols)
    required_stable = max(resolved.min_stablecoin_weight, limits.stablecoin_target)
    if stable_weight + 1e-9 < required_stable:
        violations.append(
            RiskViolation(
                "ERROR",
                "STABLECOIN_FLOOR",
                f"stablecoin weight {stable_weight:.2%} is below required {required_stable:.2%}",
            )
        )

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
                "ERROR",
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
    event_caps: dict[str, float] = {}
    for raw_symbol, assessment in assessments.items():
        symbol = str(raw_symbol).strip().upper()
        if resolved.is_excluded(symbol):
            continue
        confidence, event_risk, risk_tier = _assessment(assessment)
        if event_risk != "NORMAL" and weights.get(symbol, 0.0) > 0:
            event_caps[symbol] = event_risk_deployment_factor(event_risk, policy=resolved)
        if event_risk in {"SEVERE", "CRITICAL"} and weights.get(symbol, 0.0) > 0:
            severe_symbols.append(symbol)
        elif event_risk in {"ELEVATED", "HIGH"} and weights.get(symbol, 0.0) > 0:
            multiplier = event_risk_deployment_factor(event_risk, policy=resolved)
            violations.append(
                RiskViolation(
                    "WARNING",
                    "EVENT_RISK_DEPLOYMENT_CAP",
                    f"{symbol} event risk is {event_risk}; new deployment is capped at {multiplier:.0%}",
                )
            )
        if symbol in resolved.satellite_symbols and weights.get(symbol, 0.0) > 0:
            if confidence == "LOW":
                violations.append(
                    RiskViolation(
                        "WARNING",
                        "LOW_CONFIDENCE_EXPOSURE",
                        f"low-confidence satellite {symbol} has non-zero exposure",
                    )
                )
            if event_risk in {"SEVERE", "CRITICAL"}:
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
    if overlays is not None:
        overlay_values = overlays if isinstance(overlays, MarketOverlays) else MarketOverlays.from_mapping(overlays)
        if resolved.positioning.get("enabled", True):
            for symbol, facts in overlay_values.positioning_by_asset.items():
                if facts.bias in {"LONG_BIASED", "LONG_CROWDED"} and facts.risk in {"HIGH", "EXTREME"}:
                    violations.append(
                        RiskViolation(
                            "WARNING",
                            "POSITIONING_CROWDED_LONG",
                            f"{symbol} has confirmed long-crowded positioning; immediate deployment should be conservative",
                        )
                    )
                if facts.risk == "EXTREME" or facts.leverage_state == "EXTREME":
                    violations.append(
                        RiskViolation(
                            "WARNING",
                            "POSITIONING_EXTREME",
                            f"{symbol} has extreme positioning risk; overlays cannot increase or independently exit exposure",
                        )
                    )
        cycle = overlay_values.btc_cycle
        if cycle is not None and resolved.btc_cycle.get("enabled", True):
            if cycle.cycle_risk == "HIGH":
                violations.append(
                    RiskViolation(
                        "WARNING",
                        "BTC_CYCLE_RISK_HIGH",
                        "BTC cycle context has high non-clock risk confirmation; target allocation remains unchanged",
                    )
                )
            elif cycle.cycle_risk == "ELEVATED":
                violations.append(
                    RiskViolation(
                        "WARNING",
                        "BTC_CYCLE_RISK_ELEVATED",
                        "BTC cycle context has elevated non-clock risk confirmation; deployment may be reduced",
                        )
                    )
    liveness_values = _liveness_values(chain_liveness, assessments)
    action_values = tuple(actions or ())
    increase_symbols = _increase_symbols(action_values)
    if decision_confidence is not None:
        confidence_score = (
            getattr(decision_confidence, "score", None)
            if not isinstance(decision_confidence, Mapping)
            else decision_confidence.get("score", decision_confidence.get("confidence_score"))
        )
        if confidence_score is not None:
            confidence_score = float(confidence_score)
            if not math.isfinite(confidence_score) or not 0 <= confidence_score <= 1:
                raise ValueError("decision_confidence score must be finite and in [0, 1]")
            if increase_symbols and confidence_score < 0.60:
                violations.append(
                    RiskViolation(
                        "ERROR",
                        "DECISION_CONFIDENCE_BLOCK",
                        "decision confidence is LOW; new INCREASE exposure is blocked",
                    )
                )
            elif confidence_score < 0.80:
                violations.append(
                    RiskViolation(
                        "WARNING",
                        "DECISION_CONFIDENCE_CAP",
                        "decision confidence is MEDIUM; new deployment must remain reduced",
                    )
                )
    current = _weights(current_weights) if current_weights is not None else None
    deployment_caps: dict[str, float] = dict(event_caps)
    blocked_symbols: list[str] = []
    for symbol, status in liveness_values.items():
        factor = chain_liveness_deployment_factor(status, policy=resolved)
        if status == "HEALTHY":
            continue
        deployment_caps[symbol] = min(deployment_caps.get(symbol, 1.0), factor)
        has_exposure = (
            symbol in increase_symbols
            or (
                current is not None
                and weights.get(symbol, 0.0) > current.get(symbol, 0.0) + 1e-12
            )
            or (current is None and not action_values and weights.get(symbol, 0.0) > 0)
        )
        if status == "DEGRADED":
            if has_exposure:
                violations.append(
                    RiskViolation(
                        "WARNING",
                        "CHAIN_LIVENESS_DEGRADED",
                        f"{symbol} chain liveness is DEGRADED; immediate new deployment is capped at {factor:.0%}",
                    )
                )
        elif has_exposure:
            blocked_symbols.append(symbol)
            code = "CHAIN_LIVENESS_HALTED" if status == "HALTED" else "CHAIN_LIVENESS_UNAVAILABLE"
            message = (
                f"{symbol} chain liveness is HALTED; INCREASE/new exposure is blocked"
                if status == "HALTED"
                else f"{symbol} chain liveness is {status}; hard-critical evidence blocks INCREASE/new exposure"
            )
            violations.append(RiskViolation("ERROR", code, message))
    return RiskCheckResult(tuple(violations), deployment_caps, tuple(dict.fromkeys(blocked_symbols)))


__all__ = [
    "RiskCheckResult",
    "RiskViolation",
    "apply_chain_liveness_deployment_cap",
    "chain_liveness_deployment_factor",
    "event_risk_deployment_factor",
    "run_risk_gate",
]
