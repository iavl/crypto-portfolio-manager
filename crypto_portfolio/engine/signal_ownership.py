"""Signal ownership report (Strategy V2 Phase 3).

Deterministic, policy-derived diagnostic answering one question per signal
family: which layer holds primary authority over target allocation, and
which other layers merely read it. Reading a signal twice is not a bug —
double *counting* it toward target authority is. The report flags every
family whose consumers span more than one target-authority layer with
``MULTIPLE_POLICY_AUTHORITY`` so the residual overlaps stay visible instead
of hiding in configuration.
"""

from __future__ import annotations

from typing import Any

from ..models.policy import Policy, resolve_policy

# Target-authority layers: a layer in this set can move target allocation.
_TARGET_AUTHORITY_LAYERS = {
    "asset_score",
    "asset_selection",
    "regime",
    "risk_engine",
    "execution",
    "hard_risk_gate",
}

# The canonical ownership table (docs/SIGNAL_OWNERSHIP.md).
_PRIMARY_OWNERS: dict[str, str] = {
    "trend_momentum": "asset_score",
    "relative_strength": "asset_selection",
    "asset_relative_alpha": "asset_selection",
    "fundamentals": "asset_score",
    "valuation": "asset_score",
    "macro_liquidity": "regime",
    "market_breadth": "regime",
    "market_volatility": "risk_engine",
    "correlation": "risk_engine",
    "beta": "risk_engine",
    "atr": "execution",
    "support_resistance": "execution",
    "volume_profile": "execution",
    "breakout": "execution",
    "event_security": "hard_risk_gate",
    "liveness": "hard_risk_gate",
    "portfolio_drawdown": "risk_engine",
}


def _scoring_consumers(policy: Policy) -> dict[str, set[str]]:
    consumers: dict[str, set[str]] = {}
    for profile_name, weights in policy.scoring_profiles.items():
        for factor, weight in weights.items():
            if float(weight) <= 0:
                continue
            signal = {
                "trend": "trend_momentum",
                "relative_strength_btc": "relative_strength",
                "fundamentals": "fundamentals",
                "valuation": "valuation",
                "onchain": "fundamentals",
                "capital_flows": "macro_liquidity",
                "btc_valuation": "valuation",
                "macro_liquidity": "macro_liquidity",
            }.get(factor)
            if signal is not None:
                consumers.setdefault(signal, set()).add("asset_score")
    return consumers


def _regime_consumers(policy: Policy) -> dict[str, set[str]]:
    consumers: dict[str, set[str]] = {}
    weights = (policy.regime_model or {}).get("domain_weights", {})
    mapping = {
        "trend": ("trend_momentum", float(weights.get("trend", 0.0))),
        "volatility": ("market_volatility", float(weights.get("volatility", 0.0))),
        "flows": ("macro_liquidity", float(weights.get("flows", 0.0))),
        "breadth": ("market_breadth", float(weights.get("breadth", 0.0))),
    }
    for _, (signal, weight) in mapping.items():
        if weight > 0:
            consumers.setdefault(signal, set()).add("regime")
    return consumers


def _satellite_alpha_consumers(policy: Policy) -> dict[str, set[str]]:
    # Strategy V2.3 Phase 1: the per-asset BTC-relative alpha ensembles own
    # satellite deployment selection in volatility-budget mode; the generic
    # composite score no longer carries that authority there.
    consumers: dict[str, set[str]] = {}
    if (policy.risk_engine or {}).get("mode") == "volatility_budget":
        consumers.setdefault("asset_relative_alpha", set()).add("asset_selection")
    return consumers


def _risk_engine_consumers(policy: Policy) -> dict[str, set[str]]:
    consumers: dict[str, set[str]] = {}
    engine = policy.risk_engine or {}
    if engine.get("mode") == "volatility_budget":
        consumers.setdefault("market_volatility", set()).add("risk_engine")
        consumers.setdefault("correlation", set()).add("risk_engine")
        consumers.setdefault("beta", set()).add("risk_engine")
    if (policy.drawdown_budget_overlay or {}).get("enabled", False):
        # The drawdown overlay owns the emergency brake in both modes; in
        # legacy mode it additionally owns normal sizing (documented V1
        # carry-over, see docs/SIGNAL_OWNERSHIP.md).
        consumers.setdefault("portfolio_drawdown", set()).add("risk_engine")
    return consumers


def _execution_consumers(policy: Policy) -> dict[str, set[str]]:
    consumers: dict[str, set[str]] = {}
    factor_rules = policy.factor_rules or {}
    if "trend" in factor_rules:
        consumers.setdefault("trend_momentum", set()).add("execution")
    wait = (policy.execution_overlay or {}).get("wait", {})
    if wait.get("enabled", True):
        consumers.setdefault("atr", set()).add("execution")
    rebalance = policy.rebalance or {}
    if (rebalance.get("direction_flip_confirmation") or {}).get("enabled", False):
        consumers.setdefault("trend_momentum", set()).add("execution")
    execution = policy.execution or {}
    if execution.get("volume_profile") is not None or "volume_profile" in (policy.volume_profile or {}):
        consumers.setdefault("volume_profile", set()).add("execution")
    if execution.get("breakout"):
        consumers.setdefault("breakout", set()).add("execution")
    consumers.setdefault("support_resistance", set()).add("execution")
    return consumers


def _hard_gate_consumers() -> dict[str, set[str]]:
    return {
        "event_security": {"hard_risk_gate"},
        "liveness": {"hard_risk_gate"},
    }


def signal_ownership_report(policy: Policy | None = None) -> dict[str, Any]:
    """Per-signal ownership and residual multi-authority flags."""
    resolved = policy or resolve_policy()
    consumers: dict[str, set[str]] = {}
    for source in (
        _scoring_consumers(resolved),
        _satellite_alpha_consumers(resolved),
        _regime_consumers(resolved),
        _risk_engine_consumers(resolved),
        _execution_consumers(resolved),
        _hard_gate_consumers(),
    ):
        for signal, layers in source.items():
            consumers.setdefault(signal, set()).update(layers)
    rows: list[dict[str, Any]] = []
    for signal in sorted(set(_PRIMARY_OWNERS) | set(consumers)):
        primary = _PRIMARY_OWNERS.get(signal, "unassigned")
        layers = sorted(consumers.get(signal, set()))
        authority_layers = [layer for layer in layers if layer in _TARGET_AUTHORITY_LAYERS]
        rows.append({
            "signal": signal,
            "primary_owner": primary,
            "consumers": layers,
            "multiple_policy_authority": len(authority_layers) > 1,
            "authority_layers": authority_layers,
        })
    flagged = [row["signal"] for row in rows if row["multiple_policy_authority"]]
    regime_trend_weight = float((resolved.regime_model or {}).get("domain_weights", {}).get("trend", 0.0))
    return {
        "contract": "SIGNAL_OWNERSHIP_V2",
        "signals": rows,
        "multiple_policy_authority_signals": flagged,
        "notes": [
            "Reading a signal in more than one layer is allowed; holding "
            "target-allocation authority in more than one layer is flagged.",
            f"regime trend domain weight is {regime_trend_weight:.2f}: residual "
            "context-level trend authority in the regime after the Phase 3 "
            "reduction, pending Phase 6 walk-forward validation.",
            "portfolio_drawdown owns the emergency brake under the risk engine "
            "and additionally owns normal sizing while risk_engine.mode is "
            "legacy_drawdown (the frozen V1 behavior kept for A/B replay).",
        ],
    }


__all__ = ["signal_ownership_report"]
