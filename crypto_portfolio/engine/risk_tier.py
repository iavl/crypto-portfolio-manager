"""Deterministic risk-tier estimation (Strategy V2 Phase 4).

Tiers keep their historical names (``normal``, ``high_beta``, ``high``) but
long-held assets get them from measured 90-day realized volatility and
90-day beta to BTC instead of manual labels, with entry/exit hysteresis so
a threshold-crossing asset cannot flip daily. The tier is provenance-carrying
(``DETERMINISTIC_ESTIMATE``) and, under the volatility-budget risk engine,
a secondary constraint rather than the primary sizing lever.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from ..models.policy import Policy, resolve_policy

_TIERS = {"normal", "high_beta", "high"}


def _finite(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return result


def deterministic_risk_tier(
    *,
    asset_volatility: float,
    btc_volatility: float,
    beta_to_btc: float | None = None,
    previous_tier: str | None = None,
    policy: Policy | None = None,
) -> dict[str, Any]:
    """Derive one asset's risk tier from measured risk, with hysteresis.

    ``asset_volatility`` and ``btc_volatility`` are annualized realized
    volatilities over the configured window; ``beta_to_btc`` is the
    trailing beta (``None`` when undefined, e.g. a constant BTC series —
    then only the volatility ratio decides). An asset enters ``high_beta``
    when EITHER the beta or the volatility ratio crosses its enter
    threshold, and leaves only when BOTH fall below their exit thresholds
    (the hysteresis band), so ``beta 1.49 -> 1.51`` around an enter
    threshold of 1.5 cannot produce daily flips.
    """
    resolved = policy or resolve_policy()
    config = resolved.risk_tier_estimation
    vol = _finite(asset_volatility, "asset_volatility", minimum=0.0)
    btc = _finite(btc_volatility, "btc_volatility", minimum=0.0)
    if vol <= 0 or btc <= 0:
        # A zero measured volatility is a degenerate series, not a risk-free
        # asset: the tier stays unmeasured rather than defaulting to calm.
        raise ValueError("asset_volatility and btc_volatility must be positive")
    beta = None if beta_to_btc is None else _finite(beta_to_btc, "beta_to_btc")
    if previous_tier is not None:
        previous = str(previous_tier).strip().lower()
        if previous not in _TIERS:
            raise ValueError(f"previous_tier must be one of {sorted(_TIERS)}")
    else:
        previous = None
    ratio = vol / btc
    beta_enter = float(config["beta_enter"])
    beta_exit = float(config["beta_exit"])
    vol_enter = float(config["relative_vol_enter"])
    vol_exit = float(config["relative_vol_exit"])
    if not 0 < beta_exit < beta_enter or not 0 < vol_exit < vol_enter:
        raise ValueError("risk tier thresholds must satisfy exit < enter")
    currently_high = previous in {"high_beta", "high"}
    beta_high = (
        (beta >= beta_enter if not currently_high else beta > beta_exit)
        if beta is not None
        else currently_high  # an undefined beta never enters on its own
    )
    vol_high = ratio >= vol_enter if not currently_high else ratio > vol_exit
    if beta_high or vol_high:
        # ``high`` (not ``high_beta``) is reserved for explicitly supplied
        # severity: measurement alone distinguishes normal from high_beta.
        tier = "high_beta"
    else:
        tier = "normal"
    return {
        "tier": tier,
        "source": "DETERMINISTIC_ESTIMATE",
        "basis": {
            "asset_volatility": vol,
            "btc_volatility": btc,
            "volatility_ratio": ratio,
            "beta_to_btc": beta,
            "previous_tier": previous,
            "entered_from": (
                "beta" if beta is not None and beta_high and not vol_high
                else "volatility_ratio" if vol_high and not (beta is not None and beta_high)
                else "both" if beta_high and vol_high else "none"
            ),
        },
    }


def estimate_risk_tiers(
    *,
    annualized_volatility: Mapping[str, float],
    beta: Mapping[str, float | None] | None = None,
    btc_symbol: str = "BTC",
    previous_tiers: Mapping[str, str] | None = None,
    policy: Policy | None = None,
) -> dict[str, dict[str, Any]]:
    """Derive tiers for every measured asset against the BTC anchor.

    Assets without a volatility measurement are absent from the result:
    an unmeasured tier is missing data (fail-closed), never a default.
    """
    resolved = policy or resolve_policy()
    btc_symbol = str(btc_symbol).strip().upper()
    if btc_symbol not in annualized_volatility:
        raise ValueError(f"risk tier estimation requires volatility for {btc_symbol}")
    vols: dict[str, float] = {}
    for raw_symbol, value in annualized_volatility.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError("volatility symbols must be non-empty")
        if symbol in vols:
            raise ValueError(f"duplicate volatility for {symbol}")
        vols[symbol] = _finite(value, f"volatility[{symbol}]", minimum=0.0)
    betas = {str(s).strip().upper(): v for s, v in (beta or {}).items()}
    previous = {str(s).strip().lower(): t for s, t in (previous_tiers or {}).items()}
    result: dict[str, dict[str, Any]] = {}
    for symbol, vol in vols.items():
        if symbol == btc_symbol:
            continue
        result[symbol] = deterministic_risk_tier(
            asset_volatility=vol,
            btc_volatility=vols[btc_symbol],
            beta_to_btc=betas.get(symbol),
            previous_tier=previous.get(symbol),
            policy=resolved,
        )
    return result


def tier_strategic_fraction(
    tier: str,
    *,
    source: str,
    policy: Policy | None = None,
) -> float:
    """Strategic fraction of the satellite envelope for one tier.

    Under the volatility-budget risk engine a measured
    (``DETERMINISTIC_ESTIMATE``) tier is a secondary constraint: portfolio
    risk contributions own sizing, so every measured tier competes for the
    full envelope and the tier only bounds maximum exposure through the
    hard-cap machinery. Manual and policy-default tiers keep their
    configured fractions in both engines (the frozen V1 semantics kept for
    A/B replay in legacy mode and for human overrides anywhere).
    """
    resolved = policy or resolve_policy()
    normalized = str(tier).strip().lower()
    if normalized not in _TIERS:
        raise ValueError(f"risk tier must be one of {sorted(_TIERS)}")
    source_normalized = str(source).strip().upper()
    if source_normalized not in {"POLICY_DEFAULT", "MANUAL_ASSESSMENT", "DETERMINISTIC_ESTIMATE"}:
        raise ValueError("risk tier source is unsupported")
    mode = (resolved.risk_engine or {}).get("mode", "legacy_drawdown")
    caps = resolved.allocation["risk_tier_caps"]
    configured = (
        caps.get(normalized)
        or caps.get(normalized.replace("-", "_"))
        or caps["normal"]
    )
    if mode == "volatility_budget" and source_normalized == "DETERMINISTIC_ESTIMATE":
        return 1.0
    return float(configured["strategic_fraction_of_satellite_envelope"])


__all__ = [
    "deterministic_risk_tier",
    "estimate_risk_tiers",
    "tier_strategic_fraction",
]
