#!/usr/bin/env python3
"""Build, audit, run, evaluate, and report historical strategy research."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_portfolio.models.backtest import BacktestSpec, default_backtest_spec  # noqa: E402
from crypto_portfolio.models.policy import load_policy, policy_from_mapping, policy_hash  # noqa: E402
from crypto_portfolio.models.time import parse_timestamp  # noqa: E402
from crypto_portfolio.research.data_audit import coverage_matrix  # noqa: E402
from crypto_portfolio.research.dataset import build_binance_dataset, load_dataset  # noqa: E402
from crypto_portfolio.research.evidence_series import EvidenceContext, load_evidence_series  # noqa: E402
from crypto_portfolio.research.decision_evaluation import evaluate_decision_history  # noqa: E402
from crypto_portfolio.research.historical_builder import (  # noqa: E402
    apply_semantic_scenario, build_historical_reviews, rebind_initial_weights,
    review_gap_diagnostics,
)
from crypto_portfolio.research.orchestrator import (  # noqa: E402
    build_risk_inputs_for_reviews,
    run_historical_backtest,
)
from crypto_portfolio.research.reporting import render_run_report  # noqa: E402
from crypto_portfolio.research.score_evaluation import evaluate_scores  # noqa: E402
from crypto_portfolio.research.stress import drawdown_boundary_stress, drawdown_budget_stress  # noqa: E402
from crypto_portfolio.state._jsonl import read_records  # noqa: E402
from crypto_portfolio.state.decisions import (  # noqa: E402
    default_decision_path, default_status_event_path,
)


def _json_default(value):
    if hasattr(value, "as_dict"):
        return value.as_dict()
    raise TypeError(f"unsupported research result value: {value.__class__.__name__}")


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=_json_default) + "\n", encoding="utf-8")


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def _default_end() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _series_maps(series, *, prefer_normalized: bool):
    daily, hourly = {}, {}
    has_normalized = any(key.startswith("normalized:") for key in series)
    for key, value in series.items():
        if prefer_normalized and has_normalized and not key.startswith("normalized:"):
            continue
        if not prefer_normalized and not key.startswith("binance:"):
            continue
        target = daily if value.timeframe == "1D" else hourly if value.timeframe == "1H" else None
        if target is not None:
            target[value.symbol] = value
    return daily, hourly


def _core_policy():
    raw = json.loads((REPO_ROOT / "config" / "policy.json").read_text(encoding="utf-8"))
    modified = deepcopy(raw)
    modified["universe"]["satellites"] = []
    return policy_from_mapping(modified)


def _price_rows(series):
    result = {}
    for model in series.values():
        if model.timeframe != "1D":
            continue
        result[model.symbol] = [
            {
                "timestamp": (parse_timestamp(candle.timestamp) + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                "price": candle.close,
            }
            for candle in model.completed_candles()
        ]
    return result


def command_build(args):
    canonical = load_policy()
    # The base spec keeps the default window so an early --end-at is never
    # validated against the default 2024 start before overrides apply.
    spec = default_backtest_spec(
        run_id=args.run_id, end_at=_default_end(),
        policy_hash=policy_hash(canonical), git_sha=_git_sha(),
    ) if args.spec is None else BacktestSpec.from_mapping(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    overrides = (
        ("warmup_start_at", args.warmup_start_at),
        ("start_at", args.start_at),
        ("end_at", args.end_at),
    )
    if any(value for _, value in overrides):
        payload = spec.as_dict()
        for field, value in overrides:
            if value:
                payload[field] = value
        spec = BacktestSpec.from_mapping(payload)
    result = build_binance_dataset(spec, output_root=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_audit(args):
    spec, manifest, _ = load_dataset(args.dataset)
    result = {
        "run_id": spec.run_id, "strict_ready": manifest.strict_ready,
        "blockers": list(manifest.blockers), "coverage_matrix": coverage_matrix(manifest),
    }
    if args.output:
        _write(Path(args.output), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_run(args):
    spec, manifest, series = load_dataset(args.dataset)
    root = Path(args.dataset)
    run = {
        "run_id": spec.run_id, "spec": spec.as_dict(), "manifest": manifest.as_dict(),
        "valuation_basis": (
            "USD_ASSUMED_STABLECOIN_PEG" if manifest.strict_ready and spec.stablecoin_peg_assumption
            else "USD" if manifest.strict_ready else "USDT_APPROXIMATION"
        ),
        "strict_status": (
            "ELIGIBLE_WITH_ASSUMED_STABLE_PEG" if manifest.strict_ready and spec.stablecoin_peg_assumption
            else "ELIGIBLE" if manifest.strict_ready else "BLOCKED"
        ),
        "policy_hashes": {}, "stress": drawdown_boundary_stress(),
        "budget_stress": drawdown_budget_stress(), "score_evaluations": {},
        "runs": {}, "review_calendar": {},
    }
    if not manifest.strict_ready and not args.allow_usdt_approximation:
        run["status"] = "BLOCKED_BY_DATA_MANIFEST"
        path = root / "run.json"
        _write(path, run)
        print(json.dumps({"status": run["status"], "output": str(path), "blockers": list(manifest.blockers)}, ensure_ascii=False, indent=2))
        return
    daily, hourly = _series_maps(series, prefer_normalized=manifest.strict_ready)
    execution_series = daily if spec.execution_timeframe == "1D" else hourly
    harvested = load_evidence_series(root)
    evidence = EvidenceContext.from_series(harvested) if harvested else None
    policies = {"core": _core_policy(), "full": load_policy()}
    if args.risk_engine_mode:
        policies = {
            name: policy_from_mapping({
                **policy.as_dict(),
                "risk_engine": {
                    **(policy.as_dict().get("risk_engine") or {}),
                    "mode": args.risk_engine_mode,
                },
            })
            for name, policy in policies.items()
        }
        run["risk_engine_mode_override"] = args.risk_engine_mode
    for scope_name, scope_symbols in spec.asset_scopes.items():
        policy = policies[scope_name]
        run["policy_hashes"][scope_name] = policy_hash(policy)
        try:
            base_reviews = build_historical_reviews(
                daily_by_symbol=daily, execution_by_symbol=execution_series,
                hourly_by_symbol=execution_series, execution_timeframe=spec.execution_timeframe,
                symbols=scope_symbols, initial_weights=next(iter(spec.initial_portfolios.values())),
                initial_value=spec.initial_value_usd, start_at=spec.start_at,
                end_at=spec.end_at, policy=policy, semantic_score=None,
                evidence=evidence,
            )
        except Exception as exc:
            for portfolio_name in spec.initial_portfolios:
                for semantic in (None, *spec.semantic_scenarios):
                    mode = "strict" if semantic is None else f"synthetic_{semantic}"
                    run["runs"][f"{scope_name}/{portfolio_name}/{mode}/main_cost"] = {
                        "status": "BLOCKED", "reason": f"{exc.__class__.__name__}: {exc}",
                    }
            continue
        run["review_calendar"][scope_name] = review_gap_diagnostics(
            daily, symbols=scope_symbols, start_at=spec.start_at, end_at=spec.end_at,
            produced_reviews=len(base_reviews),
        )
        scope_risk_inputs = None
        if (policy.risk_engine or {}).get("mode") == "volatility_budget":
            scope_risk_inputs = build_risk_inputs_for_reviews(
                base_reviews, daily_by_symbol=daily, policy=policy,
            )
        score_observations = []
        for review in base_reviews:
            for symbol, raw_assessment in review.assessments.items():
                assessment = raw_assessment
                score_observations.append({
                    "timestamp": review.as_of, "symbol": symbol,
                    "score": assessment["weighted_score"],
                    "normalized_score": assessment.get("normalized_score"),
                    "coverage": assessment.get("score_coverage") or 0.0,
                    "factor_scores": assessment.get("factor_scores", {}),
                    "synthetic": False,
                })
        raw_price_series = {key: value for key, value in series.items() if key.startswith("binance:")}
        run["score_evaluations"][scope_name] = evaluate_scores(score_observations, _price_rows(raw_price_series))
        for portfolio_name, initial_weights in spec.initial_portfolios.items():
            for semantic in (None, *spec.semantic_scenarios):
                mode = "strict" if semantic is None else f"synthetic_{semantic}"
                try:
                    reviews = rebind_initial_weights(base_reviews, initial_weights, spec.initial_value_usd)
                    if semantic is not None:
                        reviews = apply_semantic_scenario(reviews, policy, semantic)
                    name = f"{scope_name}/{portfolio_name}/{mode}/main_cost"
                    run["runs"][name] = {
                        "status": "COMPLETED", "validation_mode": (
                            "STRICT_POINT_IN_TIME" if semantic is None else "SYNTHETIC_ASSUMPTIONS"
                        ),
                        "result": run_historical_backtest(
                            reviews, policy=policy, fee_bps=spec.fee_bps, slippage_bps=spec.slippage_bps,
                            risk_inputs_by_review=scope_risk_inputs,
                        ),
                    }
                    if semantic is None:
                        weekly_name = f"{scope_name}/{portfolio_name}/strict/weekly_sensitivity"
                        run["runs"][weekly_name] = {
                            "status": "COMPLETED", "validation_mode": "CADENCE_SENSITIVITY",
                            "result": run_historical_backtest(
                                reviews, policy=policy, fee_bps=spec.fee_bps,
                                slippage_bps=spec.slippage_bps, ordinary_review_weekday=0,
                                risk_inputs_by_review=scope_risk_inputs,
                            ),
                        }
                        for cost in spec.cost_sensitivity_bps:
                            cost_name = f"{scope_name}/{portfolio_name}/strict/cost_{cost:g}bps"
                            run["runs"][cost_name] = {
                                "status": "COMPLETED", "validation_mode": "COST_SENSITIVITY",
                                "result": run_historical_backtest(
                                    reviews, policy=policy, fee_bps=cost, slippage_bps=0.0,
                                    risk_inputs_by_review=scope_risk_inputs,
                                ),
                            }
                except Exception as exc:
                    name = f"{scope_name}/{portfolio_name}/{mode}/main_cost"
                    run["runs"][name] = {"status": "BLOCKED", "reason": f"{exc.__class__.__name__}: {exc}"}
    run["status"] = "COMPLETED_WITH_LIMITATIONS"
    path = root / "run.json"
    _write(path, run)
    print(json.dumps({"status": run["status"], "output": str(path), "runs": len(run["runs"])}, ensure_ascii=False, indent=2))


def command_evaluate_decisions(args):
    _, _, series = load_dataset(args.dataset)
    decisions = read_records(Path(args.decisions) if args.decisions else default_decision_path())
    events = read_records(Path(args.status_events) if args.status_events else default_status_event_path())
    result = evaluate_decision_history(
        decisions, events, _price_rows({key: value for key, value in series.items() if key.startswith("binance:")}),
        artifact_root=args.artifact_root,
    )
    output = Path(args.output) if args.output else Path(args.dataset) / "decision-evaluation.json"
    _write(output, result)
    print(json.dumps({"output": str(output), "valid_decisions": result["valid_decisions"],
                      "excluded_decisions": result["excluded_decisions"]}, ensure_ascii=False, indent=2))


def command_evaluate_scores(args):
    spec, _, series = load_dataset(args.dataset)
    daily, hourly = _series_maps(
        {key: value for key, value in series.items() if key.startswith("binance:")},
        prefer_normalized=False,
    )
    execution_series = daily if spec.execution_timeframe == "1D" else hourly
    harvested = load_evidence_series(args.dataset)
    evidence = EvidenceContext.from_series(harvested) if harvested else None
    observations_by_scope = {}
    for scope_name, symbols in spec.asset_scopes.items():
        policy = _core_policy() if scope_name == "core" else load_policy()
        reviews = build_historical_reviews(
            daily_by_symbol=daily, execution_by_symbol=execution_series,
            hourly_by_symbol=execution_series, execution_timeframe=spec.execution_timeframe, symbols=symbols,
            initial_weights=next(iter(spec.initial_portfolios.values())),
            initial_value=spec.initial_value_usd, start_at=spec.start_at,
            end_at=spec.end_at, policy=policy, semantic_score=None,
            evidence=evidence,
        )
        observations = []
        for review in reviews:
            for symbol, assessment in review.assessments.items():
                observations.append({
                    "timestamp": review.as_of, "symbol": symbol,
                    "score": assessment["weighted_score"],
                    "normalized_score": assessment.get("normalized_score"),
                    "coverage": assessment.get("score_coverage") or 0.0,
                    "factor_scores": assessment.get("factor_scores", {}), "synthetic": False,
                })
        observations_by_scope[scope_name] = evaluate_scores(observations, _price_rows({
            key: value for key, value in series.items() if key.startswith("binance:")
        }))
    output = Path(args.output) if args.output else Path(args.dataset) / "score-evaluation.json"
    _write(output, {"contract": "STRICT_POINT_IN_TIME_INPUTS", "scopes": observations_by_scope})
    print(json.dumps({"output": str(output), "scopes": list(observations_by_scope)}, ensure_ascii=False, indent=2))


def command_report(args):
    path = Path(args.run)
    run = json.loads(path.read_text(encoding="utf-8"))
    result = render_run_report(
        run, args.output or path.parent / "report", run_dir=path.parent
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_v21(args):
    """Strategy V2.1 validation: preregistered A-H ladder + attribution.

    Runs the frozen A-H mechanism ladder (plan 8.4) over one dataset scope
    plus the market/structural score ranking evaluation (plan 8.5) and the
    stall/cash attribution summaries, all stamped with the frozen validation
    manifest (plan 8.2).
    """
    from crypto_portfolio.models.policy import load_policy
    from crypto_portfolio.research.historical_builder import (
        build_historical_reviews,
        rebind_initial_weights,
    )
    from crypto_portfolio.research.orchestrator import build_risk_inputs_for_reviews
    from crypto_portfolio.research.v21_validation import (
        evaluate_v3_family_scores,
        run_v21_ablation,
        validation_manifest,
    )

    spec, manifest, series = load_dataset(args.dataset)
    daily, hourly = _series_maps(series, prefer_normalized=manifest.strict_ready)
    execution_series = daily if spec.execution_timeframe == "1D" else hourly
    harvested = load_evidence_series(Path(args.dataset))
    evidence = EvidenceContext.from_series(harvested) if harvested else None
    scope_name, portfolio_name = args.scope.split("/")
    symbols = spec.asset_scopes[scope_name]
    policy = _core_policy() if scope_name == "core" else load_policy()
    policy = policy_from_mapping({
        **policy.as_dict(),
        "risk_engine": {**policy.as_dict()["risk_engine"], "mode": "volatility_budget"},
    })
    reviews = build_historical_reviews(
        daily_by_symbol=daily, execution_by_symbol=execution_series,
        hourly_by_symbol=execution_series, execution_timeframe=spec.execution_timeframe,
        symbols=symbols, initial_weights=next(iter(spec.initial_portfolios.values())),
        initial_value=spec.initial_value_usd, start_at=spec.start_at,
        end_at=spec.end_at, policy=policy, semantic_score=None, evidence=evidence,
    )
    reviews = rebind_initial_weights(
        reviews, spec.initial_portfolios[portfolio_name], spec.initial_value_usd,
    )
    risk_inputs = build_risk_inputs_for_reviews(reviews, daily_by_symbol=daily, policy=policy)
    ladder = run_v21_ablation(
        reviews, policy=policy, daily_by_symbol=daily,
        risk_inputs_by_review=risk_inputs,
        fee_bps=spec.fee_bps, slippage_bps=spec.slippage_bps,
        git_sha=_git_sha(),
    )
    observations = []
    for review in reviews:
        for symbol, raw in review.assessments.items():
            observations.append({
                "timestamp": review.as_of, "symbol": symbol,
                "score": raw["weighted_score"],
                "normalized_score": raw.get("normalized_score"),
                "coverage": raw.get("score_coverage") or 0.0,
                "factor_scores": raw.get("factor_scores", {}),
                "synthetic": False,
            })
    result = {
        "run_id": spec.run_id,
        "experiment": f"{scope_name}/{portfolio_name}/strict",
        "manifest": validation_manifest(
            policy, git_sha=_git_sha(), data_manifest=manifest.as_dict(),
        ),
        "window": {"start_at": spec.start_at, "end_at": spec.end_at},
        "ablation_ladder": ladder,
        "family_score_evaluation": evaluate_v3_family_scores(
            observations, _price_rows({k: v for k, v in series.items()}),
            policy=policy,
        ),
    }
    path = Path(args.output) if args.output else Path(args.dataset) / "v21-validation.json"
    _write(path, result)
    print(json.dumps({
        "status": "COMPLETED", "output": str(path),
        "rungs": [rung["rung"] for rung in ladder["rungs"]],
    }, ensure_ascii=False, indent=2))


def _etf_differentials(harvested, moments):
    """Point-in-time ETH-minus-BTC ETF net-flow/AUM differential per moment."""
    if not harvested:
        return None
    from crypto_portfolio.research.evidence_series import etf_net_to_aum

    flows = {
        asset: harvested.get(f"sosovalue:etf:{asset}:netflow")
        for asset in ("ETH", "BTC")
    }
    aum = {
        asset: harvested.get(f"sosovalue:etf:{asset}:aum")
        for asset in ("ETH", "BTC")
    }
    if any(series is None for series in (*flows.values(), *aum.values())):
        return None
    result = {}
    for moment in moments:
        eth_ratio = etf_net_to_aum(flows["ETH"], aum["ETH"], str(moment))
        btc_ratio = etf_net_to_aum(flows["BTC"], aum["BTC"], str(moment))
        if eth_ratio is None or btc_ratio is None:
            continue
        result[str(moment)] = eth_ratio - btc_ratio
    return result or None


def command_v22(args):
    """Strategy V2.2 alpha validation: preregistered ladder + attribution.

    Runs the frozen V2.2 A-H ladder (plan 8.2) over one dataset scope, the
    ETH/BTC signal evaluation and admission, the structural ranking-power
    diagnostic that gates rung F, satellite opportunity-cost attribution,
    and the policy decision matrix — all stamped with the frozen validation
    manifest. With --sister-dataset the sister window's ladder and
    evaluations join the admission and decision steps.
    """
    from crypto_portfolio.engine.eth_relative_alpha import (
        ethbtc_ratio_series,
        evaluate_relative_signals,
        relative_signal_observations,
    )
    from crypto_portfolio.models.policy import load_policy, policy_from_mapping
    from crypto_portfolio.research.evidence_series import EvidenceContext, load_evidence_series
    from crypto_portfolio.research.historical_builder import (
        build_historical_reviews,
        rebind_initial_weights,
    )
    from crypto_portfolio.research.orchestrator import build_risk_inputs_for_reviews
    from crypto_portfolio.research.v22_validation import (
        assemble_v22_validation,
        ethbtc_forward_excess_summary,
        evaluate_signal_admission,
        run_v22_ablation,
        structural_ranking_admission,
        structural_ranking_power,
    )

    def window(root: Path, build_reviews: bool):
        spec, manifest, series = load_dataset(root)
        daily, hourly = _series_maps(series, prefer_normalized=manifest.strict_ready)
        execution_series = daily if spec.execution_timeframe == "1D" else hourly
        harvested = load_evidence_series(root)
        scope_name, portfolio_name = args.scope.split("/")
        symbols = spec.asset_scopes[scope_name]
        policy = _core_policy() if scope_name == "core" else load_policy()
        policy = policy_from_mapping({
            **policy.as_dict(),
            "risk_engine": {**policy.as_dict()["risk_engine"], "mode": "volatility_budget"},
        })
        evidence_structural = (
            EvidenceContext.from_series(harvested, include_structural=True)
            if harvested else None
        )
        evidence_plain = (
            EvidenceContext.from_series(harvested)
            if harvested else None
        )
        if not build_reviews:
            from crypto_portfolio.research.historical_builder import _candidate_boundaries
            from crypto_portfolio.models.time import parse_timestamp
            btc_candles = daily["BTC"].completed_candles()
            moments = [
                as_of.isoformat().replace("+00:00", "Z")
                for as_of, _ in _candidate_boundaries(
                    btc_candles,
                    start=parse_timestamp(spec.start_at),
                    end=parse_timestamp(spec.end_at),
                )
            ]
            reviews = None
        else:
            def build(evidence):
                built = build_historical_reviews(
                    daily_by_symbol=daily, execution_by_symbol=execution_series,
                    hourly_by_symbol=execution_series,
                    execution_timeframe=spec.execution_timeframe,
                    symbols=symbols,
                    initial_weights=next(iter(spec.initial_portfolios.values())),
                    initial_value=spec.initial_value_usd, start_at=spec.start_at,
                    end_at=spec.end_at, policy=policy, semantic_score=None,
                    evidence=evidence,
                )
                return rebind_initial_weights(
                    built, spec.initial_portfolios[portfolio_name], spec.initial_value_usd,
                )

            reviews = build(evidence_plain)
            structural_reviews = build(evidence_structural)
            moments = [review.as_of for review in reviews]
        ratio = ethbtc_ratio_series(daily["ETH"], daily["BTC"])
        etf_differential = _etf_differentials(harvested, moments)
        eth_eval = evaluate_relative_signals(
            relative_signal_observations(
                ratio, moments, etf_differential_by_moment=etf_differential,
            ),
        )
        structural_eval = structural_ranking_power(
            evidence_structural, moments, prices=daily,
        )
        return {
            "spec": spec, "policy": policy, "reviews": reviews,
            "structural_reviews": None if not build_reviews else structural_reviews,
            "daily": daily,
            "risk_inputs": (
                build_risk_inputs_for_reviews(reviews, daily_by_symbol=daily, policy=policy)
                if reviews is not None else None
            ),
            "eth_eval": eth_eval, "structural_eval": structural_eval,
            "moments": moments, "ratio": ratio,
        }

    primary = window(Path(args.dataset), build_reviews=True)
    sister = window(Path(args.sister_dataset), build_reviews=False) if args.sister_dataset else None
    eth_admission = evaluate_signal_admission({
        "primary": primary["eth_eval"],
        **({"sister": sister["eth_eval"]} if sister else {}),
    })
    structural_admission = structural_ranking_admission({
        "primary": primary["structural_eval"],
        **({"sister": sister["structural_eval"]} if sister else {}),
    })
    ladder = run_v22_ablation(
        primary["reviews"], policy=primary["policy"],
        daily_by_symbol=primary["daily"],
        risk_inputs_by_review=primary["risk_inputs"],
        structural_reviews=primary["structural_reviews"],
        fee_bps=primary["spec"].fee_bps, slippage_bps=primary["spec"].slippage_bps,
        git_sha=_git_sha(),
        eth_tilt_admitted=bool(eth_admission["eth_tilt_admitted"]),
        structural_admitted=bool(structural_admission["structural_admitted"]),
    )
    sister_ladder = None
    if sister is not None and sister["reviews"] is None:
        # The sister window only contributes evaluations here; the decision
        # matrix records the single-window limitation honestly.
        sister_ladder = None
    result = assemble_v22_validation(
        ladder, policy=primary["policy"], daily_by_symbol=primary["daily"],
        eth_signal_evaluation=primary["eth_eval"],
        eth_signal_admission=eth_admission,
        structural_evaluation=primary["structural_eval"],
        structural_admission=structural_admission,
        sister_ladder_result=sister_ladder,
        ethbtc_excess=ethbtc_forward_excess_summary(primary["ratio"], primary["moments"]),
    )
    result["window"] = {"start_at": primary["spec"].start_at, "end_at": primary["spec"].end_at}
    result["run_id"] = primary["spec"].run_id
    result["experiment"] = f"{args.scope}/strict"
    path = Path(args.output) if args.output else Path(args.dataset) / "v22-validation.json"
    from crypto_portfolio.research.v21_validation import validation_manifest
    result["manifest"] = validation_manifest(
        primary["policy"], git_sha=_git_sha(),
        data_manifest=None,
    )
    _write(path, result)
    print(json.dumps({
        "status": "COMPLETED", "output": str(path),
        "rungs": [rung.get("rung") for rung in result["rungs"]],
        "eth_tilt_admitted": eth_admission["eth_tilt_admitted"],
        "structural_admitted": structural_admission["structural_admitted"],
    }, ensure_ascii=False, indent=2))




def command_v23(args):
    """Strategy V2.3 validation: ladder, registry, regime variants, decisions.

    Runs the frozen V2.3 A-I ladder (plan 10.2) over one dataset scope on
    structural point-in-time reviews, evaluates the ETH/BNB/AAVE
    BTC-relative signals and the unified admission across both windows,
    freezes the alpha registry, compares the regime authority variants,
    attributes risk and alpha, and derives the policy decision matrix — all
    stamped with the full V2.3 experiment identity (git SHA, policy hash,
    dataset manifest, registry hash, risk budget mode, carry convention).
    """
    from crypto_portfolio.engine.aave_relative_alpha import (
        aave_signal_observations,
        aavebtc_ratio_series,
        evaluate_aave_admission,
        evaluate_aave_signals,
    )
    from crypto_portfolio.engine.bnb_relative_alpha import (
        bnb_signal_observations,
        bnbbtc_ratio_series,
        evaluate_bnb_admission,
        evaluate_bnb_signals,
    )
    from crypto_portfolio.engine.eth_relative_alpha import (
        ethbtc_ratio_series,
        evaluate_relative_signals,
        evaluate_signal_admission,
        relative_signal_observations,
    )
    from crypto_portfolio.research.alpha_registry import (
        admitted_signals_for_asset,
        asset_admission_summary,
        build_registry,
    )
    from crypto_portfolio.research.cash_carry import (
        CashCarryConvention,
        rate_points_from_percent_series,
    )
    from crypto_portfolio.research.regime_variants import run_regime_variant_comparison
    from crypto_portfolio.research.v23_validation import (
        V23_STRATEGY_VERSION,
        alpha_attribution_report,
        run_v23_ablation,
        v23_manifest,
        v23_policy_decision_matrix,
    )

    def growth_maps(evidence, moments):
        bnb_growth = {
            moment: {
                "chain_tvl": ((evidence.structural or {}).get("BNB") or {}).get("chain_tvl"),
                "stablecoins": ((evidence.structural or {}).get("BNB") or {}).get("stablecoins"),
                "fees": ((evidence.structural or {}).get("BNB") or {}).get("fees"),
            }
            for moment in moments
        }
        aave_growth = {
            moment: {
                "tvl": ((evidence.structural or {}).get("AAVE") or {}).get("tvl"),
                "borrowed": ((evidence.structural or {}).get("AAVE") or {}).get("borrowed"),
                "fees": ((evidence.structural or {}).get("AAVE") or {}).get("fees"),
            }
            for moment in moments
        }
        return bnb_growth, aave_growth

    def window(root: Path):
        spec, manifest, series = load_dataset(root)
        daily, hourly = _series_maps(series, prefer_normalized=manifest.strict_ready)
        execution_series = daily if spec.execution_timeframe == "1D" else hourly
        harvested = load_evidence_series(root)
        evidence = (
            EvidenceContext.from_series(harvested, include_structural=True)
            if harvested else None
        )
        scope_name, portfolio_name = args.scope.split("/")
        symbols = spec.asset_scopes[scope_name]
        policy = _core_policy() if scope_name == "core" else load_policy()
        policy = policy_from_mapping({
            **policy.as_dict(),
            "risk_engine": {**policy.as_dict()["risk_engine"], "mode": "volatility_budget"},
        })
        reviews = rebind_initial_weights(
            build_historical_reviews(
                daily_by_symbol=daily, execution_by_symbol=execution_series,
                hourly_by_symbol=execution_series,
                execution_timeframe=spec.execution_timeframe, symbols=symbols,
                initial_weights=next(iter(spec.initial_portfolios.values())),
                initial_value=spec.initial_value_usd, start_at=spec.start_at,
                end_at=spec.end_at, policy=policy, semantic_score=None,
                evidence=evidence,
            ),
            spec.initial_portfolios[portfolio_name], spec.initial_value_usd,
        )
        risk_inputs = build_risk_inputs_for_reviews(
            reviews, daily_by_symbol=daily, policy=policy,
        )
        moments = [review.as_of for review in reviews]
        bnb_growth, aave_growth = growth_maps(evidence, moments)
        evaluations = {}
        if "ETH" in daily:
            ratio = ethbtc_ratio_series(daily["ETH"], daily["BTC"])
            evaluations["ETH"] = evaluate_relative_signals(
                relative_signal_observations(
                    ratio, moments, horizons=(30, 90, 180),
                    etf_differential_by_moment=_etf_differentials(harvested, moments),
                ), horizons=(30, 90, 180),
            )
        if "BNB" in daily:
            evaluations["BNB"] = evaluate_bnb_signals(
                bnb_signal_observations(
                    bnbbtc_ratio_series(daily["BNB"], daily["BTC"]), moments,
                    horizons=(30, 90, 180), growth_series_by_moment=bnb_growth,
                ), horizons=(30, 90, 180),
            )
        if "AAVE" in daily:
            evaluations["AAVE"] = evaluate_aave_signals(
                aave_signal_observations(
                    aavebtc_ratio_series(daily["AAVE"], daily["BTC"]), moments,
                    horizons=(30, 90, 180), growth_series_by_moment=aave_growth,
                ), horizons=(30, 90, 180),
            )
        carry_points = ()
        if harvested and "fred:DFF" in harvested:
            carry_points = rate_points_from_percent_series(harvested["fred:DFF"])
        return {
            "spec": spec, "manifest": manifest, "policy": policy,
            "reviews": reviews, "risk_inputs": risk_inputs,
            "evaluations": evaluations, "carry_points": carry_points,
        }

    primary = window(Path(args.dataset))
    sister = window(Path(args.sister_dataset)) if args.sister_dataset else None
    eth_admission = evaluate_signal_admission({
        "primary": primary["evaluations"]["ETH"],
        **({"sister": sister["evaluations"]["ETH"]} if sister else {}),
    })
    bnb_admission = evaluate_bnb_admission({
        "primary": primary["evaluations"]["BNB"],
        **({"sister": sister["evaluations"]["BNB"]} if sister else {}),
    })
    aave_admission = evaluate_aave_admission({
        "primary": primary["evaluations"]["AAVE"],
        **({"sister": sister["evaluations"]["AAVE"]} if sister else {}),
    })
    registry = build_registry({
        "ETH": eth_admission, "BNB": bnb_admission, "AAVE": aave_admission,
    })
    registry_summary = asset_admission_summary(registry)
    bnb_admitted = admitted_signals_for_asset(registry, "BNB")
    aave_admitted = admitted_signals_for_asset(registry, "AAVE")
    eth_admitted = bool(eth_admission["eth_tilt_admitted"])

    carry_mode = "ZERO" if args.no_carry else "RISK_FREE_PROXY"
    carry = (
        CashCarryConvention("ZERO")
        if carry_mode == "ZERO"
        else CashCarryConvention("RISK_FREE_PROXY", primary["carry_points"])
    )

    def ladder(source):
        return run_v23_ablation(
            source["reviews"], policy=source["policy"],
            risk_inputs_by_review=source["risk_inputs"],
            fee_bps=source["spec"].fee_bps, slippage_bps=source["spec"].slippage_bps,
            git_sha=_git_sha(), data_manifest=source["manifest"].as_dict(),
            alpha_registry_hash=registry["registry_hash"],
            cash_carry=carry, cash_carry_name=carry_mode,
            bnb_admitted_signals=bnb_admitted,
            aave_admitted_signals=aave_admitted,
            eth_tilt_admitted=eth_admitted,
        )

    primary_ladder = ladder(primary)
    sister_ladder = ladder(sister) if sister else None
    regime_variants = run_regime_variant_comparison(
        primary["reviews"], policy=primary["policy"],
        risk_inputs_by_review=primary["risk_inputs"],
        fee_bps=primary["spec"].fee_bps, slippage_bps=primary["spec"].slippage_bps,
        git_sha=_git_sha(),
    )

    def slim(ladder_result):
        return {
            "rungs": [
                {key: value for key, value in rung.items() if key != "result"}
                for rung in ladder_result["rungs"]
            ],
            "alpha_attribution": alpha_attribution_report(ladder_result["rungs"]),
            "note": ladder_result["note"],
        }

    result = {
        "strategy_version": V23_STRATEGY_VERSION,
        "run_id": primary["spec"].run_id,
        "experiment": f"{args.scope}/strict",
        "window": {"start_at": primary["spec"].start_at, "end_at": primary["spec"].end_at},
        "manifest": v23_manifest(
            primary["policy"], git_sha=_git_sha(),
            data_manifest=primary["manifest"].as_dict(),
            alpha_registry_hash=registry["registry_hash"],
            cash_carry_convention=carry_mode,
        ),
        "carry_convention": carry_mode,
        "alpha_registry": registry,
        "alpha_registry_summary": registry_summary,
        "signal_evaluations": {
            "primary": primary["evaluations"],
            **({"sister": sister["evaluations"]} if sister else {}),
        },
        "admissions": {
            "ETH": eth_admission, "BNB": bnb_admission, "AAVE": aave_admission,
        },
        "ladder": slim(primary_ladder),
        **({"sister_ladder": slim(sister_ladder)} if sister_ladder else {}),
        "regime_variants": {
            "ownership": regime_variants["ownership"],
            "variants": [
                {key: value for key, value in row.items() if key != "manifest"}
                for row in regime_variants["variants"]
            ],
        },
        "policy_decision": v23_policy_decision_matrix(
            primary_ladder, sister_ladder,
            bnb_admitted=bool(bnb_admitted),
            aave_admitted=bool(aave_admitted),
            eth_admitted=eth_admitted,
        ),
    }
    path = Path(args.output) if args.output else Path(args.dataset) / "v23-validation.json"
    _write(path, result)
    print(json.dumps({
        "status": "COMPLETED", "output": str(path),
        "carry": carry_mode,
        "rungs": [rung["rung"] for rung in primary_ladder["rungs"]],
        "admitted": {
            "BNB": list(bnb_admitted), "AAVE": list(aave_admitted), "ETH": eth_admitted,
        },
        "registry_hash": registry["registry_hash"][:16],
        "policy_decision": {
            module: verdict["decision"]
            for module, verdict in result["policy_decision"]["modules"].items()
        },
    }, ensure_ascii=False, indent=2))

def command_budget_sensitivity(args):
    from crypto_portfolio.research.sensitivity import drawdown_budget_sensitivity
    budgets = tuple(float(item) for item in str(args.budgets).split(",") if item.strip())
    result = drawdown_budget_sensitivity(
        args.dataset, budgets=budgets, scope=args.scope, portfolio=args.portfolio,
    )
    output = Path(args.output) if args.output else Path(args.dataset) / "budget-sensitivity.json"
    _write(output, result)
    for row in result["rows"]:
        print(
            f"D={row['budget']:.2f}: cagr={row['cagr']:+.4f} total={row['total_return']:+.4f} "
            f"maxdd={row['maximum_drawdown']:.4f} avg_cash={row['average_cash_weight']:.3f} "
            f"trades={row['trades']}({row['buys']}B/{row['sells']}S) "
            f"breach_days={row['breach_days']} budget_held={row['budget_held']}"
        )
    print(json.dumps({"output": str(output), "mechanism_note": result["mechanism_note"]}, ensure_ascii=False))


def command_validate(args):
    """Walk-forward validation diagnostics over one frozen dataset (Phase 6)."""
    from crypto_portfolio.models.policy import load_policy, policy_hash
    from crypto_portfolio.research.validation import (
        PARAMETER_CLASSIFICATION,
        block_bootstrap,
        dynamic_universe_eligibility,
        gap_risk_stress,
        stablecoin_stress,
        threshold_rank_monotonicity,
        walk_forward_windows,
    )

    root = Path(args.dataset)
    spec, manifest, series = load_dataset(root)
    run = json.loads((root / "run.json").read_text(encoding="utf-8"))
    policy = load_policy()
    experiment = run["runs"].get(args.experiment)
    if not experiment or experiment.get("status") != "COMPLETED":
        raise SystemExit(f"experiment {args.experiment} is not COMPLETED in run.json")
    result_payload = experiment["result"]

    # Daily strategy returns from the ledger valuation path.
    valuations = result_payload["valuations"]
    navs = [float(item["total_value_usd"]) for item in valuations]
    returns = [navs[i] / navs[i - 1] - 1.0 for i in range(1, len(navs))]
    stable_symbols = set(policy.stable_symbols)
    risky_valuations = [
        {
            "nav": float(item["total_value_usd"]),
            "risky_weight": max(0.0, 1.0 - sum(
                float(weight) for symbol, weight in (item.get("weights") or {}).items()
                if symbol in stable_symbols
            )),
        }
        for item in valuations
    ]

    # Point-in-time universe eligibility at quarterly boundaries.
    from crypto_portfolio.models.market import OHLCVSeries
    from crypto_portfolio.models.time import parse_timestamp
    from datetime import timedelta
    daily = {k: v for k, v in series.items() if isinstance(v, OHLCVSeries) and v.timeframe == "1D"}
    universe_rows = []
    boundaries = sorted({
        parse_timestamp(spec.start_at) + timedelta(days=step)
        for step in range(0, max(1, (parse_timestamp(spec.end_at) - parse_timestamp(spec.start_at)).days) + 1, 90)
    })
    caches = {
        s.symbol: [item for item in s.completed_candles()]
        for s in daily.values()
    }
    for boundary in boundaries:
        closes: dict[str, list[float]] = {}
        volumes: dict[str, list[float]] = {}
        for symbol, candles in caches.items():
            window = [c for c in candles if parse_timestamp(c.timestamp) < boundary]
            closes[symbol] = [float(c.close) for c in window]
            volumes[symbol] = [float(c.volume) for c in window]
        row = dynamic_universe_eligibility(
            closes_by_symbol=closes, volumes_by_symbol=volumes,
        )
        universe_rows.append({"as_of": boundary.date().isoformat(), **row})

    # Threshold calibration over the score-evaluation rows.
    score_rows: list[dict[str, Any]] = []
    score_path = root / "score-evaluation.json"
    if score_path.exists():
        evaluation = json.loads(score_path.read_text(encoding="utf-8"))
        for scope in (evaluation.get("scopes") or {}).values():
            for row in scope.get("rows", []):
                for horizon in (30, 90, 180):
                    label = (row.get("labels") or {}).get(str(horizon)) or {}
                    if label.get("status") != "AVAILABLE":
                        continue
                    score_rows.append({
                        "symbol": row["symbol"],
                        "horizon": horizon,
                        "normalized_score": row.get("normalized_score"),
                        "score": row.get("score"),
                        "forward_return": label.get("forward_return"),
                        "relative_return_vs_btc": label.get("relative_return_vs_btc"),
                    })
    calibration: dict[str, Any] = {}
    for horizon in (30, 90, 180):
        horizon_rows = [r for r in score_rows if r["horizon"] == horizon]
        block = {"samples": len(horizon_rows)}
        for field in ("normalized_score", "score"):
            block[field] = threshold_rank_monotonicity(
                horizon_rows, score_field=field, forward_field="forward_return",
            )
        block["normalized_vs_btc_relative"] = threshold_rank_monotonicity(
            horizon_rows, score_field="normalized_score",
            forward_field="relative_return_vs_btc",
        )
        calibration[str(horizon)] = block

    final_weights = {
        str(symbol): float(weight)
        for symbol, weight in (valuations[-1].get("weights") or {}).items()
    }
    result = {
        "contract": "STRATEGY_V2_VALIDATION",
        "freeze": {
            "git_sha": _git_sha(),
            "policy_hash": policy_hash(policy),
            "dataset_manifest_id": manifest.manifest_id,
            "dataset_strict_ready": manifest.strict_ready,
            "window": {"start_at": spec.start_at, "end_at": spec.end_at},
            "experiment": args.experiment,
            "universe": sorted(set(policy.core_symbols) | set(policy.satellite_symbols)),
        },
        "parameter_classification": PARAMETER_CLASSIFICATION,
        "walk_forward_windows": walk_forward_windows(
            start_at=spec.start_at, end_at=spec.end_at,
            train_years=args.train_years, validate_years=args.validate_years,
        ),
        "dynamic_universe": universe_rows,
        "threshold_calibration": calibration,
        "block_bootstrap": block_bootstrap(
            returns, block_days=args.block_days, draws=args.draws,
            seed=args.seed, risk_budget=float(policy.max_portfolio_drawdown),
        ),
        "gap_risk_stress": gap_risk_stress(
            valuations=risky_valuations, risk_budget=float(policy.max_portfolio_drawdown),
        ),
        "stablecoin_stress": stablecoin_stress(final_weights, policy=policy),
        "attribution": result_payload.get("strategy_attribution"),
    }
    output = Path(args.output) if args.output else root / "validation.json"
    _write(output, result)
    print(json.dumps({
        "output": str(output),
        "windows": len(result["walk_forward_windows"]),
        "calibration_samples": len(score_rows),
        "bootstrap_breach_frequency": result["block_bootstrap"]["breach_frequency"],
        "gap_breaches": sum(1 for row in result["gap_risk_stress"]["rows"] if row["budget_breach"]),
    }, ensure_ascii=False))


def command_ablation(args):
    """Layer-ablation replays over one frozen dataset (Phase 6)."""
    from crypto_portfolio.models.policy import load_policy, policy_from_mapping, policy_hash
    from crypto_portfolio.research.historical_builder import (
        build_historical_reviews,
        rebind_initial_weights,
    )
    from crypto_portfolio.research.validation import ablation_policy

    spec, manifest, series = load_dataset(args.dataset)
    daily, hourly = _series_maps(series, prefer_normalized=manifest.strict_ready)
    execution_series = daily if spec.execution_timeframe == "1D" else hourly
    harvested = load_evidence_series(Path(args.dataset))
    evidence = EvidenceContext.from_series(harvested) if harvested else None
    scope_name, portfolio_name, mode, cost = args.experiment.split("/")
    symbols = spec.asset_scopes[scope_name]
    policy = _core_policy() if scope_name == "core" else load_policy()
    initial_weights = spec.initial_portfolios[portfolio_name]
    variants: list[tuple[str, dict[str, Any]]] = [
        ("baseline", {}),
        ("without_fundamentals", {"disable_factors": ("fundamentals",)}),
        ("without_valuation", {"disable_factors": ("valuation", "btc_valuation")}),
        ("without_onchain", {"disable_factors": ("onchain",)}),
        ("without_relative_strength", {"disable_factors": ("relative_strength_btc",)}),
        ("without_regime_trend_domain", {}),
        ("without_execution_overlay", {"disable_execution_overlay": True}),
        ("without_satellites", {"disable_satellites": True}),
        ("without_drawdown_emergency_overlay", {"disable_emergency_overlay": True}),
    ]
    rows = []
    for name, options in variants:
        if args.only is not None and name != args.only:
            continue
        variant_manifest: dict[str, Any] = {}
        if name == "without_regime_trend_domain":
            import json as _json
            variant_data = _json.loads(_json.dumps(policy.as_dict()))
            weights = dict(variant_data["regime_model"]["domain_weights"])
            # Zero the trend domain and renormalize the systemic domains; the
            # key stays because the parser requires the exact domain set.
            weights["trend"] = 0.0
            total_weight = sum(weights.values())
            variant_data["regime_model"]["domain_weights"] = {
                k: v / total_weight for k, v in weights.items()
            }
            variant_manifest = {"disable_regime_trend_domain": True}
        else:
            variant_data, variant_manifest = ablation_policy(policy, **options)
        try:
            variant_policy = policy_from_mapping(variant_data)
            reviews = build_historical_reviews(
                daily_by_symbol=daily, execution_by_symbol=execution_series,
                hourly_by_symbol=execution_series, execution_timeframe=spec.execution_timeframe,
                symbols=symbols, initial_weights=next(iter(spec.initial_portfolios.values())),
                initial_value=spec.initial_value_usd, start_at=spec.start_at,
                end_at=spec.end_at, policy=variant_policy, semantic_score=None,
                evidence=evidence,
            )
            reviews = rebind_initial_weights(reviews, initial_weights, spec.initial_value_usd)
            result = run_historical_backtest(
                reviews, policy=variant_policy,
                fee_bps=spec.fee_bps, slippage_bps=spec.slippage_bps,
            )
        except Exception as exc:
            rows.append({
                "variant": name, "status": "BLOCKED",
                "reason": f"{exc.__class__.__name__}: {exc}",
                "policy_hash": policy_hash(variant_policy),
            })
            continue
        metrics = result["metrics"]
        rows.append({
            "variant": name,
            "status": "COMPLETED",
            "policy_hash": policy_hash(variant_policy),
            "manifest": variant_manifest,
            "cagr": metrics["cagr"],
            "total_return": metrics["total_return"],
            "maximum_drawdown": metrics["maximum_drawdown"],
            "annualized_volatility": metrics["annualized_volatility"],
            "sharpe_rf_zero": metrics["sharpe_rf_zero"],
            "trades": len(result["trades"]),
            "total_turnover": result["total_turnover"],
            "regime_counts": result["regime_counts"],
            "risk_engine_diagnostics": result.get("risk_engine_diagnostics"),
            "attribution": result.get("strategy_attribution"),
        })
    output = Path(args.output) if args.output else Path(args.dataset) / "ablation.json"
    _write(output, {
        "contract": "STRATEGY_V2_ABLATION",
        "freeze": {"git_sha": _git_sha(), "experiment": args.experiment},
        "rows": rows,
    })
    for row in rows:
        if row["status"] != "COMPLETED":
            print(f"{row['variant']}: BLOCKED {row['reason'][:80]}")
            continue
        print(
            f"{row['variant']}: cagr={row['cagr']:+.4f} maxdd={row['maximum_drawdown']:.4f} "
            f"vol={row['annualized_volatility']:.4f} sharpe={row['sharpe_rf_zero']:.3f} "
            f"trades={row['trades']} turnover={row['total_turnover']:.3f}"
        )
    print(json.dumps({"output": str(output)}, ensure_ascii=False))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-dataset")
    build.add_argument("--run-id", default="strategy-validation-2024-present")
    build.add_argument("--end-at")
    build.add_argument("--start-at", help="override the default review window start")
    build.add_argument("--warmup-start-at", help="override the warm-up start feeding indicator history")
    build.add_argument("--spec")
    build.add_argument("--output")
    build.set_defaults(handler=command_build)
    audit = sub.add_parser("audit-data")
    audit.add_argument("dataset")
    audit.add_argument("--output")
    audit.set_defaults(handler=command_audit)
    run = sub.add_parser("run")
    run.add_argument("dataset")
    run.add_argument("--allow-usdt-approximation", action="store_true")
    run.add_argument(
        "--risk-engine-mode",
        choices=["legacy_drawdown", "volatility_budget"],
        default=None,
        help="research-only override of risk_engine.mode for A/B replay; "
        "the canonical policy file is never rewritten",
    )
    run.set_defaults(handler=command_run)
    decisions = sub.add_parser("evaluate-decisions")
    decisions.add_argument("dataset")
    decisions.add_argument("--decisions")
    decisions.add_argument("--status-events")
    decisions.add_argument("--artifact-root")
    decisions.add_argument("--output")
    decisions.set_defaults(handler=command_evaluate_decisions)
    scores = sub.add_parser("evaluate-scores")
    scores.add_argument("dataset")
    scores.add_argument("--output")
    scores.set_defaults(handler=command_evaluate_scores)
    report = sub.add_parser("report")
    report.add_argument("run")
    report.add_argument("--output")
    report.set_defaults(handler=command_report)
    validate = sub.add_parser("validate")
    validate.add_argument("dataset")
    validate.add_argument("--experiment", default="full/core_existing/strict/main_cost")
    validate.add_argument("--train-years", type=int, default=2)
    validate.add_argument("--validate-years", type=int, default=1)
    validate.add_argument("--block-days", type=int, default=21)
    validate.add_argument("--draws", type=int, default=200)
    validate.add_argument("--seed", type=int, default=20260925)
    validate.add_argument("--output")
    validate.set_defaults(handler=command_validate)

    ablation = sub.add_parser("ablation")
    ablation.add_argument("dataset")
    ablation.add_argument("--experiment", default="full/core_existing/strict/main_cost")
    ablation.add_argument("--only", default=None, help="run a single variant by name")
    ablation.add_argument("--output")
    ablation.set_defaults(handler=command_ablation)

    v21 = sub.add_parser("v21")
    v21.add_argument("dataset")
    v21.add_argument("--scope", default="full/core_existing")
    v21.add_argument("--output")
    v21.set_defaults(handler=command_v21)

    v22 = sub.add_parser("v22")
    v22.add_argument("dataset")
    v22.add_argument("--scope", default="full/core_existing")
    v22.add_argument("--sister-dataset", default=None,
                     help="second validation window dataset for cross-window admission")
    v22.add_argument("--output")
    v22.set_defaults(handler=command_v22)

    v23 = sub.add_parser("v23")
    v23.add_argument("dataset")
    v23.add_argument("--scope", default="full/core_existing")
    v23.add_argument("--sister-dataset", default=None,
                     help="second validation window dataset for cross-window admission")
    v23.add_argument("--no-carry", action="store_true",
                     help="run with the ZERO cash carry convention")
    v23.add_argument("--output")
    v23.set_defaults(handler=command_v23)

    sensitivity = sub.add_parser("budget-sensitivity")
    sensitivity.add_argument("dataset")
    sensitivity.add_argument("--budgets", default="0.15,0.20,0.25")
    sensitivity.add_argument("--scope", default="core")
    sensitivity.add_argument("--portfolio", default="core_existing")
    sensitivity.add_argument("--output")
    sensitivity.set_defaults(handler=command_budget_sensitivity)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
