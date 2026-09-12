"""Deterministic market-regime classification from structured inputs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.confidence import ConfidenceCap, ConfidenceResult, DEFAULT_HIGH_MIN, DEFAULT_MEDIUM_MIN
from ..models.policy import Policy, resolve_policy
from .confidence import REGIME_DOMAIN_WEIGHTS, calculate_regime_confidence


_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_UNKNOWN = {"", "UNKNOWN", "UNAVAILABLE", "MISSING", "N/A"}
_SEVERE_EVENT = {"SEVERE", "CRITICAL"}
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
    domains: Mapping[str, Any] | None = None
    domain_confidence: Mapping[str, Any] | None = None
    evidence_ids: tuple[str, ...] = ()
    provenance_complete: bool = True

    def __post_init__(self) -> None:
        domains = self.domains if self.domains is not None else self.domain_confidence
        if domains is not None and not isinstance(domains, Mapping):
            raise ValueError("regime domains must be an object or null")
        object.__setattr__(self, "domains", dict(domains or {}))
        object.__setattr__(self, "domain_confidence", dict(self.domain_confidence or {}))
        if isinstance(self.evidence_ids, (str, bytes)):
            raise ValueError("regime evidence_ids must be a sequence")
        ids = tuple(str(item).strip() for item in self.evidence_ids)
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise ValueError("regime evidence_ids must contain unique non-empty values")
        object.__setattr__(self, "evidence_ids", ids)
        if not isinstance(self.provenance_complete, bool):
            raise ValueError("regime provenance_complete must be boolean")

    def as_dict(self) -> dict[str, Any]:
        return {
            "btc_trend": self.btc_trend,
            "volatility_state": self.volatility_state,
            "portfolio_drawdown_band": self.portfolio_drawdown_band,
            "flow_state": self.flow_state,
            "breadth_state": self.breadth_state,
            "systemic_event_risk": self.systemic_event_risk,
            "domains": dict(self.domains or {}),
            "domain_confidence": dict(self.domain_confidence or {}),
            "evidence_ids": list(self.evidence_ids),
            "provenance_complete": self.provenance_complete,
        }


@dataclass(frozen=True)
class RegimeResult:
    regime: str
    confidence: str
    reasons: tuple[str, ...]
    confidence_score: float | None = None
    raw_confidence_score: float | None = None
    domain_confidence: Mapping[str, Any] = None
    domain_weights: Mapping[str, float] = None
    caps: tuple[ConfidenceCap, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "confidence_score": self.confidence_score,
            "raw_confidence_score": self.raw_confidence_score,
            "domain_confidence": {
                name: value.as_dict() if isinstance(value, ConfidenceResult) else value
                for name, value in sorted((self.domain_confidence or {}).items())
            },
            "domain_weights": dict(self.domain_weights or {}),
            "caps": [item.as_dict() if isinstance(item, ConfidenceCap) else dict(item) for item in self.caps],
            "evidence_ids": list(self.evidence_ids),
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
        if drawdown < -budget:
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


_DOMAIN_NAMES = tuple(REGIME_DOMAIN_WEIGHTS)

_REGIME_LEVELS = {"NORMAL": 0, "DEFENSIVE": 1, "CAPITAL_PRESERVATION": 2}
_LEVEL_NAMES = ("NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION")


def _previous_regime_name(previous: Any) -> str | None:
    if previous is None:
        return None
    if isinstance(previous, RegimeResult):
        name: Any = previous.regime
    elif isinstance(previous, Mapping):
        name = previous.get("regime", previous.get("market_regime"))
    else:
        name = previous
    if name is None:
        return None
    name = str(name).strip().upper()
    if name in _UNKNOWN:
        return None
    if name not in _REGIME_LEVELS:
        raise ValueError("previous regime is unsupported")
    return name


def _cap_regime_transition(
    regime: str,
    floor_regime: str,
    previous: Any,
    resolved: Policy,
    reasons: list[str],
) -> str:
    """Bound regime transitions per review; mandatory floors stay immediate.

    A vote-based jump straight to CAPITAL_PRESERVATION (or straight back to
    NORMAL) must pass through one defensive review first, so a single noisy
    observation cannot rotate the stable sleeve by 35 points in either
    direction. Severe systemic events return before this cap applies, and the
    drawdown floor is re-asserted afterwards so `-0.6D`/`-0.8D` stay mandatory.
    """
    previous_name = _previous_regime_name(previous)
    if previous_name is None:
        return regime
    transitions = resolved.regime_transitions if isinstance(resolved.regime_transitions, Mapping) else {}
    if not transitions.get("enabled", True):
        return regime
    max_notches = transitions.get("max_notches_per_review", 1)
    if isinstance(max_notches, bool) or not isinstance(max_notches, int) or not 1 <= max_notches <= 2:
        raise ValueError("regime_transitions.max_notches_per_review must be 1 or 2")
    previous_level = _REGIME_LEVELS[previous_name]
    computed_level = _REGIME_LEVELS[regime]
    lower = max(0, previous_level - max_notches)
    upper = min(len(_LEVEL_NAMES) - 1, previous_level + max_notches)
    capped = min(max(computed_level, lower), upper)
    capped = max(capped, _REGIME_LEVELS[floor_regime])
    if capped != computed_level:
        reasons.append(
            f"transition from {previous_name} is capped at {max_notches} notch(es) per review; "
            f"the computed {regime} awaits confirmation"
        )
    return _LEVEL_NAMES[capped]


def _domain_score(value: Any, *, state: Any = None) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    if isinstance(value, ConfidenceResult):
        return value.score, value.reasons, value.evidence_ids
    if isinstance(value, Mapping):
        nested = value.get("data_confidence", value.get("confidence"))
        if isinstance(nested, Mapping) and "score" in nested:
            return (
                float(nested["score"]),
                tuple(str(item) for item in value.get("reasons", nested.get("reasons", ()))),
                tuple(str(item) for item in value.get("evidence_ids", nested.get("evidence_ids", ()))),
            )
        if "score" in value or "confidence_score" in value:
            return (
                float(value.get("score", value.get("confidence_score"))),
                tuple(str(item) for item in value.get("reasons", ())),
                tuple(str(item) for item in value.get("evidence_ids", ())),
            )
        state = value.get("state", state)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number) or not 0 <= number <= 1:
            raise ValueError("regime domain confidence must be finite and in [0, 1]")
        return number, (), ()
    normalized = _state(state if state is not None else value)
    if normalized in _UNKNOWN:
        return 0.0, ("UNKNOWN_DOMAIN",), ()
    return 1.0, (), ()


def _regime_confidence(
    inputs: RegimeInputs,
    resolved: Policy,
) -> ConfidenceResult:
    configured = resolved.confidence.get("regime_domain_weights", REGIME_DOMAIN_WEIGHTS) if resolved.confidence else REGIME_DOMAIN_WEIGHTS
    domains = inputs.domains or inputs.domain_confidence or {}
    scalar_input = not bool(domains)
    state_values = {
        "trend": inputs.btc_trend,
        "volatility": inputs.volatility_state,
        "breadth": inputs.breadth_state,
        "flows": inputs.flow_state,
        "portfolio_drawdown": inputs.portfolio_drawdown_band,
        "systemic_risk": inputs.systemic_event_risk,
    }
    scores: dict[str, Any] = {}
    reasons: set[str] = set()
    evidence_ids = set(inputs.evidence_ids)
    for name in _DOMAIN_NAMES:
        value = domains.get(name)
        score, domain_reasons, domain_evidence = _domain_score(value, state=state_values[name])
        scores[name] = score
        reasons.update(f"{name}:{item}" for item in domain_reasons)
        evidence_ids.update(domain_evidence)
    caps: list[ConfidenceCap] = []
    unknown = {name for name, score in scores.items() if score == 0}
    if scalar_input and inputs.provenance_complete and not (
        inputs.systemic_event_risk is True or _state(inputs.systemic_event_risk) in _SEVERE_EVENT
    ):
        caps.append(ConfidenceCap(
            "SCALAR_INPUT_NO_PROVENANCE",
            math.nextafter(DEFAULT_HIGH_MIN, 0.0),
            "PORTFOLIO",
            "regime caller supplied scalar states without provenance",
        ))
        reasons.add("SCALAR_INPUT_NO_PROVENANCE")
    if "portfolio_drawdown" in unknown and "systemic_risk" in unknown:
        caps.append(ConfidenceCap(
            "DRAWDOWN_SYSTEMIC_UNKNOWN",
            resolved.confidence.get("caps", {}).get("regime_drawdown_systemic_unknown", math.nextafter(DEFAULT_MEDIUM_MIN, 0.0)),
            "PORTFOLIO",
            "portfolio drawdown and systemic evidence are unknown",
        ))
    elif unknown & {"portfolio_drawdown", "systemic_risk"}:
        caps.append(ConfidenceCap(
            "CRITICAL_DOMAIN_UNKNOWN",
            resolved.confidence.get("caps", {}).get("regime_unknown_critical", math.nextafter(DEFAULT_HIGH_MIN, 0.0)),
            "PORTFOLIO",
            "a critical regime domain is unknown",
        ))
    return calculate_regime_confidence(
        scores,
        domain_weights=configured,
        caps=caps,
        reasons=reasons,
        evidence_ids=evidence_ids,
        policy=resolved,
    )


def determine_regime(
    inputs: RegimeInputs | dict[str, Any],
    *,
    policy: Policy | None = None,
    previous: RegimeResult | Mapping[str, Any] | str | None = None,
) -> RegimeResult:
    """Deterministic market-regime classification from structured inputs.

    ``previous`` is the prior review's effective regime (a RegimeResult, a
    mapping with ``regime``/``market_regime``, or the regime name). When the
    transition policy is enabled, the result moves at most
    ``max_notches_per_review`` notches away from it; severe systemic events and
    the mandatory drawdown floors are never delayed by the cap.
    """
    resolved = policy or resolve_policy()
    if isinstance(inputs, Mapping):
        inputs = RegimeInputs(**inputs)
    if not isinstance(inputs, RegimeInputs):
        raise ValueError("inputs must be RegimeInputs or a mapping")

    event = inputs.systemic_event_risk
    event_state = _state(event)
    if event is True or event_state in _SEVERE_EVENT:
        confidence = _regime_confidence(inputs, resolved)
        return RegimeResult(
            "CAPITAL_PRESERVATION",
            "HIGH" if event is True or event_state in _SEVERE_EVENT else confidence.band,
            ("severe systemic event risk overrides normal confirmation",),
            confidence.score,
            confidence.raw_score,
            confidence.dimensions,
            dict(resolved.confidence.get("regime_domain_weights", REGIME_DOMAIN_WEIGHTS)) if resolved.confidence else dict(REGIME_DOMAIN_WEIGHTS),
            confidence.caps,
            confidence.evidence_ids,
        )
    if isinstance(event, str) and event_state in {"HIGH", "ELEVATED"}:
        confidence = _regime_confidence(inputs, resolved)
        return RegimeResult(
            "CAPITAL_PRESERVATION",
            confidence.band,
            (f"systemic event risk is {event_state}",),
            confidence.score,
            confidence.raw_score,
            confidence.dimensions,
            dict(resolved.confidence.get("regime_domain_weights", REGIME_DOMAIN_WEIGHTS)) if resolved.confidence else dict(REGIME_DOMAIN_WEIGHTS),
            confidence.caps,
            confidence.evidence_ids,
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
    floor_regime = _drawdown_floor(inputs.portfolio_drawdown_band, resolved)
    regime = max((regime, floor_regime), key=lambda name: floors[name])
    regime = _cap_regime_transition(regime, floor_regime, previous, resolved, reasons)
    confidence_result = _regime_confidence(inputs, resolved)
    confidence = confidence_result.band
    if not reasons:
        reasons.append("no confirmed risk-off combination")
    return RegimeResult(
        regime,
        confidence,
        tuple(reasons),
        confidence_result.score,
        confidence_result.raw_score,
        confidence_result.dimensions,
        dict(resolved.confidence.get("regime_domain_weights", REGIME_DOMAIN_WEIGHTS)) if resolved.confidence else dict(REGIME_DOMAIN_WEIGHTS),
        confidence_result.caps,
        confidence_result.evidence_ids,
    )


def regime_engine(
    inputs: RegimeInputs | dict[str, Any],
    *,
    policy: Policy | None = None,
    previous: RegimeResult | Mapping[str, Any] | str | None = None,
) -> RegimeResult:
    return determine_regime(inputs, policy=policy, previous=previous)


__all__ = ["RegimeInputs", "RegimeResult", "determine_regime", "regime_engine"]
