"""Policy-boundary stress diagnostics independent of strategy profitability."""

from __future__ import annotations

from typing import Any

from ..engine.regime import RegimeInputs, determine_regime
from ..engine.risk import drawdown_budget_ladder_path, drawdown_budget_overlay_floor
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


def drawdown_budget_stress(policy: Policy | None = None) -> dict[str, Any]:
    """Stress the drawdown budget ladder itself, independent of any replay.

    Three questions a label-mapping boundary test cannot answer:

    - does the overlay floor rise monotonically as drawdown worsens, reaching
      a fully stable book at the budget limit;
    - does a severe risky-sleeve decline, applied in steps with the ladder
      rebalancing between steps, stay inside the budget (and does the same
      path clearly breach when the overlay is disabled, documenting why the
      overlay exists);
    - can a confirmed market recovery heal a budget-limit drawdown instead of
      locking the book at 100% stable forever.
    """
    resolved = policy or resolve_policy()
    budget = float(resolved.max_portfolio_drawdown)
    overlay = resolved.drawdown_budget_overlay or {}
    enabled = bool(overlay.get("enabled", False))
    recovery_reviews = int(overlay.get("recovery_reviews", 1))
    recovery_floor = float(overlay.get("recovery_risky_floor", 0.0))

    floors: list[dict[str, Any]] = []
    monotonic = True
    previous_floor = -1.0
    for fraction in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.5):
        drawdown = -fraction * budget
        floor, _ = drawdown_budget_overlay_floor(resolved, drawdown)
        capped = min(1.0, fraction)
        monotonic = monotonic and floor >= previous_floor - 1e-12
        previous_floor = floor
        floors.append({
            "drawdown": drawdown,
            "budget_consumed": capped,
            "stable_floor": floor,
            "floor_tracks_budget": abs(floor - (capped if enabled else 0.0)) < 1e-12,
        })

    severe = min(
        float(value) for symbol, value in resolved.stress_scenario.items()
        if symbol not in resolved.stable_symbols and not resolved.is_excluded(symbol)
    )
    # The severest configured single-asset decline applied to the whole risky
    # sleeve from the most exposed regime target; 40 geometric steps.
    normal_risky = 1.0 - resolved.regime("NORMAL").stablecoin_target
    ladder_on = drawdown_budget_ladder_path(
        resolved, risky_sleeve_return=severe, steps=40,
        regime_risky_weight=normal_risky,
    )
    disabled_policy = resolved
    if enabled:
        import dataclasses
        disabled_policy = dataclasses.replace(
            resolved, drawdown_budget_overlay={**(resolved.drawdown_budget_overlay or {}), "enabled": False}
        )
    ladder_off = drawdown_budget_ladder_path(
        disabled_policy, risky_sleeve_return=severe, steps=40,
        regime_risky_weight=normal_risky,
    )
    tolerance = ladder_on["single_step_bound"] + 1e-9
    ladder_holds = abs(ladder_on["worst_drawdown"]) <= ladder_on["budget"] + tolerance

    # Recovery: start at the budget limit with a confirmed market recovery,
    # then rally the risky sleeve; drawdown must heal strictly and the risky
    # allowance must stay above zero (no 100%-stable lock-in).
    rally = drawdown_budget_ladder_path(
        resolved, risky_sleeve_return=-severe / 2.0, steps=40,
        regime_risky_weight=normal_risky,
        start_drawdown=-budget, market_recovery_streak=recovery_reviews,
    )
    recovery_heals = rally["terminal_drawdown"] > -budget + 1e-9

    return {
        "enabled": enabled,
        "risk_budget": budget,
        "recovery_reviews": recovery_reviews,
        "recovery_risky_floor": recovery_floor,
        "floors": floors,
        "floor_monotonic": monotonic,
        "severe_sleeve_return": severe,
        "ladder_enabled_path": ladder_on,
        "ladder_disabled_path": ladder_off,
        "ladder_tolerance": tolerance,
        "checks": {
            "floor_monotone_nondecreasing": monotonic,
            "floor_reaches_full_stable_at_budget": abs(floors[5]["stable_floor"] - 1.0) < 1e-12,
            "severe_decline_stays_within_budget": ladder_holds,
            "disabled_overlay_breaches_on_same_path": (
                abs(ladder_off["worst_drawdown"]) > ladder_off["budget"] + tolerance
            ),
            "recovery_heals_budget_limit_drawdown": recovery_heals,
        },
    }


__all__ = ["drawdown_boundary_stress", "drawdown_budget_stress"]
