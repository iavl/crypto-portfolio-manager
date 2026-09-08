"""Deterministic conversion of technical/factual data into regime inputs."""

from __future__ import annotations

import math
from typing import Any, Mapping

from ..facts.models import FlowFacts
from ..models.market import TechnicalSnapshot
from ..models.policy import Policy, resolve_policy
from .factors.flows import classify_flow_state
from .regime import RegimeInputs


def btc_trend_state(snapshot: TechnicalSnapshot | Mapping[str, Any] | None) -> str:
    if snapshot is None:
        return "UNKNOWN"
    value = snapshot.get("trend_state") if isinstance(snapshot, Mapping) else snapshot.trend_state
    value = str(value).strip().upper()
    if value in {"STRONG_UPTREND", "UPTREND", "BULLISH", "RISING"}:
        return "BULLISH"
    if value in {"STRONG_DOWNTREND", "DOWNTREND", "BEARISH", "BREAKDOWN", "WEAK"}:
        return "BEARISH"
    if value in {"NEUTRAL", "RANGE", "SIDEWAYS"}:
        return "NEUTRAL"
    return "UNKNOWN"


def volatility_state(snapshot: TechnicalSnapshot | Mapping[str, Any] | None) -> str:
    if snapshot is None:
        return "UNKNOWN"
    value = snapshot.get("volatility_state") if isinstance(snapshot, Mapping) else snapshot.volatility_state
    value = str(value).strip().upper()
    if value in {"HIGH", "EXTREME", "ELEVATED"}:
        return "ELEVATED"
    if value in {"LOW", "NORMAL"}:
        return value
    return "UNKNOWN"


def flow_state(value: Any, *, policy: Policy | None = None) -> str:
    if isinstance(value, FlowFacts):
        return classify_flow_state(value, policy=policy)
    if hasattr(value, "current") and isinstance(value.current, Mapping):
        values = tuple(value.current.values())
        return classify_flow_state(values[-1] if values else None, policy=policy)
    if isinstance(value, Mapping) and "state" in value:
        state = str(value["state"]).strip().upper()
        if state not in {"POSITIVE", "NEUTRAL", "NEGATIVE", "UNKNOWN"}:
            raise ValueError("flow state is unsupported")
        return state
    return classify_flow_state(value, policy=policy)


def breadth_state(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, Mapping):
        value = value.get("state")
    elif hasattr(value, "current") and isinstance(value.current, Mapping):
        values = tuple(value.current.values())
        value = values[-1] if values else None
    if isinstance(value, str):
        state = value.strip().upper()
        if state == "HEALTHY":
            return "HEALTHY"
        if state == "WEAK":
            return "WEAK"
        if state == "NEUTRAL":
            return "NEUTRAL"
        if state == "UNKNOWN":
            return state
        raise ValueError("breadth state is unsupported")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("breadth must be a state or numeric fraction")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("breadth fraction must be finite and in [0, 1]")
    if value >= 0.6:
        return "HEALTHY"
    if value <= 0.4:
        return "WEAK"
    return "NEUTRAL"


def build_regime_inputs(
    btc_snapshot: TechnicalSnapshot | Mapping[str, Any] | None = None,
    flow_facts: FlowFacts | Mapping[str, Any] | float | int | None = None,
    portfolio_drawdown: float | str | None = "UNKNOWN",
    breadth: Any = "UNKNOWN",
    systemic_event_risk: bool | str = False,
    *,
    policy: Policy | None = None,
    domains: Mapping[str, Any] | None = None,
    domain_confidence: Mapping[str, Any] | None = None,
    evidence_ids: tuple[str, ...] = (),
    provenance_complete: bool = True,
) -> RegimeInputs:
    """Build inputs for the existing deterministic regime authority."""
    resolved = policy or resolve_policy()
    if isinstance(portfolio_drawdown, Mapping):
        portfolio_drawdown = portfolio_drawdown.get("portfolio_drawdown", "UNKNOWN")
    elif hasattr(portfolio_drawdown, "current_drawdown"):
        portfolio_drawdown = portfolio_drawdown.current_drawdown
    return RegimeInputs(
        btc_trend=btc_trend_state(btc_snapshot),
        volatility_state=volatility_state(btc_snapshot),
        portfolio_drawdown_band="UNKNOWN" if portfolio_drawdown is None else portfolio_drawdown,
        flow_state=flow_state(flow_facts, policy=resolved),
        breadth_state=breadth_state(breadth),
        systemic_event_risk=systemic_event_risk,
        domains=domains,
        domain_confidence=domain_confidence,
        evidence_ids=evidence_ids,
        provenance_complete=provenance_complete,
    )


__all__ = [
    "RegimeInputs",
    "breadth_state",
    "btc_trend_state",
    "build_regime_inputs",
    "flow_state",
    "volatility_state",
]
