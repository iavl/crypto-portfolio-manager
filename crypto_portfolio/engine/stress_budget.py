"""Stress-loss budget: ex-ante crash sizing from portfolio stress scenarios.

Strategy V2.3 Phase 3. ``stress_loss = sum(weight_i x scenario_return_i)``
over the risky sleeve for each configured scenario; the stress-loss budget
is the largest tolerable portfolio stress loss. The cap solver finds the
largest uniform scale of the risky sleeve whose WORST-scenario loss fits the
budget — the tightest of the strategic/volatility/stress/emergency caps in
the volatility-budget engine. HARD_TARGET semantics reuse
``risk.max_portfolio_drawdown`` as the budget; WARNING_BAND uses the
human-decided ``risk.hard_stress_loss_limit`` instead. Deterministic,
standard-library-only, fail-closed on missing scenario coverage.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

STRESS_BUDGET_CONTRACT = "STRESS_LOSS_BUDGET_V23"


def _risky_weights(weights: Mapping[str, float]) -> dict[str, float]:
    if not isinstance(weights, Mapping):
        raise ValueError("weights must be an object")
    parsed: dict[str, float] = {}
    for raw_symbol, raw_weight in weights.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError("weights contain an empty symbol")
        if symbol in parsed:
            raise ValueError(f"weights contain duplicate symbol {symbol}")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)) \
                or not math.isfinite(float(raw_weight)):
            raise ValueError(f"weights[{symbol}] must be a finite number")
        weight = float(raw_weight)
        if weight < 0:
            raise ValueError(f"weights[{symbol}] must be non-negative")
        parsed[symbol] = weight
    return parsed


def stress_loss_by_scenario(
    weights: Mapping[str, float],
    scenarios: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    """Portfolio stress loss per scenario: ``sum(w_i x r_i)`` over the sleeve.

    A positive weight on a symbol the scenario does not price is a clear
    error, never a silently skipped exposure. Returns are losses (negative
    fractions), so a scenario loss is reported as a negative fraction.
    """
    parsed = _risky_weights(weights)
    if not isinstance(scenarios, Mapping) or not scenarios:
        raise ValueError("scenarios must be a non-empty object")
    exposed = [symbol for symbol, weight in parsed.items() if weight > 0]
    result: dict[str, float] = {}
    for raw_name, raw_returns in scenarios.items():
        name = str(raw_name).strip()
        if not name:
            raise ValueError("scenario names must be non-empty")
        if not isinstance(raw_returns, Mapping):
            raise ValueError(f"scenario {name} must map symbols to returns")
        loss = 0.0
        for symbol in exposed:
            raw_return = raw_returns.get(symbol)
            if raw_return is None:
                raise ValueError(
                    f"stress scenario {name} does not price exposed asset {symbol}"
                )
            if isinstance(raw_return, bool) or not isinstance(raw_return, (int, float)) \
                    or not math.isfinite(float(raw_return)) or not -1.0 <= float(raw_return) <= 0.0:
                raise ValueError(
                    f"stress scenario {name} return for {symbol} must be a "
                    "fraction in [-1, 0]"
                )
            loss += parsed[symbol] * float(raw_return)
        result[name] = loss
    return result


def stress_loss_cap(
    weights: Mapping[str, float],
    scenarios: Mapping[str, Mapping[str, float]],
    budget: float,
) -> dict[str, Any]:
    """Largest uniform risky-sleeve scale whose worst stress loss fits.

    Returns ``cap`` in [0, 1] (``None`` when no scenario produces a loss, so
    the constraint is unavailable), the binding (worst) scenario, the loss at
    full scale, and the budget utilization of the unscaled sleeve.
    """
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) \
            or not math.isfinite(float(budget)) or not 0 < float(budget) <= 1:
        raise ValueError("budget must be a fraction in (0, 1]")
    losses = stress_loss_by_scenario(weights, scenarios)
    worst_name = min(losses, key=lambda name: losses[name])
    worst_loss = losses[worst_name]  # negative fraction or 0
    magnitude = -worst_loss
    if magnitude <= 1e-12:
        return {
            "cap": None,
            "binding_stress_scenario": None,
            "worst_loss": 0.0,
            "stress_budget_utilization": 0.0,
        }
    utilization = magnitude / float(budget)
    cap = 1.0 if utilization <= 1.0 else float(budget) / magnitude
    return {
        "cap": cap,
        "binding_stress_scenario": worst_name,
        "worst_loss": worst_loss,
        "stress_budget_utilization": utilization,
    }


def stress_budget_diagnostics(
    weights: Mapping[str, float],
    scenarios: Mapping[str, Mapping[str, float]],
    budget: float,
) -> dict[str, Any]:
    """Full ex-ante stress block reported by the allocation engine."""
    losses = stress_loss_by_scenario(weights, scenarios)
    solver = stress_loss_cap(weights, scenarios, budget)
    return {
        "contract": STRESS_BUDGET_CONTRACT,
        "budget": float(budget),
        "stress_loss_by_scenario": dict(sorted(losses.items())),
        "binding_stress_scenario": solver["binding_stress_scenario"],
        "stress_budget_utilization": solver["stress_budget_utilization"],
        "stress_cap": solver["cap"],
    }


__all__ = [
    "STRESS_BUDGET_CONTRACT",
    "stress_budget_diagnostics",
    "stress_loss_by_scenario",
    "stress_loss_cap",
]
