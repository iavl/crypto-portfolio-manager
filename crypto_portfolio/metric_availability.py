"""Canonical collection-availability policy for decision metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .metrics_registry import metric_definition


REQUIREMENTS = ("REQUIRED", "OPTIONAL", "PREMIUM_ONLY")
FALLBACK_MODES = ("STRUCTURED_ONLY", "WEB_ALLOWED")
REASON_CODES = (
    "OPTIONAL_PROVIDER_UNAVAILABLE",
    "PREMIUM_PROVIDER_NOT_CONFIGURED",
    "OPTIONAL_METRIC_DISABLED",
    "OPTIONAL_PROVIDER_UNSUPPORTED",
    "OPTIONAL_SOURCE_UNAVAILABLE",
    "METHODOLOGY_NOT_DEFINED",
    "DERIVED_INPUT_UNAVAILABLE",
)

_OPTIONAL_METRICS = {
    "btc_valuation.mvrv_zscore": "OPTIONAL_SOURCE_UNAVAILABLE",
    "eth.monetary.issuance_365d_eth": "OPTIONAL_PROVIDER_UNAVAILABLE",
    "eth.monetary.net_supply_growth_365d": "OPTIONAL_PROVIDER_UNAVAILABLE",
    "eth.staking.active_effective_stake_eth": "OPTIONAL_PROVIDER_UNAVAILABLE",
    "eth.staking.active_effective_stake_change_30d": "DERIVED_INPUT_UNAVAILABLE",
    "flows.eth_active_stake_change_to_supply_30d": "DERIVED_INPUT_UNAVAILABLE",
}


@dataclass(frozen=True)
class MetricAvailabilityPolicy:
    metric_key: str
    requirement: str = "REQUIRED"
    reason_code: str | None = None

    def __post_init__(self) -> None:
        key = metric_definition(self.metric_key).key
        requirement = str(self.requirement).strip().upper()
        if requirement not in REQUIREMENTS:
            raise ValueError(f"metric requirement must be one of {REQUIREMENTS}")
        if self.reason_code is not None:
            reason = str(self.reason_code).strip().upper()
            if reason not in REASON_CODES:
                raise ValueError(f"unknown metric availability reason: {reason}")
        else:
            reason = None
        object.__setattr__(self, "metric_key", key)
        object.__setattr__(self, "requirement", requirement)
        object.__setattr__(self, "reason_code", reason)

    @property
    def is_skippable(self) -> bool:
        return self.requirement != "REQUIRED"

    @property
    def fallback_mode(self) -> str:
        return metric_definition(self.metric_key).fallback_mode


def metric_availability(asset: str, metric_key: str) -> MetricAvailabilityPolicy:
    """Return the explicit availability rule for one asset/metric pair."""
    asset = str(asset).strip().upper()
    definition = metric_definition(metric_key)
    key = definition.key
    if key in _OPTIONAL_METRICS:
        return MetricAvailabilityPolicy(key, "OPTIONAL", _OPTIONAL_METRICS[key])
    if key == "fundamentals.developer_activity":
        return MetricAvailabilityPolicy(key, "OPTIONAL", "OPTIONAL_PROVIDER_UNAVAILABLE")
    if definition.is_event_risk:
        return MetricAvailabilityPolicy(key)
    if definition.decision_role != "SCORING_FACTOR":
        return MetricAvailabilityPolicy(key, "OPTIONAL", "OPTIONAL_PROVIDER_UNAVAILABLE")
    return MetricAvailabilityPolicy(key)


def metric_fallback_mode(metric_key: str) -> str:
    """Return the explicit fallback owner for one metric."""
    definition = metric_definition(metric_key)
    return definition.fallback_mode


def fallback_mode(metric_key: str) -> str:
    """Short alias for callers that only need fallback ownership."""
    return metric_fallback_mode(metric_key)


def skip_reason(policy: MetricAvailabilityPolicy, detail: Any | None = None) -> str:
    """Render a stable reason code with optional safe diagnostic detail."""
    code = policy.reason_code or (
        "PREMIUM_PROVIDER_NOT_CONFIGURED"
        if policy.requirement == "PREMIUM_ONLY"
        else "OPTIONAL_PROVIDER_UNAVAILABLE"
    )
    if detail is None or not str(detail).strip():
        return code
    return f"{code}: {str(detail).strip()}"


def is_skippable(metric_key: str, asset: str) -> bool:
    return metric_availability(asset, metric_key).is_skippable


def contributes_to_scoring_coverage(asset: str, metric_key: str) -> bool:
    """Return whether one asset/metric belongs in the scoring denominator."""
    definition = metric_definition(metric_key)
    return (
        definition.applies_to(asset)
        and definition.decision_role == "SCORING_FACTOR"
        and metric_availability(asset, metric_key).requirement == "REQUIRED"
    )


__all__ = [
    "MetricAvailabilityPolicy",
    "FALLBACK_MODES",
    "REASON_CODES",
    "REQUIREMENTS",
    "is_skippable",
    "fallback_mode",
    "contributes_to_scoring_coverage",
    "metric_fallback_mode",
    "metric_availability",
    "skip_reason",
]
