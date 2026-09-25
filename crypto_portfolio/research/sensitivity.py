"""Drawdown-budget sensitivity replay over a frozen dataset.

Re-runs the strict replay under alternative ``risk.max_portfolio_drawdown``
values and reports, per budget, the headline metrics plus whether the
drawdown budget was actually held (``|maxDD| <= budget + tolerance`` and zero
breach days).  With the drawdown budget overlay enabled, ``MaxDD ~= D`` is a
construction of ``risky_cap = 1 - |dd| / D``, not evidence of skill: the
interesting outputs are the exposure and return responses to the budget, not
the bound itself.  This module changes no policy files and writes nothing
into ``run.json``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from ..models.policy import policy_from_mapping
from .dataset import load_dataset
from .evidence_series import EvidenceContext, load_evidence_series
from .historical_builder import build_historical_reviews
from .orchestrator import run_historical_backtest

# Headroom for one-review discretization between the budget and the realized
# maximum drawdown; observed overshoot is two orders of magnitude smaller.
BUDGET_TOLERANCE = 0.01


def _series_maps(series: dict[str, Any], *, prefer_normalized: bool) -> dict[str, Any]:
    has_normalized = any(key.startswith("normalized:") for key in series)
    prefix = "normalized:" if (prefer_normalized and has_normalized) else "binance:"
    return {
        value.symbol: value
        for key, value in series.items()
        if key.startswith(prefix) and value.timeframe == "1D"
    }


def drawdown_budget_sensitivity(
    dataset_root: str | Path,
    *,
    budgets: tuple[float, ...] = (0.15, 0.20, 0.25),
    scope: str = "core",
    portfolio: str = "core_existing",
    policy_path: str | Path | None = None,
) -> dict[str, Any]:
    """Replay one strict experiment across alternative drawdown budgets."""
    if not budgets or any(
        isinstance(budget, bool) or not isinstance(budget, (int, float))
        or not 0 < float(budget) < 1 for budget in budgets
    ):
        raise ValueError("budgets must contain fractions in (0, 1)")
    root = Path(dataset_root)
    spec, manifest, series = load_dataset(root)
    if scope not in spec.asset_scopes:
        raise ValueError(
            f"scope must be one of {sorted(spec.asset_scopes)}: {scope!r} is not in the spec"
        )
    if portfolio not in spec.initial_portfolios:
        raise ValueError(
            f"portfolio must be one of {sorted(spec.initial_portfolios)}: {portfolio!r} is unknown"
        )
    config_path = Path(policy_path) if policy_path else Path(__file__).resolve().parents[2] / "config" / "policy.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    symbols = spec.asset_scopes[scope]
    if scope == "core":
        raw = copy.deepcopy(raw)
        raw["universe"]["satellites"] = []
    daily = _series_maps(series, prefer_normalized=manifest.strict_ready)
    harvested = load_evidence_series(root)
    evidence = EvidenceContext.from_series(harvested) if harvested else None
    rows: list[dict[str, Any]] = []
    for budget in budgets:
        modified = copy.deepcopy(raw)
        modified["risk"]["max_portfolio_drawdown"] = float(budget)
        policy = policy_from_mapping(modified)
        reviews = build_historical_reviews(
            daily_by_symbol=daily, execution_by_symbol=daily,
            hourly_by_symbol=daily, execution_timeframe=spec.execution_timeframe,
            symbols=symbols, initial_weights=dict(spec.initial_portfolios[portfolio]),
            initial_value=spec.initial_value_usd, start_at=spec.start_at,
            end_at=spec.end_at, policy=policy, semantic_score=None,
            evidence=evidence,
        )
        result = run_historical_backtest(
            reviews, policy=policy,
            fee_bps=float(spec.fee_bps), slippage_bps=float(spec.slippage_bps),
        )
        metrics = result["metrics"]
        trades = [trade for review in result["reviews"] for trade in review["trades"]]
        buys = sum(1 for trade in trades if trade["side"] == "BUY")
        rows.append({
            "budget": float(budget),
            "cagr": metrics["cagr"],
            "total_return": metrics["total_return"],
            "maximum_drawdown": metrics["maximum_drawdown"],
            "annualized_volatility": metrics["annualized_volatility"],
            "average_cash_weight": result["average_cash_weight"],
            "total_turnover": result["total_turnover"],
            "trades": len(trades),
            "buys": buys,
            "sells": len(trades) - buys,
            "breach_days": result["constraint_violation_counts"].get("DRAWDOWN_BREACH", 0),
            "budget_held": (
                abs(metrics["maximum_drawdown"]) <= float(budget) + BUDGET_TOLERANCE
                and result["constraint_violation_counts"].get("DRAWDOWN_BREACH", 0) == 0
            ),
        })
    overlay_enabled = bool(
        (policy_from_mapping(raw).drawdown_budget_overlay or {}).get("enabled", False)
    )
    return {
        "run_id": spec.run_id,
        "scope": scope,
        "portfolio": portfolio,
        "mode": "strict",
        "window": {"start_at": spec.start_at, "end_at": spec.end_at},
        "overlay_enabled": overlay_enabled,
        "mechanism_note": (
            "with the drawdown budget overlay enabled, maximum drawdown converging to the "
            "budget is a construction of risky_cap = 1 - |drawdown| / D; read the exposure "
            "and return response, not the bound"
            if overlay_enabled else
            "the drawdown budget overlay is disabled in this policy; the budget is not "
            "enforced at position level"
        ),
        "rows": rows,
    }


__all__ = ["BUDGET_TOLERANCE", "drawdown_budget_sensitivity"]
