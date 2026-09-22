"""Policy-boundary stress diagnostics independent of strategy profitability."""

from __future__ import annotations

from typing import Any

from ..engine.regime import RegimeInputs, determine_regime
from ..models.policy import Policy, resolve_policy


def drawdown_boundary_stress(policy: Policy | None = None) -> dict[str, Any]:
    resolved = policy or resolve_policy()
    budget = resolved.max_portfolio_drawdown
    samples = (-0.6 * budget + 0.0001, -0.6 * budget, -0.8 * budget + 0.0001,
               -0.8 * budget, -budget, -budget - 0.0001)
    rows = []
    levels = {"NORMAL": 0, "DEFENSIVE": 1, "CAPITAL_PRESERVATION": 2}
    previous_level = -1
    monotonic = True
    for drawdown in samples:
        result = determine_regime(
            RegimeInputs(
                btc_trend="BULLISH", volatility_state="LOW", flow_state="POSITIVE",
                breadth_state="HEALTHY", portfolio_drawdown_band=drawdown,
            ),
            policy=resolved,
            previous="NORMAL",
        )
        current_level = levels[result.regime]
        monotonic = monotonic and current_level >= previous_level
        previous_level = current_level
        rows.append({"drawdown": drawdown, "regime": result.regime, "reasons": list(result.reasons)})
    severe = determine_regime(
        RegimeInputs(
            btc_trend="BULLISH", volatility_state="LOW", flow_state="POSITIVE",
            breadth_state="HEALTHY", portfolio_drawdown_band=0.0,
            systemic_event_risk="SEVERE",
        ),
        policy=resolved,
        previous="NORMAL",
    )
    return {
        "risk_budget": budget, "boundaries": rows, "worsening_is_monotonic": monotonic,
        "severe_event_regime": severe.regime,
        "checks": {
            "minus_60pct_budget_at_least_defensive": levels[rows[1]["regime"]] >= levels["DEFENSIVE"],
            "minus_80pct_budget_is_capital_preservation": rows[3]["regime"] == "CAPITAL_PRESERVATION",
            "below_budget_is_capital_preservation": rows[-1]["regime"] == "CAPITAL_PRESERVATION",
            "severe_event_bypasses_transition_delay": severe.regime == "CAPITAL_PRESERVATION",
        },
    }


__all__ = ["drawdown_boundary_stress"]
