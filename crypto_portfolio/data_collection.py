"""Compact, stderr-friendly data collection telemetry."""

from __future__ import annotations

import sys
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, TextIO

from .metric_availability import contributes_to_scoring_coverage, metric_availability
from .engine.metric_plan import DERIVED_METRIC_DEPENDENCIES
from .metrics_registry import REVIEW_TYPES, metric_definition
from .models.metrics_history import CollectionEvent, MetricObservation
from .models.policy import Policy, resolve_policy
from .state.metrics import (
    append_collection_event,
    append_metric_observation,
    classify_metric_change,
)


_STATUS_ORDER = ("SUCCESS", "FAILED", "STALE", "CONFLICT", "NOT_APPLICABLE", "SKIPPED")
_FETCH_FAILURE_STATUSES = {"FAILED", "STALE"}
_DERIVED_METRICS = set(DERIVED_METRIC_DEPENDENCIES) | {"market.flow_state"}
_ERROR_CODE_DESCRIPTIONS = {
    "CACHE_CORRUPT": "provider cache was corrupt",
    "CACHE_EXPIRED": "provider cache entry expired",
    "CACHE_MISS": "provider cache has no usable value",
    "CONFIG_DISABLED": "provider is disabled by configuration",
    "CREDENTIAL_MISSING": "provider credential is not visible to the runtime",
    "ADAPTER_UNAVAILABLE": "provider adapter is not registered",
    "DERIVED_INPUT_UNAVAILABLE": "derived input was unavailable",
    "METHODOLOGY_NOT_DEFINED": "no supported methodology is defined for this scope",
    "DNS_RESOLUTION_FAILED": "provider hostname could not be resolved",
    "CONNECT_TIMEOUT": "provider connection timed out",
    "READ_TIMEOUT": "provider response read timed out",
    "CONNECTION_REFUSED": "provider connection was refused",
    "CONNECTION_RESET": "provider connection was reset",
    "PROXY_ERROR": "provider proxy or tunnel failed",
    "HTTP_400": "provider rejected the request",
    "HTTP_401": "provider authentication failed",
    "HTTP_402": "provider requires an active subscription or plan",
    "RATED_SUBSCRIPTION_INACTIVE": "Rated subscription is not active",
    "ENTITLEMENT_REQUIRED": "provider plan does not include this metric",
    "HTTP_403": "provider rejected the request",
    "HTTP_403_AUTH": "provider authentication was rejected",
    "HTTP_403_RATE_LIMIT": "provider rate limit was exceeded",
    "HTTP_403_ACCESS_DENIED": "provider denied endpoint access",
    "HTTP_403_REGION_RESTRICTED": "provider endpoint is region restricted",
    "HTTP_403_WAF": "provider web application firewall rejected the request",
    "HTTP_403_UNKNOWN": "provider returned an unclassified forbidden response",
    "HTTP_404": "provider endpoint was not found",
    "HTTP_429": "provider rate limit was exceeded",
    "HTTP_5XX": "provider returned a server error",
    "INVALID_JSON": "provider returned invalid JSON",
    "RESPONSE_TOO_LARGE": "provider response exceeded the safety limit",
    "PROVIDER_SCHEMA_ERROR": "provider response could not be normalized",
    "PROVIDER_SCHEMA_CHANGED": "provider response contract changed",
    "INSUFFICIENT_SOURCE_COVERAGE": "required event sources were not all reachable",
    "NO_PROVIDER_ROUTE": "no structured provider route was configured",
    "PROVIDER_DISABLED": "provider is disabled or unavailable",
    "PROVIDER_INSUFFICIENT_HISTORY": "provider could not provide the bounded history required by the methodology",
    "PROVIDER_NOT_APPLICABLE": "provider does not apply to this metric",
    "PROVIDER_PLAN_RESTRICTED": "provider plan does not permit this metric",
    "UNAVAILABLE_BY_METHODOLOGY": "provider data cannot support the required methodology",
    "PROVIDER_UNSUPPORTED": "provider does not support this metric",
    "OPTIONAL_PROVIDER_UNSUPPORTED": "optional provider does not support this metric",
    "OPTIONAL_SOURCE_UNAVAILABLE": "no exact optional source is available",
    "RATE_LIMITED": "provider rate limit was exceeded",
    "NO_MARKET_DATA": "no compatible market data was available",
    "TLS_CERTIFICATE_VERIFY_FAILED": "provider TLS certificate verification failed",
    "CIRCUIT_OPEN": "provider circuit is open after transient failures",
    "REQUEST_BUDGET_EXHAUSTED": "provider request budget was exhausted",
    "RPC_BLOCK_TIMESTAMP_SEARCH_LIMIT": "RPC block timestamp search exceeded its safety bound",
    "RPC_BLOCK_TIMESTAMP_LIMIT": "RPC returned too many blocks requiring timestamp resolution",
    "RPC_LOG_REQUEST_LIMIT": "RPC log range exceeded its safety bound",
}


def _display(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def collection_decision_effect(event: CollectionEvent, *, review_type: str | None = None) -> str:
    """Return the deterministic scoring/decision effect used by collection telemetry."""
    definition = metric_definition(event.metric_key)
    if definition.decision_role == "EVENT_RISK":
        effect = "event-risk gate input; excluded from base scoring coverage"
        if event.status != "SUCCESS":
            hard_critical = definition.is_critical_for(review_type) if review_type is not None else definition.critical
            if hard_critical:
                effect += "; CRITICAL DATA FAILURE; high-conviction trade blocked"
            elif review_type is not None and definition.critical:
                effect += "; not hard-critical for this review"
    elif definition.decision_role != "SCORING_FACTOR":
        effect = "context only; excluded from base scoring coverage"
    elif event.status == "SUCCESS":
        effect = "available for scoring/history"
    elif event.status == "NOT_APPLICABLE":
        effect = "excluded from applicable coverage"
    elif event.status == "SKIPPED":
        requirement = metric_availability(event.asset, event.metric_key).requirement
        effect = f"excluded from applicable coverage ({requirement.lower()} metric)"
    elif metric_availability(event.asset, event.metric_key).is_skippable:
        effect = "optional evidence unavailable; excluded from applicable coverage"
    else:
        effect = "coverage/confidence reduced"
        hard_critical = definition.is_critical_for(review_type) if review_type is not None else definition.critical
        if hard_critical:
            effect += "; CRITICAL DATA FAILURE; high-conviction trade blocked"
        elif review_type is not None and definition.critical:
            effect += "; not hard-critical for this review"
    return effect


def _safe_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    from .providers.http import redact_secrets

    text = str(redact_secrets(value)).strip()
    if not text:
        return None
    lowered = text.lstrip().lower()
    if (
        lowered.startswith(("{", "["))
        or "<html" in lowered
        or "<!doctype" in lowered
        or "authorization:" in lowered
        or "cookie:" in lowered
        or "response body" in lowered
        or "raw response" in lowered
        or "content-type:" in lowered
        or '"headers"' in lowered
        or '"body"' in lowered
    ):
        return None
    return text


def _safe_log(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    from .providers.http import redact_log

    text = redact_log(value)
    if not text:
        return None
    lowered = text.lstrip().lower()
    if (
        lowered.startswith(("{", "["))
        or "<html" in lowered
        or "<!doctype" in lowered
        or "authorization:" in lowered
        or "cookie:" in lowered
        or "response body" in lowered
        or "raw response" in lowered
        or "content-type:" in lowered
        or '"headers"' in lowered
        or '"body"' in lowered
    ):
        return None
    return text


def _safe_endpoint(value: Any) -> str | None:
    text = _safe_text(value)
    if text is None:
        return None
    from .providers.http import redact_url

    try:
        return redact_url(text)
    except ValueError:
        return text


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if hasattr(value, "as_dict"):
        value = value.as_dict()
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must contain mappings")
    return value


def _sequence(value: Any, field: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a sequence")
    return tuple(value)


def _acquisition_field(acquisition: Any, field: str) -> Any:
    if isinstance(acquisition, Mapping):
        return acquisition.get(field, ())
    return getattr(acquisition, field, ())


def _result_event(result: Any) -> CollectionEvent:
    if isinstance(result, CollectionEvent):
        return result
    if isinstance(result, Mapping):
        event = result.get("event", result if "metric_key" in result else None)
    else:
        event = getattr(result, "event", None)
    if isinstance(event, CollectionEvent):
        return event
    return CollectionEvent.from_mapping(_mapping(event, "results.event"))


def _attempt_matches(attempt: Mapping[str, Any], event: CollectionEvent) -> bool:
    asset = str(attempt.get("asset", "")).strip().upper()
    metric_keys = attempt.get("metric_keys", ())
    if isinstance(metric_keys, str):
        metric_keys = (metric_keys,)
    direct = (
        asset == event.asset
        and isinstance(metric_keys, (list, tuple))
        and event.metric_key in {str(key).strip().lower() for key in metric_keys}
    )
    return direct


def _event_category(metric_key: str) -> str | None:
    from .events import event_metric_category

    return event_metric_category(metric_key)


def _attempt_entry(attempt: Mapping[str, Any]) -> dict[str, Any]:
    provider = _safe_text(attempt.get("provider"))
    status = _safe_text(attempt.get("status"))
    if provider is None or status is None:
        raise ValueError("provider attempts require provider and status")
    result: dict[str, Any] = {"provider": provider, "status": status.upper()}
    error_code = _safe_text(attempt.get("error_code"))
    if error_code is not None:
        result["error_code"] = error_code.upper()
    reason = _safe_text(attempt.get("reason")) or _safe_text(attempt.get("detail"))
    if reason is not None:
        result["reason"] = reason
    for field_name in ("exception_class", "detail"):
        value = _safe_text(attempt.get(field_name))
        if value is not None:
            result[field_name] = value
    endpoint = _safe_endpoint(attempt.get("endpoint"))
    if endpoint is not None:
        result["endpoint"] = endpoint
    method = _safe_text(attempt.get("method"))
    if method is not None:
        result["method"] = method.upper()
    attempt_number = attempt.get("attempt")
    if isinstance(attempt_number, int) and not isinstance(attempt_number, bool) and attempt_number >= 1:
        result["attempt"] = attempt_number
    retryable = attempt.get("retryable")
    if isinstance(retryable, bool):
        result["retryable"] = retryable
    status_code = attempt.get("status_code")
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        result["status_code"] = status_code
    log = _safe_log(attempt.get("log"))
    if log is not None:
        result["log"] = log
    return result


def _failed_current_attempt(attempt: Mapping[str, Any]) -> bool:
    status = str(attempt.get("status", "")).strip().upper()
    return bool(status and status != "SUCCESS")


def _event_scan_status(event_scans: tuple[Mapping[str, Any], ...], event: CollectionEvent) -> str | None:
    category = _event_category(event.metric_key)
    if category is None:
        return None
    for scan in event_scans:
        if (
            str(scan.get("asset", "")).strip().upper() == event.asset
            and str(scan.get("category", "")).strip().lower() == category
        ):
            status = _safe_text(scan.get("status"))
            if status == "INSUFFICIENT_SOURCE_COVERAGE":
                return status
    return None


def _failure_stage(
    event: CollectionEvent,
    attempts: tuple[Mapping[str, Any], ...],
    web_fallbacks: tuple[Mapping[str, Any], ...],
) -> str:
    details = tuple(
        text.lower()
        for text in (
            _safe_text(event.reason),
            _safe_text(event.refresh_error_detail),
        )
        if text is not None
    )
    if any("provider value rejected" in text or "event scan result rejected" in text for text in details):
        return "VALIDATION"
    if any(
        marker in text
        for text in details
        for marker in ("derived_input_unavailable", "insufficient_aligned_history")
    ) or event.metric_key in _DERIVED_METRICS and not attempts:
        return "DERIVED"
    if _event_category(event.metric_key) is not None:
        return "EVENT_SCAN"
    if event.refresh_provider or event.refresh_error_code or attempts:
        return "PROVIDER"
    if any(
        str(item.get("asset", "")).strip().upper() == event.asset
        and str(item.get("metric_key", "")).strip().lower() == event.metric_key
        for item in web_fallbacks
    ):
        return "WEB_FALLBACK"
    return "UNKNOWN"


def _failure_reason(event: CollectionEvent, attempt: Mapping[str, Any] | None, error_code: str | None) -> str:
    values = (
        event.reason,
        event.refresh_error_detail,
        attempt.get("reason") if attempt else None,
        attempt.get("detail") if attempt else None,
    )
    for value in values:
        text = _safe_text(value)
        if text is not None:
            return text
    if error_code is not None:
        return _ERROR_CODE_DESCRIPTIONS.get(error_code, "原因未提供")
    return "原因未提供"


def _failure_error_code(
    event: CollectionEvent,
    attempt: Mapping[str, Any] | None,
    event_scan_code: str | None,
) -> str | None:
    for value in (
        event.refresh_error_code,
        attempt.get("error_code") if attempt else None,
        event_scan_code,
    ):
        text = _safe_text(value)
        if text is not None:
            return text.upper()
    return None


def build_failed_data_fetches(
    acquisition: Any,
    *,
    review_type: str | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Project final unresolved acquisition results into safe metric-level records."""
    if acquisition is None:
        return ()
    if review_type is None:
        plan = _acquisition_field(acquisition, "plan")
        if hasattr(plan, "review_type"):
            review_type = plan.review_type
        elif isinstance(plan, Mapping):
            review_type = plan.get("review_type")
    if review_type is not None:
        normalized_review = review_type.strip().upper() if isinstance(review_type, str) else review_type
        if normalized_review not in REVIEW_TYPES:
            raise ValueError(f"review_type must be one of {list(REVIEW_TYPES)}")
        review_type = normalized_review
    results = _sequence(_acquisition_field(acquisition, "results"), "acquisition.results")
    raw_attempts = _sequence(_acquisition_field(acquisition, "attempts"), "acquisition.attempts")
    attempts = tuple(_mapping(item, "acquisition.attempts") for item in raw_attempts)
    raw_event_scans = _sequence(_acquisition_field(acquisition, "event_scans"), "acquisition.event_scans")
    event_scans = tuple(_mapping(item, "acquisition.event_scans") for item in raw_event_scans)
    raw_web_fallbacks = _sequence(_acquisition_field(acquisition, "web_fallbacks"), "acquisition.web_fallbacks")
    web_fallbacks = tuple(_mapping(item, "acquisition.web_fallbacks") for item in raw_web_fallbacks)
    raw_pending = _sequence(
        _acquisition_field(acquisition, "pending_event_scans"),
        "acquisition.pending_event_scans",
    )
    pending_event_groups: set[tuple[str, str]] = set()
    for item in raw_pending:
        if isinstance(item, Mapping):
            asset = str(item.get("asset", "")).strip().upper()
            category = str(item.get("category", "")).strip().lower()
        else:
            asset = str(getattr(item, "asset", "")).strip().upper()
            category = str(getattr(item, "category", "")).strip().lower()
        if asset and category:
            pending_event_groups.add((asset, category))
    pending_web_identities = {
        (
            str(item.get("asset", "")).strip().upper(),
            str(item.get("metric_key", "")).strip().lower(),
        )
        for item in web_fallbacks
    }
    rows: list[dict[str, Any]] = []
    for raw_result in results:
        event = _result_event(raw_result)
        if metric_availability(event.asset, event.metric_key).is_skippable:
            continue
        category = _event_category(event.metric_key)
        group = ("MARKET", category) if category == "regulatory" else (event.asset, category)
        if (
            (category is not None and group in pending_event_groups)
            or (event.asset, event.metric_key) in pending_web_identities
        ):
            continue
        relevant_attempts = tuple(item for item in attempts if _attempt_matches(item, event))
        include = event.status == "FAILED" or (
            event.status == "STALE"
            and (
                event.refresh_provider is not None
                or event.refresh_error_code is not None
                or event.refresh_error_detail is not None
                or any(_failed_current_attempt(item) for item in relevant_attempts)
                or _event_scan_status(event_scans, event) is not None
            )
        )
        if not include or event.status not in _FETCH_FAILURE_STATUSES:
            continue
        safe_attempts = tuple(_attempt_entry(item) for item in relevant_attempts)
        final_attempt = relevant_attempts[-1] if relevant_attempts else None
        error_code = _failure_error_code(
            event,
            final_attempt,
            _event_scan_status(event_scans, event),
        )
        provider = _safe_text(event.refresh_provider)
        if provider is None and final_attempt is not None:
            provider = _safe_text(final_attempt.get("provider"))
        endpoint = _safe_endpoint(event.refresh_endpoint)
        if endpoint is None and final_attempt is not None:
            endpoint = _safe_endpoint(final_attempt.get("endpoint"))
        row: dict[str, Any] = {
            "asset": event.asset,
            "metric_key": metric_definition(event.metric_key).key,
            "status": event.status,
            "failure_stage": _failure_stage(event, relevant_attempts, web_fallbacks),
            "reason": _failure_reason(event, final_attempt, error_code),
            "critical": metric_definition(event.metric_key).is_critical_for(review_type) if review_type else metric_definition(event.metric_key).critical,
            "decision_role": metric_definition(event.metric_key).decision_role,
            "decision_effect": collection_decision_effect(event, review_type=review_type),
            "attempts": list(safe_attempts),
        }
        if error_code is not None:
            row["error_code"] = error_code
        if provider is not None:
            row["provider"] = provider
        if endpoint is not None:
            row["endpoint"] = endpoint
        if event.last_observation_at is not None:
            row["last_observation_at"] = event.last_observation_at
        rows.append(row)
    rows.sort(key=lambda item: (not item["critical"], item["asset"], item["metric_key"]))
    return tuple(rows)


def collection_summary(
    events: Iterable[CollectionEvent],
    *,
    weights: Mapping[str, float] | None = None,
    review_type: str | None = None,
    policy: Policy | None = None,
    asset: str | None = None,
) -> dict[str, Any]:
    values = tuple(events)
    if any(not isinstance(event, CollectionEvent) for event in values):
        raise ValueError("events must contain CollectionEvent objects")
    if review_type is not None:
        review_type = review_type.strip().upper() if isinstance(review_type, str) else review_type
        if review_type not in REVIEW_TYPES:
            raise ValueError(f"review_type must be one of {list(REVIEW_TYPES)}")
    counts = Counter(event.status for event in values)
    resolved_policy = policy or resolve_policy()

    def policy_scoped(event: CollectionEvent) -> bool:
        if event.asset == "MARKET":
            return True
        if hasattr(resolved_policy, "is_excluded") and resolved_policy.is_excluded(event.asset):
            return False
        classify = getattr(resolved_policy, "classify", None)
        return classify is None or classify(event.asset) != "other"

    scoring_events = [
        event for event in values
        if metric_definition(event.metric_key).decision_role == "SCORING_FACTOR" and policy_scoped(event)
    ]
    applicable = [
        event for event in scoring_events
        if contributes_to_scoring_coverage(event.asset, event.metric_key)
        and event.status != "NOT_APPLICABLE"
    ]
    if isinstance(resolved_policy, Mapping):
        policy_weights = dict(resolved_policy["scoring_profiles"]["default"])
        scoring_policy = resolved_policy.get("scoring", {})
    else:
        policy_weights = dict(resolved_policy.scoring_profile(asset or "MARKET"))
        scoring_policy = resolved_policy.scoring
    supplied_weights = policy_weights if weights is None else weights
    if not isinstance(supplied_weights, Mapping):
        raise ValueError("weights must be an object")
    factor_weights: dict[str, float] = {}
    metric_weights: dict[str, float] = {}
    for raw_key, raw_weight in supplied_weights.items():
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError("collection weights must be finite non-negative numbers")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("collection weights must be finite non-negative numbers")
        key = str(raw_key).strip().lower()
        if key in policy_weights:
            factor_weights[key] = weight
        else:
            definition = metric_definition(key)
            if definition.decision_role == "SCORING_FACTOR":
                metric_weights[key] = weight
    if metric_weights and not factor_weights:
        factor_weights = {
            factor: sum(weight for key, weight in metric_weights.items() if metric_definition(key).factor == factor)
            for factor in {metric_definition(key).factor for key in metric_weights}
        }
    if not factor_weights:
        factor_weights = policy_weights
    by_factor: dict[str, list[CollectionEvent]] = {}
    for event in applicable:
        by_factor.setdefault(metric_definition(event.metric_key).factor, []).append(event)
    factor_coverage = {
        factor: sum(event.status == "SUCCESS" for event in factor_events) / len(factor_events)
        for factor, factor_events in by_factor.items()
    }
    per_request_coverage = sum(event.status == "SUCCESS" for event in applicable) / len(applicable) if applicable else 0.0
    weighted_factors = {
        factor: coverage
        for factor, coverage in factor_coverage.items()
        if factor_weights.get(factor, 0.0) > 0
    }
    total_factor_weight = sum(factor_weights.get(factor, 0.0) for factor in weighted_factors)
    policy_weighted_coverage = (
        sum(factor_weights[factor] * weighted_factors[factor] for factor in weighted_factors) / total_factor_weight
        if total_factor_weight else 0.0
    )
    critical_failures = sum(
        event.status in {"FAILED", "STALE", "CONFLICT"}
        and (
            metric_definition(event.metric_key).is_critical_for(review_type)
            if review_type is not None
            else metric_definition(event.metric_key).critical
        )
        for event in values
    )
    minimum = float(scoring_policy["minimum_investable_coverage"])
    medium = float(scoring_policy["medium_confidence_min_coverage"])
    high = float(scoring_policy["high_confidence_min_coverage"])
    if critical_failures or policy_weighted_coverage < minimum or policy_weighted_coverage < medium:
        confidence = "LOW"
    elif policy_weighted_coverage < high:
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"
    skipped_optional = sum(
        event.status == "SKIPPED"
        and metric_availability(event.asset, event.metric_key).requirement == "OPTIONAL"
        for event in values
    )
    skipped_premium = sum(
        event.status == "SKIPPED"
        and metric_availability(event.asset, event.metric_key).requirement == "PREMIUM_ONLY"
        for event in values
    )
    result = {
        "requested": len(values),
        "counts": {status: counts.get(status, 0) for status in _STATUS_ORDER},
        "critical_failures": critical_failures,
        "coverage": policy_weighted_coverage,
        "evidence_coverage": policy_weighted_coverage,
        "per_request_coverage": per_request_coverage,
        "policy_weighted_coverage": policy_weighted_coverage,
        "factor_coverage": factor_coverage,
        "policy_factor_weights": {factor: factor_weights[factor] for factor in weighted_factors},
        "hard_critical_failure": bool(critical_failures),
        "confidence": confidence,
        "overlay_requested": len(values) - len(scoring_events),
        "review_type": review_type,
        "skipped_optional": skipped_optional,
        "skipped_premium": skipped_premium,
    }
    result["counts"].update({
        "SKIPPED_OPTIONAL": skipped_optional,
        "SKIPPED_PREMIUM": skipped_premium,
    })
    return result


def format_collection_event(
    event: CollectionEvent,
    observation: MetricObservation | None = None,
    previous: MetricObservation | None = None,
    *,
    review_type: str | None = None,
) -> str:
    if not isinstance(event, CollectionEvent):
        raise ValueError("event must be a CollectionEvent")
    current = observation.value if observation else None
    change = None
    if observation and previous and isinstance(current, (int, float)) and isinstance(previous.value, (int, float)) and previous.value != 0:
        change = (float(current) - float(previous.value)) / float(previous.value)
    summary = _display(current)
    if change is not None:
        summary = f"{summary} ({change:+.2%})"
    lines = [
        f"[DATA] {event.asset} {event.metric_key} {event.status} {summary}",
        f"       source: {event.source or (observation.source if observation else 'N/A')}",
        f"       observed_at: {event.observed_at or (observation.observed_at if observation else 'N/A')}",
        f"       freshness_reference_at: {observation.freshness_reference_at if observation else 'N/A'}",
        f"       fetched_at: {event.fetched_at or (observation.fetched_at if observation else event.timestamp)}",
    ]
    if event.refresh_provider:
        lines.append(f"       provider: {event.refresh_provider}")
    if event.refresh_endpoint:
        lines.append(f"       endpoint: {event.refresh_endpoint}")
    if event.refresh_error_code:
        lines.append(f"       error_code: {event.refresh_error_code}")
    if event.last_observation_at:
        lines.append(f"       last_observation: {event.last_observation_at} (STALE)")
    if observation is not None:
        lines.append(f"       Current: {_display(observation.value)}")
    if previous is not None:
        lines.append(f"       Previous: {_display(previous.value)}")
        lines.append(f"       Change: {f'{change:+.2%}' if change is not None else 'N/A'}")
        lines.append(
            f"       Trend: {classify_metric_change(event.metric_key, observation.value if observation else None, previous.value, stale=observation.freshness != 'CURRENT' if observation else False)}"
        )
    if event.reason:
        lines.append(f"       reason: {event.reason}")
    lines.append(f"       scoring_effect: {collection_decision_effect(event, review_type=review_type)}")
    return "\n".join(lines)


def format_collection_summary(summary: Mapping[str, Any]) -> str:
    counts = summary["counts"]
    return "\n".join(
        (
            "Data Collection Summary",
            f"Requested metrics: {summary['requested']}",
            f"SUCCESS: {counts['SUCCESS']}  STALE: {counts['STALE']}  FAILED: {counts['FAILED']}",
            f"CONFLICT: {counts['CONFLICT']}  NOT_APPLICABLE: {counts['NOT_APPLICABLE']}",
            f"SKIPPED_OPTIONAL: {counts.get('SKIPPED_OPTIONAL', 0)}  SKIPPED_PREMIUM: {counts.get('SKIPPED_PREMIUM', 0)}",
            f"Critical failures: {summary['critical_failures']}",
            f"Per-request coverage: {summary.get('per_request_coverage', summary['coverage']):.0%}",
            f"Policy-weighted coverage: {summary.get('policy_weighted_coverage', summary['coverage']):.0%}",
            f"Decision confidence: {summary['confidence']}",
            f"Pending external resolution: {summary.get('pending_external_resolution', 0)}",
            f"Overlay context metrics: {summary.get('overlay_requested', 0)}",
        )
    )


def format_overlay_summary(overlays: Any) -> str:
    """Render compact positioning/cycle telemetry without raw source data."""
    from .models.market_overlays import MarketOverlays

    value = overlays if isinstance(overlays, MarketOverlays) else MarketOverlays.from_mapping(overlays)
    lines = ["Positioning & Cycle Context"]
    for symbol, facts in value.positioning_by_asset.items():
        lines.append(
            f"{symbol} Positioning: {facts.bias} / {facts.risk} "
            f"({facts.confidence} confidence; Social {facts.social_state})"
        )
    if value.btc_cycle is not None:
        cycle = value.btc_cycle
        lines.append(
            f"BTC Cycle: {cycle.market_cycle_state} / {cycle.cycle_risk} "
            f"({cycle.confidence} confidence; {cycle.halving_context})"
        )
    if value.effective_deployment_caps:
        lines.append("Effective deployment caps: " + ", ".join(
            f"{symbol} {factor:.0%}" for symbol, factor in value.effective_deployment_caps.items()
        ))
    lines.extend(value.warnings)
    return "\n".join(lines)


@dataclass
class CollectionReporter:
    """Persist and print collection results without polluting JSON stdout."""

    stream: TextIO | None = None
    observation_path: str | None = None
    event_path: str | None = None
    weights: Mapping[str, float] | None = None
    routing: Mapping[str, str] | None = None
    review_type: str | None = None
    policy: Policy | None = None

    def __post_init__(self) -> None:
        self.stream = self.stream or sys.stderr
        self.events: list[CollectionEvent] = []

    def record(
        self,
        event: CollectionEvent,
        observation: MetricObservation | None = None,
        previous: MetricObservation | None = None,
    ) -> None:
        if not isinstance(event, CollectionEvent):
            raise ValueError("event must be a CollectionEvent")
        if event.status == "SUCCESS":
            if not isinstance(observation, MetricObservation):
                raise ValueError("SUCCESS requires a MetricObservation")
            if observation.asset != event.asset or observation.metric_key != event.metric_key:
                raise ValueError("observation does not match collection event")
            if self.observation_path:
                append_metric_observation(observation, self.observation_path)
        if self.event_path:
            append_collection_event(event, self.event_path)
        self.events.append(event)
        print(format_collection_event(event, observation, previous, review_type=self.review_type), file=self.stream)

    def summary(self) -> dict[str, Any]:
        result = collection_summary(
            self.events,
            weights=self.weights,
            review_type=self.review_type,
            policy=self.policy,
        )
        if self.routing is not None:
            from .model_routing import routing_metadata

            result["routing_metadata"] = routing_metadata(self.routing)
        return result

    def record_result(self, result: Any, previous: MetricObservation | None = None) -> None:
        from .engine.metric_normalization import NormalizedMetricResult, normalize_metric_result

        normalized = result if isinstance(result, NormalizedMetricResult) else normalize_metric_result(result)
        self.record(normalized.event, normalized.observation, previous)

    def print_summary(self) -> dict[str, Any]:
        result = self.summary()
        print(format_collection_summary(result), file=self.stream)
        return result


__all__ = [
    "CollectionReporter",
    "collection_summary",
    "build_failed_data_fetches",
    "collection_decision_effect",
    "format_collection_event",
    "format_collection_summary",
    "format_overlay_summary",
]
