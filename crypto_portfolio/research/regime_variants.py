"""Regime authority-ownership variants (Strategy V2.3 Phase 2).

Preregistered research variants that change WHO owns volatility risk, never
the thresholds or multipliers: the canonical regime (R0) lets the volatility
domain vote and weigh; R1 removes volatility authority (trend+flows+breadth);
R2 keeps only flows+breadth (systemic/breadth); R3 removes every ordinary
domain so only a severe systemic event can leave NORMAL. The comparison
metric — CAGR sacrificed per 1pp of MaxDD saved — prices each variant's
de-risking. Canonical policy stays R0 until the Phase 6 decision matrix and
the Phase 7 migration; nothing here re-tunes ``domain_weights``,
``normal_max``, ``defensive_max``, or the regime multipliers.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..models.policy import Policy

_REGIME_VARIANT_NAMES = ("R0", "R1", "R2", "R3")

# Preregistered variant table: excluded ordinary domains per variant.
REGIME_VARIANTS: Mapping[str, tuple[str, ...]] = {
    "R0": (),
    "R1": ("volatility",),
    "R2": ("trend", "volatility"),
    "R3": ("trend", "volatility", "flows", "breadth"),
}

_VARIANT_NOTES: Mapping[str, str] = {
    "R0": "current: trend + volatility + flows + breadth",
    "R1": "no volatility authority: trend + flows + breadth",
    "R2": "systemic/breadth: flows + breadth",
    "R3": "severe-event only: ordinary regimes never scale the target vol",
}

_ORDINARY_DOMAINS = ("trend", "volatility", "flows", "breadth")


def _copy(policy: Policy) -> dict[str, Any]:
    return json.loads(json.dumps(policy.as_dict()))


def regime_variant_policy(policy: Policy, variant: str) -> dict[str, Any]:
    """Canonical policy mapping with one variant's ownership declared."""
    name = str(variant).strip().upper()
    if name not in _REGIME_VARIANT_NAMES:
        raise ValueError(
            "variant must be one of " + ", ".join(_REGIME_VARIANT_NAMES)
        )
    data = _copy(policy)
    data["regime_model"]["excluded_domains"] = list(REGIME_VARIANTS[name])
    return data


def regime_variant_ownership(policy: Policy) -> dict[str, Any]:
    """Per-variant authority report (who owns which risk class)."""
    weights = dict(
        (policy.regime_model or {}).get("domain_weights", {})
    )
    rows: dict[str, Any] = {}
    for name in _REGIME_VARIANT_NAMES:
        excluded = REGIME_VARIANTS[name]
        active = [domain for domain in _ORDINARY_DOMAINS if domain not in excluded]
        rows[name] = {
            "note": _VARIANT_NOTES[name],
            "active_domains": active,
            "excluded_domains": list(excluded),
            "regime_volatility_authority": "volatility" in active,
            # The volatility-budget engine owns realized-vol/covariance risk
            # in every variant; the question is whether the regime ALSO owns
            # a volatility state vote (R0) or not (R1+).
            "volatility_owners": (
                ["volatility_budget", "regime"] if "volatility" in active
                else ["volatility_budget"]
            ),
            "severe_systemic_override_preserved": True,
        }
    return {
        "contract": "REGIME_OWNERSHIP_VARIANTS_V23",
        "variants": rows,
        "domain_weights_unchanged": weights,
        "note": (
            "ownership declarations only: domain weights, score thresholds, "
            "and regime multipliers are identical across variants"
        ),
    }


def cagr_sacrificed_per_maxdd_pp_saved(
    baseline: Mapping[str, Any],
    variant: Mapping[str, Any],
) -> float | None:
    """CAGR percentage points given up per 1pp of MaxDD magnitude avoided.

    Both metrics come from ``performance_metrics``-style tables: ``cagr`` as
    a decimal fraction and ``maximum_drawdown`` as a negative fraction.
    Positive means the variant bought drawdown reduction at that CAGR price;
    a variant that sacrificed CAGR AND deepened the drawdown has nothing to
    price and returns None.
    """
    def value(table: Mapping[str, Any], key: str) -> float | None:
        raw = table.get(key)
        if raw is None:
            return None
        number = float(raw)
        if not (number == number) or number in (float("inf"), float("-inf")):
            return None
        return number

    cagr_base = value(baseline, "cagr")
    cagr_variant = value(variant, "cagr")
    dd_base = value(baseline, "maximum_drawdown")
    dd_variant = value(variant, "maximum_drawdown")
    if None in (cagr_base, cagr_variant, dd_base, dd_variant):
        return None
    cagr_sacrificed_pp = (cagr_base - cagr_variant) * 100.0
    maxdd_saved_pp = (abs(dd_base) - abs(dd_variant)) * 100.0
    if maxdd_saved_pp <= 1e-12:
        return None
    return cagr_sacrificed_pp / maxdd_saved_pp


def run_regime_variant_comparison(
    reviews,
    *,
    policy: Policy,
    risk_inputs_by_review,
    fee_bps: float,
    slippage_bps: float,
    git_sha: str = "unset",
    backtest_runner: Any = None,
) -> dict[str, Any]:
    """Run every regime variant over identical frozen reviews.

    Each variant replays the same strategy with only the regime's domain
    ownership changed; the table reports the risk/return metrics plus the
    efficiency metric against R0. Research-only diagnostics.
    """
    from ..models.policy import policy_from_mapping
    from .orchestrator import run_historical_backtest as default_runner
    from .v21_validation import _metrics_table, validation_manifest

    if backtest_runner is None:
        backtest_runner = default_runner
    ownership = regime_variant_ownership(policy)
    baseline_table: Mapping[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    for name in _REGIME_VARIANT_NAMES:
        variant_policy = policy_from_mapping(regime_variant_policy(policy, name))
        result = backtest_runner(
            reviews, policy=variant_policy,
            fee_bps=fee_bps, slippage_bps=slippage_bps,
            risk_inputs_by_review=risk_inputs_by_review,
        )
        table = _metrics_table(result)
        table.update({
            "calmar": result["metrics"].get("calmar"),
            "cvar_95_period": result["metrics"].get("cvar_95_period"),
            "worst_30d_return": result["metrics"].get("worst_30d_return"),
            "worst_90d_return": result["metrics"].get("worst_90d_return"),
        })
        rows.append({
            "variant": name,
            "note": ownership["variants"][name]["note"],
            "manifest": validation_manifest(variant_policy, git_sha=git_sha),
            "metrics": table,
            "cagr_sacrificed_per_maxdd_pp_saved_vs_r0": (
                cagr_sacrificed_per_maxdd_pp_saved(baseline_table, table)
                if baseline_table is not None else None
            ),
            "regime_counts": result.get("regime_counts"),
        })
        if name == "R0":
            baseline_table = table
    return {
        "contract": "REGIME_AUTHORITY_ABLATION_V23",
        "ownership": ownership,
        "variants": rows,
        "note": "preregistered ownership variants; thresholds and multipliers frozen",
    }


__all__ = [
    "REGIME_VARIANTS",
    "cagr_sacrificed_per_maxdd_pp_saved",
    "regime_variant_ownership",
    "regime_variant_policy",
    "run_regime_variant_comparison",
]
