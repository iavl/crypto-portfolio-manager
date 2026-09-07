"""Canonical collection-availability policy for decision metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .metrics_registry import metric_definition


REQUIREMENTS = ("REQUIRED", "OPTIONAL", "PREMIUM_ONLY")
REASON_CODES = (
    "OPTIONAL_PROVIDER_UNAVAILABLE",
    "PREMIUM_PROVIDER_NOT_CONFIGURED",
    "OPTIONAL_METRIC_DISABLED",
    "OPTIONAL_PROVIDER_UNSUPPORTED",
    "METHODOLOGY_NOT_DEFINED",
    "DERIVED_INPUT_UNAVAILABLE",
)


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


def metric_availability(asset: str, metric_key: str) -> MetricAvailabilityPolicy:
    """Return the explicit availability rule for one asset/metric pair."""
    asset = str(asset).strip().upper()
    definition = metric_definition(metric_key)
    key = definition.key
    if key == "flows.exchange_netflow":
        return MetricAvailabilityPolicy(key, "PREMIUM_ONLY", "PREMIUM_PROVIDER_NOT_CONFIGURED")
    if key == "fundamentals.developer_activity":
        return MetricAvailabilityPolicy(key, "OPTIONAL", "OPTIONAL_PROVIDER_UNAVAILABLE")
    if key == "fundamentals.stablecoin_liquidity" and asset not in {"ETH", "SOL", "BNB"}:
        if asset == "AAVE":
            return MetricAvailabilityPolicy(key, "OPTIONAL", "OPTIONAL_PROVIDER_UNAVAILABLE")
        return MetricAvailabilityPolicy(key, "OPTIONAL", "METHODOLOGY_NOT_DEFINED")
    if key == "fundamentals.active_users" and asset == "AAVE":
        return MetricAvailabilityPolicy(key, "OPTIONAL", "OPTIONAL_PROVIDER_UNAVAILABLE")
    if key in {"tokenomics.annualized_emissions", "tokenomics.supply_growth"} and asset not in {"BTC", "ETH"}:
        return MetricAvailabilityPolicy(key, "OPTIONAL", "METHODOLOGY_NOT_DEFINED")
    if definition.is_event_risk:
        return MetricAvailabilityPolicy(key)
    if definition.decision_role != "SCORING_FACTOR":
        return MetricAvailabilityPolicy(key, "OPTIONAL", "OPTIONAL_PROVIDER_UNAVAILABLE")
    return MetricAvailabilityPolicy(key)


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


__all__ = [
    "MetricAvailabilityPolicy",
    "REASON_CODES",
    "REQUIREMENTS",
    "is_skippable",
    "metric_availability",
    "skip_reason",
]
