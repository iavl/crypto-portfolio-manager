"""Deterministic market-regime classification from structured inputs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.policy import Policy, resolve_policy


_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_UNKNOWN = {"", "UNKNOWN", "UNAVAILABLE", "MISSING", "N/A"}
_SEVERE_EVENT = {"SEVERE", "CRITICAL", "SYSTEMIC", "CATASTROPHIC"}
_BEARISH = {"BEARISH", "BREAKDOWN", "DOWN", "WEAK"}
_ELEVATED_VOL = {"ELEVATED", "HIGH", "EXTREME"}
_RISK_OFF = {"OUTFLOW", "NEGATIVE", "WEAK", "CONTRACTION", "BEARISH", "DOWN"}


@dataclass(frozen=True)
class RegimeInputs:
    btc_trend: str = "UNKNOWN"
    volatility_state: str = "UNKNOWN"
    portfolio_drawdown_band: str | float = "UNKNOWN"
    flow_state: str = "UNKNOWN"
    breadth_state: str = "UNKNOWN"
    systemic_event_risk: bool | str = False


@dataclass(frozen=True)
class RegimeResult:
    regime: str
    confidence: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }


def _state(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, str):
        return value.strip().upper()
    return str(value).strip().upper()


def _drawdown_level(value: str | float, policy: Policy) -> tuple[bool, bool, str]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        drawdown = float(value)
        if drawdown != drawdown or drawdown == float("inf") or drawdown == float("-inf"):
            raise ValueError("portfolio_drawdown_band must be finite")
        budget = policy.max_portfolio_drawdown
        if drawdown <= -budget:
            return True, True, f"portfolio drawdown {drawdown:.2%} breached the risk budget"
        if drawdown <= -0.6 * budget:
            return True, False, f"portfolio drawdown {drawdown:.2%} is materially elevated"
        if drawdown <= -0.4 * budget:
            return True, False, f"portfolio drawdown {drawdown:.2%} needs reassessment"
        return False, False, f"portfolio drawdown {drawdown:.2%} is within the normal band"
    state = _state(value)
    if state in {"CAPITAL_PRESERVATION", "BREACH", "SEVERE"}:
        return True, True, f"portfolio drawdown band is {state}"
    if state in {"DEFENSIVE", "ELEVATED", "HIGH"}:
        return True, False, f"portfolio drawdown band is {state}"
    if state in _UNKNOWN:
        return False, False, "portfolio drawdown band is unavailable"
    return False, False, f"portfolio drawdown band is {state}"


def _drawdown_floor(value: str | float, policy: Policy) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        drawdown = float(value)
        if not math.isfinite(drawdown):
            raise ValueError("portfolio_drawdown_band must be finite")
        budget = policy.max_portfolio_drawdown
        if drawdown <= -0.8 * budget:
            return "CAPITAL_PRESERVATION"
        if drawdown <= -0.6 * budget:
            return "DEFENSIVE"
        return "NORMAL"
    state = _state(value)
    if state in {"CAPITAL_PRESERVATION", "BREACH", "SEVERE"}:
        return "CAPITAL_PRESERVATION"
    if state in {"DEFENSIVE", "ELEVATED", "HIGH"}:
        return "DEFENSIVE"
    return "NORMAL"


def determine_regime(
    inputs: RegimeInputs | dict[str, Any], *, policy: Policy | None = None
) -> RegimeResult:
    resolved = policy or resolve_policy()
    if isinstance(inputs, Mapping):
        inputs = RegimeInputs(**inputs)
    if not isinstance(inputs, RegimeInputs):
        raise ValueError("inputs must be RegimeInputs or a mapping")

    event = inputs.systemic_event_risk
    event_state = _state(event)
    if event is True or event_state in _SEVERE_EVENT:
        return RegimeResult(
            "CAPITAL_PRESERVATION",
            "HIGH" if event is True or event_state in _SEVERE_EVENT else "MEDIUM",
            ("severe systemic event risk overrides normal confirmation",),
        )
    if isinstance(event, str) and event_state in {"HIGH", "ELEVATED"}:
        return RegimeResult(
            "CAPITAL_PRESERVATION",
            "MEDIUM",
            (f"systemic event risk is {event_state}",),
        )

    risk_count = 0
    severe_count = 0
    reasons: list[str] = []
    unknown = 0

    trend = _state(inputs.btc_trend)
    if trend in _BEARISH:
        risk_count += 1
        reasons.append(f"BTC trend is {trend}")
    elif trend in _UNKNOWN:
        unknown += 1

    volatility = _state(inputs.volatility_state)
    if volatility in _ELEVATED_VOL:
        risk_count += 1
        reasons.append(f"volatility is {volatility}")
    elif volatility in _UNKNOWN:
        unknown += 1

    drawdown_risk, drawdown_severe, drawdown_reason = _drawdown_level(
        inputs.portfolio_drawdown_band, resolved
    )
    if drawdown_risk:
        risk_count += 1
        reasons.append(drawdown_reason)
    if drawdown_severe:
        severe_count += 1
    if _state(inputs.portfolio_drawdown_band) in _UNKNOWN:
        unknown += 1

    flow = _state(inputs.flow_state)
    if flow in _RISK_OFF:
        risk_count += 1
        reasons.append(f"capital flows are {flow}")
    elif flow in _UNKNOWN:
        unknown += 1

    breadth = _state(inputs.breadth_state)
    if breadth in _RISK_OFF:
        risk_count += 1
        reasons.append(f"market breadth is {breadth}")
    elif breadth in _UNKNOWN:
        unknown += 1

    if severe_count or risk_count >= 3:
        regime = "CAPITAL_PRESERVATION"
    elif risk_count >= 2:
        regime = "DEFENSIVE"
    else:
        regime = "NORMAL"
    floors = {"NORMAL": 0, "DEFENSIVE": 1, "CAPITAL_PRESERVATION": 2}
    regime = max(
        (regime, _drawdown_floor(inputs.portfolio_drawdown_band, resolved)),
        key=lambda name: floors[name],
    )
    confidence = "LOW" if unknown >= 3 else "MEDIUM" if unknown else "HIGH"
    if not reasons:
        reasons.append("no confirmed risk-off combination")
    return RegimeResult(regime, confidence, tuple(reasons))


def regime_engine(
    inputs: RegimeInputs | dict[str, Any], *, policy: Policy | None = None
) -> RegimeResult:
    return determine_regime(inputs, policy=policy)


__all__ = ["RegimeInputs", "RegimeResult", "determine_regime", "regime_engine"]
