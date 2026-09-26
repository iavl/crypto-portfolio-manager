"""Strategy V2.1 validation: ablation ladder, stall and score attribution.

Phase E of Strategy V2.1. Everything here is research-only diagnostics: the
preregistered A-H ablation ladder is built from explicit policy overrides,
stall attribution classifies reviews that executed nothing from the data the
replay already produced, and the version manifest freezes the exact code,
policy, and data identity behind every experiment. No output of this module
feeds back into the allocation or risk engines.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from ..engine.portfolio_risk import PortfolioRiskInputs
from ..models.policy import Policy, policy_from_mapping

V21_SCORING_VERSION = "scoring-v3-phase-c"

STALL_REASONS = (
    "EVENT_RISK",
    "LIVENESS",
    "EMERGENCY_OVERLAY",
    "RECOVERY_STATE",
    "VOLATILITY_BUDGET",
    "MARKET_REGIME",
    "EVIDENCE_BLOCK",
    "SCORE_BLOCK",
    "EXECUTION_WAIT",
    "REBALANCE_BAND",
    "NO_CAPITAL_AVAILABLE",
)


def _canonical_sha256(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validation_manifest(
    policy: Policy,
    *,
    git_sha: str,
    data_manifest: Mapping[str, Any] | None = None,
    universe_rules: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze the identity of one validation experiment (plan 8.2)."""
    resolved_universe = universe_rules or {
        "core": list(policy.core_symbols),
        "satellites": list(policy.satellite_symbols),
        "stable": list(policy.stable_symbols),
    }
    return {
        "git_sha": git_sha,
        "policy_hash": _canonical_sha256(policy.as_dict()),
        "risk_engine_mode": (policy.risk_engine or {}).get("mode", "legacy_drawdown"),
        "scoring_v3_version": V21_SCORING_VERSION,
        "scoring_v3_families_sha256": _canonical_sha256(policy.scoring_v3),
        "recovery_config": dict((policy.risk_engine or {}).get("recovery", {})),
        "capital_hierarchy": dict(policy.capital_hierarchy or {}),
        "universe_rules": resolved_universe,
        "data_manifest": dict(data_manifest) if data_manifest else None,
    }


def _copy(policy: Policy | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(policy, Mapping):
        return json.loads(json.dumps(dict(policy)))
    return json.loads(json.dumps(policy.as_dict()))


def v21_ablation_ladder(policy: Policy) -> list[dict[str, Any]]:
    """Preregistered A-H rungs (plan 8.4) as explicit policy overrides.

    Each rung names exactly what it adds over the previous one; the rungs are
    mechanism isolations, never parameter searches. The structural ablation
    (rung D) mirrors the market family onto the structural family so structure
    carries no independent information while keeping both families well
    formed; the recovery ablation (rungs A-E) pins the FSM past any feasible
    trough horizon so de-risking stays but staged re-risk never happens.
    """
    base = _copy(policy)
    base["risk_engine"]["mode"] = "volatility_budget"
    ladder: list[dict[str, Any]] = []

    def rung(letter: str, name: str, note: str, data: dict[str, Any], zero_cost: bool = False):
        ladder.append({
            "rung": letter,
            "name": name,
            "adds": note,
            "policy": data,
            "zero_cost": zero_cost,
        })

    # A: BTC volatility targeting only.
    data = _copy(base)
    data["universe"]["satellites"] = []
    data["core_allocation"]["anchor"] = {"BTC": 1.0, "ETH": 0.0}
    data["risk_engine"]["regime_risk_scaling"] = {
        regime: {"target_volatility_multiplier": 1.0}
        for regime in ("NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION")
    }
    data["risk_engine"]["recovery"].update({
        "stage_1_reviews": 10**6, "stage_2_reviews": 10**6, "release_reviews": 10**6,
    })
    rung("A", "btc_volatility_targeting", "BTC-only sleeve sized by the volatility budget", data)

    # B: + market-only regime scaling.
    data = _copy(ladder[-1]["policy"])
    data["risk_engine"]["regime_risk_scaling"] = json.loads(json.dumps(
        policy.as_dict()["risk_engine"]["regime_risk_scaling"]
    ))
    rung("B", "market_only_regime", "regime scales the target volatility band", data)

    # C: + ETH tilt.
    data = _copy(ladder[-1]["policy"])
    data["core_allocation"]["anchor"] = json.loads(json.dumps(
        policy.as_dict()["core_allocation"]["anchor"]
    ))
    rung("C", "eth_tilt", "ETH competes for core budget via the anchor", data)

    # D: + market-score satellites (structure mirrors the market family).
    data = _copy(ladder[-1]["policy"])
    data["universe"]["satellites"] = json.loads(json.dumps(
        policy.as_dict()["universe"]["satellites"]
    ))
    market_mirror = {"trend": 0.34, "relative_strength_btc": 0.33, "capital_flows": 0.33}
    for families in data["scoring_v3"]["families"].values():
        families["structural"] = dict(market_mirror)
    for families in data["scoring_v3"]["asset_families"].values():
        families["structural"] = dict(market_mirror)
    rung("D", "market_score_satellites", "satellites enter on market scores alone", data)

    # E: + structural score.
    data = _copy(ladder[-1]["policy"])
    data["scoring_v3"] = json.loads(json.dumps(policy.as_dict()["scoring_v3"]))
    rung("E", "structural_score", "full conviction requires structural evidence", data)

    # F: + emergency recovery FSM.
    data = _copy(ladder[-1]["policy"])
    data["risk_engine"]["recovery"] = json.loads(json.dumps(
        policy.as_dict()["risk_engine"]["recovery"]
    ))
    rung("F", "recovery_fsm", "staged re-risk after confirmed market recovery", data)

    # G: + execution layer costs isolated (F without costs).
    rung("G", "execution_layer_reference", "rung F without execution costs",
         _copy(ladder[-1]["policy"]), zero_cost=True)

    # H: full Strategy V2.1.
    rung("H", "full_v21", "the complete V2.1 policy with costs", _copy(base))
    return ladder


def _metrics_table(result: Mapping[str, Any]) -> dict[str, Any]:
    metrics = result["metrics"]
    comparison = result.get("benchmark_comparison", {}).get("vol_matched_btc_cash_investable") or {}
    return {
        "cagr": metrics.get("cagr"),
        "max_drawdown": metrics.get("maximum_drawdown"),
        "volatility": metrics.get("annualized_volatility"),
        "sharpe": metrics.get("sharpe_rf_zero"),
        "sortino": metrics.get("sortino_target_zero"),
        "total_return": metrics.get("total_return"),
        "turnover": result.get("total_turnover"),
        "average_cash_weight": result.get("average_cash_weight"),
        "vol_matched_excess_annualized": comparison.get("excess_return_annualized"),
    }


def run_v21_ablation(
    reviews: Sequence[Any],
    *,
    policy: Policy,
    daily_by_symbol: Mapping[str, Any],
    risk_inputs_by_review: Sequence[PortfolioRiskInputs],
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    git_sha: str = "unset",
    backtest_runner: Any = None,
) -> dict[str, Any]:
    """Run the preregistered A-H ladder over identical frozen reviews.

    ``backtest_runner`` is injectable for tests; production passes
    ``run_historical_backtest``. Every rung carries the frozen validation
    manifest and the metric delta against the previous rung.
    """
    if backtest_runner is None:
        from .orchestrator import run_historical_backtest as backtest_runner
    ladder = v21_ablation_ladder(policy)
    rungs: list[dict[str, Any]] = []
    previous: Mapping[str, Any] | None = None
    for entry in ladder:
        rung_policy = policy_from_mapping(entry["policy"])
        result = backtest_runner(
            reviews, policy=rung_policy,
            fee_bps=0.0 if entry["zero_cost"] else fee_bps,
            slippage_bps=0.0 if entry["zero_cost"] else slippage_bps,
            risk_inputs_by_review=risk_inputs_by_review,
        )
        table = _metrics_table(result)
        delta = (
            {key: (table[key] - previous[key] if table[key] is not None and previous[key] is not None else None)
             for key in table}
            if previous is not None else None
        )
        rungs.append({
            "rung": entry["rung"],
            "name": entry["name"],
            "adds": entry["adds"],
            "manifest": validation_manifest(rung_policy, git_sha=git_sha),
            "metrics": table,
            "delta_vs_previous": delta,
            "stall_attribution": result.get("stall_attribution"),
            "emergency_recovery": result.get("emergency_recovery_diagnostics"),
            "risk_authority_separation": result.get("risk_authority_separation"),
        })
        previous = table
    return {
        "scoring_v3_version": V21_SCORING_VERSION,
        "rungs": rungs,
        "note": "preregistered mechanism isolations; not parameter searches",
        "daily_symbols": sorted(str(symbol) for symbol in daily_by_symbol),
    }


def classify_stall(row: Mapping[str, Any]) -> str | None:
    """Deterministic root cause for a review that executed nothing (plan 8.7).

    ``row`` is one ``review_rows`` entry from ``run_historical_backtest``.
    A review with trades is never stalled. The reason is the first matching
    predicate in a fixed order, so the classification is reproducible from
    the replay record alone.
    """
    if row.get("trades"):
        return None
    risk_gate = row.get("risk_gate") or {}
    codes = {
        str(violation.get("code"))
        for violation in risk_gate.get("violations", ())
    }
    allocation = row.get("allocation") or {}
    engine = allocation.get("risk_engine") or {}
    emergency = engine.get("emergency_overlay_state") or {}
    state = str(emergency.get("state", "NORMAL"))
    binding = str(engine.get("binding_risk_constraint", ""))
    allowances = allocation.get("deployment_allowances") or {}
    rebalance = row.get("rebalance") or {}
    entry_outcomes = row.get("entry_outcomes") or {}
    if "CHAIN_LIVENESS_HALTED" in codes or "CHAIN_LIVENESS_UNAVAILABLE" in codes:
        return "LIVENESS"
    if "SEVERE_EVENT_EXPOSURE" in codes or "EVENT_RISK_DEPLOYMENT_CAP" in codes:
        return "EVENT_RISK"
    if state in {"EMERGENCY", "BREACH"}:
        return "EMERGENCY_OVERLAY"
    if state in {"RECOVERY_1", "RECOVERY_2"}:
        return "RECOVERY_STATE"
    if binding == "volatility_budget":
        return "VOLATILITY_BUDGET"
    if binding == "emergency_overlay":
        return "EMERGENCY_OVERLAY"
    if any(str(entry.get("status")) == "WAIT" for entry in entry_outcomes.values()):
        return "EXECUTION_WAIT"
    regime_row = row.get("regime") or {}
    if str(regime_row.get("regime")) in {"DEFENSIVE", "CAPITAL_PRESERVATION"}:
        return "MARKET_REGIME"
    for details in allowances.values():
        if details.get("preserve_existing") or str(details.get("conviction_state")) == "NO_NEW_RISK":
            return "EVIDENCE_BLOCK"
        if str(details.get("eligibility_state")) == "INELIGIBLE" and str(
            details.get("conviction_state")
        ) in {"WATCH_ONLY", "HARD_EXIT"}:
            return "SCORE_BLOCK"
    if str(rebalance.get("decision")) in {"HOLD", "NO_TRADE"}:
        return "REBALANCE_BAND"
    return "NO_CAPITAL_AVAILABLE"


def stall_attribution(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate stall reasons over the replay (plan 8.7)."""
    reasons: dict[str, int] = {name: 0 for name in STALL_REASONS}
    stalled = 0
    current_streak = 0
    longest_streak = 0
    for row in rows:
        reason = classify_stall(row)
        if reason is None:
            current_streak = 0
            continue
        stalled += 1
        reasons[reason] = reasons.get(reason, 0) + 1
        current_streak += 1
        longest_streak = max(longest_streak, current_streak)
    total = len(rows)
    return {
        "reviews": total,
        "stalled_reviews": stalled,
        "stalled_share": stalled / total if total else 0.0,
        "reason_counts": {name: reasons[name] for name in STALL_REASONS if reasons[name]},
        "longest_consecutive_stalled_reviews": longest_streak,
        "trading_stalled_root_causes": sorted(
            (name for name, count in reasons.items() if count),
            key=lambda name: -reasons[name],
        ),
    }


def evaluate_v3_family_scores(
    observations: Iterable[Mapping[str, Any]],
    prices: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    policy: Policy,
    horizons: Sequence[int] = (30, 90, 180),
) -> dict[str, Any]:
    """Ranking power of the market and structural families (plan 8.5).

    Derives each family's normalized score from the observation's factor
    scores through the canonical scoring_v3 functions, then evaluates both
    families and the composite on the same forward-return machinery. The
    conviction-state distribution is reported alongside so tactical versus
    full-conviction samples can be told apart.
    """
    from ..engine.scoring_v3 import asset_conviction_state
    from .score_evaluation import evaluate_scores

    market_observations: list[dict[str, Any]] = []
    structural_observations: list[dict[str, Any]] = []
    convictions: dict[str, int] = {}
    for raw in observations:
        symbol = str(raw["symbol"]).strip().upper()
        assessment = {
            "factor_scores": dict(raw.get("factor_scores", {})),
            "score_coverage": raw.get("coverage"),
            "normalized_score": raw.get("normalized_score"),
            "weighted_score": raw.get("score"),
            "critical_data_complete": True,
        }
        block = asset_conviction_state(assessment, policy=policy, symbol=symbol)
        convictions[block["conviction_state"]] = convictions.get(block["conviction_state"], 0) + 1
        base = {
            "timestamp": raw["timestamp"], "symbol": symbol,
            "factor_scores": dict(raw.get("factor_scores", {})),
            "synthetic": bool(raw.get("synthetic", False)),
        }
        if block["normalized_market_score"] is not None:
            market_observations.append({
                **base, "score": float(block["market_score"]),
                "normalized_score": float(block["normalized_market_score"]),
                "coverage": float(block["market_coverage"]),
            })
        if block["normalized_structural_score"] is not None:
            structural_observations.append({
                **base, "score": float(block["structural_score"]),
                "normalized_score": float(block["normalized_structural_score"]),
                "coverage": float(block["structural_coverage"]),
            })
    return {
        "scoring_v3_version": V21_SCORING_VERSION,
        "market": evaluate_scores(market_observations, prices, horizons=horizons),
        "structural": evaluate_scores(structural_observations, prices, horizons=horizons),
        "conviction_state_counts": dict(sorted(convictions.items())),
    }


__all__ = [
    "STALL_REASONS",
    "V21_SCORING_VERSION",
    "classify_stall",
    "evaluate_v3_family_scores",
    "run_v21_ablation",
    "stall_attribution",
    "validation_manifest",
    "v21_ablation_ladder",
]
