"""Read-only validity gate for a finished historical backtest run.

A backtest can be arithmetically correct and still not be a test of the
strategy. This module inspects a completed run directory and says which of the
two it is, using only artifacts the run already wrote:

- ``manifest.json``           -- what the data audit did and did not block on
- ``score-evaluation.json``   -- factor availability, coverage, forward returns
- ``report/*.trades.csv``     -- realized direction of every experiment
- ``decision-evaluation.json``-- decision mark-to-market sample size
- ``spec.json``/``series/``   -- observed drawdown of the traded assets

It never recomputes portfolio results, never edits the run, and never changes
the policy. Every finding is derived deterministically from the files on disk.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..engine.feasibility import (
    ENTRY_LOCKED_BY_COVERAGE,
    Finding,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    check_policy_feasibility,
)

__all__ = [
    "COVERAGE_BELOW_INVESTABLE",
    "DECISION_SAMPLE_EMPTY",
    "MANIFEST_BLOCKERS_IGNORE_SIGNAL_LAYER",
    "MANIFEST_MISSING",
    "NO_TRADES_IN_WINDOW",
    "ONE_WAY_RATCHER",
    "SORTING_UNDERPOWERED",
    "TRADING_STALLED",
    "RunValidityReport",
    "build_validity_report",
    "coverage_summary",
    "factor_availability",
    "observed_drawdowns",
    "profile_coverage",
    "sorting_power",
    "threshold_hit_rates",
    "trade_direction_summary",
    "verdict",
]

SEVERITY_INFO = "INFO"

MANIFEST_MISSING = "MANIFEST_MISSING"
MANIFEST_BLOCKERS_IGNORE_SIGNAL_LAYER = "MANIFEST_BLOCKERS_IGNORE_SIGNAL_LAYER"
COVERAGE_BELOW_INVESTABLE = "COVERAGE_BELOW_INVESTABLE"
SORTING_UNDERPOWERED = "SORTING_UNDERPOWERED"
ONE_WAY_RATCHER = "ONE_WAY_RATCHER"
NO_TRADES_IN_WINDOW = "NO_TRADES_IN_WINDOW"
TRADING_STALLED = "TRADING_STALLED"
DECISION_SAMPLE_EMPTY = "DECISION_SAMPLE_EMPTY"

# Independent blocks below this count cannot distinguish a rank correlation
# from noise; |rho| needs roughly 0.63 at n = 10 for p < 0.05.
MIN_INDEPENDENT_BLOCKS = 20
# A one-sided strategy is reported as a ratchet only when it also stops trading
# well before the window ends, so a short all-buys period is not mislabelled.
STALL_GAP_DAYS = 60

VERDICT_NOT_A_TEST = "DEGENERATE_NOT_A_TEST_OF_THE_STRATEGY"
VERDICT_UNDERPOWERED = "VALID_RUN_UNDERPOWERED_INFERENCE"
VERDICT_OK = "NO_STRUCTURAL_OBJECTION_FOUND"


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _day(value: Any) -> str:
    return str(value or "")[:10]


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def coverage_summary(score_evaluation: Any) -> dict[str, dict[str, float]]:
    """Observed coverage statistics per scope from a score-evaluation artifact."""
    result: dict[str, dict[str, float]] = {}
    scopes = (score_evaluation or {}).get("scopes") or {}
    for scope, payload in scopes.items():
        rows = payload.get("rows") or []
        values = [
            float(row["coverage"]) for row in rows
            if isinstance(row.get("coverage"), (int, float))
        ]
        bands: dict[str, int] = {}
        for row in rows:
            label = str(row.get("coverage_band", "UNKNOWN"))
            bands[label] = bands.get(label, 0) + 1
        if not values:
            result[scope] = {"samples": 0.0}
            continue
        result[scope] = {
            "samples": float(len(values)),
            "min": min(values),
            "median": _median(values),
            "max": max(values),
            "bands": bands,
        }
    return result


def factor_availability(score_evaluation: Any) -> dict[str, dict[str, dict[str, int]]]:
    """Per-scope, per-factor availability counts."""
    result: dict[str, dict[str, dict[str, int]]] = {}
    scopes = (score_evaluation or {}).get("scopes") or {}
    for scope, payload in scopes.items():
        counts: dict[str, dict[str, int]] = {}
        for row in payload.get("rows") or []:
            for factor, record in (row.get("factor_scores") or {}).items():
                bucket = counts.setdefault(factor, {})
                label = str(record.get("availability", "UNKNOWN"))
                bucket[label] = bucket.get(label, 0) + 1
        result[scope] = counts
    return result


def profile_coverage(
    score_evaluation: Any, policy: Any
) -> dict[str, dict[str, Any]]:
    """Coverage per scoring profile, recomputed from the row reliabilities.

    The artifact reports one aggregate ``coverage`` per reading, but the score
    band depends on the coverage of the *profile* that scored the asset. Terms
    are reconstructed exactly as ``engine.scoring`` defines them,
    ``sum(weight_i * reliability_i)``, from the per-factor reliabilities the
    artifact already stores.
    """
    buckets: dict[str, list[float]] = {}
    for scope, payload in ((score_evaluation or {}).get("scopes") or {}).items():
        for row in payload.get("rows") or []:
            symbol = str(row.get("symbol", "")).strip().upper()
            records = row.get("factor_scores") or {}
            if not symbol or not records:
                continue
            profile = policy.scoring_profile_name(symbol)
            weights = policy.scoring_profile(symbol)
            total = 0.0
            for factor, record in records.items():
                weight = float(weights.get(factor, 0.0))
                if weight <= 0.0:
                    continue
                reliability = record.get("reliability")
                if isinstance(reliability, (int, float)):
                    total += weight * float(reliability)
            buckets.setdefault(f"{scope}:{profile}", []).append(total)
    return {
        key: {"samples": float(len(values)), "coverage": _median(values)}
        for key, values in sorted(buckets.items())
    }


def threshold_hit_rates(
    score_evaluation: Any, thresholds: Iterable[float]
) -> dict[str, dict[str, float]]:
    """Share of readings at or above each threshold, per scope."""
    result: dict[str, dict[str, float]] = {}
    scopes = (score_evaluation or {}).get("scopes") or {}
    for scope, payload in scopes.items():
        scores = [
            float(row["score"]) for row in payload.get("rows") or []
            if isinstance(row.get("score"), (int, float))
        ]
        if not scores:
            continue
        result[scope] = {
            f"{float(threshold):g}": sum(1 for value in scores if value >= threshold)
            / len(scores)
            for threshold in thresholds
        }
    return result


def sorting_power(score_evaluation: Any) -> tuple[dict[str, Any], ...]:
    """Forward-return association rows, with their independent-block count."""
    rows: list[dict[str, Any]] = []
    scopes = (score_evaluation or {}).get("scopes") or {}
    for scope, payload in scopes.items():
        for horizon, stats in (payload.get("horizons") or {}).items():
            rows.append({
                "scope": scope,
                "horizon_days": int(horizon),
                "available_samples": stats.get("available_samples"),
                "non_overlapping_samples": stats.get("non_overlapping_samples"),
                "pending_samples": stats.get("pending_samples"),
                "score_forward_spearman": stats.get("score_forward_spearman"),
                "score_relative_spearman": stats.get("score_relative_spearman"),
            })
    return tuple(sorted(rows, key=lambda row: (row["scope"], row["horizon_days"])))


def trade_direction_summary(trades_csv: Path) -> dict[str, Any]:
    """Direction split and activity span of one experiment's trade log."""
    dates: list[str] = []
    sides: dict[str, int] = {}
    reasons: dict[str, int] = {}
    if not trades_csv.exists():
        return {"status": "MISSING", "path": str(trades_csv)}
    with trades_csv.open("r", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            side = str(row.get("side", "")).strip().upper()
            sides[side] = sides.get(side, 0) + 1
            reason = str(row.get("reason", "")).strip().upper()
            reasons[reason] = reasons.get(reason, 0) + 1
            dates.append(str(row.get("timestamp", "")))
    dates.sort()
    return {
        "status": "AVAILABLE",
        "path": str(trades_csv),
        "trades": sum(sides.values()),
        "sides": sides,
        "reasons": reasons,
        "first_trade": _day(dates[0]) if dates else None,
        "last_trade": _day(dates[-1]) if dates else None,
    }


def observed_drawdowns(series_dir: Path, window: tuple[str, str] | None = None) -> dict[str, float]:
    """Peak-to-trough close drawdown per asset inside the run window."""
    result: dict[str, float] = {}
    if not series_dir.is_dir():
        return result
    for path in sorted(series_dir.glob("normalized-*-USD-1D.json")):
        payload = _load_json(path)
        candles = (payload or {}).get("candles") or []
        symbol = str((payload or {}).get("symbol") or "").strip().upper()
        if not symbol or not candles:
            continue
        peak = float("-inf")
        worst = 0.0
        for candle in candles:
            if not isinstance(candle.get("close"), (int, float)):
                continue
            day = _day(candle.get("timestamp"))
            if window and not (window[0] <= day <= window[1]):
                continue
            close = float(candle["close"])
            peak = max(peak, close)
            if peak > 0:
                worst = min(worst, close / peak - 1.0)
        if math.isfinite(peak) and peak > 0:
            result[symbol] = worst
    return result


@dataclass(frozen=True)
class RunValidityReport:
    run_id: str
    findings: tuple[Finding, ...]
    coverage: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    profile_coverage: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    factor_availability: Mapping[str, Mapping[str, Mapping[str, int]]] = field(
        default_factory=dict
    )
    sorting: tuple[Mapping[str, Any], ...] = ()
    experiments: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    observed_drawdowns: Mapping[str, float] = field(default_factory=dict)
    feasibility: Mapping[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.severity == SEVERITY_ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.severity == SEVERITY_WARNING)

    @property
    def verdict(self) -> str:
        codes = {item.code for item in self.errors}
        if codes & {COVERAGE_BELOW_INVESTABLE, ENTRY_LOCKED_BY_COVERAGE, ONE_WAY_RATCHER}:
            return VERDICT_NOT_A_TEST
        if codes & {SORTING_UNDERPOWERED}:
            return VERDICT_UNDERPOWERED
        return VERDICT_OK


def verdict(findings: Iterable[Finding]) -> str:
    """Standalone verdict for a bare finding sequence."""
    codes = {item.code for item in findings if item.severity == SEVERITY_ERROR}
    if codes & {COVERAGE_BELOW_INVESTABLE, ENTRY_LOCKED_BY_COVERAGE, ONE_WAY_RATCHER}:
        return VERDICT_NOT_A_TEST
    if codes & {SORTING_UNDERPOWERED}:
        return VERDICT_UNDERPOWERED
    return VERDICT_OK


def _manifest_findings(manifest: Any, report_dir: Path) -> tuple[Finding, ...]:
    if manifest is None:
        return (Finding(
            code=MANIFEST_MISSING, severity=SEVERITY_ERROR, subject="manifest.json",
            message="run has no manifest.json; the data audit cannot be reviewed",
            values={"path": str(report_dir.parent / "manifest.json")},
        ),)
    blockers = tuple(manifest.get("blockers") or ())
    series = tuple(manifest.get("series") or ())
    kinds = sorted({str(item.get("metric", "UNKNOWN")) for item in series})
    return (Finding(
        code=MANIFEST_BLOCKERS_IGNORE_SIGNAL_LAYER,
        severity=SEVERITY_INFO,
        subject="manifest.json",
        message=(
            "manifest blockers cover "
            f"{', '.join(kinds) or 'no series'} completeness only; factor "
            "availability and scoring coverage are not gated here, so an empty "
            "blocker list does not mean the signal layer is usable"
        ),
        values={"blockers": blockers, "strict_ready": manifest.get("strict_ready"),
                "series_count": len(series), "metrics": tuple(kinds)},
    ),)


def _signal_findings(
    coverage: Mapping[str, Mapping[str, Any]],
    sorting: tuple[Mapping[str, Any], ...],
    minimum_investable: float,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    if not coverage:
        findings.append(Finding(
            code=COVERAGE_BELOW_INVESTABLE, severity=SEVERITY_ERROR,
            subject="score-evaluation.json",
            message=(
                "no scoring readings were found, so neither coverage nor the "
                "reachable score band can be established"
            ),
            values={},
        ))
    for scope, stats in sorted(coverage.items()):
        median = stats.get("median")
        samples = stats.get("samples") or 0
        if not samples or not isinstance(median, (int, float)):
            findings.append(Finding(
                code=COVERAGE_BELOW_INVESTABLE, severity=SEVERITY_ERROR,
                subject=scope,
                message=f"scope {scope} has no score readings to inspect",
                values=dict(stats),
            ))
            continue
        if float(median) < minimum_investable:
            findings.append(Finding(
                code=COVERAGE_BELOW_INVESTABLE, severity=SEVERITY_ERROR,
                subject=scope,
                message=(
                    f"median coverage {float(median):.4f} never reaches "
                    f"minimum_investable_coverage {minimum_investable:.2f} "
                    f"(max {float(stats.get('max', 0.0)):.4f}); the score is a "
                    "reduced function of the available factors, not the full model"
                ),
                values=dict(stats),
            ))
    for row in sorting:
        blocks = row.get("non_overlapping_samples")
        if blocks is None:
            continue
        if int(blocks) < MIN_INDEPENDENT_BLOCKS:
            findings.append(Finding(
                code=SORTING_UNDERPOWERED, severity=SEVERITY_ERROR,
                subject=f"{row['scope']}/{row['horizon_days']}d",
                message=(
                    f"only {int(blocks)} independent blocks behind "
                    f"{row.get('available_samples')} overlapping samples; "
                    f"spearman {row.get('score_forward_spearman')} is not "
                    "distinguishable from noise at this power"
                ),
                values=dict(row),
            ))
    return tuple(findings)


def _trade_findings(
    experiment: str,
    summary: Mapping[str, Any],
    window_edge: str | None,
) -> tuple[Finding, ...]:
    if summary.get("status") != "AVAILABLE":
        return ()
    findings: list[Finding] = []
    trades = int(summary.get("trades") or 0)
    sides = {str(k).upper(): int(v) for k, v in (summary.get("sides") or {}).items()}
    buys = sides.get("BUY", 0)
    sells = sides.get("SELL", 0)
    last = summary.get("last_trade")
    if trades == 0:
        findings.append(Finding(
            code=NO_TRADES_IN_WINDOW, severity=SEVERITY_WARNING, subject=experiment,
            message="experiment never traded; it is a buy-and-hold start, not a strategy path",
            values=dict(summary),
        ))
        return tuple(findings)
    if buys == 0 or sells == 0:
        findings.append(Finding(
            code=ONE_WAY_RATCHER, severity=SEVERITY_ERROR, subject=experiment,
            message=(
                f"all {trades} trades are "
                f"{'SELL' if sells and not buys else 'BUY'} only; the direction "
                "gate is asymmetric, so the experiment measures a one-way "
                "exposure adjuster rather than a portfolio strategy"
            ),
            values={**summary, "blocked_direction": "BUY" if not buys else "SELL"},
        ))
    if window_edge and last and _gap_days(last, window_edge) > STALL_GAP_DAYS:
        findings.append(Finding(
            code=TRADING_STALLED, severity=SEVERITY_WARNING, subject=experiment,
            message=(
                f"last trade {last} is {_gap_days(last, window_edge)} days before "
                f"the window ends {window_edge}; the tail of the path is unmanaged"
            ),
            values={**summary, "window_end": window_edge,
                    "stall_days": _gap_days(last, window_edge)},
        ))
    return tuple(findings)


def _gap_days(start: str, end: str) -> int:
    from datetime import date

    try:
        left = date.fromisoformat(start)
        right = date.fromisoformat(end)
    except ValueError:
        return 0
    return (right - left).days


def _decision_findings(decision_evaluation: Any) -> tuple[Finding, ...]:
    if decision_evaluation is None:
        return (Finding(
            code=DECISION_SAMPLE_EMPTY, severity=SEVERITY_WARNING,
            subject="decision-evaluation.json",
            message="no decision mark-to-market artifact; decision quality is unmeasured",
            values={},
        ),)
    valid = decision_evaluation.get("valid_decisions")
    excluded = decision_evaluation.get("excluded_decisions")
    reasons: dict[str, int] = {}
    for item in decision_evaluation.get("exclusions") or ():
        label = str(item.get("reason", "UNKNOWN")).split(":")[0].strip()
        reasons[label] = reasons.get(label, 0) + 1
    if not valid:
        return (Finding(
            code=DECISION_SAMPLE_EMPTY, severity=SEVERITY_WARNING,
            subject="decision-evaluation.json",
            message=(
                f"0 usable decisions against {excluded} excluded; decision quality "
                "has no sample and must not be reported either way"
            ),
            values={"valid_decisions": valid, "excluded_decisions": excluded,
                    "exclusion_reasons": reasons},
        ),)
    return ()


def build_validity_report(
    directory: str | Path,
    *,
    policy: Any | None = None,
    minimum_investable: float | None = None,
) -> RunValidityReport:
    """Inspect one finished run directory and report what it can support."""
    root = Path(directory).expanduser()
    spec = _load_json(root / "spec.json") or {}
    manifest = _load_json(root / "manifest.json")
    score_evaluation = _load_json(root / "score-evaluation.json")
    decision_evaluation = _load_json(root / "decision-evaluation.json")

    from ..models.policy import resolve_policy

    resolved = policy or resolve_policy()
    floor = (
        float(minimum_investable)
        if minimum_investable is not None
        else float(resolved.scoring.get("minimum_investable_coverage", 0.0))
    )

    coverage = coverage_summary(score_evaluation)
    profile_cov = profile_coverage(score_evaluation, resolved)
    sorting = sorting_power(score_evaluation)
    window = (_day(spec.get("start_at")), _day(spec.get("end_at")))
    window = (window[0], window[1]) if all(window) else None
    drawdowns = observed_drawdowns(root / "series", window)

    report_dir = root / "report"
    experiments: dict[str, dict[str, Any]] = {}
    trade_findings: list[Finding] = []
    if report_dir.is_dir():
        for trades_csv in sorted(report_dir.glob("*.trades.csv")):
            experiment = trades_csv.name[: -len(".trades.csv")]
            summary = trade_direction_summary(trades_csv)
            experiments[experiment] = summary
            trade_findings.extend(
                _trade_findings(experiment, summary, window[1] if window else None)
            )

    per_profile: dict[str, float] = {}
    for key, stats in profile_cov.items():
        profile = key.split(":", 1)[1]
        value = float(stats.get("coverage") or 0.0)
        per_profile[profile] = max(per_profile.get(profile, 0.0), value)

    feasibility = check_policy_feasibility(
        resolved,
        coverage=per_profile or None,
        realized_drawdown_by_asset=drawdowns or None,
    )

    findings: list[Finding] = []
    findings.extend(_manifest_findings(manifest, report_dir))
    findings.extend(_signal_findings(coverage, sorting, floor))
    findings.extend(trade_findings)
    findings.extend(_decision_findings(decision_evaluation))
    findings.extend(feasibility.findings)

    return RunValidityReport(
        run_id=str(spec.get("run_id") or root.name),
        findings=tuple(findings),
        coverage=coverage,
        profile_coverage=profile_cov,
        factor_availability=factor_availability(score_evaluation),
        sorting=sorting,
        experiments=experiments,
        observed_drawdowns=drawdowns,
        feasibility={
            "ok": feasibility.ok,
            "profile_coverage_used": per_profile,
            "score_bands": {
                name: {"coverage": band.coverage, "low": band.low, "high": band.high}
                for name, band in feasibility.score_bands.items()
            },
            "drawdown_rows": [dict(row) for row in feasibility.drawdown_rows],
            "errors": len(feasibility.errors),
            "warnings": len(feasibility.warnings),
        },
    )
