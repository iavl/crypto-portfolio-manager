#!/usr/bin/env python3
"""Build, audit, run, evaluate, and report historical strategy research."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
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
from crypto_portfolio.research.decision_evaluation import evaluate_decision_history  # noqa: E402
from crypto_portfolio.research.historical_builder import (  # noqa: E402
    apply_semantic_scenario, build_historical_reviews, rebind_initial_weights,
)
from crypto_portfolio.research.orchestrator import run_historical_backtest  # noqa: E402
from crypto_portfolio.research.reporting import render_run_report  # noqa: E402
from crypto_portfolio.research.score_evaluation import evaluate_scores  # noqa: E402
from crypto_portfolio.research.stress import drawdown_boundary_stress  # noqa: E402
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
    for key, value in series.items():
        if prefer_normalized and not key.startswith("normalized:"):
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
    spec = default_backtest_spec(
        run_id=args.run_id, end_at=args.end_at or _default_end(),
        policy_hash=policy_hash(canonical), git_sha=_git_sha(),
    ) if args.spec is None else BacktestSpec.from_mapping(json.loads(Path(args.spec).read_text(encoding="utf-8")))
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
        "valuation_basis": "USD" if manifest.strict_ready else "USDT_APPROXIMATION",
        "strict_status": "ELIGIBLE" if manifest.strict_ready else "BLOCKED",
        "policy_hashes": {}, "stress": drawdown_boundary_stress(), "score_evaluations": {}, "runs": {},
    }
    if not manifest.strict_ready and not args.allow_usdt_approximation:
        run["status"] = "BLOCKED_BY_DATA_MANIFEST"
        path = root / "run.json"
        _write(path, run)
        print(json.dumps({"status": run["status"], "output": str(path), "blockers": list(manifest.blockers)}, ensure_ascii=False, indent=2))
        return
    daily, hourly = _series_maps(series, prefer_normalized=manifest.strict_ready)
    policies = {"core": _core_policy(), "full": load_policy()}
    for scope_name, scope_symbols in spec.asset_scopes.items():
        policy = policies[scope_name]
        run["policy_hashes"][scope_name] = policy_hash(policy)
        try:
            base_reviews = build_historical_reviews(
                daily_by_symbol=daily, hourly_by_symbol=hourly,
                symbols=scope_symbols, initial_weights=next(iter(spec.initial_portfolios.values())),
                initial_value=spec.initial_value_usd, start_at=spec.start_at,
                end_at=spec.end_at, policy=policy, semantic_score=None,
            )
        except Exception as exc:
            for portfolio_name in spec.initial_portfolios:
                for semantic in (None, *spec.semantic_scenarios):
                    mode = "strict" if semantic is None else f"synthetic_{semantic}"
                    run["runs"][f"{scope_name}/{portfolio_name}/{mode}/main_cost"] = {
                        "status": "BLOCKED", "reason": f"{exc.__class__.__name__}: {exc}",
                    }
            continue
        score_observations = []
        for review in base_reviews:
            for symbol, raw_assessment in review.assessments.items():
                assessment = raw_assessment
                score_observations.append({
                    "timestamp": review.as_of, "symbol": symbol,
                    "score": assessment["weighted_score"],
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
                        ),
                    }
                    if semantic is None:
                        weekly_name = f"{scope_name}/{portfolio_name}/strict/weekly_sensitivity"
                        run["runs"][weekly_name] = {
                            "status": "COMPLETED", "validation_mode": "CADENCE_SENSITIVITY",
                            "result": run_historical_backtest(
                                reviews, policy=policy, fee_bps=spec.fee_bps,
                                slippage_bps=spec.slippage_bps, ordinary_review_weekday=0,
                            ),
                        }
                        for cost in spec.cost_sensitivity_bps:
                            cost_name = f"{scope_name}/{portfolio_name}/strict/cost_{cost:g}bps"
                            run["runs"][cost_name] = {
                                "status": "COMPLETED", "validation_mode": "COST_SENSITIVITY",
                                "result": run_historical_backtest(
                                    reviews, policy=policy, fee_bps=cost, slippage_bps=0.0,
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
    observations_by_scope = {}
    for scope_name, symbols in spec.asset_scopes.items():
        policy = _core_policy() if scope_name == "core" else load_policy()
        reviews = build_historical_reviews(
            daily_by_symbol=daily, hourly_by_symbol=hourly, symbols=symbols,
            initial_weights=next(iter(spec.initial_portfolios.values())),
            initial_value=spec.initial_value_usd, start_at=spec.start_at,
            end_at=spec.end_at, policy=policy, semantic_score=None,
        )
        observations = []
        for review in reviews:
            for symbol, assessment in review.assessments.items():
                observations.append({
                    "timestamp": review.as_of, "symbol": symbol,
                    "score": assessment["weighted_score"],
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
    result = render_run_report(run, args.output or path.parent / "report")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-dataset")
    build.add_argument("--run-id", default="strategy-validation-2024-present")
    build.add_argument("--end-at")
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
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
