#!/usr/bin/env python3
"""Report whether the policy is feasible and whether a run tests it.

Read-only. Nothing here rewrites the policy, the run, or any artifact, and no
threshold or risk number is changed. The command answers two questions the
existing tooling does not ask:

1. Can the configured score thresholds and the configured risk budget be
   satisfied at all, given the policy's own weights and stress scenario?
2. Does a finished backtest directory actually exercise the strategy, or does
   it measure a degenerate path (missing evidence, blocked entries, one-way
   trading)?

Exit status is 1 when any ERROR-severity finding is present.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_portfolio.engine.feasibility import (  # noqa: E402
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    check_policy_feasibility,
    coverage_required_for,
    score_thresholds,
)
from crypto_portfolio.models.policy import load_policy, policy_hash  # noqa: E402


def _render_policy(policy, coverage) -> list[str]:
    lines: list[str] = []
    report = check_policy_feasibility(policy, coverage=coverage)
    lines.append(f"policy hash: {policy_hash(policy)}")
    lines.append(
        f"risk budget: {policy.max_portfolio_drawdown:.4f}  "
        f"min stablecoin: {policy.min_stablecoin_weight:.4f}  "
        f"minimum investable coverage: "
        f"{float(policy.scoring.get('minimum_investable_coverage', 0)):.2f}"
    )
    lines.append("")

    lines.append("score reachability")
    lines.append(f"  {'threshold':<48}{'value':>7}{'needs cov':>10}  profiles")
    for threshold in score_thresholds(policy):
        required = coverage_required_for(threshold.value)
        lines.append(
            f"  {threshold.name:<48}{threshold.value:>7.1f}{required:>10.2f}  "
            f"{', '.join(threshold.profiles) or '-'}"
        )
    if report.score_bands:
        lines.append("")
        lines.append("reachable score band at observed coverage")
        for name, band in sorted(report.score_bands.items()):
            lines.append(
                f"  {name:<20} coverage {band.coverage:.4f}  "
                f"band [{band.low:.2f}, {band.high:.2f}]"
            )
    lines.append("")

    from crypto_portfolio.engine.signal_ownership import signal_ownership_report
    ownership = signal_ownership_report(policy)
    lines.append("signal ownership")
    lines.append(
        f"  {'signal':<22}{'primary owner':<18}consumers"
    )
    for row in ownership["signals"]:
        lines.append(
            f"  {row['signal']:<22}{row['primary_owner']:<18}{', '.join(row['consumers'])}"
            + ("  [MULTIPLE_POLICY_AUTHORITY]" if row["multiple_policy_authority"] else "")
        )
    lines.append("")

    lines.append("drawdown budget under each scenario")
    lines.append(
        f"  {'regime':<22}{'composition':<24}{'scenario':<18}"
        f"{'projected':>11}{'budget':>9}{'breach':>8}{'need stable':>13}"
    )
    for row in report.drawdown_rows:
        if row.get("status") != "AVAILABLE":
            continue
        required = row.get("required_stablecoin_target")
        lines.append(
            f"  {row['regime']:<22}{row['scenario']:<24}"
            f"{str(row.get('scenario_name', '')):<16}"
            f"{row['projected_drawdown']:>11.4f}{row['budget']:>9.4f}"
            f"{str(row['budget_breach']):>8}"
            f"{'-' if required is None else f'{required:.4f}':>13}"
        )
    lines.append("")
    lines.extend(_render_findings(report.findings))
    return lines


def _render_findings(findings) -> list[str]:
    lines: list[str] = []
    errors = [item for item in findings if item.severity == SEVERITY_ERROR]
    warnings = [item for item in findings if item.severity == SEVERITY_WARNING]
    other = [
        item for item in findings
        if item.severity not in {SEVERITY_ERROR, SEVERITY_WARNING}
    ]
    for label, group in (("ERROR", errors), ("WARNING", warnings), ("INFO", other)):
        if not group:
            continue
        lines.append(f"{label} ({len(group)})")
        seen: set[tuple[str, str]] = set()
        for item in group:
            key = (item.code, item.subject)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  [{item.code}] {item.subject}")
            lines.append(f"      {item.message}")
        lines.append("")
    return lines


def _render_run(directory: Path, policy, minimum_investable) -> list[str]:
    from crypto_portfolio.research.validity_gate import build_validity_report

    report = build_validity_report(
        directory, policy=policy, minimum_investable=minimum_investable
    )
    lines: list[str] = []
    lines.append(f"run: {report.run_id}")
    lines.append(f"verdict: {report.verdict}")
    lines.append("")

    lines.append("observed coverage")
    for scope, stats in sorted(report.coverage.items()):
        lines.append(
            f"  {scope:<8} samples {int(stats.get('samples', 0)):<6} "
            f"min {stats.get('min', float('nan')):.4f} "
            f"median {stats.get('median', float('nan')):.4f} "
            f"max {stats.get('max', float('nan')):.4f}  "
            f"bands {stats.get('bands')}"
        )
    for key, stats in sorted(report.profile_coverage.items()):
        lines.append(
            f"  {key:<24} coverage {stats['coverage']:.4f} "
            f"({int(stats['samples'])} readings)"
        )
    lines.append("")

    lines.append("factor availability")
    for scope, counts in sorted(report.factor_availability.items()):
        lines.append(f"  scope {scope}")
        for factor, bucket in sorted(counts.items()):
            total = sum(bucket.values()) or 1
            available = bucket.get("AVAILABLE", 0)
            lines.append(
                f"    {factor:<24} AVAILABLE {available:>5}/{total:<5} "
                f"({available / total:.1%})  "
                f"MISSING {bucket.get('MISSING', 0):>5}  "
                f"NOT_APPLICABLE {bucket.get('NOT_APPLICABLE', 0):>5}"
            )
    lines.append("")

    lines.append("forward-return association")
    lines.append(
        f"  {'scope/horizon':<20}{'available':>10}{'blocks':>8}"
        f"{'spearman':>11}{'relative':>11}"
    )
    for row in report.sorting:
        lines.append(
            f"  {row['scope'] + '/' + str(row['horizon_days']) + 'd':<20}"
            f"{str(row.get('available_samples')):>10}"
            f"{str(row.get('non_overlapping_samples')):>8}"
            f"{row.get('score_forward_spearman', float('nan')):>11.4f}"
            f"{row.get('score_relative_spearman', float('nan')):>11.4f}"
        )
    lines.append("")

    lines.append("observed asset drawdown")
    for symbol, value in sorted(report.observed_drawdowns.items()):
        lines.append(f"  {symbol:<8} {value:>9.4f}")
    lines.append("")

    one_way = [
        name for name, item in sorted(report.experiments.items())
        if item.get("status") == "AVAILABLE"
        and int(item.get("trades") or 0) > 0
        and (not item["sides"].get("BUY") or not item["sides"].get("SELL"))
    ]
    silent = [
        name for name, item in sorted(report.experiments.items())
        if item.get("status") == "AVAILABLE" and int(item.get("trades") or 0) == 0
    ]
    lines.append(f"experiments total {len(report.experiments)}")
    lines.append(f"  one-way (single direction only): {len(one_way)}")
    for name in one_way[:6]:
        item = report.experiments[name]
        lines.append(
            f"    {name}: {item['trades']} trades "
            f"{item['sides']} first {item['first_trade']} last {item['last_trade']}"
        )
    if len(one_way) > 6:
        lines.append(f"    ... and {len(one_way) - 6} more")
    lines.append(f"  never traded: {len(silent)}")
    for name in silent[:6]:
        item = report.experiments[name]
        lines.append(f"    {name}: {item['trades']} trades")
    if len(silent) > 6:
        lines.append(f"    ... and {len(silent) - 6} more")
    lines.append("")

    lines.extend(_render_findings(report.findings))
    return lines


def _json_payload(policy, coverage, run_directory, minimum_investable) -> dict:
    report = check_policy_feasibility(policy, coverage=coverage)
    payload = {
        "policy_hash": policy_hash(policy),
        "risk_budget": policy.max_portfolio_drawdown,
        "minimum_investable_coverage": float(
            policy.scoring.get("minimum_investable_coverage", 0.0)
        ),
        "thresholds": [
            {
                "name": item.name,
                "value": item.value,
                "required_coverage": coverage_required_for(item.value),
                "profiles": list(item.profiles),
                "entry_style": item.entry_style,
            }
            for item in score_thresholds(policy)
        ],
        "score_bands": {
            name: {"coverage": band.coverage, "low": band.low, "high": band.high}
            for name, band in report.score_bands.items()
        },
        "drawdown_rows": [dict(row) for row in report.drawdown_rows],
        "findings": [
            {
                "code": item.code,
                "severity": item.severity,
                "subject": item.subject,
                "message": item.message,
                "values": _plain(item.values),
            }
            for item in report.findings
        ],
        "errors": len(report.errors),
        "warnings": len(report.warnings),
        "ok": report.ok,
    }
    if run_directory is not None:
        from crypto_portfolio.research.validity_gate import build_validity_report

        run = build_validity_report(
            run_directory, policy=policy, minimum_investable=minimum_investable
        )
        payload["run"] = {
            "run_id": run.run_id,
            "verdict": run.verdict,
            "coverage": _plain(run.coverage),
            "profile_coverage": _plain(run.profile_coverage),
            "factor_availability": _plain(run.factor_availability),
            "sorting": [dict(row) for row in run.sorting],
            "observed_drawdowns": _plain(run.observed_drawdowns),
            "experiments": _plain(run.experiments),
            "findings": [
                {
                    "code": item.code,
                    "severity": item.severity,
                    "subject": item.subject,
                    "message": item.message,
                    "values": _plain(item.values),
                }
                for item in run.findings
            ],
        }
    return payload


def _plain(value):
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check policy feasibility and backtest-run validity (read-only)."
    )
    parser.add_argument(
        "run_directory", nargs="?", default=None,
        help="finished run directory to inspect; omit for a policy-only check",
    )
    parser.add_argument(
        "--coverage", type=float, default=None,
        help="assume one coverage level for every scoring profile",
    )
    parser.add_argument(
        "--minimum-investable-coverage", type=float, default=None,
        help="override the policy floor used to judge observed coverage",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    policy = load_policy()
    coverage = args.coverage
    if coverage is None and args.run_directory is None:
        coverage = 1.0

    run_directory = Path(args.run_directory).expanduser() if args.run_directory else None
    if run_directory is not None and not run_directory.is_dir():
        parser.error(f"run directory not found: {run_directory}")

    if args.json:
        payload = _json_payload(
            policy, coverage, run_directory, args.minimum_investable_coverage
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        lines = _render_policy(policy, coverage)
        if run_directory is not None:
            lines.append("")
            lines.append("=" * 72)
            lines.append("")
            lines.extend(
                _render_run(run_directory, policy, args.minimum_investable_coverage)
            )
        print("\n".join(lines))

    report = check_policy_feasibility(policy, coverage=coverage)
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
