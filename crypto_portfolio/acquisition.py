"""On-demand, cache-first metric acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from collections import Counter
import math
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .data_collection import collection_summary
from .engine.factors.flows import classify_flow_state
from .metric_availability import metric_availability, skip_reason
from .engine.derived_metrics import derive_metric_observations
from .engine.metric_normalization import NormalizedMetricResult, normalize_metric_result, persist_metric_result
from .engine.metric_plan import MetricCollectionPlan, MetricRequest
from .engine.technical import derive_aligned_relative_return
from .events import EventScanner, EventSourceScanRequest, EventSourceScanResponse, event_metric_category
from .metrics_registry import metric_definition
from .models.events import EventScanResult
from .models.metrics_history import MetricObservation
from .models.time import normalize_timestamp, parse_timestamp
from .providers.base import FetchMode
from .providers.config import load_provider_config
from .providers.http import redact_secrets
from .providers.routes import current_delivery_basis, metric_is_mutable, metric_reuse_ttl_seconds, provider_chain
from .providers.router import ProviderRouter
from .state.metrics import latest_usable_observation, read_metric_observations


_EVENT_METRIC_KEYS = {
    "security": "risk.security_event_status",
    "governance": "risk.governance_event_status",
    "regulatory": "risk.regulatory_event_status",
}
_PROVIDER_SKIP_ERROR_CODES = {
    "NO_PROVIDER_ROUTE",
    "PROVIDER_DISABLED",
    "PROVIDER_UNSUPPORTED",
    "PROVIDER_PLAN_RESTRICTED",
    "PROVIDER_NOT_APPLICABLE",
    "CACHE_MISS",
    "CACHE_EXPIRED",
    "CACHE_CORRUPT",
    "PROVIDER_INSUFFICIENT_HISTORY",
}
_DERIVED_DEPENDENCIES = {
    "valuation.fdv_market_cap_ratio": ("valuation.fdv", "valuation.market_cap"),
    "derivatives.open_interest_to_market_cap": ("derivatives.open_interest_usd", "valuation.market_cap"),
    "btc_valuation.price_to_realized_price": ("market.spot_price", "btc_valuation.realized_price"),
}


class AcquisitionResolutionRequired(RuntimeError):
    """Control-flow signal that hard-critical external evidence is pending."""


@dataclass(frozen=True)
class WebFallbackRequest:
    asset: str
    metric_key: str
    reason: str
    preferred_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.asset, str) or not self.asset.strip():
            raise ValueError("web fallback asset must be a non-empty string")
        if not isinstance(self.metric_key, str) or not self.metric_key.strip():
            raise ValueError("web fallback metric_key must be a non-empty string")
        metric_definition(self.metric_key)
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("web fallback reason must be a non-empty string")
        if isinstance(self.preferred_sources, str):
            raise ValueError("web fallback preferred_sources must be a sequence")
        sources = tuple(str(item).strip() for item in self.preferred_sources if str(item).strip())
        object.__setattr__(self, "asset", self.asset.strip().upper())
        object.__setattr__(self, "metric_key", metric_definition(self.metric_key).key)
        object.__setattr__(self, "reason", self.reason.strip())
        object.__setattr__(self, "preferred_sources", tuple(dict.fromkeys(sources)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "metric_key": self.metric_key,
            "reason": self.reason,
            "preferred_sources": list(self.preferred_sources),
        }


@dataclass(frozen=True)
class AcquisitionResult:
    plan: MetricCollectionPlan
    results: tuple[NormalizedMetricResult, ...]
    web_fallbacks: tuple[WebFallbackRequest, ...] = ()
    summary: Mapping[str, Any] | None = None
    attempts: tuple[Mapping[str, Any], ...] = ()
    event_scan_requests: tuple[EventSourceScanRequest, ...] = ()
    event_scans: tuple[EventScanResult, ...] = ()

    @property
    def observations(self) -> tuple[MetricObservation, ...]:
        return tuple(item.observation for item in self.results if item.observation is not None)

    @property
    def events(self) -> tuple[Any, ...]:
        return tuple(item.event for item in self.results)

    @property
    def collection_results(self) -> tuple[NormalizedMetricResult, ...]:
        return self.results

    @property
    def pending_event_scans(self) -> tuple[EventSourceScanRequest, ...]:
        return self.event_scan_requests

    @property
    def pending_web_fallbacks(self) -> tuple[WebFallbackRequest, ...]:
        return self.web_fallbacks

    @property
    def hard_critical_unresolved(self) -> tuple[tuple[str, str], ...]:
        pending_groups = {
            (request.asset, request.category)
            for request in self.event_scan_requests
        }
        completed_groups = {
            ("MARKET" if scan.category == "regulatory" else scan.asset, scan.category)
            for scan in self.event_scans
            if scan.status != "INSUFFICIENT_SOURCE_COVERAGE"
        }
        result_by_identity = {
            (item.event.asset, item.event.metric_key): item
            for item in self.results
        }
        unresolved = []
        for request in self.plan.requests:
            if not request.definition.applies_to(request.asset):
                continue
            category = event_metric_category(request.metric_key)
            if not request.critical:
                continue
            result = result_by_identity.get((request.asset, request.metric_key))
            if request.metric_key == "risk.chain_liveness_status":
                if result is None or result.status != "SUCCESS":
                    unresolved.append((request.asset, request.metric_key))
                continue
            if category is None:
                continue
            group = ("MARKET", category) if category == "regulatory" else (request.asset, category)
            missing_resolution = group in pending_groups or (
                group not in completed_groups and result is not None and result.status != "SUCCESS"
            )
            if missing_resolution and (request.asset, request.metric_key) not in unresolved:
                unresolved.append((request.asset, request.metric_key))
        return tuple(unresolved)

    @property
    def requires_external_resolution(self) -> bool:
        return bool(self.pending_event_scans or self.pending_web_fallbacks or self.hard_critical_unresolved)

    @property
    def ready_for_scoring(self) -> bool:
        return not self.hard_critical_unresolved

    def require_scoring_ready(self) -> None:
        if not self.ready_for_scoring:
            pending = ", ".join(f"{asset}:{key}" for asset, key in self.hard_critical_unresolved)
            raise AcquisitionResolutionRequired(f"hard-critical event scan resolution required: {pending}")

    assert_ready_for_scoring = require_scoring_ready

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.as_dict(),
            "results": [item.as_dict() for item in self.results],
            "web_fallbacks": [item.as_dict() for item in self.web_fallbacks],
            "summary": dict(self.summary or {}),
            "attempts": [dict(item) for item in self.attempts],
            "event_scan_requests": [item.as_dict() for item in self.event_scan_requests],
            "event_scans": [item.as_dict() for item in self.event_scans],
            "requires_external_resolution": self.requires_external_resolution,
            "pending_event_scans": [item.as_dict() for item in self.pending_event_scans],
            "pending_web_fallbacks": [item.as_dict() for item in self.pending_web_fallbacks],
            "hard_critical_unresolved": [list(item) for item in self.hard_critical_unresolved],
            "ready_for_scoring": self.ready_for_scoring,
        }


def resolve_fetch_mode(value: FetchMode | str | None = None) -> FetchMode:
    if value is not None:
        return FetchMode.parse(value)
    import os

    return FetchMode.parse(os.environ.get("CRYPTO_PORTFOLIO_FETCH_MODE", "AUTO"))


fetch_mode_from_env = resolve_fetch_mode


def _now(value: str | datetime | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "now")


def _observations(value: Any) -> list[MetricObservation]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if "observations" in value:
            value = value["observations"]
        elif "metric_key" in value:
            value = (value,)
        else:
            flattened: list[Any] = []
            for item in value.values():
                if isinstance(item, Mapping) and "metric_key" not in item:
                    flattened.extend(item.values())
                else:
                    flattened.append(item)
            value = flattened
    if isinstance(value, MetricObservation):
        return [value]
    if isinstance(value, (str, bytes)):
        raise ValueError("observations must be a sequence")
    return [item if isinstance(item, MetricObservation) else MetricObservation.from_mapping(item) for item in value]


def _active_observation_source(observation: MetricObservation, as_of: str | datetime) -> bool:
    """Keep retired provider/methodology points readable but out of live cache."""
    source = observation.source.strip().lower()
    if source == "coinglass":
        return False
    if source == "defillama" and observation.metric_key in {
        "valuation.market_cap", "valuation.fdv", "valuation.fdv_market_cap_ratio",
    }:
        return False
    if source == "binance" and observation.metric_key == "derivatives.futures_basis_annualized":
        return current_delivery_basis(observation.metadata, as_of)
    return True


def format_acquisition_summary(summary: Mapping[str, Any]) -> str:
    """Render compact acquisition telemetry without raw provider payloads."""
    return "\n".join((
        "Acquisition Summary",
        f"Metrics requested: {summary.get('metrics_requested', summary.get('requested', 0))}",
        f"Fresh observation hits: {summary.get('fresh_observation_hits', 0)}",
        f"Provider cache hits: {summary.get('provider_cache_hits', 0)}",
        f"API requests: {summary.get('api_requests', 0)}",
        f"API-derived metrics: {summary.get('api_derived_metrics', 0)}",
        f"Provider fallbacks: {summary.get('provider_fallbacks', 0)}",
        f"Web fallbacks: {summary.get('web_fallbacks', 0)}",
        f"Event scan source requests: {summary.get('event_scan_requests', 0)}",
        f"Event scans completed: {summary.get('event_scans', 0)}",
        f"Provider failures: {summary.get('provider_failures_by_error_code', {})}",
        f"Failed after all fallbacks: {summary.get('failed_after_fallbacks', 0)}",
    ))


def _expand_derived_dependencies(plan: MetricCollectionPlan) -> MetricCollectionPlan:
    """Fetch dependencies for a direct derived-metric request without exposing them as results."""
    identities = {(request.asset, request.metric_key) for request in plan.requests}
    additions: list[MetricRequest] = []
    for request in plan.requests:
        if request.metric_key.startswith("relative.return_vs_btc_") and request.asset != "BTC":
            horizon = request.metric_key.rsplit("_", 1)[-1]
            dependency_identities = ((request.asset, f"market.return_{horizon}"), ("BTC", f"market.return_{horizon}"))
        else:
            dependency_identities = tuple(
                (request.asset, dependency)
                for dependency in _DERIVED_DEPENDENCIES.get(request.metric_key, ())
            )
        for identity in dependency_identities:
            dependency = identity[1]
            if identity in identities or not metric_definition(dependency).applies_to(request.asset):
                continue
            identities.add(identity)
            additions.append(MetricRequest(
                identity[0],
                dependency,
                review_type=plan.review_type,
                reason=f"derived dependency for {request.metric_key}",
            ))
    if not additions:
        return plan
    return MetricCollectionPlan(
        review_type=plan.review_type,
        requests=(*plan.requests, *additions),
        assets=plan.assets,
        discovery_required_assets=plan.discovery_required_assets,
        collector_model=plan.collector_model,
    )


class AcquisitionManager:
    """Resolve a metric plan using local observations, provider caches, then APIs."""

    def __init__(
        self,
        router: ProviderRouter | None = None,
        *,
        observation_path: str | Path | None = None,
        event_path: str | Path | None = None,
        config: Mapping[str, Any] | None = None,
        persist: bool = True,
        fetch_mode: FetchMode | str | None = None,
        event_scanner: EventScanner | None = None,
    ) -> None:
        self.config = dict(config or load_provider_config())
        self.router = router or ProviderRouter(config=self.config)
        self.observation_path = observation_path
        self.event_path = event_path
        self.persist = persist
        self.fetch_mode = resolve_fetch_mode(fetch_mode)
        self.event_scanner = event_scanner or EventScanner()

    def run(
        self,
        plan: MetricCollectionPlan | Mapping[str, Any],
        *,
        mode: FetchMode | str | None = None,
        fetch_mode: FetchMode | str | None = None,
        as_of: str | datetime | None = None,
        now: str | datetime | None = None,
        cached_observations: Iterable[MetricObservation | Mapping[str, Any]] | None = None,
        persist: bool | None = None,
        event_scan_results: Mapping[Any, EventScanResult | Mapping[str, Any]] | Iterable[EventScanResult | Mapping[str, Any]] | None = None,
        event_source_scan_responses: Mapping[Any, EventSourceScanResponse | Mapping[str, Any]] | Iterable[EventSourceScanResponse | Mapping[str, Any]] | None = None,
        event_source_fetcher: Callable[[EventSourceScanRequest], Any] | None = None,
    ) -> AcquisitionResult:
        requested_model = plan if isinstance(plan, MetricCollectionPlan) else MetricCollectionPlan.from_mapping(plan)
        model = _expand_derived_dependencies(requested_model)
        selected_mode = resolve_fetch_mode(fetch_mode if fetch_mode is not None else (mode if mode is not None else self.fetch_mode))
        current = _now(now)
        cutoff = as_of if as_of is not None else current
        local = _observations(cached_observations)
        if cached_observations is None:
            local = read_metric_observations(self.observation_path, invalid=[])
        reusable: dict[tuple[str, str], MetricObservation] = {}
        stale: dict[tuple[str, str], MetricObservation] = {}
        preflight_skips: dict[tuple[str, str], str] = {}
        pending: list[MetricRequest] = []
        historical_liveness: set[tuple[str, str]] = set()
        fresh_hits = 0
        for request in model.requests:
            identity = (request.asset, request.metric_key)
            if not request.definition.applies_to(request.asset):
                continue
            availability = metric_availability(request.asset, request.metric_key)
            if availability.reason_code == "METHODOLOGY_NOT_DEFINED":
                preflight_skips[identity] = skip_reason(availability)
                continue
            candidate = latest_usable_observation(
                request.asset,
                request.metric_key,
                as_of=cutoff,
                observations=local,
                max_age_seconds=(
                    metric_reuse_ttl_seconds(
                        request.metric_key,
                        self.router.config.get("cache_ttl_seconds"),
                    )
                    if (
                        metric_is_mutable(request.metric_key)
                        and provider_chain(request.metric_key, request.asset)
                        and not (
                            as_of is not None
                            and request.metric_key in {"valuation.market_cap", "valuation.fdv"}
                        )
                    )
                    else None
                ),
            )
            if (
                candidate is not None
                and _active_observation_source(candidate, cutoff)
                and (selected_mode != FetchMode.REFRESH or not metric_is_mutable(request.metric_key))
            ):
                reusable[identity] = candidate
                fresh_hits += 1
                continue
            if local:
                candidates = [
                    item for item in local
                    if item.asset == request.asset
                    and item.metric_key == request.metric_key
                    and not (
                        request.metric_key == "risk.chain_liveness_status"
                        and as_of is not None
                        and parse_timestamp(item.observed_at) > parse_timestamp(normalize_timestamp(
                            as_of.isoformat() if isinstance(as_of, datetime) else as_of,
                            "as_of",
                        ))
                    )
                ]
                if candidates:
                    stale[identity] = max(candidates, key=lambda item: (item.observed_at, item.observation_id))
            if (
                request.metric_key == "risk.chain_liveness_status"
                and as_of is not None
                and parse_timestamp(normalize_timestamp(
                    as_of.isoformat() if isinstance(as_of, datetime) else as_of,
                    "as_of",
                )) < parse_timestamp(current)
            ):
                historical_liveness.add(identity)
                continue
            pending.append(request)

        self._synchronize_relative_dependencies(model, reusable, pending)
        provider_requests = self.router.build_requests(pending, as_of=as_of, now=current)
        routed = self.router.collect(provider_requests, mode=selected_mode, as_of=as_of, now=current)
        routed_values = {
            (str(item.get("asset", "")).strip().upper(), str(item.get("metric_key", "")).strip().lower()): item
            for item in routed.observations
        }
        relative_values, relative_reasons = self._derive_relative_observations(
            model.requests,
            reusable,
            routed_values,
            fetched_at=current,
            series_by_identity=self._relative_series_inputs(model.requests, reusable, routed_values),
            as_of=as_of,
        )
        routed_values.update(relative_values)
        routed_values.update(self._derive_flow_state_observation(
            model.requests,
            reusable,
            routed_values,
            fetched_at=current,
        ))
        derived_values, derived_reasons = derive_metric_observations(
            model.requests,
            reusable,
            routed_values,
            fetched_at=current,
            as_of=as_of,
        )
        routed_values.update(derived_values)
        routed_reasons = {
            (str(item.get("asset", "")).strip().upper(), str(item.get("metric_key", "")).strip().lower()): str(item.get("reason", ""))
            for item in routed.unresolved_details
        }
        routed_reasons.update(relative_reasons)
        routed_reasons.update(derived_reasons)
        routed_diagnostics = {
            (str(item.get("asset", "")).strip().upper(), str(item.get("metric_key", "")).strip().lower()): item
            for item in routed.unresolved_details
            if isinstance(item, Mapping)
        }
        provider_failure_identities = {
            (attempt.asset, metric_key)
            for attempt in routed.attempts
            if attempt.error_code not in (None, *_PROVIDER_SKIP_ERROR_CODES)
            for metric_key in attempt.metric_keys
        }
        scanner = self.event_scanner
        if event_scan_results is not None and event_source_scan_responses is not None:
            raise ValueError("provide only one of event_scan_results or event_source_scan_responses")
        source_response_input = event_source_scan_responses
        if source_response_input is None and self._contains_event_source_responses(event_scan_results):
            source_response_input = event_scan_results
            event_scan_results = None
        event_scans = self._coerce_event_scans(event_scan_results)
        source_responses = self._coerce_event_source_responses(source_response_input)
        event_errors: dict[tuple[str, str], str] = {}
        missing_event_identities = [
            (request.asset, request.metric_key)
            for request in model.requests
            if event_metric_category(request.metric_key) is not None
            and (request.asset, request.metric_key) not in reusable
            and (request.asset, event_metric_category(request.metric_key)) not in event_scans
        ]
        event_groups: dict[tuple[str, str], None] = {}
        regulatory_assets: list[str] = []
        for asset, metric_key in missing_event_identities:
            category = event_metric_category(metric_key)
            if category == "regulatory":
                if asset not in regulatory_assets:
                    regulatory_assets.append(asset)
            elif category is not None:
                event_groups[(asset, category)] = None
        shared_regulatory = event_scans.get(("MARKET", "regulatory"))
        if isinstance(shared_regulatory, EventScanResult):
            for asset in regulatory_assets:
                event_scans[(asset, "regulatory")] = self._map_shared_regulatory(shared_regulatory, asset)
        scan_as_of = as_of if as_of is not None else current
        if source_responses:
            for asset, category in event_groups:
                requests = scanner.build_requests(asset, category, scan_as_of, review_type=model.review_type)
                responses = self._responses_for_requests(requests, source_responses)
                if not responses:
                    continue
                try:
                    event_scans[(asset, category)] = scanner.scan(
                        asset,
                        category,
                        scan_as_of,
                        responses=responses,
                        review_type=model.review_type,
                        fetch_mode=selected_mode,
                    )
                except Exception as exc:
                    event_errors[(asset, category)] = f"event source response rejected: {redact_secrets(str(exc))}"
            if regulatory_assets and not all((asset, "regulatory") in event_scans for asset in regulatory_assets):
                requests = scanner.build_requests("MARKET", "regulatory", scan_as_of, review_type=model.review_type)
                responses = self._responses_for_requests(requests, source_responses)
                if responses:
                    try:
                        event_scans.update({
                            (asset, "regulatory"): scan
                            for asset, scan in scanner.scan_shared_regulatory(
                                regulatory_assets,
                                scan_as_of,
                                responses=responses,
                                review_type=model.review_type,
                                fetch_mode=selected_mode,
                            ).items()
                        })
                    except Exception as exc:
                        reason = f"shared regulatory event response rejected: {redact_secrets(str(exc))}"
                        event_errors.update({(asset, "regulatory"): reason for asset in regulatory_assets})
        if event_source_fetcher is not None and selected_mode != FetchMode.CACHE_ONLY:
            for asset, category in event_groups:
                try:
                    event_scans[(asset, category)] = scanner.scan(
                        asset, category, scan_as_of,
                        source_fetcher=event_source_fetcher,
                        review_type=model.review_type,
                        fetch_mode=selected_mode,
                    )
                except Exception as exc:
                    event_errors[(asset, category)] = f"event source scan failed: {redact_secrets(str(exc))}"
            if regulatory_assets:
                try:
                    event_scans.update({
                        (asset, "regulatory"): scan
                        for asset, scan in scanner.scan_shared_regulatory(
                            regulatory_assets, scan_as_of,
                            source_fetcher=event_source_fetcher,
                            review_type=model.review_type,
                            fetch_mode=selected_mode,
                        ).items()
                    })
                except Exception as exc:
                    reason = f"shared regulatory event scan failed: {redact_secrets(str(exc))}"
                    event_errors.update({(asset, "regulatory"): reason for asset in regulatory_assets})
        event_scan_requests: list[EventSourceScanRequest] = []
        request_identities = {(request.asset, request.metric_key) for request in model.requests}
        for asset, category in event_groups:
            if (asset, category) in event_scans:
                continue
            if selected_mode != FetchMode.CACHE_ONLY:
                event_scan_requests.extend(scanner.build_requests(
                    asset, category, scan_as_of, review_type=model.review_type,
                ))
        if regulatory_assets and selected_mode != FetchMode.CACHE_ONLY:
            if not all((asset, "regulatory") in event_scans for asset in regulatory_assets):
                event_scan_requests.extend(scanner.build_requests(
                    "MARKET", "regulatory", scan_as_of, review_type=model.review_type,
                ))
        unique_event_requests = {
            (item.asset, item.category, item.source_id): item for item in event_scan_requests
        }
        event_scan_requests = list(unique_event_requests.values())
        for (asset, category), scan in tuple(event_scans.items()):
            if not isinstance(scan, EventScanResult):
                continue
            key = _EVENT_METRIC_KEYS.get(category)
            if key is None or (asset, key) not in request_identities:
                continue
            if scan.status == "INSUFFICIENT_SOURCE_COVERAGE":
                event_errors[(asset, category)] = (
                    "event scan returned INSUFFICIENT_SOURCE_COVERAGE; required sources were not all reachable"
                )
                continue
            try:
                routed_values[(asset, key)] = scanner.observation(scan, key, fetched_at=current)
            except ValueError as exc:
                event_errors[(asset, category)] = f"event scan result rejected: {exc}"
        results: list[NormalizedMetricResult] = []
        web_fallbacks: list[WebFallbackRequest] = []
        should_persist = self.persist if persist is None else persist

        def add_web_fallback(request: MetricRequest, reason: str) -> None:
            if (
                selected_mode == FetchMode.CACHE_ONLY
                or not self.router.allow_web
                or event_metric_category(request.metric_key) is not None
                or request.metric_key == "risk.chain_liveness_status"
                or request.metric_key.startswith(("macro.", "btc_valuation."))
            ):
                return
            chain = provider_chain(request.metric_key, request.asset)
            web_fallbacks.append(WebFallbackRequest(
                request.asset,
                request.metric_key,
                reason,
                (*chain, "official/current sources") if chain else ("official/current sources",),
            ))

        for request in requested_model.requests:
            identity = (request.asset, request.metric_key)
            raw = reusable.get(identity)
            if raw is not None:
                payload = raw.as_dict()
                payload["metadata"] = {**(raw.metadata or {}), "source_mode": "CACHE_OBSERVATION"}
                payload["timestamp"] = current
                normalized = normalize_metric_result(payload, now=current, as_of=as_of)
            elif not request.definition.applies_to(request.asset):
                normalized = normalize_metric_result({
                    "timestamp": current,
                    "asset": request.asset,
                    "metric_key": request.metric_key,
                    "status": "NOT_APPLICABLE",
                    "reason": "metric is outside its registry asset scope",
                    "source": "python-applicability",
                }, now=current)
            elif identity in preflight_skips:
                normalized = self._skipped(request, current, preflight_skips[identity])
            elif identity in routed_values:
                try:
                    normalized = normalize_metric_result(
                        routed_values[identity], now=current, as_of=as_of,
                        review_type=model.review_type,
                    )
                except ValueError as exc:
                    reason = f"provider value rejected: {exc}"
                    add_web_fallback(request, reason)
                    normalized = self._failure(request, current, reason)
                else:
                    if normalized.observation is not None and normalized.observation.freshness != "CURRENT":
                        reason = "provider returned an observation outside the registry freshness window"
                        add_web_fallback(request, reason)
                        normalized = self._failure(request, current, reason, stale=True)
            else:
                old = stale.get(identity)
                diagnostic = routed_diagnostics.get(identity)
                category = event_metric_category(request.metric_key)
                if identity in historical_liveness:
                    reason = f"historical chain liveness evidence unavailable at {cutoff}"
                    diagnostic = {
                        "provider": "chain_liveness",
                        "error_code": "HISTORICAL_LIVENESS_UNAVAILABLE",
                        "detail": "current chain state is not valid historical evidence",
                    }
                elif category is not None:
                    reason = event_errors.get(
                        (request.asset, category),
                        f"{category} event scan requires the returned authoritative source plan",
                    )
                    if diagnostic is None:
                        diagnostic = {"detail": reason}
                elif not provider_chain(request.metric_key, request.asset):
                    reason = "no configured structured provider route for this metric"
                    diagnostic = {"error_code": "NO_PROVIDER_ROUTE", "detail": reason}
                else:
                    reason = routed_reasons.get(identity, "no configured provider returned a usable observation")
                if old is not None:
                    failure_reason = reason
                    refresh = routed_diagnostics.get(identity)
                    refresh_reason = str(refresh.get("reason", "")).strip() if refresh else failure_reason
                    reason = f"last observation is stale as of {cutoff}"
                    if refresh_reason:
                        reason += f"; refresh failed: {refresh_reason}"
                if diagnostic and diagnostic.get("error_code") == "PROVIDER_NOT_APPLICABLE":
                    normalized = normalize_metric_result({
                        "timestamp": current,
                        "asset": request.asset,
                        "metric_key": request.metric_key,
                        "status": "NOT_APPLICABLE",
                        "reason": reason,
                        "source": "provider-router",
                    }, now=current)
                else:
                    availability = metric_availability(request.asset, request.metric_key)
                    provider_failed = (
                        identity in provider_failure_identities
                        or (
                            diagnostic is not None
                            and diagnostic.get("error_code") not in _PROVIDER_SKIP_ERROR_CODES
                        )
                    )
                    if availability.is_skippable and provider_failed:
                        normalized = self._failure(
                            request,
                            current,
                            reason,
                            stale=old is not None,
                            diagnostic=diagnostic,
                            previous=old,
                        )
                    elif availability.is_skippable:
                        normalized = self._skipped(
                            request,
                            current,
                            skip_reason(availability, reason),
                            diagnostic=diagnostic,
                        )
                    else:
                        if category is None and request.metric_key != "risk.chain_liveness_status":
                            add_web_fallback(request, reason)
                        normalized = self._failure(
                            request,
                            current,
                            reason,
                            stale=old is not None,
                            diagnostic=diagnostic,
                            previous=old,
                        )
            if should_persist:
                persist_metric_result(
                    normalized,
                    observation_path=self.observation_path,
                    event_path=self.event_path,
                )
            results.append(normalized)

        events = tuple(item.event for item in results)
        summary = collection_summary(events, review_type=model.review_type)
        provider_failures = Counter(
            str(attempt.error_code)
            for attempt in routed.attempts
            if attempt.error_code
        )
        completed_event_groups = {
            ("MARKET" if scan.category == "regulatory" else scan.asset, scan.category)
            for scan in event_scans.values()
        }
        event_sources_required = len(event_scan_requests)
        event_sources_reachable = 0
        for asset, category in completed_event_groups:
            requests = scanner.build_requests(asset, category, scan_as_of, review_type=model.review_type)
            event_sources_required += len(requests)
            scan = next(
                (
                    item for (scan_asset, scan_category), item in event_scans.items()
                    if scan_category == category
                    and ("MARKET" if scan_category == "regulatory" else scan_asset) == asset
                ),
                None,
            )
            if scan is not None:
                event_sources_reachable += round(scan.coverage * len(requests))
        summary.update({
            "metrics_requested": len(requested_model.requests),
            "fresh_observation_hits": fresh_hits,
            "provider_cache_hits": routed.provider_cache_hits,
            "api_requests": routed.api_requests,
            "api_derived_metrics": routed.api_derived_metrics,
            "provider_fallbacks": routed.provider_fallbacks,
            "web_fallbacks": len(web_fallbacks),
            "failed_after_fallbacks": sum(item.status in {"FAILED", "STALE", "CONFLICT"} for item in events),
            "fetch_mode": selected_mode.value,
            "event_scan_requests": len(event_scan_requests),
            "event_scans": len(event_scans),
            "provider_failures_by_error_code": dict(sorted(provider_failures.items())),
            "event_sources_reachable": event_sources_reachable,
            "event_sources_required": event_sources_required,
        })
        return AcquisitionResult(
            requested_model,
            tuple(results),
            tuple(web_fallbacks),
            summary,
            tuple(attempt.as_dict() for attempt in routed.attempts),
            tuple(event_scan_requests),
            tuple(event_scans.values()),
        )

    collect = run
    acquire = run

    @staticmethod
    def _failure(
        request: MetricRequest,
        timestamp: str,
        reason: str,
        *,
        stale: bool = False,
        diagnostic: Mapping[str, Any] | None = None,
        previous: MetricObservation | None = None,
    ) -> NormalizedMetricResult:
        diagnostic = diagnostic or {}
        return normalize_metric_result({
            "timestamp": timestamp,
            "asset": request.asset,
            "metric_key": request.metric_key,
            "status": "STALE" if stale else "FAILED",
            "reason": reason,
            "source": "provider-router",
            "last_observation_id": previous.observation_id if previous else None,
            "last_observation_at": previous.observed_at if previous else None,
            "refresh_provider": diagnostic.get("provider"),
            "refresh_endpoint": diagnostic.get("endpoint"),
            "refresh_error_code": diagnostic.get("error_code"),
            "refresh_error_detail": diagnostic.get("detail"),
        }, now=timestamp)

    @staticmethod
    def _skipped(
        request: MetricRequest,
        timestamp: str,
        reason: str,
        *,
        diagnostic: Mapping[str, Any] | None = None,
    ) -> NormalizedMetricResult:
        diagnostic = diagnostic or {}
        return normalize_metric_result({
            "timestamp": timestamp,
            "asset": request.asset,
            "metric_key": request.metric_key,
            "status": "SKIPPED",
            "reason": reason,
            "source": "python-availability",
            "refresh_provider": diagnostic.get("provider"),
            "refresh_endpoint": diagnostic.get("endpoint"),
            "refresh_error_code": diagnostic.get("error_code"),
            "refresh_error_detail": diagnostic.get("detail"),
        }, now=timestamp)

    @staticmethod
    def _relative_scalar_compatible(
        asset: MetricObservation | None,
        btc: MetricObservation | None,
    ) -> bool:
        if asset is None or btc is None or asset.source != btc.source:
            return False
        try:
            if parse_timestamp(asset.observed_at).date() != parse_timestamp(btc.observed_at).date():
                return False
        except ValueError:
            return False
        if asset.period != btc.period:
            return False
        for field in ("venue", "market", "quote_currency"):
            left = (asset.metadata or {}).get(field)
            right = (btc.metadata or {}).get(field)
            if left is None or right is None or str(left).strip().lower() != str(right).strip().lower():
                return False
        return True

    @classmethod
    def _synchronize_relative_dependencies(
        cls,
        plan: MetricCollectionPlan,
        reusable: dict[tuple[str, str], MetricObservation],
        pending: list[MetricRequest],
    ) -> None:
        """Invalidate both scalar inputs when a relative cohort is not aligned."""
        pending_identities = {(request.asset, request.metric_key) for request in pending}
        requests_by_identity = {(request.asset, request.metric_key): request for request in plan.requests}
        for request in plan.requests:
            if not request.metric_key.startswith("relative.return_vs_btc_") or request.asset == "BTC":
                continue
            horizon = request.metric_key.rsplit("_", 1)[-1]
            dependencies = (
                (request.asset, f"market.return_{horizon}"),
                ("BTC", f"market.return_{horizon}"),
            )
            asset_value = reusable.get(dependencies[0])
            btc_value = reusable.get(dependencies[1])
            if cls._relative_scalar_compatible(asset_value, btc_value):
                continue
            for identity in dependencies:
                reusable.pop(identity, None)
                dependency = requests_by_identity.get(identity)
                if dependency is not None and identity not in pending_identities:
                    pending.append(dependency)
                    pending_identities.add(identity)

    def _relative_series_inputs(
        self,
        requests: Iterable[MetricRequest],
        reusable: Mapping[tuple[str, str], MetricObservation],
        routed: Mapping[tuple[str, str], Mapping[str, Any]],
    ) -> dict[tuple[str, str], Any]:
        """Load cached normalized OHLCV for routed relative-return inputs."""
        identities = {
            (request.asset, f"market.return_{request.metric_key.rsplit('_', 1)[-1]}")
            for request in requests
            if request.metric_key.startswith("relative.return_vs_btc_") and request.asset != "BTC"
        }
        identities.update(("BTC", identity[1]) for identity in tuple(identities))
        result: dict[tuple[str, str], Any] = {}
        cache = getattr(self.router, "cache", None)
        if cache is None:
            return result
        for identity in identities:
            observation = reusable.get(identity) or routed.get(identity)
            if observation is None:
                continue
            if isinstance(observation, MetricObservation):
                asset = observation.asset
                metadata = dict(observation.metadata or {})
                source = observation.source
            else:
                asset = str(observation.get("asset", identity[0])).strip().upper()
                metadata = dict(observation.get("metadata") or {})
                source = str(observation.get("source", "")).strip().lower()
            if not metadata.get("ohlcv_hash"):
                continue
            market = str(metadata.get("market", "spot")).strip().lower()
            quote = str(metadata.get("quote_currency", "USDT")).strip().upper()
            providers = tuple(dict.fromkeys((source, "binance", "bybit")))
            for provider in providers:
                if provider not in {"binance", "bybit"}:
                    continue
                try:
                    series = cache.load_series(provider, asset, "1D", market=market, quote_currency=quote)
                except ValueError:
                    continue
                if series is not None:
                    result[identity] = series
                    break
        return result

    @staticmethod
    def _coerce_event_scans(
        value: Mapping[Any, EventScanResult | Mapping[str, Any]] | Iterable[EventScanResult | Mapping[str, Any]] | None,
    ) -> dict[tuple[str, str], EventScanResult]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            items = value.items()
        else:
            items = ((None, item) for item in value)
        result: dict[tuple[str, str], EventScanResult] = {}
        for key, raw in items:
            scan = raw if isinstance(raw, EventScanResult) else EventScanResult.from_mapping(raw)
            identity = (scan.asset, scan.category)
            if isinstance(key, tuple) and len(key) == 2:
                target = str(key[0]).strip().upper()
                category = str(key[1]).strip().lower()
                if scan.asset == "MARKET" and category == "regulatory" and target != "MARKET":
                    scan = AcquisitionManager._map_shared_regulatory(scan, target)
                identity = (target, category)
            result[identity] = scan
        return result

    @staticmethod
    def _contains_event_source_responses(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, EventSourceScanResponse):
            return True
        if isinstance(value, Mapping):
            if "source_id" in value and "reachable" in value:
                return True
            return any(AcquisitionManager._contains_event_source_responses(item) for item in value.values())
        if isinstance(value, (str, bytes)):
            return False
        try:
            return any(AcquisitionManager._contains_event_source_responses(item) for item in value)
        except TypeError:
            return False

    @staticmethod
    def _coerce_event_source_responses(
        value: Mapping[Any, EventSourceScanResponse | Mapping[str, Any]] | Iterable[EventSourceScanResponse | Mapping[str, Any]] | None,
    ) -> tuple[EventSourceScanResponse, ...]:
        if value is None:
            return ()
        if isinstance(value, EventSourceScanResponse):
            return (value,)
        if isinstance(value, Mapping):
            if "source_id" in value:
                value = (value,)
            else:
                flattened: list[Any] = []
                for item in value.values():
                    if isinstance(item, Mapping) and "source_id" not in item and not isinstance(item, EventSourceScanResponse):
                        flattened.extend(item.values())
                    elif isinstance(item, (list, tuple)):
                        flattened.extend(item)
                    else:
                        flattened.append(item)
                value = flattened
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            raise ValueError("event source scan responses must be a sequence or source mapping")
        return tuple(
            item if isinstance(item, EventSourceScanResponse) else EventSourceScanResponse.from_mapping(item)
            for item in value
        )

    @staticmethod
    def _responses_for_requests(
        requests: Iterable[EventSourceScanRequest],
        responses: Iterable[EventSourceScanResponse],
    ) -> tuple[EventSourceScanResponse, ...]:
        requests = tuple(requests)
        by_source = {response.source_id: response for response in responses}
        return tuple(
            by_source.get(
                request.source_id,
                EventSourceScanResponse(
                    source_id=request.source_id,
                    reachable=False,
                    checked_at=request.as_of,
                    items=(),
                    error="external event source stage returned no response",
                ),
            )
            for request in requests
        )

    @staticmethod
    def _map_shared_regulatory(scan: EventScanResult, asset: str) -> EventScanResult:
        if scan.asset == asset:
            return scan
        if scan.asset != "MARKET" or scan.category != "regulatory":
            raise ValueError("only a shared MARKET regulatory scan can be mapped to an asset")
        events = tuple(
            item for item in scan.material_events
            if not item.get("affected_assets")
            or asset in item.get("affected_assets", ())
            or "MARKET" in item.get("affected_assets", ())
        )
        return EventScanResult(
            asset=asset,
            category="regulatory",
            scan_as_of=scan.scan_as_of,
            lookback_days=scan.lookback_days,
            sources_checked=scan.sources_checked,
            material_events=events,
            coverage=scan.coverage,
            confidence=scan.confidence,
        )

    @staticmethod
    def _derive_flow_state_observation(
        requests: Iterable[MetricRequest],
        reusable: Mapping[tuple[str, str], MetricObservation],
        routed: Mapping[tuple[str, str], Mapping[str, Any]],
        *,
        fetched_at: str,
    ) -> dict[tuple[str, str], Mapping[str, Any]]:
        identity = ("MARKET", "market.flow_state")
        if not any((request.asset, request.metric_key) == identity for request in requests):
            return {}
        flow_identity = ("MARKET", "flows.etf_net_1d")
        observation = reusable.get(flow_identity)
        if observation is not None:
            value, observed_at, observation_id, source, metadata, confidence = (
                observation.value,
                observation.observed_at,
                observation.observation_id,
                observation.source,
                dict(observation.metadata or {}),
                observation.confidence,
            )
        else:
            raw = routed.get(flow_identity)
            if raw is None:
                return {}
            value = raw.get("value")
            observed_at = raw.get("observed_at")
            observation_id = raw.get("observation_id")
            source = raw.get("source")
            metadata = dict(raw.get("metadata") or {})
            confidence = str(raw.get("confidence", "MEDIUM")).upper()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not isinstance(observed_at, str)
            or not isinstance(source, str)
        ):
            return {}
        return {identity: {
            "asset": "MARKET",
            "metric_key": "market.flow_state",
            "value": classify_flow_state(value),
            "period": "1d",
            "observed_at": observed_at,
            "fetched_at": fetched_at,
            "source": "python-derived",
            "confidence": confidence,
            "summary": "Derived from the latest completed MARKET ETF 1D net flow.",
            "metadata": {
                "source_mode": "DERIVED",
                "calculation": "classify_flow_state(flows.etf_net_1d)",
                "source_observation_id": observation_id,
                "source": source,
                "source_metadata": metadata,
            },
        }}

    @staticmethod
    def _derive_relative_observations(
        requests: Iterable[MetricRequest],
        reusable: Mapping[tuple[str, str], MetricObservation],
        routed: Mapping[tuple[str, str], Mapping[str, Any]],
        *,
        fetched_at: str,
        series_by_identity: Mapping[tuple[str, str], Any] | None = None,
        as_of: str | datetime | None = None,
    ) -> tuple[dict[tuple[str, str], Mapping[str, Any]], dict[tuple[str, str], str]]:
        """Derive BTC-relative returns from one aligned completed-candle cohort."""
        result: dict[tuple[str, str], Mapping[str, Any]] = {}
        reasons: dict[tuple[str, str], str] = {}
        series_by_identity = series_by_identity or {}

        def value_for(identity: tuple[str, str]) -> tuple[Any, str | None, str | None, str | None, Mapping[str, Any]]:
            observation = reusable.get(identity)
            if observation is not None:
                return observation.value, observation.observed_at, observation.observation_id, observation.source, dict(observation.metadata or {})
            raw = routed.get(identity)
            if raw is None:
                return None, None, None, None, {}
            return raw.get("value"), raw.get("observed_at"), raw.get("observation_id"), raw.get("source"), dict(raw.get("metadata") or {})

        for request in requests:
            key = request.metric_key
            if not key.startswith("relative.return_vs_btc_") or request.asset == "BTC":
                continue
            horizon = key.rsplit("_", 1)[-1]
            asset_identity = (request.asset, f"market.return_{horizon}")
            btc_identity = ("BTC", f"market.return_{horizon}")
            asset_value, asset_observed, asset_id, asset_source, asset_metadata = value_for(asset_identity)
            btc_value, btc_observed, btc_id, btc_source, btc_metadata = value_for(btc_identity)
            output_identity = (request.asset, key)
            asset_series = series_by_identity.get(asset_identity)
            btc_series = series_by_identity.get(btc_identity)
            if asset_series is not None or btc_series is not None:
                if asset_series is None or btc_series is None:
                    reasons[output_identity] = "DERIVED_INPUT_UNAVAILABLE: both asset and BTC OHLCV series are required"
                    continue
                try:
                    aligned = derive_aligned_relative_return(
                        asset_series,
                        btc_series,
                        horizon_days=int(horizon[:-1]),
                        as_of=as_of,
                    )
                except ValueError as exc:
                    reasons[output_identity] = f"DERIVED_INPUT_UNAVAILABLE: {exc}"
                    continue
                if aligned is None:
                    reasons[output_identity] = "INSUFFICIENT_ALIGNED_HISTORY: no common completed candle supports the requested calendar horizon"
                    continue
                result[output_identity] = {
                    "asset": request.asset,
                    "metric_key": key,
                    "value": aligned["value"],
                    "unit": "fraction",
                    "period": horizon,
                    "observed_at": aligned["observed_at"],
                    "fetched_at": fetched_at,
                    "source": "python-derived",
                    "confidence": "HIGH",
                    "metadata": {
                        "source_mode": "DERIVED",
                        "calculation": "aligned asset return minus aligned BTC return",
                        "venue": aligned["venue"],
                        "market": aligned["market"],
                        "quote_currency": aligned["quote_currency"],
                        "horizon": horizon,
                        "common_anchor": aligned["common_anchor"],
                        "asset_ohlcv_hash": aligned["asset_ohlcv_hash"],
                        "btc_ohlcv_hash": aligned["btc_ohlcv_hash"],
                        "source_observation_ids": [item for item in (asset_id, btc_id) if item],
                    },
                }
                continue
            if (
                isinstance(asset_value, bool) or not isinstance(asset_value, (int, float))
                or isinstance(btc_value, bool) or not isinstance(btc_value, (int, float))
                or not math.isfinite(float(asset_value)) or not math.isfinite(float(btc_value))
                or float(asset_value) - float(btc_value) < -1
                or asset_observed is None or btc_observed is None
                or asset_source is None or btc_source is None or asset_source != btc_source
            ):
                reasons[output_identity] = "DERIVED_INPUT_UNAVAILABLE: asset and BTC returns are missing or incompatible"
                continue
            try:
                if parse_timestamp(asset_observed).date() != parse_timestamp(btc_observed).date():
                    reasons[output_identity] = "DERIVED_INPUT_UNAVAILABLE: asset and BTC returns do not share a completed daily anchor"
                    continue
            except ValueError:
                reasons[output_identity] = "DERIVED_INPUT_UNAVAILABLE: asset or BTC return timestamp is invalid"
                continue
            result[output_identity] = {
                "asset": request.asset,
                "metric_key": key,
                "value": float(asset_value) - float(btc_value),
                "unit": "fraction",
                "period": horizon,
                "observed_at": asset_observed,
                "fetched_at": fetched_at,
                "source": "python-derived",
                "confidence": "HIGH",
                "metadata": {
                    "source_mode": "DERIVED",
                    "calculation": "aligned asset return minus aligned BTC return",
                    "horizon": horizon,
                    "common_anchor": parse_timestamp(asset_observed).date().isoformat(),
                    "venue": asset_metadata.get("venue"),
                    "market": asset_metadata.get("market"),
                    "quote_currency": asset_metadata.get("quote_currency"),
                    "asset_ohlcv_hash": asset_metadata.get("ohlcv_hash"),
                    "btc_ohlcv_hash": btc_metadata.get("ohlcv_hash"),
                    "source_observation_ids": [item for item in (asset_id, btc_id) if item],
                    "source_metadata": {"asset": asset_metadata, "btc": btc_metadata},
                },
            }
        return result, reasons


MetricAcquisitionManager = AcquisitionManager


__all__ = [
    "AcquisitionManager",
    "AcquisitionResult",
    "AcquisitionResolutionRequired",
    "FetchMode",
    "MetricAcquisitionManager",
    "WebFallbackRequest",
    "format_acquisition_summary",
    "resolve_fetch_mode",
    "fetch_mode_from_env",
]
