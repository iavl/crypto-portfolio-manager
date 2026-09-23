"""Deterministic policy feasibility checks: score reachability and drawdown budget.

Read-only diagnostics. They never mutate the policy, never change a threshold,
and never override an engine output. They exist because two contradictions in
the canonical policy are invisible to every other check in the repository.

Score reachability
==================
``engine.scoring._score_factors`` composes the base score as::

    coverage    = sum_i(weight_i * reliability_i)
    effective_i = 50 + reliability_i * (raw_i - 50)   # AVAILABLE
    effective_i = 50                                  # MISSING with weight > 0
    score       = sum_i(weight_i * effective_i)

``raw_i`` is bounded to [0, 100], so an AVAILABLE factor contributes within
``50 +- 50 * reliability_i`` and a MISSING factor contributes exactly 50. The
score an asset can possibly attain is therefore::

    score in [50 - 50 * coverage, 50 + 50 * coverage]

The half-width is ``50 * coverage``. With 55% of the profile weight missing the
reachable band collapses to [27.5, 72.5], and any threshold above the band's top
cannot fire however strong the surviving evidence is. Equivalently a threshold
``t`` needs ``coverage >= (t - 50) / 50`` before it is reachable at all, and that
requirement is a property of the data set, not of the market.

A sharper companion failure: ``engine.scoring._coverage_gate_band`` forces the
LOW band whenever coverage is below ``scoring.minimum_investable_coverage``, and
``execution.confidence_deployment_factor.LOW`` is a hard zero in the canonical
policy. When both hold, every score-driven increase is blocked regardless of
score, so the strategy can only ever reduce exposure. That is what a
"strict, no semantic evidence" backtest measures.

Drawdown-budget feasibility
===========================
``risk.max_portfolio_drawdown`` is compared against a realized drawdown, but
nothing asks whether the configured regimes can satisfy it in the first place.
The most defensive portfolio a regime permits holds ``stablecoin_target`` in
stables, so the implied drawdown under a scenario is
``(1 - stablecoin_target) * stressed_risky_return``. When that already exceeds
the budget the regime is infeasible by construction, and no execution-speed
change repairs it: only the target, the floors, or the stress assumption can.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.policy import Policy, resolve_policy
from .risk import stress_diagnostic

__all__ = [
    "DRAWDOWN_BUDGET_INFEASIBLE",
    "ENTRY_LOCKED_BY_COVERAGE",
    "FeasibilityReport",
    "Finding",
    "REGIME_BELOW_REQUIRED_STABLE_TARGET",
    "SCENARIO_CORE_ANCHOR",
    "SCENARIO_WORST_ASSET",
    "SCORE_BAND_COLLAPSED",
    "SCORE_THRESHOLD_UNREACHABLE",
    "STRESS_UNDERSTATES_REALIZED",
    "ScoreBand",
    "ScoreThreshold",
    "check_policy_feasibility",
    "coverage_required_for",
    "drawdown_budget_feasibility",
    "profile_score_bands",
    "score_band",
    "score_thresholds",
]

SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"

SCORE_BAND_COLLAPSED = "SCORE_BAND_COLLAPSED"
SCORE_THRESHOLD_UNREACHABLE = "SCORE_THRESHOLD_UNREACHABLE"
ENTRY_LOCKED_BY_COVERAGE = "ENTRY_LOCKED_BY_COVERAGE"
DRAWDOWN_BUDGET_INFEASIBLE = "DRAWDOWN_BUDGET_INFEASIBLE"
REGIME_BELOW_REQUIRED_STABLE_TARGET = "REGIME_BELOW_REQUIRED_STABLE_TARGET"
STRESS_UNDERSTATES_REALIZED = "STRESS_UNDERSTATES_REALIZED"

SCENARIO_CORE_ANCHOR = "CORE_ANCHOR"
SCENARIO_WORST_ASSET = "WORST_CONFIGURED_ASSET"

# A threshold is only "structurally" out of reach once the surviving evidence
# cannot lift the band to it. Below this coverage the band is narrow enough that
# every entry-style threshold in the canonical policy is unreachable.
_ENTRY_STYLE_KEYS = frozenset(
    {"satellite_entry_score", "satellite_full_score", "increase_min_score",
     "relative_increase_min_score"}
)


@dataclass(frozen=True)
class ScoreBand:
    """Closed interval of base-score values reachable at a given coverage."""

    coverage: float
    low: float
    high: float

    def contains(self, threshold: float) -> bool:
        return self.low <= threshold <= self.high


@dataclass(frozen=True)
class ScoreThreshold:
    """One configured score threshold and the profiles it governs."""

    name: str
    value: float
    profiles: tuple[str, ...]
    entry_style: bool


@dataclass(frozen=True)
class Finding:
    """One deterministic feasibility finding."""

    code: str
    severity: str
    subject: str
    message: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class FeasibilityReport:
    findings: tuple[Finding, ...]
    score_bands: Mapping[str, ScoreBand]
    drawdown_rows: tuple[Mapping[str, Any], ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.severity == SEVERITY_ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.severity == SEVERITY_WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors


def _fraction(value: Any, name: str, *, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or (result == 0.0 and not allow_zero):
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def score_band(coverage: float) -> ScoreBand:
    """Reachable base-score interval implied by ``coverage``.

    ``coverage`` equals ``sum(weight_i * reliability_i)``, i.e. the share of
    profile weight backed by usable evidence.
    """
    value = _fraction(coverage, "coverage")
    if value > 1.0:
        raise ValueError("coverage must be at most 1")
    half_width = 50.0 * value
    return ScoreBand(coverage=value, low=50.0 - half_width, high=50.0 + half_width)


def coverage_required_for(threshold: float) -> float:
    """Smallest coverage at which ``threshold`` becomes reachable at all."""
    value = _fraction(threshold, "threshold")
    if value > 100.0:
        raise ValueError("threshold must be at most 100")
    return max(0.0, (value - 50.0) / 50.0)


def _profiles_of(policy: Policy, symbols: Any) -> tuple[str, ...]:
    found = {policy.scoring_profile_name(symbol) for symbol in symbols}
    return tuple(sorted(found))


def _profiles_with_positive_weight(policy: Policy, factor: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name, weights in policy.scoring_profiles.items()
            if float(weights.get(factor, 0.0)) > 0.0
        )
    )


def _allocation_section(policy: Policy, key: str) -> Mapping[str, Any]:
    section = policy.allocation.get(key)
    return section if isinstance(section, Mapping) else {}


def score_thresholds(policy: Policy) -> tuple[ScoreThreshold, ...]:
    """Every configured score threshold, labelled with the profiles it governs."""
    satellite_profiles = _profiles_of(policy, policy.satellite_symbols)
    core_profiles = _profiles_of(policy, policy.core_symbols)
    relative_profiles = _profiles_with_positive_weight(policy, "relative_strength_btc")
    rows: list[ScoreThreshold] = []

    for key in ("satellite_entry_score", "satellite_exit_score",
                "satellite_soft_exit_score", "satellite_full_score"):
        if key in policy.allocation:
            rows.append(ScoreThreshold(
                name=key,
                value=_fraction(policy.allocation[key], f"allocation.{key}"),
                profiles=satellite_profiles,
                entry_style=key in _ENTRY_STYLE_KEYS,
            ))

    relative = _allocation_section(policy, "relative_strength")
    for key in ("increase_min_score", "hard_block_below_score"):
        if key in relative:
            rows.append(ScoreThreshold(
                name=f"relative_strength.{key}",
                value=_fraction(relative[key], f"allocation.relative_strength.{key}"),
                profiles=relative_profiles,
                entry_style=key in _ENTRY_STYLE_KEYS,
            ))

    eth = policy.core_allocation.get("eth")
    if isinstance(eth, Mapping):
        eth_symbol = next(
            (item for item in policy.core_symbols if item.upper() == "ETH"), None
        )
        eth_profiles = _profiles_of(policy, [eth_symbol]) if eth_symbol else core_profiles
        for key in ("increase_min_score", "hold_min_score",
                    "relative_increase_min_score", "relative_reduce_below_score"):
            if key in eth:
                rows.append(ScoreThreshold(
                    name=f"core_allocation.eth.{key}",
                    value=_fraction(eth[key], f"core_allocation.eth.{key}"),
                    profiles=eth_profiles,
                    entry_style=key in _ENTRY_STYLE_KEYS,
                ))
    return tuple(rows)


def profile_score_bands(
    policy: Policy, coverage: float | Mapping[str, float]
) -> dict[str, ScoreBand]:
    """Reachable band per scoring profile at the supplied coverage."""
    if isinstance(coverage, Mapping):
        return {
            name: score_band(float(coverage.get(name, 0.0)))
            for name in sorted(policy.scoring_profiles)
        }
    return {name: score_band(coverage) for name in sorted(policy.scoring_profiles)}


def _coverage_for(coverage: float | Mapping[str, float], profile: str) -> float:
    if isinstance(coverage, Mapping):
        return float(coverage.get(profile, 0.0))
    return float(coverage)


def _score_reachability_findings(
    policy: Policy, coverage: float | Mapping[str, float]
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    minimum_investable = float(policy.scoring.get("minimum_investable_coverage", 0.0))
    low_deployment = policy.execution.get("confidence_deployment_factor", {})

    for threshold in score_thresholds(policy):
        required = coverage_required_for(threshold.value)
        blockers = []
        for profile in threshold.profiles:
            available = _coverage_for(coverage, profile)
            if required > available + 1e-12:
                blockers.append(
                    {"profile": profile, "coverage": available, "required": required}
                )
        if not blockers:
            continue
        findings.append(Finding(
            code=SCORE_THRESHOLD_UNREACHABLE,
            severity=SEVERITY_ERROR if threshold.entry_style else SEVERITY_WARNING,
            subject=threshold.name,
            message=(
                f"threshold {threshold.value:.1f} needs coverage >= {required:.4f} "
                f"but observed coverage is lower for "
                f"{', '.join(item['profile'] for item in blockers)}; "
                f"the gate can never fire for those profiles"
            ),
            values={
                "threshold": threshold.value,
                "required_coverage": required,
                "profiles": tuple(item["profile"] for item in blockers),
                "observed_coverage": tuple(item["coverage"] for item in blockers),
            },
        ))

    for profile in sorted(policy.scoring_profiles):
        available = _coverage_for(coverage, profile)
        if available >= minimum_investable:
            continue
        band = score_band(available)
        capped = [
            name
            for name, value in sorted(low_deployment.items())
            if float(value) == 0.0
        ]
        if not capped:
            continue
        findings.append(Finding(
            code=ENTRY_LOCKED_BY_COVERAGE,
            severity=SEVERITY_ERROR,
            subject=profile,
            message=(
                f"coverage {available:.4f} is below minimum_investable_coverage "
                f"{minimum_investable:.2f}, so the coverage gate is LOW and "
                f"confidence_deployment_factor zeroes {', '.join(capped)}; every "
                f"score-driven increase is blocked regardless of score"
            ),
            values={
                "profile": profile,
                "coverage": available,
                "minimum_investable_coverage": minimum_investable,
                "band_low": band.low,
                "band_high": band.high,
                "zeroed_bands": tuple(capped),
            },
        ))
    return tuple(findings)


def _band_findings(
    policy: Policy, coverage: float | Mapping[str, float]
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for profile in sorted(policy.scoring_profiles):
        available = _coverage_for(coverage, profile)
        if available >= 0.999:
            continue
        band = score_band(available)
        findings.append(Finding(
            code=SCORE_BAND_COLLAPSED,
            severity=SEVERITY_WARNING,
            subject=profile,
            message=(
                f"profile {profile} can only produce scores in "
                f"[{band.low:.2f}, {band.high:.2f}] at coverage {available:.4f}"
            ),
            values={
                "profile": profile,
                "coverage": available,
                "band_low": band.low,
                "band_high": band.high,
                "compression": 1.0 - available,
            },
        ))
    return tuple(findings)


def _stable_symbol(policy: Policy) -> str:
    if not policy.stable_symbols:
        raise ValueError("policy declares no stable symbols")
    return policy.stable_symbols[0]


def _scenario_returns(policy: Policy, scenario: Mapping[str, float]) -> dict[str, float]:
    returns = {
        str(symbol).strip().upper(): float(value)
        for symbol, value in scenario.items()
    }
    for symbol in policy.stable_symbols:
        returns[symbol] = 0.0
    return returns


def _core_anchor_weights(
    policy: Policy, symbols: tuple[str, ...], risky: float
) -> Mapping[str, float] | None:
    anchor = policy.core_allocation.get("anchor")
    if not isinstance(anchor, Mapping):
        return None
    usable = {
        str(symbol).strip().upper(): float(weight)
        for symbol, weight in anchor.items()
        if str(symbol).strip().upper() in symbols
    }
    total = sum(usable.values())
    if total <= 0.0:
        return None
    return {symbol: risky * weight / total for symbol, weight in usable.items()}


def _worst_asset_weights(
    policy: Policy, scenario: Mapping[str, float], risky: float
) -> tuple[str, Mapping[str, float]] | None:
    candidates = [
        (str(symbol).strip().upper(), float(value))
        for symbol, value in scenario.items()
        if str(symbol).strip().upper() not in policy.stable_symbols
        and not policy.is_excluded(symbol)
    ]
    if not candidates:
        return None
    symbol, _ = min(candidates, key=lambda item: (item[1], item[0]))
    return symbol, {symbol: risky}


def _drawdown_rows_for(
    policy: Policy,
    *,
    regime: str,
    scenario: Mapping[str, float],
    scenario_name: str,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[Finding, ...]]:
    limits = policy.regimes[regime]
    stable_target = float(limits.stablecoin_target)
    stable_symbol = _stable_symbol(policy)
    risky = 1.0 - stable_target
    returns = _scenario_returns(policy, scenario)
    covered = sorted(
        symbol
        for symbol in returns
        if symbol not in policy.stable_symbols and not policy.is_excluded(symbol)
    )
    rows: list[dict[str, Any]] = []
    findings: list[Finding] = []
    scenarios: list[tuple[str, Mapping[str, float] | None]] = []
    scenarios.append((
        SCENARIO_CORE_ANCHOR,
        _core_anchor_weights(policy, tuple(covered), risky) if risky > 0 else {},
    ))
    worst = _worst_asset_weights(policy, {s: returns[s] for s in covered}, risky)
    scenarios.append((SCENARIO_WORST_ASSET, worst[1] if worst else None))

    for name, risky_weights in scenarios:
        if risky_weights is None:
            rows.append({
                "regime": regime, "scenario": name, "status": "UNAVAILABLE",
                "detail": "policy does not define a usable risky composition",
            })
            continue
        weights: dict[str, float] = {}
        if stable_target > 0.0:
            weights[stable_symbol] = stable_target
        for symbol, weight in risky_weights.items():
            weights[symbol] = weights.get(symbol, 0.0) + weight
        diagnostic = stress_diagnostic(
            weights, returns, current_drawdown=0.0,
            risk_budget=policy.max_portfolio_drawdown,
        )
        stressed_risky = diagnostic["scenario_return"]
        required_stable = None
        if risky > 0 and stressed_risky < 0:
            risky_only = diagnostic["scenario_return"] / risky
            if risky_only < 0:
                required_stable = max(
                    0.0,
                    1.0 - policy.max_portfolio_drawdown / abs(risky_only),
                )
        row = {
            "regime": regime,
            "scenario": name,
            "scenario_name": scenario_name,
            "status": "AVAILABLE",
            "stablecoin_target": stable_target,
            "risky_weight": risky,
            "scenario_return": stressed_risky,
            "projected_drawdown": diagnostic["projected_drawdown"],
            "budget": policy.max_portfolio_drawdown,
            "budget_breach": diagnostic["budget_breach"],
            "required_stablecoin_target": required_stable,
            "weights": dict(sorted(weights.items())),
        }
        rows.append(row)
        if diagnostic["budget_breach"]:
            findings.append(Finding(
                code=DRAWDOWN_BUDGET_INFEASIBLE,
                severity=SEVERITY_ERROR,
                subject=f"{regime}/{name}",
                message=(
                    f"regime {regime} at its most defensive target still projects "
                    f"{diagnostic['projected_drawdown']:.4f} against a "
                    f"{policy.max_portfolio_drawdown:.4f} budget under {scenario_name}"
                ),
                values=row,
            ))
        if required_stable is not None and stable_target < required_stable - 1e-12:
            findings.append(Finding(
                code=REGIME_BELOW_REQUIRED_STABLE_TARGET,
                severity=SEVERITY_WARNING,
                subject=f"{regime}/{name}",
                message=(
                    f"regime {regime} targets {stable_target:.4f} in stables but "
                    f"needs {required_stable:.4f} to hold the budget under "
                    f"{scenario_name}"
                ),
                values={**row, "required_stablecoin_target": required_stable},
            ))
    return tuple(rows), tuple(findings)


def drawdown_budget_feasibility(
    policy: Policy | None = None,
    *,
    scenario: Mapping[str, float] | None = None,
    scenario_name: str = "POLICY_STRESS",
    realized_drawdown_by_asset: Mapping[str, float] | None = None,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[Finding, ...]]:
    """Per-regime implied drawdown vs the configured risk budget.

    The scenario defaults to ``policy.stress_scenario``. Every scenario assumes
    stables return zero, which is the existing diagnostic convention, not a
    claim that a peg is risk-free.
    """
    resolved = policy or resolve_policy()
    active = dict(resolved.stress_scenario if scenario is None else scenario)
    rows: list[dict[str, Any]] = []
    findings: list[Finding] = []
    for regime in sorted(resolved.regimes):
        regime_rows, regime_findings = _drawdown_rows_for(
            resolved, regime=regime, scenario=active, scenario_name=scenario_name
        )
        rows.extend(regime_rows)
        findings.extend(regime_findings)

    if realized_drawdown_by_asset:
        realized = {
            str(symbol).strip().upper(): float(value)
            for symbol, value in realized_drawdown_by_asset.items()
        }
        per_asset = {
            symbol: {
                "realized": realized[symbol],
                "configured": float(active[symbol]),
                "ratio": (
                    realized[symbol] / float(active[symbol])
                    if float(active[symbol]) < 0 else None
                ),
            }
            for symbol in sorted(realized)
            if symbol in active and float(active[symbol]) < 0
        }
        if per_asset:
            worst_symbol, worst = max(
                per_asset.items(), key=lambda item: item[1]["ratio"] or 0.0
            )
            findings.append(Finding(
                code=STRESS_UNDERSTATES_REALIZED,
                severity=(
                    SEVERITY_WARNING
                    if (worst["ratio"] or 1.0) > 1.0
                    else "INFO"
                ),
                subject=worst_symbol,
                message=(
                    f"{worst_symbol} realized {worst['realized']:.4f} against a "
                    f"configured stress of {worst['configured']:.4f}, a factor of "
                    f"{worst['ratio']:.2f}; the budget was calibrated to the "
                    "configured value"
                ),
                values={"worst_symbol": worst_symbol, "per_asset": per_asset},
            ))
            realized_rows, realized_findings = drawdown_budget_feasibility(
                resolved,
                scenario=realized,
                scenario_name="REALIZED_HISTORY",
            )
            rows.extend(realized_rows)
            findings.extend(realized_findings)
    return tuple(rows), tuple(findings)


def check_policy_feasibility(
    policy: Policy | None = None,
    *,
    coverage: float | Mapping[str, float] | None = None,
    realized_drawdown_by_asset: Mapping[str, float] | None = None,
) -> FeasibilityReport:
    """Full deterministic feasibility report for one policy."""
    resolved = policy or resolve_policy()
    findings: list[Finding] = []
    bands: Mapping[str, ScoreBand] = {}
    if coverage is not None:
        bands = profile_score_bands(resolved, coverage)
        findings.extend(_band_findings(resolved, coverage))
        findings.extend(_score_reachability_findings(resolved, coverage))
    rows, drawdown_findings = drawdown_budget_feasibility(
        resolved, realized_drawdown_by_asset=realized_drawdown_by_asset
    )
    findings.extend(drawdown_findings)
    return FeasibilityReport(
        findings=tuple(findings), score_bands=bands, drawdown_rows=rows
    )
