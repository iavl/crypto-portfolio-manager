"""Walk-forward validation tooling (Strategy V2 Phase 6).

Deterministic, standard-library-only research functions behind the
validation protocol in docs/STRATEGY_V2_VALIDATION_PROTOCOL.md. Everything
here is read-only analysis over frozen artifacts: no engine input is
mutated, no parameter is tuned, and every randomization is seeded so a run
can be reproduced exactly.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..engine.risk import remaining_drawdown_capacity
from ..models.policy import Policy, resolve_policy

# 6.6 - parameter classification. Structural parameters are design decisions
# that walk-forward validation may never search; calibrated parameters are
# the tunable surface; forbidden optimization is a process rule, not code.
PARAMETER_CLASSIFICATION: dict[str, dict[str, Any]] = {
    "structural": {
        "factor_ownership": "docs/SIGNAL_OWNERSHIP.md (one primary owner per signal family)",
        "hard_gates": "event/liveness/critical-data gates; severe-event override",
        "score_semantics": "three score spaces; normalized thresholds; low-evidence contract",
        "risk_engine_modes": "legacy_drawdown vs volatility_budget; min-composed caps",
        "emergency_floor_alignment": "caution/emergency fractions anchored to the mandatory -0.60D/-0.80D floors",
        "benchmarks": "risk-matched primary; BTC as opportunity-cost reference",
    },
    "calibrated": {
        "target_volatility": "risk_engine.portfolio_risk (placeholder 0.25)",
        "max_volatility": "risk_engine.portfolio_risk (placeholder 0.35)",
        "volatility_window_weights": "risk_engine.portfolio_risk (40/60 30D/90D)",
        "emergency_stage_risky_caps": "risk_engine.emergency_overlay (0.90/0.60/0.25)",
        "coverage_floor": "scoring.minimum_normalization_coverage (placeholder 0.6)",
        "satellite_thresholds": "allocation 57/62/67/85 compared in the normalized space",
        "risk_tier_thresholds": "risk_tier_estimation beta 1.5/1.3, relative vol 1.6/1.3",
        "wait_expiry_reviews": "execution_overlay.wait (placeholder 5)",
        "regime_domain_weights": "regime_model 0.15/0.30/0.30/0.25",
        "stress_scenario_values": "stress_scenarios (only moderate carries V1 calibration)",
    },
    "forbidden": {
        "rule": "no grid search over calibrated parameters maximizing historical CAGR or any single-window metric",
        "required_process": "rank monotonicity and regime robustness first; a parameter moves only with a documented reason that is not a backtest result",
    },
}


def walk_forward_windows(
    *,
    start_at: str,
    end_at: str,
    train_years: int = 2,
    validate_years: int = 1,
) -> list[dict[str, str]]:
    """Rolling train/validate boundary calendar (6.5).

    Calibration ("train") windows roll forward by one validate step; each
    validation window never overlaps its own training data. Dates are
    boundary strings; callers map them onto their own data availability.
    """
    from datetime import date, timedelta

    from ..models.time import parse_timestamp

    start = parse_timestamp(start_at).date()
    end = parse_timestamp(end_at).date()
    if isinstance(train_years, bool) or isinstance(validate_years, bool):
        raise ValueError("window lengths must be integers")
    if train_years < 1 or validate_years < 1:
        raise ValueError("train and validate windows must be at least one year")
    windows: list[dict[str, str]] = []
    train_start = start
    while True:
        train_end = date(
            train_start.year + train_years, train_start.month, train_start.day
        ) - timedelta(days=1)
        validate_start = train_end + timedelta(days=1)
        validate_end = date(
            validate_start.year + validate_years, validate_start.month, validate_start.day
        ) - timedelta(days=1)
        if validate_start > end:
            break
        windows.append({
            "train_start": train_start.isoformat(),
            "train_end": train_end.isoformat(),
            "validate_start": validate_start.isoformat(),
            "validate_end": min(validate_end, end).isoformat(),
        })
        if validate_end >= end:
            break
        train_start = validate_start
    if not windows:
        raise ValueError("the requested window is shorter than one validate step")
    return windows


def dynamic_universe_eligibility(
    *,
    closes_by_symbol: Mapping[str, Sequence[float]],
    volumes_by_symbol: Mapping[str, Sequence[float]] | None = None,
    minimum_history_days: int = 365,
    minimum_median_volume_usd: float = 1_000_000.0,
) -> dict[str, Any]:
    """Point-in-time eligible universe from trailing data only (6.3).

    An asset is eligible when it has at least ``minimum_history_days``
    trailing daily observations and, when volumes are supplied, a median
    dollar volume at or above the floor. Ineligible assets drop out of the
    universe snapshot for that boundary — the anti-survivorship rule: no
    asset is present before its own history qualifies it.
    """
    if isinstance(minimum_history_days, bool) or not isinstance(minimum_history_days, int):
        raise ValueError("minimum_history_days must be an integer")
    if minimum_history_days < 30:
        raise ValueError("minimum_history_days must be at least 30")
    if not math.isfinite(minimum_median_volume_usd) or minimum_median_volume_usd < 0:
        raise ValueError("minimum_median_volume_usd must be finite and >= 0")
    eligible: list[str] = []
    reasons: dict[str, str] = {}
    for raw_symbol, closes in closes_by_symbol.items():
        symbol = str(raw_symbol).strip().upper()
        if not isinstance(closes, Sequence) or isinstance(closes, (str, bytes)):
            raise ValueError(f"closes for {symbol} must be a sequence")
        if len(closes) < minimum_history_days:
            reasons[symbol] = f"INSUFFICIENT_HISTORY ({len(closes)} < {minimum_history_days} days)"
            continue
        volumes = (volumes_by_symbol or {}).get(symbol)
        if volumes is not None:
            if len(volumes) != len(closes):
                raise ValueError(f"volume/close length mismatch for {symbol}")
            window = [float(v) * float(c) for v, c in zip(volumes[-minimum_history_days:], closes[-minimum_history_days:])]
            ordered = sorted(window)
            median = ordered[len(ordered) // 2]
            if median < minimum_median_volume_usd:
                reasons[symbol] = f"INSUFFICIENT_LIQUIDITY (median ${median:,.0f}/day < ${minimum_median_volume_usd:,.0f})"
                continue
        eligible.append(symbol)
    return {
        "eligible": sorted(eligible),
        "ineligible": dict(sorted(reasons.items())),
        "rules": {
            "minimum_history_days": minimum_history_days,
            "minimum_median_volume_usd": minimum_median_volume_usd,
        },
    }


def threshold_rank_monotonicity(
    rows: Sequence[Mapping[str, Any]],
    *,
    score_field: str = "normalized_score",
    forward_field: str = "forward_return",
    buckets: int = 5,
) -> dict[str, Any]:
    """Does a higher score bucket actually predict better outcomes? (6.7)

    Rows carry a score and a realized forward outcome. Buckets are equal
   -width score ranges; the statistic is the fraction of adjacent bucket
    pairs whose mean outcome is non-decreasing, plus each bucket's sample
    count. A score without monotone buckets has no ranking power at this
    horizon, whatever its level.
    """
    if isinstance(buckets, bool) or not isinstance(buckets, int) or buckets < 2:
        raise ValueError("buckets must be an integer >= 2")
    pairs: list[tuple[float, float]] = []
    for row in rows:
        score = row.get(score_field)
        outcome = row.get(forward_field)
        if score is None or outcome is None:
            continue
        score_value = float(score)
        outcome_value = float(outcome)
        if not (math.isfinite(score_value) and math.isfinite(outcome_value)):
            continue
        if not 0 <= score_value <= 100:
            raise ValueError("scores must be in [0, 100]")
        pairs.append((score_value, outcome_value))
    if len(pairs) < buckets:
        return {"status": "INSUFFICIENT_SAMPLES", "samples": len(pairs)}
    bucket_rows: list[dict[str, Any]] = []
    width = 100.0 / buckets
    for index in range(buckets):
        low = index * width
        high = low + width
        selected = [outcome for score, outcome in pairs if (low <= score < high or (index == buckets - 1 and score == 100.0))]
        bucket_rows.append({
            "bucket": f"[{low:.0f},{high:.0f})" if index < buckets - 1 else f"[{low:.0f},100]",
            "samples": len(selected),
            "mean_outcome": (sum(selected) / len(selected)) if selected else None,
        })
    means = [row["mean_outcome"] for row in bucket_rows if row["mean_outcome"] is not None]
    comparisons: list[bool] = []
    for left_row, right_row in zip(bucket_rows, bucket_rows[1:]):
        left, right = left_row["mean_outcome"], right_row["mean_outcome"]
        # Adjacent pairs with an empty bucket carry no ordering information
        # and must not count as violations.
        if left is None or right is None:
            continue
        comparisons.append(right >= left - 1e-12)
    return {
        "status": "AVAILABLE",
        "samples": len(pairs),
        "score_field": score_field,
        "buckets": bucket_rows,
        "monotone_adjacent_share": (sum(1 for item in comparisons if item) / len(comparisons)) if comparisons else None,
        "empty_bucket_means": sum(1 for row in bucket_rows if row["mean_outcome"] is None),
        "note": "adjacent-share is necessary, not sufficient: also inspect the bucket means",
        "comparable_bucket_means": len(means),
    }


def block_bootstrap(
    returns: Sequence[float],
    *,
    block_days: int = 21,
    draws: int = 200,
    seed: int = 20260925,
    periods_per_year: float = 365.0,
    risk_budget: float | None = None,
) -> dict[str, Any]:
    """Seeded block bootstrap over realized strategy returns (6.10).

    Circular block bootstrap with a fixed seed: draw ``len(returns) //
    block_days`` blocks per path. Reports the MaxDD / CAGR / Sharpe
    distributions and, with a risk budget, how often a resampled path
    breaches it — the honest answer to whether holding the budget was luck.
    """
    if isinstance(block_days, bool) or not isinstance(block_days, int) or block_days < 2:
        raise ValueError("block_days must be an integer >= 2")
    if isinstance(draws, bool) or not isinstance(draws, int) or draws < 10:
        raise ValueError("draws must be an integer >= 10")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    values = [float(item) for item in returns]
    if len(values) < block_days * 4:
        raise ValueError("bootstrap needs at least four blocks of history")
    if any(not math.isfinite(item) or item <= -1.0 for item in values):
        raise ValueError("returns must be finite and > -100%")
    blocks = [values[index:index + block_days] for index in range(0, len(values) - block_days + 1, block_days)]
    rng = _SeededRandom(seed)
    block_count = math.ceil(len(values) / block_days)
    max_drawdowns: list[float] = []
    cagrs: list[float] = []
    sharpes: list[float] = []
    breaches = 0
    for _ in range(draws):
        path_returns: list[float] = []
        for _ in range(block_count):
            path_returns.extend(blocks[rng.below(len(blocks))])
        nav = 1.0
        peak = 1.0
        worst = 0.0
        for item in path_returns:
            nav *= 1.0 + item
            peak = max(peak, nav)
            worst = min(worst, nav / peak - 1.0)
        years = len(path_returns) / periods_per_year
        cagr = (nav ** (1.0 / years) - 1.0) if nav > 0 and years > 0 else -1.0
        mean = sum(path_returns) / len(path_returns)
        variance = sum((item - mean) ** 2 for item in path_returns) / (len(path_returns) - 1)
        volatility = math.sqrt(variance) * math.sqrt(periods_per_year) if variance > 0 else 0.0
        sharpe = (cagr / volatility) if volatility > 0 else None
        max_drawdowns.append(worst)
        cagrs.append(cagr)
        if sharpe is not None:
            sharpes.append(sharpe)
        if risk_budget is not None and worst < -float(risk_budget):
            breaches += 1
    return {
        "status": "AVAILABLE",
        "draws": draws,
        "block_days": block_days,
        "seed": seed,
        # Stable per-seed fingerprint so reproducibility is testable without
        # exposing (and inviting comparison of) raw draw lists.
        "fingerprint": round(sum(round(value, 12) for value in max_drawdowns) , 6),
        "samples": len(values),
        "max_drawdown": _distribution(max_drawdowns),
        "cagr": _distribution(cagrs),
        "sharpe_rf_zero": _distribution(sharpes) if sharpes else None,
        "risk_budget": risk_budget,
        "breach_frequency": (breaches / draws) if risk_budget is not None else None,
        "methodology": "circular block bootstrap over the realized daily return path; independence across blocks is an assumption, not a finding",
    }


class _SeededRandom:
    """Deterministic LCG so bootstrap draws reproduce exactly."""

    def __init__(self, seed: int) -> None:
        self._state = (seed * 6364136223846793005 + 1442695040888963407) % (1 << 64)

    def below(self, bound: int) -> int:
        if bound <= 0:
            raise ValueError("bound must be positive")
        self._state = (self._state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (self._state >> 33) % bound


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(values)
    count = len(ordered)

    def percentile(fraction: float) -> float:
        index = min(count - 1, max(0, int(round(fraction * (count - 1)))))
        return ordered[index]

    return {
        "mean": sum(ordered) / count,
        "p05": percentile(0.05),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "min": ordered[0],
        "max": ordered[-1],
    }


def gap_risk_stress(
    *,
    valuations: Sequence[Mapping[str, Any]],
    risk_budget: float,
    gaps: Sequence[float] = (-0.10, -0.20, -0.30),
) -> dict[str, Any]:
    """One-day gap shocks applied at the strategy's worst exposure (6.11).

    A reactive overlay cannot pre-cut a gap: the diagnostic reports the
    pre-shock risky exposure at the maximum-exposure valuation, the shock
    loss from that point, and the drawdown the book would carry into the
    next review (before any overlay response).
    """
    parsed = [(float(item["nav"]), float(item.get("risky_weight", 0.0))) for item in valuations]
    if not parsed:
        raise ValueError("valuations are required")
    # The shock lands at the maximum-exposure valuation; the pre-shock
    # drawdown is that point's own peak-relative drawdown, not the path's
    # worst trough-to-peak distance.
    index = max(range(len(parsed)), key=lambda i: parsed[i][1])
    nav, exposure = parsed[index]
    peak_before = max(item[0] for item in parsed[: index + 1])
    drawdown_before = min(0.0, nav / peak_before - 1.0) if peak_before > 0 else 0.0
    rows = []
    for raw_gap in gaps:
        gap = float(raw_gap)
        if not math.isfinite(gap) or gap >= 0 or gap <= -1:
            raise ValueError("gaps must be negative fractions in (-1, 0)")
        portfolio_return = exposure * gap
        projected = (1.0 + drawdown_before) * (1.0 + portfolio_return) - 1.0
        rows.append({
            "gap": gap,
            "pre_shock_risky_exposure": exposure,
            "pre_shock_nav_drawdown": drawdown_before,
            "portfolio_shock_return": portfolio_return,
            "projected_drawdown": min(0.0, projected),
            "budget_breach": projected < -float(risk_budget),
            "remaining_capacity": remaining_drawdown_capacity(min(0.0, projected), risk_budget),
        })
    return {
        "status": "DIAGNOSTIC_ONLY",
        "risk_budget": float(risk_budget),
        "rows": rows,
        "note": "a reactive overlay absorbs the first gap at pre-shock exposure by construction; this quantifies it",
    }


def stablecoin_stress(
    weights: Mapping[str, float],
    *,
    policy: Policy | None = None,
) -> dict[str, Any]:
    """Stable-sleeve stress math and issuer concentration (6.12).

    Runs the configured ``stablecoin_depeg`` scenario over the sleeve and
    reports single-issuer concentration so the pending cap decision has
    numbers in front of it. The configured scenario prices a uniform 5%
    depeg for major issuers (framework placeholder); the single-issuer
    severe case applies −20% to the largest holding alone.
    """
    resolved = policy or resolve_policy()
    scenario = resolved.stress_scenarios["stablecoin_depeg"]
    parsed = {str(s).strip().upper(): float(w) for s, w in weights.items()}
    total = sum(parsed.values())
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        raise ValueError("weights must sum to 1")
    stable_symbols = [s for s in parsed if s in resolved.stable_symbols]
    sleeve = {s: parsed[s] for s in stable_symbols}
    sleeve_total = sum(sleeve.values())
    depeg = {
        symbol: sleeve[symbol] * float(scenario.get(symbol, 0.0) or 0.0)
        for symbol in sleeve
        if sleeve[symbol] > 0
    }
    single = max(sleeve, key=lambda s: sleeve[s]) if sleeve else None
    single_stress = {
        symbol: (sleeve[symbol] * (-0.20 if symbol == single else 0.0))
        for symbol in sleeve
        if sleeve[symbol] > 0
    }
    custody = {symbol: -sleeve[symbol] for symbol in sleeve if sleeve[symbol] > 0}
    return {
        "status": "DIAGNOSTIC_ONLY",
        "sleeve_weight": sleeve_total,
        "issuer_concentration": {
            symbol: sleeve[symbol] / sleeve_total for symbol in sorted(sleeve)
        } if sleeve_total > 0 else {},
        "largest_issuer": {"symbol": single, "share_of_sleeve": (sleeve[single] / sleeve_total) if single and sleeve_total > 0 else None},
        "scenarios": {
            "configured_depeg": {
                "sleeve_return": sum(depeg.values()),
                "portfolio_return": sum(depeg.values()),
                "asset_contributions": depeg,
            },
            "single_issuer_minus_20pct": {
                "issuer": single,
                "sleeve_return": sum(single_stress.values()),
                "portfolio_return": sum(single_stress.values()),
                "asset_contributions": single_stress,
            },
            "custody_loss_on_sleeve": {
                "sleeve_return": sum(custody.values()),
                "portfolio_return": sum(custody.values()),
                "asset_contributions": custody,
            },
        },
        "pending_policy_decision": "single-issuer / venue caps were deliberately not introduced in Phase 6; numbers here feed that decision",
    }


def ablation_policy(
    policy: Policy,
    *,
    disable_factors: Sequence[str] = (),
    disable_satellites: bool = False,
    disable_emergency_overlay: bool = False,
    disable_execution_overlay: bool = False,
) -> dict[str, Any]:
    """Research-only policy variant for one ablation (6.9).

    Returns ``(policy_mapping, ablation_manifest)`` for
    ``policy_from_mapping``: factor weights are
    zeroed and renormalized across the remaining factors (a zero weight is
    the contract's NOT_APPLICABLE), satellites can be dropped, and the
    emergency/execution overlays can be disabled. Variants are stamped with
    their ablation manifest for reproducibility.
    """
    import json

    data = json.loads(json.dumps(policy.as_dict()))
    disabled = {str(factor).strip().lower() for factor in disable_factors}
    for profile_name, weights in data["scoring_profiles"].items():
        remaining = sum(
            float(weight) for factor, weight in weights.items()
            if factor not in disabled
        )
        if remaining <= 0:
            raise ValueError(f"ablation would empty profile {profile_name}")
        # Zero weight is the contract's NOT_APPLICABLE: the factor key stays,
        # its weight moves to zero, and the surviving weights renormalize so
        # the profile still sums to exactly 1.
        data["scoring_profiles"][profile_name] = {
            factor: (
                0.0 if factor in disabled
                else float(weight) / remaining
            )
            for factor, weight in weights.items()
        }
    if disable_satellites:
        data["universe"]["satellites"] = []
    if disable_emergency_overlay:
        data["risk"]["drawdown_budget_overlay"]["enabled"] = False
    if disable_execution_overlay:
        data["execution_overlay"]["wait"]["enabled"] = False
        data["positioning"]["enabled"] = False
        data["btc_cycle"]["enabled"] = False
    manifest = {
        "disable_factors": sorted(disabled),
        "disable_satellites": disable_satellites,
        "disable_emergency_overlay": disable_emergency_overlay,
        "disable_execution_overlay": disable_execution_overlay,
    }
    return data, manifest


__all__ = [
    "PARAMETER_CLASSIFICATION",
    "ablation_policy",
    "block_bootstrap",
    "dynamic_universe_eligibility",
    "gap_risk_stress",
    "stablecoin_stress",
    "threshold_rank_monotonicity",
    "walk_forward_windows",
]
