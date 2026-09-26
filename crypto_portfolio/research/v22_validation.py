"""Strategy V2.2 alpha validation: ladder, attribution, policy decisions.

Phase E of Strategy V2.2. Everything is preregistered research: the A-H
ladder is built from explicit policy and review overrides (never parameter
searches), the structural rung F only runs when the structural ranking
power passes, and the policy decision matrix derives KEEP / RESEARCH_ONLY /
REMOVE from both validation windows' risk-adjusted deltas plus the
admission verdicts. No output feeds back into the allocation engines.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..engine.eth_relative_alpha import (
    ethbtc_ratio_series,
    evaluate_relative_signals,
    evaluate_signal_admission,
    relative_signal_observations,
    spearman,
)
from ..engine.portfolio_risk import PortfolioRiskInputs
from ..models.policy import Policy, policy_from_mapping
from .opportunity_cost import satellite_alpha_attribution
from .structural_panel import structural_factors, structural_panel
from .v21_validation import _metrics_table, validation_manifest

V22_STRATEGY_VERSION = "strategy-v2.2"

HORIZONS = (30, 90, 180)


def _copy(policy: Policy | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(policy, Mapping):
        return json.loads(json.dumps(dict(policy)))
    return json.loads(json.dumps(policy.as_dict()))


def v22_ablation_ladder(policy: Policy) -> list[dict[str, Any]]:
    """Preregistered V2.2 rungs (plan 8.2) as explicit overrides.

    A BTC-only volatility targeting; B adds the market regime machinery
    (scaling + recovery FSM); C adds the ETH relative-alpha tilt; D adds
    satellite deployment through the TACTICAL_ONLY path (structural
    evidence intentionally absent); E is the structural ranking-power
    diagnostic (no replay); F adds FULL_CONVICTION structural deployment
    and only runs when E passes; G isolates execution costs; H is the full
    V2.2 with admission-gated tilts.
    """
    base = _copy(policy)
    base["risk_engine"]["mode"] = "volatility_budget"
    base["core_allocation"]["mode"] = "btc_baseline_with_active_tilts"
    base["core_allocation"]["eth"]["tilt_enabled"] = False
    ladder: list[dict[str, Any]] = []

    def rung(letter, name, note, data, *, zero_cost=False, reviews="default", diagnostic=False):
        ladder.append({
            "rung": letter, "name": name, "adds": note, "policy": data,
            "zero_cost": zero_cost, "reviews": reviews, "diagnostic": diagnostic,
        })

    # A: BTC-only volatility targeting.
    data = _copy(base)
    data["universe"]["satellites"] = []
    data["risk_engine"]["regime_risk_scaling"] = {
        regime: {"target_volatility_multiplier": 1.0}
        for regime in ("NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION")
    }
    data["risk_engine"]["recovery"].update({
        "stage_1_reviews": 10**6, "stage_2_reviews": 10**6, "release_reviews": 10**6,
    })
    rung("A", "btc_volatility_targeting",
         "BTC baseline sized by the volatility budget alone", data)

    # B: + market regime machinery (scaling + recovery FSM).
    data = _copy(ladder[-1]["policy"])
    data["risk_engine"]["regime_risk_scaling"] = json.loads(json.dumps(
        policy.as_dict()["risk_engine"]["regime_risk_scaling"]
    ))
    data["risk_engine"]["recovery"] = json.loads(json.dumps(
        policy.as_dict()["risk_engine"]["recovery"]
    ))
    rung("B", "market_regime_machinery",
         "regime scales the target volatility; recovery FSM re-risks in stages", data)

    # C: + ETH relative-alpha tilt (preregistered rule, ungated mechanism test).
    data = _copy(ladder[-1]["policy"])
    data["core_allocation"]["eth"]["tilt_enabled"] = True
    rung("C", "eth_relative_alpha_tilt",
         "the discrete ETH/BTC alpha state earns the preregistered tilt", data)

    # D: + satellites on the TACTICAL_ONLY path (structural evidence absent).
    data = _copy(ladder[-1]["policy"])
    data["universe"]["satellites"] = json.loads(json.dumps(
        policy.as_dict()["universe"]["satellites"]
    ))
    rung("D", "satellite_tactical_only",
         "satellites deploy market-driven at the tactical fraction", data)

    # E: structural ranking-power diagnostic — no replay rung.
    rung("E", "structural_ranking_power",
         "structural factors observed against forward BTC-relative returns",
         _copy(ladder[-1]["policy"]), diagnostic=True)

    # F: + FULL_CONVICTION structural deployment (real structural evidence).
    rung("F", "structural_full_conviction",
         "structural evidence can lift satellite conviction", _copy(ladder[-1]["policy"]),
         reviews="structural")

    # G: execution-cost isolation (the active rung without costs).
    rung("G", "execution_layer_reference",
         "the deployment rung without execution costs", _copy(ladder[-1]["policy"]),
         zero_cost=True, reviews="structural")

    # H: full V2.2 — admission-gated tilt and structural evidence, costs on.
    data = _copy(ladder[-2]["policy"])  # D policy
    rung("H", "full_v22",
         "BTC baseline with admission-gated ETH tilt, satellites, and costs", data)
    return ladder


def _rung_table(result: Mapping[str, Any]) -> dict[str, Any]:
    table = _metrics_table(result)
    attribution = result.get("strategy_attribution") or {}
    table.update({
        "average_btc_weight": (attribution.get("average_weights") or {}).get("BTC"),
        "average_eth_weight": (attribution.get("average_weights") or {}).get("ETH"),
        "average_risky_exposure": 1.0 - float(result.get("average_cash_weight") or 0.0),
        "trades": sum(1 for _ in result.get("trades") or ()),
        "total_cost_usd": result.get("total_cost_usd"),
    })
    return table


def run_v22_ablation(
    reviews: Sequence[Any],
    *,
    policy: Policy,
    daily_by_symbol: Mapping[str, Any],
    risk_inputs_by_review: Sequence[PortfolioRiskInputs],
    structural_reviews: Sequence[Any] | None = None,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    git_sha: str = "unset",
    eth_tilt_admitted: bool = False,
    structural_admitted: bool = False,
    satellite_symbols: Sequence[str] | None = None,
    backtest_runner: Any = None,
) -> dict[str, Any]:
    """Run the preregistered V2.2 ladder over frozen reviews.

    Rung F (and H's structural leg) only deploys structural evidence when
    the ranking-power verdict admitted it; otherwise F is recorded as
    skipped and never silently replaced. Every rung carries the frozen
    validation manifest and its delta against the previous replay rung.
    """
    if backtest_runner is None:
        from .orchestrator import run_historical_backtest as backtest_runner
    ladder = v22_ablation_ladder(policy)
    # Rung H's tilt gating follows the admission verdict.
    for entry in ladder:
        if entry["rung"] == "H":
            entry["policy"]["core_allocation"]["eth"]["tilt_enabled"] = eth_tilt_admitted
    rungs: list[dict[str, Any]] = []
    previous: Mapping[str, Any] | None = None
    latest_deployment_result: Mapping[str, Any] | None = None
    latest_deployment_reviews: Sequence[Any] | None = None
    for entry in ladder:
        if entry["rung"] == "E":
            rungs.append({
                "rung": "E", "name": entry["name"], "adds": entry["adds"],
                "manifest": validation_manifest(policy_from_mapping(entry["policy"]), git_sha=git_sha),
                "metrics": None, "delta_vs_previous": None,
                "note": "diagnostic-only rung: structural ranking power, no replay",
            })
            continue
        if entry["rung"] == "F" and not structural_admitted:
            rungs.append({
                "rung": "F", "name": entry["name"], "adds": entry["adds"],
                "manifest": validation_manifest(policy_from_mapping(entry["policy"]), git_sha=git_sha),
                "metrics": None, "delta_vs_previous": None,
                "skipped": "STRUCTURAL_RANKING_POWER_NOT_ADMITTED",
            })
            continue
        if entry["rung"] == "G" and not structural_admitted:
            # Without structural deployment the execution reference runs on
            # rung D's configuration.
            entry = {**entry, "policy": _copy(
                next(item for item in ladder if item["rung"] == "D")["policy"]
            ), "reviews": "default"}
        if entry["rung"] == "H":
            entry["reviews"] = "structural" if structural_admitted else "default"
        active_reviews = reviews
        if entry["reviews"] == "structural":
            if structural_reviews is None:
                raise ValueError("structural reviews are required for rung " + entry["rung"])
            active_reviews = structural_reviews
        rung_policy = policy_from_mapping(entry["policy"])
        result = backtest_runner(
            active_reviews, policy=rung_policy,
            fee_bps=0.0 if entry["zero_cost"] else fee_bps,
            slippage_bps=0.0 if entry["zero_cost"] else slippage_bps,
            risk_inputs_by_review=risk_inputs_by_review,
        )
        table = _rung_table(result)
        delta = (
            {key: (table[key] - previous[key] if table[key] is not None and previous[key] is not None else None)
             for key in table}
            if previous is not None else None
        )
        rungs.append({
            "rung": entry["rung"], "name": entry["name"], "adds": entry["adds"],
            "manifest": validation_manifest(rung_policy, git_sha=git_sha),
            "metrics": table, "delta_vs_previous": delta,
            "stall_attribution": result.get("stall_attribution"),
            "emergency_recovery": result.get("emergency_recovery_diagnostics"),
            "result": result,
        })
        previous = table
        if entry["rung"] in {"D", "F"}:
            latest_deployment_result = result
            latest_deployment_reviews = active_reviews
        if entry["rung"] == "H":
            full_result = result
            full_reviews = active_reviews
    return {
        "strategy_version": V22_STRATEGY_VERSION,
        "rungs": rungs,
        "note": "preregistered mechanism isolations; not parameter searches",
        "daily_symbols": sorted(str(symbol) for symbol in daily_by_symbol),
        "_full_result": full_result,
        "_full_reviews": full_reviews,
        "_deployment_result": latest_deployment_result,
        "_deployment_reviews": latest_deployment_reviews,
    }


# ---------------------------------------------------------------------------
# ETH validation (plan 8.5)
# ---------------------------------------------------------------------------


def ethbtc_forward_excess_summary(
    ratio_points: Sequence[Any],
    moments: Sequence[Any],
    *,
    horizons: Sequence[int] = HORIZONS,
) -> dict[str, Any]:
    """Mean forward ETH/BTC excess per horizon over the review moments."""
    rows = relative_signal_observations(ratio_points, moments, horizons=horizons)
    summary: dict[str, Any] = {}
    for raw_horizon in horizons:
        horizon = str(int(raw_horizon))
        values = [
            row["labels"][horizon]["forward_ethbtc_return"]
            for row in rows
            if row["labels"][horizon]["status"] == "AVAILABLE"
        ]
        summary[horizon] = {
            "samples": len(values),
            "mean": sum(values) / len(values) if values else None,
        }
    return summary


def eth_validation_block(
    full_result: Mapping[str, Any],
    full_reviews: Sequence[Any],
    *,
    policy: Policy,
) -> dict[str, Any]:
    from ..engine.eth_relative_alpha import eth_tilt_attribution

    attribution = eth_tilt_attribution(full_result["reviews"], full_reviews, policy=policy)
    entries = attribution["entries"]
    tilt_count = sum(1 for entry in entries if entry["tilt_requested_fraction"] > 0)
    average_tilt = (
        sum(entry["tilt_requested_weight"] for entry in entries) / len(entries)
        if entries else None
    )
    return {
        "tilt_count": tilt_count,
        "average_eth_tilt_weight": average_tilt,
        "state_counts": attribution["state_counts"],
        "total_requested_tilt_weight": attribution["total_requested_tilt_weight"],
        "total_final_tilt_weight": attribution["total_final_tilt_weight"],
        "weighted_portfolio_contribution": (
        # ETH tilt contribution realized: sum of final ETH weight times the
        # realized ETH-minus-BTC return of each following period.
            sum(
                (entry["tilt_final_weight"] or 0.0) * (entry["ethbtc_opportunity_cost_return"] or 0.0)
                for entry in entries
            )
        ),
    }


# ---------------------------------------------------------------------------
# Structural ranking power (plan 8.7)
# ---------------------------------------------------------------------------


def structural_ranking_power(
    context: Any,
    moments: Sequence[str],
    prices: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    symbols: Sequence[str] = ("AAVE", "SOL", "BNB"),
    horizons: Sequence[int] = (90, 180),
) -> dict[str, Any]:
    """Structural factor vs forward BTC-relative return ranking power.

    Per symbol and factor: Spearman IC at 90/180 days, tercile bucket mean
    relative returns with a monotonicity flag, independent observation
    count, and a seeded bootstrap CI over block ICs.
    """
    from datetime import timedelta

    from ..engine.eth_relative_alpha import bootstrap_mean_ci
    from ..models.time import parse_timestamp as parse

    def price_rows(series: Any) -> list[tuple[Any, float]]:
        return [
            (parse(candle.timestamp), float(candle.close))
            for candle in series.completed_candles()
        ]

    histories = {str(symbol).upper(): price_rows(series) for symbol, series in prices.items()}

    def close_at_or_after(rows: Sequence[tuple[Any, float]], moment: Any):
        return next((item for item in rows if item[0] >= moment), None)

    panel = structural_panel(context, moments, symbols=symbols)
    report: dict[str, Any] = {}
    for symbol in symbols:
        symbol_rows = [row for row in panel["rows"] if row["symbol"] == symbol]
        factors: dict[str, Any] = {}
        for name in sorted({key for row in symbol_rows for key in row["factors"]}):
            horizon_report: dict[str, Any] = {}
            for raw_horizon in horizons:
                horizon = int(raw_horizon)
                pairs: list[tuple[float, float]] = []
                for row in symbol_rows:
                    value = row["factors"].get(name)
                    if value is None:
                        continue
                    moment = parse(row["timestamp"])
                    asset = close_at_or_after(
                        histories.get(symbol, ()), moment,
                    )
                    btc = close_at_or_after(histories.get("BTC", ()), moment)
                    if asset is None or btc is None:
                        continue
                    asset_end = close_at_or_after(histories.get(symbol, ()), moment + timedelta(days=horizon))
                    btc_end = close_at_or_after(histories.get("BTC", ()), moment + timedelta(days=horizon))
                    if asset_end is None or btc_end is None:
                        continue
                    relative = (
                        asset_end[1] / asset[1] - (btc_end[1] / btc[1])
                    )
                    pairs.append((float(value), relative))
                # Independent observations: non-overlapping moments.
                ordered = sorted(pairs, key=lambda item: item[0])
                terciles: list[float | None] = []
                if ordered:
                    size = len(ordered) / 3
                    for index in range(3):
                        members = ordered[int(round(index * size)):int(round((index + 1) * size))]
                        terciles.append(
                            sum(value for _, value in members) / len(members)
                            if members else None
                        )
                block_ics: list[float] = []
                if len(pairs) >= 12:
                    step = max(4, len(pairs) // 4)
                    for start in range(0, len(pairs), step):
                        chunk = pairs[start:start + step]
                        ic = spearman(
                            [pair[0] for pair in chunk], [pair[1] for pair in chunk],
                        )
                        if ic is not None:
                            block_ics.append(ic)
                horizon_report[str(horizon)] = {
                    "samples": len(pairs),
                    "spearman_ic": spearman(
                        [pair[0] for pair in pairs], [pair[1] for pair in pairs],
                    ),
                    "bucket_mean_relative_returns": terciles,
                    "bucket_monotonic": (
                        all(
                            later is not None and earlier is not None
                            and later >= earlier - 1e-12
                            for earlier, later in zip(terciles, terciles[1:])
                        )
                        if len(terciles) == 3 and all(v is not None for v in terciles)
                        else None
                    ),
                    "independent_observations": len(block_ics) * 4 if block_ics else 0,
                    "block_ic_bootstrap": bootstrap_mean_ci(block_ics) if block_ics else None,
                }
            factors[name] = horizon_report
        report[symbol] = {
            "factors": factors,
            "coverage": panel["coverage"].get(symbol),
        }
    return {
        "contract": "POINT_IN_TIME_STRUCTURAL_PANEL",
        "horizons": [int(horizon) for horizon in horizons],
        "symbols": report,
    }


def structural_ranking_admission(
    windows: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Both windows must agree: any factor with positive IC at 90D or 180D,
    monotone buckets, and at least 10 independent observations per window
    admits the structural family for deployment experiments."""
    def window_factors(window: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
        collected: dict[str, list[dict[str, Any]]] = {}
        for symbol_report in (window.get("symbols") or {}).values():
            for name, horizons in (symbol_report.get("factors") or {}).items():
                collected.setdefault(name, []).append(horizons)
        return collected

    parsed = {name: window_factors(window) for name, window in windows.items()}
    names = sorted({name for factors in parsed.values() for name in factors})
    verdicts: dict[str, Any] = {}
    for name in names:
        per_factor: list[dict[str, Any]] = []
        for window_name in sorted(parsed):
            entries = parsed[window_name].get(name, [])
            blocks = []
            positives = []
            monotone = []
            for entry in entries:
                for horizon in ("90", "180"):
                    row = entry.get(horizon) or {}
                    if row.get("spearman_ic") is not None:
                        positives.append(row["spearman_ic"] > 0)
                    if row.get("independent_observations"):
                        blocks.append(row["independent_observations"] >= 10)
                    if row.get("bucket_monotonic") is True:
                        monotone.append(True)
            per_factor.append({
                "window": window_name,
                "any_positive_ic": any(positives) if positives else None,
                "enough_independent_observations": all(blocks) if blocks else False,
                "any_monotone_buckets": any(monotone),
            })
        consistent_positive = all(
            row["any_positive_ic"] for row in per_factor if row["any_positive_ic"] is not None
        ) and len(per_factor) >= 2
        verdicts[name] = {
            "windows": per_factor,
            "admitted": bool(
                consistent_positive
                and all(row["enough_independent_observations"] for row in per_factor)
                and any(row["any_monotone_buckets"] for row in per_factor)
            ),
        }
    return {
        "rule": (
            "both windows: positive IC at 90D or 180D, >=10 independent "
            "observations, any monotone tercile path"
        ),
        "factors": verdicts,
        "structural_admitted": any(entry["admitted"] for entry in verdicts.values()),
    }


# ---------------------------------------------------------------------------
# Alpha attribution and policy decision matrix (plans 8.4 / 8.8)
# ---------------------------------------------------------------------------


def alpha_attribution_report(rungs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Module-level attribution from ladder deltas + cost/cash blocks."""
    by_letter = {
        str(rung.get("rung")): rung
        for rung in rungs
        if rung.get("metrics") is not None
    }

    def metric(letter: str, field: str) -> float | None:
        table = by_letter.get(letter, {}).get("metrics") or {}
        return table.get(field)

    cost = None
    for rung in rungs:
        if str(rung.get("rung")) == "H" and rung.get("metrics"):
            cost = rung["metrics"].get("total_cost_usd")
    attribution = {
        "btc_baseline_cagr": metric("A", "cagr"),
        "market_regime_machinery_cagr_delta": (
            (metric("B", "cagr") - metric("A", "cagr"))
            if metric("A", "cagr") is not None and metric("B", "cagr") is not None
            else None
        ),
        "eth_tilt_cagr_delta": (
            (metric("C", "cagr") - metric("B", "cagr"))
            if metric("B", "cagr") is not None and metric("C", "cagr") is not None
            else None
        ),
        "satellite_tactical_cagr_delta": (
            (metric("D", "cagr") - metric("C", "cagr"))
            if metric("C", "cagr") is not None and metric("D", "cagr") is not None
            else None
        ),
        "structural_deployment_cagr_delta": (
            (metric("F", "cagr") - metric("D", "cagr"))
            if metric("D", "cagr") is not None and metric("F", "cagr") is not None
            else None
        ),
        "execution_cost_usd": cost,
        "cash_carry": 0.0,
    }
    total = metric("H", "cagr")
    parts = [
        attribution["btc_baseline_cagr"],
        attribution["market_regime_machinery_cagr_delta"],
        attribution["eth_tilt_cagr_delta"],
        attribution["satellite_tactical_cagr_delta"],
        attribution["structural_deployment_cagr_delta"],
    ]
    if total is not None and all(part is not None for part in parts):
        attribution["interaction_residual"] = total - sum(part for part in parts)
    else:
        attribution["interaction_residual"] = None
    attribution["full_v22_cagr"] = total
    return attribution


def _module_verdict(deltas: Sequence[float | None], *, admitted: bool | None = None) -> dict[str, Any]:
    known = [value for value in deltas if value is not None]
    if not known or len(known) < len(deltas):
        return {"decision": "RESEARCH_ONLY", "reason": "insufficient windows with a runnable configuration"}
    if all(value > 0 for value in known):
        decision = "KEEP"
        reason = "positive risk-adjusted contribution in every validation window"
    elif all(value < 0 for value in known):
        decision = "REMOVE" if admitted is not True else "RESEARCH_ONLY"
        reason = "negative contribution in every validation window"
    else:
        decision = "RESEARCH_ONLY"
        reason = "window-inconsistent contribution"
    if admitted is False and decision == "KEEP":
        decision = "RESEARCH_ONLY"
        reason += "; admission not passed, production authority stays locked"
    return {"decision": decision, "reason": reason}


def policy_decision_matrix(
    primary: Mapping[str, Any],
    sister: Mapping[str, Any] | None,
    *,
    eth_tilt_admitted: bool,
    structural_admitted: bool,
) -> dict[str, Any]:
    """KEEP / RESEARCH_ONLY / REMOVE per module from both windows' deltas.

    The verdict uses vol-matched excess deltas (risk-adjusted, never raw
    CAGR), consistency across both windows, and the admission verdicts;
    unadmitted signals can never earn KEEP.
    """
    def pair(module: str) -> list[float | None]:
        def excess(result: Mapping[str, Any]) -> float | None:
            for rung in result.get("rungs", []):
                if rung.get("rung") == module and rung.get("metrics"):
                    return rung["metrics"].get("vol_matched_excess_annualized")
            return None

        return [excess(primary), excess(sister) if sister else None]

    def delta_pair(predecessor: str, rung: str) -> list[float | None]:
        def delta(result: Mapping[str, Any]) -> float | None:
            by_letter = {item.get("rung"): item for item in result.get("rungs", [])}
            base = (by_letter.get(predecessor) or {}).get("metrics") or {}
            target = (by_letter.get(rung) or {}).get("metrics") or {}
            if base and target:
                if base.get("vol_matched_excess_annualized") is None or target.get("vol_matched_excess_annualized") is None:
                    return None
                return target["vol_matched_excess_annualized"] - base["vol_matched_excess_annualized"]
            return None

        return [delta(primary), delta(sister) if sister else None]

    matrix = {
        "market_regime_machinery": _module_verdict(delta_pair("A", "B")),
        "eth_relative_alpha_tilt": _module_verdict(
            delta_pair("B", "C"), admitted=eth_tilt_admitted,
        ),
        "satellite_tactical_only": _module_verdict(delta_pair("C", "D")),
        "structural_full_conviction": _module_verdict(
            delta_pair("D", "F"), admitted=structural_admitted,
        ),
        "btc_baseline_core": {
            "decision": "KEEP",
            "reason": "preregistered V2.2 foundation: every tilt is measured against it",
        },
    }
    return {
        "rule": (
            "vol-matched excess delta vs the previous rung, consistent across "
            "both windows; unadmitted signals cap at RESEARCH_ONLY"
        ),
        "modules": matrix,
    }


def assemble_v22_validation(
    ladder_result: Mapping[str, Any],
    *,
    policy: Policy,
    daily_by_symbol: Mapping[str, Any],
    eth_signal_evaluation: Mapping[str, Any],
    eth_signal_admission: Mapping[str, Any] | None,
    structural_evaluation: Mapping[str, Any],
    structural_admission: Mapping[str, Any] | None,
    sister_ladder_result: Mapping[str, Any] | None = None,
    ethbtc_excess: Mapping[str, Any] | None = None,
    satellite_symbols: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Assemble the full validation document from a completed ladder run."""
    rungs = [
        {key: value for key, value in rung.items() if key != "result"}
        for rung in ladder_result["rungs"]
    ]
    full_result = ladder_result["_full_result"]
    full_reviews = ladder_result["_full_reviews"]
    deployment_result = ladder_result["_deployment_result"]
    deployment_reviews = ladder_result["_deployment_reviews"]
    prices = {
        symbol: [
            {"timestamp": candle.timestamp, "close": candle.close}
            for candle in series.completed_candles()
        ]
        for symbol, series in daily_by_symbol.items()
    }
    satellites = tuple(satellite_symbols) if satellite_symbols is not None else (
        "SOL", "BNB", "AAVE",
    )
    satellite_validation = satellite_alpha_attribution(
        deployment_result["reviews"], deployment_reviews,
        satellite_symbols=satellites, prices=prices,
    )
    satellite_validation.pop("entries", None)
    structural_admitted = bool(
        (structural_admission or {}).get("structural_admitted", False)
    )
    eth_tilt_admitted = bool(
        (eth_signal_admission or {}).get("eth_tilt_admitted", False)
    )
    return {
        "strategy_version": V22_STRATEGY_VERSION,
        "rungs": rungs,
        "alpha_attribution": alpha_attribution_report(ladder_result["rungs"]),
        "eth_validation": eth_validation_block(full_result, full_reviews, policy=policy),
        "ethbtc_forward_excess": ethbtc_excess,
        "eth_signal_evaluation": eth_signal_evaluation,
        "eth_signal_admission": eth_signal_admission,
        "satellite_validation": satellite_validation,
        "structural_ranking_power": structural_evaluation,
        "structural_admission": structural_admission,
        "policy_decision": policy_decision_matrix(
            ladder_result, sister_ladder_result,
            eth_tilt_admitted=eth_tilt_admitted,
            structural_admitted=structural_admitted,
        ),
    }


__all__ = [
    "V22_STRATEGY_VERSION",
    "alpha_attribution_report",
    "assemble_v22_validation",
    "eth_validation_block",
    "ethbtc_forward_excess_summary",
    "ethbtc_ratio_series",
    "evaluate_relative_signals",
    "evaluate_signal_admission",
    "policy_decision_matrix",
    "run_v22_ablation",
    "structural_factors",
    "structural_ranking_admission",
    "structural_ranking_power",
    "v22_ablation_ladder",
]
