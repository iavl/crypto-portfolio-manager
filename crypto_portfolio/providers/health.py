"""Deterministic provider health, contract, and smoke diagnostics."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..engine.metric_plan import MetricRequest
from ..metric_availability import metric_availability
from ..metrics_registry import metric_definition, normalize_metric_key
from .base import FetchMode
from .config import validate_provider_registry
from .probe import probe_provider
from .router import ProviderRouter
from .routes import provider_chain


def _names(router: ProviderRouter, providers: Iterable[str] | str, *, include_unready: bool = False) -> tuple[str, ...]:
    if providers == "all":
        names = {item.provider for item in router.provider_runtime_status()}
        if not include_unready:
            names = set(router.providers)
        return tuple(sorted(names))
    if isinstance(providers, str):
        return (providers.strip().lower(),)
    return tuple(str(item).strip().lower() for item in providers)


def _base_doctor_row(status: Any) -> dict[str, Any]:
    return {
        "provider": status.provider,
        "config": "READY" if status.config_enabled else "NOT_READY",
        "adapter": "READY" if status.adapter_available else "MISSING",
        "credential": "PRESENT" if status.credential_required and status.credential_present else "MISSING" if status.credential_required else "NOT_REQUIRED",
        "runtime": "READY" if status.runtime_ready else "NOT_READY",
        "network": "SKIPPED",
        "tls": "NOT_TESTED",
        "proxy": "NOT_TESTED",
        "http_status": None,
        "auth": "NOT_TESTED",
        "plan_access": "NOT_TESTED",
        "schema": "NOT_TESTED",
        "normalization": "NOT_TESTED",
        "latency_ms": None,
        "error_code": status.reason,
        "detail": status.reason,
    }


def doctor_providers(
    router: ProviderRouter,
    providers: Iterable[str] | str = "all",
    *,
    asset: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Run readiness checks and, for ready providers, the production probe."""
    statuses = {item.provider: item for item in router.provider_runtime_status()}
    rows: list[dict[str, Any]] = []
    for name in _names(router, providers, include_unready=True):
        status = statuses.get(name)
        if status is None:
            rows.append({
                "provider": name,
                "config": "NOT_READY",
                "adapter": "MISSING",
                "credential": "NOT_TESTED",
                "runtime": "NOT_READY",
                "network": "SKIPPED",
                "tls": "NOT_TESTED",
                "proxy": "NOT_TESTED",
                "http_status": None,
                "auth": "NOT_TESTED",
                "plan_access": "NOT_TESTED",
                "schema": "NOT_TESTED",
                "normalization": "NOT_TESTED",
                "latency_ms": None,
                "error_code": "ADAPTER_UNAVAILABLE",
                "detail": "provider is not registered",
            })
            continue
        if not status.runtime_ready:
            rows.append(_base_doctor_row(status))
            continue
        for result in probe_provider(router, name, asset=asset):
            row = _base_doctor_row(status)
            row.update(result)
            row["adapter"] = "READY"
            row["credential"] = "PRESENT" if status.credential_required else "NOT_REQUIRED"
            row["runtime"] = "READY"
            row["tls"] = "OK" if result.get("tls_verification") else "FAILED" if result.get("network") != "SKIPPED" else "NOT_TESTED"
            row["tested"] = result.get("network") != "SKIPPED"
            if result.get("error_code") is None:
                row["error_code"] = None
                row["detail"] = None
            else:
                row["error_code"] = str(result["error_code"]).upper()
                row["detail"] = result.get("detail") or row["error_code"]
            rows.append(row)
    return tuple(rows)


def contract_providers(
    router: ProviderRouter,
    providers: Iterable[str] | str = "all",
    *,
    asset: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Run the smallest available live adapter contract request."""
    rows: list[dict[str, Any]] = []
    statuses = {item.provider: item for item in router.provider_runtime_status()}
    for name in _names(router, providers, include_unready=True):
        status = statuses.get(name)
        if status is None or not status.runtime_ready:
            rows.append({
                "check": "CONTRACT",
                "provider": name,
                "status": "SKIPPED",
                "reason": status.reason if status else "ADAPTER_UNAVAILABLE",
                "error_code": status.reason if status else "ADAPTER_UNAVAILABLE",
            })
            continue
        for result in probe_provider(router, name, asset=asset):
            row = {"check": "CONTRACT", **result}
            row["status"] = "PASS" if "error_code" not in result else "FAIL"
            rows.append(row)
    return tuple(rows)


def smoke_provider(
    router: ProviderRouter,
    asset: str,
    metric: str = "market.spot_price",
) -> dict[str, Any]:
    """Exercise the production router for one deliberately live metric request."""
    normalized_asset = str(asset).strip().upper()
    metric_key = normalize_metric_key(metric)
    if metric_key == "market.price":
        metric_key = "market.spot_price"
    definition = metric_definition(metric_key)
    if not definition.applies_to(normalized_asset):
        raise ValueError(f"metric {metric_key} is not applicable to {normalized_asset}")
    request = MetricRequest(normalized_asset, metric_key)
    chain = provider_chain(metric_key, normalized_asset)
    built = router.build_requests((request,))
    if not chain or not built:
        skipped = metric_availability(normalized_asset, metric_key).is_skippable
        return {
            "check": "SMOKE",
            "asset": normalized_asset,
            "metric": metric_key,
            "mode": FetchMode.REFRESH.value,
            "chain": list(chain),
            "attempts": [],
            "provider_selected": None,
            "network_requests": 0,
            "cache_hits": 0,
            "fallback_count": 0,
            "observation_status": "SKIPPED" if skipped else "FAILED",
            "status": "SKIPPED" if skipped else "FAIL",
            "unresolved": [{"error_code": "NO_PROVIDER_ROUTE", "reason": "no configured provider route"}],
        }
    routed = router.collect(built, mode=FetchMode.REFRESH)
    selected = next((attempt.provider for attempt in routed.attempts if attempt.success), None)
    attempts = []
    for item in routed.attempts:
        row = item.as_dict()
        row.pop("log", None)
        attempts.append(row)
    unresolved = [dict(item) for item in routed.unresolved_details]
    skip_codes = {
        "NO_PROVIDER_ROUTE", "PROVIDER_DISABLED", "PROVIDER_UNSUPPORTED",
        "OPTIONAL_PROVIDER_UNSUPPORTED", "OPTIONAL_SOURCE_UNAVAILABLE",
        "PROVIDER_INSUFFICIENT_HISTORY", "CONFIG_DISABLED", "CREDENTIAL_MISSING",
        "ADAPTER_UNAVAILABLE", "DERIVED_INPUT_UNAVAILABLE",
    }
    skipped = metric_availability(normalized_asset, metric_key).is_skippable and unresolved and all(
        str(item.get("error_code", "")).upper() in skip_codes for item in unresolved
    )
    return {
        "check": "SMOKE",
        "asset": normalized_asset,
        "metric": metric_key,
        "mode": FetchMode.REFRESH.value,
        "chain": list(chain),
        "attempts": attempts,
        "provider_selected": selected,
        "network_requests": routed.api_requests,
        "cache_hits": routed.provider_cache_hits,
        "fallback_count": routed.provider_fallbacks,
        "status": "SKIPPED" if skipped else "PASS" if not routed.unresolved else "FAIL",
        "observation_status": "SKIPPED" if skipped else "SUCCESS" if routed.observations else "FAILED",
        "unresolved": unresolved,
    }


def diagnostic_failed(result: Mapping[str, Any]) -> bool:
    """Return whether a ready/tested diagnostic operation failed."""
    if result.get("config") != "READY" or result.get("tested") is False:
        return False
    if result.get("network") == "SKIPPED" and not result.get("error_code"):
        return False
    return bool(
        result.get("error_code")
        or result.get("network") == "FAILED"
        or result.get("auth") == "REJECTED"
        or result.get("plan_access") == "RESTRICTED"
        or result.get("schema") == "ERROR"
        or result.get("normalization") == "ERROR"
    )


def diagnostic_exit_code(results: Iterable[Mapping[str, Any]]) -> int:
    return 2 if any(diagnostic_failed(result) for result in results) else 0


__all__ = [
    "contract_providers",
    "diagnostic_exit_code",
    "diagnostic_failed",
    "doctor_providers",
    "smoke_provider",
    "validate_provider_registry",
]
