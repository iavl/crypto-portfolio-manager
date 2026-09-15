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


def _flow_component(value: Any, name: str) -> dict[str, Any]:
    """Normalize one asset's ETF-flow component to dollar flow, AUM, and ratio."""
    if value is None:
        return {"available": False}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} flow component must be an object or null")
    unknown = set(value) - {"net_flow_usd", "aum_usd", "flow_ratio"}
    if unknown:
        raise ValueError(f"{name} flow component contains unknown fields: {', '.join(sorted(unknown))}")
    net_flow = value.get("net_flow_usd")
    aum = value.get("aum_usd")
    ratio = value.get("flow_ratio")
    result: dict[str, Any] = {"available": True}
    for field, raw in (("net_flow_usd", net_flow), ("aum_usd", aum), ("flow_ratio", ratio)):
        if raw is None:
            result[field] = None
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
            raise ValueError(f"{name} flow component {field} must be a finite number or null")
        result[field] = float(raw)
    if result["net_flow_usd"] is not None and result["aum_usd"] not in (None, 0.0):
        if result["aum_usd"] < 0:
            raise ValueError(f"{name} flow component aum_usd must be >= 0")
        if result["flow_ratio"] is None:
            result["flow_ratio"] = result["net_flow_usd"] / result["aum_usd"]
    if result["flow_ratio"] is not None and abs(result["flow_ratio"]) > 1.0:
        raise ValueError(f"{name} flow component ratio must be a decimal fraction within +/-1")
    if not any(result[field] is not None for field in ("net_flow_usd", "flow_ratio")):
        # An AUM-only component carries no flow direction at all.
        result["available"] = False
    return result


def aggregate_market_flow(
    btc_flow: Mapping[str, Any] | None,
    eth_flow: Mapping[str, Any] | None,
    *,
    policy: Policy | None = None,
) -> dict[str, Any]:
    """Aggregate BTC and ETH ETF flows into a market-level regime flow state.

    The market regime's flow domain must describe broad market liquidity,
    not a BTC alias: dollar flows are aggregated against combined AUM, and
    pre-normalized ratios aggregate by AUM weight (equal weight only when
    AUM is unavailable, with reduced confidence and explicit fallback
    provenance). A single available major component is a documented
    fallback, never a silent BTC substitution; asset-level states stay
    available on the components for BTC/ETH scoring.
    """
    resolved = policy or resolve_policy()
    components = {"BTC": _flow_component(btc_flow, "BTC"), "ETH": _flow_component(eth_flow, "ETH")}
    available = [name for name, item in components.items() if item["available"]]
    assessment: dict[str, Any] = {
        "components": components,
        "component_states": {
            name: (classify_flow_state(item["flow_ratio"], policy=resolved) if item["available"] else "UNKNOWN")
            for name, item in components.items()
        },
        "aggregate_flow_ratio": None,
        "method": "unavailable",
        "state": "UNKNOWN",
        "confidence": "LOW",
        "fallback_provenance": (),
    }
    if not available:
        assessment["fallback_provenance"] = ("NO_MARKET_FLOW_COMPONENTS",)
        return assessment
    if len(available) == 1:
        only = components[available[0]]
        ratio = only["flow_ratio"]
        assessment["aggregate_flow_ratio"] = ratio
        assessment["state"] = classify_flow_state(ratio, policy=resolved)
        assessment["method"] = "single_asset_fallback"
        assessment["confidence"] = "MEDIUM"
        assessment["fallback_provenance"] = (f"ONLY_{available[0]}_AVAILABLE",)
        return assessment
    btc, eth = components["BTC"], components["ETH"]
    btc_aum, eth_aum = btc.get("aum_usd"), eth.get("aum_usd")
    if (
        btc.get("net_flow_usd") is not None and btc_aum
        and eth.get("net_flow_usd") is not None and eth_aum
    ):
        total_flow = btc["net_flow_usd"] + eth["net_flow_usd"]
        total_aum = btc_aum + eth_aum
        assessment["aggregate_flow_ratio"] = total_flow / total_aum
        assessment["method"] = "dollar_aum_aggregation"
        assessment["confidence"] = "HIGH"
    elif btc.get("flow_ratio") is not None and eth.get("flow_ratio") is not None:
        if btc_aum and eth_aum:
            weight = btc_aum / (btc_aum + eth_aum)
            assessment["aggregate_flow_ratio"] = (
                weight * btc["flow_ratio"] + (1.0 - weight) * eth["flow_ratio"]
            )
            assessment["method"] = "aum_weighted_ratios"
            assessment["confidence"] = "HIGH"
        else:
            assessment["aggregate_flow_ratio"] = 0.5 * (btc["flow_ratio"] + eth["flow_ratio"])
            assessment["method"] = "equal_weighted_ratios"
            assessment["confidence"] = "MEDIUM"
            assessment["fallback_provenance"] = ("COMPONENT_AUM_MISSING",)
    else:
        # One side has dollars, the other only a ratio: no common basis
        # without AUM, so fail defensive rather than invent one.
        assessment["method"] = "unavailable"
        assessment["fallback_provenance"] = ("INCONSISTENT_COMPONENT_BASIS",)
        return assessment
    assessment["state"] = classify_flow_state(assessment["aggregate_flow_ratio"], policy=resolved)
    return assessment


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
    "aggregate_market_flow",
    "breadth_state",
    "btc_trend_state",
    "build_regime_inputs",
    "flow_state",
    "volatility_state",
]
