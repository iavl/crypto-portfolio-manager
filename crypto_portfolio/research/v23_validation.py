"""Strategy V2.3 validation: ladder, risk/alpha attribution, decisions.

Phase 6 of Strategy V2.3. Everything is preregistered research: the A-I
ladder is built from explicit policy overrides (never parameter searches),
rungs D/E/F only activate an asset tilt when that asset's signals passed the
unified admission on BOTH windows, and the policy decision matrix derives
KEEP / RESEARCH_ONLY / REMOVE from cross-window risk-adjusted deltas plus
the admission verdicts. Every experiment is stamped with the git SHA, policy
hash, dataset manifest, alpha registry hash, risk budget mode, and cash
carry convention. No output feeds back into the allocation engines.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..engine.portfolio_risk import PortfolioRiskInputs
from ..models.policy import Policy, policy_from_mapping
from .v21_validation import validation_manifest

V23_STRATEGY_VERSION = "strategy-v2.3"

HORIZONS = (30, 90, 180)


def v23_manifest(
    policy: Policy,
    *,
    git_sha: str,
    data_manifest: Mapping[str, Any] | None = None,
    alpha_registry_hash: str | None = None,
    cash_carry_convention: str | None = None,
) -> dict[str, Any]:
    """Freeze the full V2.3 experiment identity (plan 10.1)."""
    return {
        **validation_manifest(policy, git_sha=git_sha, data_manifest=data_manifest),
        "strategy_version": V23_STRATEGY_VERSION,
        "alpha_registry_hash": alpha_registry_hash,
        "risk_budget_mode": policy.drawdown_budget_mode,
        "stress_loss_budget": dict(policy.stress_loss_budget or {}),
        "cash_carry_convention": cash_carry_convention,
    }


def _copy(policy: Policy | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(policy, Mapping):
        return json.loads(json.dumps(dict(policy)))
    return json.loads(json.dumps(policy.as_dict()))


def _pin_recovery_disabled(data: dict[str, Any]) -> None:
    data["risk_engine"]["recovery"].update({
        "stage_1_reviews": 10**6, "stage_2_reviews": 10**6, "release_reviews": 10**6,
    })


def v23_ablation_ladder(
    policy: Policy,
    *,
    bnb_admitted_signals: Sequence[str] = (),
    aave_admitted_signals: Sequence[str] = (),
    eth_tilt_admitted: bool = False,
) -> list[dict[str, Any]]:
    """Preregistered V2.3 rungs (plan 10.2) as explicit policy overrides.

    A BTC-only volatility targeting; B adds the stress-loss budget; C adds
    Regime V2 (volatility authority removed from the label, R1 preregistered);
    D/E/F add the BNB/AAVE/ETH admitted alpha tilts (each a diagnostic no-op
    when that asset's admission failed); G adds the emergency recovery FSM;
    H adds execution costs; I is the full V2.3 assembly. Rungs A-G run
    without costs so each decision layer's delta is measured pre-cost.
    """
    base = _copy(policy)
    base["risk_engine"]["mode"] = "volatility_budget"
    base["core_allocation"]["mode"] = "btc_baseline_with_active_tilts"
    base["core_allocation"]["eth"]["tilt_enabled"] = False
    base["risk"]["stress_loss_budget"]["enabled"] = False
    for symbol in ("BNB", "AAVE"):
        base["satellite_alpha"][symbol]["tilt_enabled"] = False
        base["satellite_alpha"][symbol]["admitted_signals"] = []
    ladder: list[dict[str, Any]] = []

    def rung(letter, name, note, data, *, zero_cost=True):
        ladder.append({
            "rung": letter, "name": name, "adds": note, "policy": data,
            "zero_cost": zero_cost,
        })

    # A: BTC-only volatility targeting (no satellites, no stress layer, no
    # regime scaling, recovery FSM pinned, ETH tilt off).
    data = _copy(base)
    data["universe"]["satellites"] = []
    data["satellite_alpha"] = {}
    data["risk_engine"]["regime_risk_scaling"] = {
        regime: {"target_volatility_multiplier": 1.0}
        for regime in ("NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION")
    }
    _pin_recovery_disabled(data)
    rung("A", "btc_volatility_targeting",
         "BTC baseline sized by the volatility budget alone", data)

    # B: + stress loss budget (HARD_TARGET crash sizing).
    data = _copy(ladder[-1]["policy"])
    data["risk"]["stress_loss_budget"]["enabled"] = True
    rung("B", "stress_loss_budget",
         "the worst crash scenario's portfolio loss must fit the budget ex ante", data)

    # C: + Regime V2 (preregistered R1: volatility authority removed).
    data = _copy(ladder[-1]["policy"])
    data["regime_model"]["excluded_domains"] = ["volatility"]
    data["risk_engine"]["regime_risk_scaling"] = json.loads(json.dumps(
        policy.as_dict()["risk_engine"]["regime_risk_scaling"]
    ))
    rung("C", "regime_v2",
         "regime keeps trend/flows/breadth; realized volatility belongs to the "
         "volatility budget only", data)

    # D: + BNB admitted alpha tilt.
    data = _copy(ladder[-1]["policy"])
    data["universe"]["satellites"] = json.loads(json.dumps(
        policy.as_dict()["universe"]["satellites"]
    ))
    data["satellite_alpha"] = json.loads(json.dumps(
        policy.as_dict()["satellite_alpha"]
    ))
    if bnb_admitted_signals:
        data["satellite_alpha"]["BNB"]["tilt_enabled"] = True
        data["satellite_alpha"]["BNB"]["admitted_signals"] = list(bnb_admitted_signals)
    rung("D", "bnb_admitted_alpha",
         "BNB's admitted BTC-relative ensemble earns the preregistered tilt "
         "(diagnostic no-op when admission failed)", data)

    # E: + AAVE admitted alpha tilt.
    data = _copy(ladder[-1]["policy"])
    if aave_admitted_signals:
        data["satellite_alpha"]["AAVE"]["tilt_enabled"] = True
        data["satellite_alpha"]["AAVE"]["admitted_signals"] = list(aave_admitted_signals)
    rung("E", "aave_admitted_alpha",
         "AAVE's admitted BTC-relative ensemble earns the preregistered tilt "
         "(diagnostic no-op when admission failed)", data)

    # F: + ETH admitted alpha tilt.
    data = _copy(ladder[-1]["policy"])
    if eth_tilt_admitted:
        data["core_allocation"]["eth"]["tilt_enabled"] = True
    rung("F", "eth_admitted_alpha",
         "the admission-gated ETH/BTC tilt (diagnostic no-op when admission "
         "failed)", data)

    # G: + emergency recovery FSM.
    data = _copy(ladder[-1]["policy"])
    data["risk_engine"]["recovery"] = json.loads(json.dumps(
        policy.as_dict()["risk_engine"]["recovery"]
    ))
    rung("G", "emergency_fsm",
         "staged re-risk after confirmed market recovery", data)

    # H: + execution (costs on).
    data = _copy(ladder[-1]["policy"])
    rung("H", "execution_costs",
         "the same configuration with execution costs applied", data, zero_cost=False)

    # I: full V2.3 — the production-candidate assembly, costs on.
    data = _copy(ladder[-1]["policy"])
    rung("I", "full_v23",
         "the complete V2.3 strategy exactly as validated", data, zero_cost=False)
    return ladder


def v23_metrics_table(result: Mapping[str, Any]) -> dict[str, Any]:
    """The V2.3 metric set (plan 10.4) for one backtest result."""
    metrics = result["metrics"]
    comparison = (
        result.get("benchmark_comparison", {}).get("vol_matched_btc_cash_investable") or {}
    )
    btc_hold = result.get("benchmark_comparison", {}).get("btc_buy_and_hold_investable") or {}
    return {
        "cagr": metrics.get("cagr"),
        "max_drawdown": metrics.get("maximum_drawdown"),
        "volatility": metrics.get("annualized_volatility"),
        "sharpe": metrics.get("sharpe_rf_zero"),
        "sortino": metrics.get("sortino_target_zero"),
        "calmar": metrics.get("calmar"),
        "cvar_95_period": metrics.get("cvar_95_period"),
        "worst_30d_return": metrics.get("worst_30d_return"),
        "worst_90d_return": metrics.get("worst_90d_return"),
        "total_return": metrics.get("total_return"),
        "turnover": result.get("total_turnover"),
        "trading_cost_usd": result.get("total_cost_usd"),
        "average_cash_weight": result.get("average_cash_weight"),
        "cash_carry_contribution": (result.get("cash_carry") or {}).get(
            "carry_contribution"
        ),
        "vol_matched_excess_annualized": comparison.get("excess_return_annualized"),
        "btc_baseline_excess_annualized": btc_hold.get("excess_return_annualized"),
    }


def risk_attribution(result: Mapping[str, Any]) -> dict[str, Any]:
    """Risk attribution (plan 10.6) from one backtest result."""
    valuations = result.get("valuations") or []
    drawdowns = [float(item["drawdown"]) for item in valuations]

    def days_beyond(level: float) -> int:
        return sum(1 for drawdown in drawdowns if drawdown < -level)

    stress_utilizations: list[float] = []
    worst_stress_losses: list[float] = []
    binding_counts: dict[str, int] = {}
    for row in result.get("reviews") or ():
        engine = (row.get("allocation") or {}).get("risk_engine") or {}
        binding = engine.get("binding_risk_constraint")
        if binding is not None:
            binding_counts[str(binding)] = binding_counts.get(str(binding), 0) + 1
        stress = engine.get("stress_budget") or {}
        if stress:
            utilization = stress.get("stress_budget_utilization")
            if isinstance(utilization, (int, float)):
                stress_utilizations.append(float(utilization))
            losses = stress.get("stress_loss_by_scenario") or {}
            for loss in losses.values():
                if isinstance(loss, (int, float)):
                    worst_stress_losses.append(float(loss))
    total = len(result.get("reviews") or ())
    return {
        "valuation_days": len(drawdowns),
        "days_beyond_15pct": days_beyond(0.15),
        "days_beyond_20pct": days_beyond(0.20),
        "days_beyond_25pct": days_beyond(0.25),
        "worst_stress_loss": min(worst_stress_losses) if worst_stress_losses else None,
        "avg_stress_budget_utilization": (
            sum(stress_utilizations) / len(stress_utilizations)
            if stress_utilizations else None
        ),
        "binding_constraint_shares": {
            name: count / total for name, count in sorted(binding_counts.items())
        } if total else {},
    }


def _module_verdict(
    deltas: Sequence[float | None], *, admitted: bool | None = None
) -> dict[str, Any]:
    known = [value for value in deltas if value is not None]
    if not known or len(known) < len(deltas):
        return {"decision": "RESEARCH_ONLY",
                "reason": "insufficient windows with a runnable configuration"}
    if all(value > 0 for value in known):
        decision, reason = "KEEP", "positive risk-adjusted contribution in every window"
    elif all(value < 0 for value in known):
        decision = "REMOVE" if admitted is not True else "RESEARCH_ONLY"
        reason = "negative contribution in every window"
    else:
        decision, reason = "RESEARCH_ONLY", "window-inconsistent contribution"
    if admitted is False and decision == "KEEP":
        decision = "RESEARCH_ONLY"
        reason += "; admission not passed, production authority stays locked"
    return {"decision": decision, "reason": reason}


def v23_policy_decision_matrix(
    primary: Mapping[str, Any],
    sister: Mapping[str, Any] | None,
    *,
    bnb_admitted: bool,
    aave_admitted: bool,
    eth_admitted: bool,
) -> dict[str, Any]:
    """KEEP / RESEARCH_ONLY / REMOVE per module (plan 10.7).

    Args mirror the ladder results: primary and (optionally) sister.

    The verdict uses vol-matched excess deltas vs the previous rung,
    consistency across both windows, and the admission verdicts; unadmitted
    alpha layers can never earn KEEP.
    """

    def delta_pair(predecessor: str, rung: str) -> list[float | None]:
        def excess(source: Mapping[str, Any]) -> float | None:
            by_letter = {item.get("rung"): item for item in source.get("rungs", [])}
            base = (by_letter.get(predecessor) or {}).get("metrics") or {}
            target = (by_letter.get(rung) or {}).get("metrics") or {}
            if not base or not target:
                return None
            if base.get("vol_matched_excess_annualized") is None \
                    or target.get("vol_matched_excess_annualized") is None:
                return None
            return (
                target["vol_matched_excess_annualized"]
                - base["vol_matched_excess_annualized"]
            )

        return [excess(primary), excess(sister) if sister else None]

    matrix = {
        "stress_loss_budget": _module_verdict(delta_pair("A", "B")),
        "regime_v2": _module_verdict(delta_pair("B", "C")),
        "bnb_admitted_alpha": _module_verdict(
            delta_pair("C", "D"), admitted=bnb_admitted,
        ),
        "aave_admitted_alpha": _module_verdict(
            delta_pair("D", "E"), admitted=aave_admitted,
        ),
        "eth_admitted_alpha": _module_verdict(
            delta_pair("E", "F"), admitted=eth_admitted,
        ),
        "emergency_fsm": _module_verdict(delta_pair("F", "G")),
        "execution_layer": _module_verdict(delta_pair("G", "H")),
        "btc_baseline_core": {
            "decision": "KEEP",
            "reason": "preregistered V2.3 foundation: every tilt is measured against it",
        },
    }
    return {
        "rule": (
            "vol-matched excess delta vs the previous rung, consistent across "
            "both windows; unadmitted alpha layers cap at RESEARCH_ONLY"
        ),
        "modules": matrix,
    }


def run_v23_ablation(
    reviews: Sequence[Any],
    *,
    policy: Policy,
    risk_inputs_by_review: Sequence[PortfolioRiskInputs],
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    git_sha: str = "unset",
    data_manifest: Mapping[str, Any] | None = None,
    alpha_registry_hash: str | None = None,
    cash_carry: Any = None,
    cash_carry_name: str | None = None,
    bnb_admitted_signals: Sequence[str] = (),
    aave_admitted_signals: Sequence[str] = (),
    eth_tilt_admitted: bool = False,
    backtest_runner: Any = None,
) -> dict[str, Any]:
    """Run the preregistered V2.3 A-I ladder over frozen reviews."""
    if backtest_runner is None:
        from .orchestrator import run_historical_backtest as backtest_runner
    ladder = v23_ablation_ladder(
        policy,
        bnb_admitted_signals=bnb_admitted_signals,
        aave_admitted_signals=aave_admitted_signals,
        eth_tilt_admitted=eth_tilt_admitted,
    )
    rungs: list[dict[str, Any]] = []
    previous: Mapping[str, Any] | None = None
    for entry in ladder:
        rung_policy = policy_from_mapping(entry["policy"])
        result = backtest_runner(
            reviews, policy=rung_policy,
            fee_bps=0.0 if entry["zero_cost"] else fee_bps,
            slippage_bps=0.0 if entry["zero_cost"] else slippage_bps,
            risk_inputs_by_review=risk_inputs_by_review,
            cash_carry=cash_carry,
        )
        table = v23_metrics_table(result)
        delta = (
            {key: (table[key] - previous[key] if table[key] is not None
                   and previous[key] is not None else None)
             for key in table}
            if previous is not None else None
        )
        rungs.append({
            "rung": entry["rung"], "name": entry["name"], "adds": entry["adds"],
            "manifest": v23_manifest(
                rung_policy, git_sha=git_sha, data_manifest=data_manifest,
                alpha_registry_hash=alpha_registry_hash,
                cash_carry_convention=cash_carry_name,
            ),
            "metrics": table, "delta_vs_previous": delta,
            "risk_attribution": risk_attribution(result),
            "regime_counts": result.get("regime_counts"),
            "stall_attribution": result.get("stall_attribution"),
            "cash_carry": result.get("cash_carry"),
            "result": result,
        })
        previous = table
    return {
        "strategy_version": V23_STRATEGY_VERSION,
        "rungs": rungs,
        "note": "preregistered mechanism isolations; not parameter searches",
    }


def alpha_attribution_report(rungs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Module-level CAGR attribution from the ladder deltas (plan 10.5)."""
    by_letter = {
        str(rung.get("rung")): rung for rung in rungs if rung.get("metrics") is not None
    }

    def cagr(letter: str) -> float | None:
        table = by_letter.get(letter, {}).get("metrics") or {}
        return table.get("cagr")

    def delta(predecessor: str, rung: str) -> float | None:
        left, right = cagr(predecessor), cagr(rung)
        return right - left if left is not None and right is not None else None

    attribution = {
        "btc_baseline_cagr": cagr("A"),
        "stress_loss_budget_cagr_delta": delta("A", "B"),
        "regime_v2_cagr_delta": delta("B", "C"),
        "bnb_alpha_cagr_delta": delta("C", "D"),
        "aave_alpha_cagr_delta": delta("D", "E"),
        "eth_alpha_cagr_delta": delta("E", "F"),
        "emergency_fsm_cagr_delta": delta("F", "G"),
        "execution_cost_cagr_delta": delta("G", "H"),
        "cash_carry_contribution": (
            by_letter.get("I", {}).get("metrics", {}).get("cash_carry_contribution")
        ),
        "full_v23_cagr": cagr("I"),
    }
    parts = [
        attribution["btc_baseline_cagr"],
        attribution["stress_loss_budget_cagr_delta"],
        attribution["regime_v2_cagr_delta"],
        attribution["bnb_alpha_cagr_delta"],
        attribution["aave_alpha_cagr_delta"],
        attribution["eth_alpha_cagr_delta"],
        attribution["emergency_fsm_cagr_delta"],
        attribution["execution_cost_cagr_delta"],
    ]
    total = attribution["full_v23_cagr"]
    if total is not None and all(part is not None for part in parts):
        attribution["interaction_residual"] = total - sum(part for part in parts)
    else:
        attribution["interaction_residual"] = None
    return attribution


__all__ = [
    "V23_STRATEGY_VERSION",
    "alpha_attribution_report",
    "risk_attribution",
    "run_v23_ablation",
    "v23_ablation_ladder",
    "v23_manifest",
    "v23_metrics_table",
    "v23_policy_decision_matrix",
]
