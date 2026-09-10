"""Canonical collection-availability policy for decision metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .metrics_registry import metric_definition, metrics_for_factor


REQUIREMENTS = ("CRITICAL", "PRIMARY", "SUPPORTING", "OPTIONAL", "PREMIUM_ONLY")
FACTOR_SUFFICIENCY_STATES = ("SUFFICIENT", "PARTIAL", "INSUFFICIENT", "NOT_APPLICABLE")
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
_SUPPORTING_METRICS = {
    "market.ma20",
    "market.relative_volume",
    "market.atr14",
    "market.realized_vol_30d",
    "market.realized_vol_90d",
    "market.drawdown",
    "valuation.market_cap",
    "valuation.fdv_market_cap_ratio",
    "valuation.fee_revenue_multiple",
    "fundamentals.stablecoin_liquidity",
    "eth.monetary.current_supply_eth",
    "eth.monetary.issuance_30d_eth",
    "eth.monetary.issuance_365d_eth",
    "eth.monetary.net_supply_growth_30d",
    "eth.monetary.net_supply_growth_90d",
    "eth.monetary.net_supply_growth_365d",
    "eth.staking.active_effective_stake_eth",
    "eth.staking.active_effective_stake_change_30d",
    "eth.l2.rent_paid_30d_usd",
    "eth.l2.rent_paid_90d_usd",
    "eth.l2.tvs_usd",
    "eth.da.ethereum_share_of_tracked_da_bytes_30d",
    "eth.da.ethereum_share_of_tracked_da_fees_30d",
    "eth.blobs.count_1d",
    "eth.blobs.count_30d",
    "eth.blobs.data_bytes_30d",
    "eth.blobs.blob_transactions_30d",
    "eth.blobs.utilization_30d",
}
_ASSET_PROFILES = {
    "BTC": "BTC",
    "ETH": "L1_SMART_CONTRACT",
    "SOL": "L1_SMART_CONTRACT",
    "BNB": "L1_SMART_CONTRACT",
    "AAVE": "DEFI_PROTOCOL",
    "LINK": "MIDDLEWARE",
}


@dataclass(frozen=True)
class MetricAvailabilityPolicy:
    metric_key: str
    requirement: str = "PRIMARY"
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
        return self.requirement in {"OPTIONAL", "PREMIUM_ONLY"}

    @property
    def importance(self) -> str:
        return self.requirement

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
    if definition.decision_role != "SCORING_FACTOR":
        return MetricAvailabilityPolicy(key, "CRITICAL" if definition.critical else "OPTIONAL")
    if definition.critical:
        return MetricAvailabilityPolicy(key, "CRITICAL")
    if key in _SUPPORTING_METRICS:
        return MetricAvailabilityPolicy(key, "SUPPORTING")
    return MetricAvailabilityPolicy(key, "PRIMARY")


def asset_profile(asset: str) -> str:
    symbol = str(asset).strip().upper()
    if not symbol:
        raise ValueError("asset must be a non-empty string")
    return _ASSET_PROFILES.get(symbol, "UNKNOWN")


def _policy_factor_requirements(policy: Any | None, factor: str) -> tuple[int, int]:
    configured = getattr(policy, "confidence", None) if policy is not None else None
    if configured is None and isinstance(policy, dict):
        configured = policy.get("confidence")
    configured = configured if isinstance(configured, dict) else {}
    values = configured.get("factor_sufficiency", {}).get(str(factor).strip().lower(), {})
    return (
        int(values.get("min_primary_available", 1)),
        int(values.get("min_total_available", 1)),
    )


def _event_value(event: Any, field: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(field, default)
    return getattr(event, field, default)


@dataclass(frozen=True)
class FactorSufficiency:
    factor: str
    status: str
    primary_available: int
    supporting_available: int
    total_available: int
    required_primary: int
    required_total: int
    missing: tuple[str, ...] = ()
    confidence: float = 0.0

    def __post_init__(self) -> None:
        factor = str(self.factor).strip().lower()
        status = str(self.status).strip().upper()
        if not factor or status not in FACTOR_SUFFICIENCY_STATES:
            raise ValueError("factor sufficiency has invalid factor or status")
        for field in (
            "primary_available", "supporting_available", "total_available",
            "required_primary", "required_total",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"factor sufficiency {field} must be a non-negative integer")
        confidence = float(self.confidence)
        if not 0 <= confidence <= 1:
            raise ValueError("factor sufficiency confidence must be in [0, 1]")
        object.__setattr__(self, "factor", factor)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "missing", tuple(sorted({str(item).strip() for item in self.missing if str(item).strip()})))
        object.__setattr__(self, "confidence", confidence)

    def as_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "status": self.status,
            "primary_available": self.primary_available,
            "supporting_available": self.supporting_available,
            "total_available": self.total_available,
            "required_primary": self.required_primary,
            "required_total": self.required_total,
            "missing": list(self.missing),
            "confidence": self.confidence,
        }

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "FactorSufficiency":
        if not isinstance(value, dict):
            raise ValueError("factor sufficiency must be an object")
        return cls(**value)


def evaluate_factor_sufficiency(
    factor: str,
    events: Any,
    *,
    policy: Any | None = None,
    asset: str | None = None,
) -> FactorSufficiency:
    factor = str(factor).strip().lower()
    required_primary, required_total = _policy_factor_requirements(policy, factor)
    if asset is not None and not any(definition.applies_to(str(asset).strip().upper()) for definition in metrics_for_factor(factor)):
        return FactorSufficiency(factor, "NOT_APPLICABLE", 0, 0, 0, required_primary, required_total, (), 0.0)
    rows = []
    for event in events:
        key = str(_event_value(event, "metric_key", "")).strip().lower()
        if not key or metric_definition(key).factor != factor:
            continue
        event_asset = str(_event_value(event, "asset", "")).strip().upper()
        if asset is not None and event_asset != str(asset).strip().upper():
            continue
        definition = metric_definition(key)
        if not definition.applies_to(event_asset) or metric_availability(event_asset, key).is_skippable:
            continue
        rows.append((key, metric_availability(event_asset, key), str(_event_value(event, "status", "")).upper()))
    by_key: dict[str, list[tuple[MetricAvailabilityPolicy, str]]] = {}
    for key, availability, status in rows:
        by_key.setdefault(key, []).append((availability, status))
    if not by_key:
        return FactorSufficiency(
            factor, "NOT_APPLICABLE" if asset is not None and not rows else "INSUFFICIENT",
            0, 0, 0, required_primary, required_total, (), 0.0,
        )
    available = {
        key for key, values in by_key.items()
        if any(status in {"SUCCESS", "AVAILABLE"} for _, status in values)
    }
    primary = sum(
        key in available and values[0][0].requirement in {"CRITICAL", "PRIMARY"}
        for key, values in by_key.items()
    )
    supporting = sum(
        key in available and values[0][0].requirement == "SUPPORTING"
        for key, values in by_key.items()
    )
    total = primary + supporting
    missing = tuple(
        key for key, values in by_key.items()
        if not any(status in {"SUCCESS", "AVAILABLE"} for _, status in values)
    )
    sufficient = primary >= required_primary and total >= required_total
    status = "SUFFICIENT" if sufficient else "PARTIAL" if total else "INSUFFICIENT"
    confidence = min(
        1.0,
        primary / required_primary if required_primary else 1.0,
        total / required_total if required_total else 1.0,
    )
    return FactorSufficiency(
        factor, status, primary, supporting, total, required_primary, required_total, missing, confidence,
    )


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
        and not metric_availability(asset, metric_key).is_skippable
    )


__all__ = [
    "MetricAvailabilityPolicy",
    "FALLBACK_MODES",
    "FACTOR_SUFFICIENCY_STATES",
    "FactorSufficiency",
    "REASON_CODES",
    "REQUIREMENTS",
    "asset_profile",
    "evaluate_factor_sufficiency",
    "is_skippable",
    "fallback_mode",
    "contributes_to_scoring_coverage",
    "metric_fallback_mode",
    "metric_availability",
    "skip_reason",
]
