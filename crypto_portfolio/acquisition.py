"""On-demand, cache-first metric acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from .data_collection import collection_summary
from .engine.metric_normalization import NormalizedMetricResult, normalize_metric_result, persist_metric_result
from .engine.metric_plan import MetricCollectionPlan, MetricRequest
from .metrics_registry import metric_definition
from .models.metrics_history import MetricObservation
from .models.time import normalize_timestamp, parse_timestamp
from .providers.base import FetchMode
from .providers.config import load_provider_config
from .providers.routes import metric_is_mutable, provider_chain
from .providers.router import ProviderRouter
from .state.metrics import latest_usable_observation, read_metric_observations


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

    @property
    def observations(self) -> tuple[MetricObservation, ...]:
        return tuple(item.observation for item in self.results if item.observation is not None)

    @property
    def events(self) -> tuple[Any, ...]:
        return tuple(item.event for item in self.results)

    @property
    def collection_results(self) -> tuple[NormalizedMetricResult, ...]:
        return self.results

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.as_dict(),
            "results": [item.as_dict() for item in self.results],
            "web_fallbacks": [item.as_dict() for item in self.web_fallbacks],
            "summary": dict(self.summary or {}),
            "attempts": [dict(item) for item in self.attempts],
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
        f"Failed after all fallbacks: {summary.get('failed_after_fallbacks', 0)}",
    ))


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
    ) -> None:
        self.config = dict(config or load_provider_config())
        self.router = router or ProviderRouter(config=self.config)
        self.observation_path = observation_path
        self.event_path = event_path
        self.persist = persist
        self.fetch_mode = resolve_fetch_mode(fetch_mode)

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
    ) -> AcquisitionResult:
        model = plan if isinstance(plan, MetricCollectionPlan) else MetricCollectionPlan.from_mapping(plan)
        selected_mode = resolve_fetch_mode(fetch_mode if fetch_mode is not None else (mode if mode is not None else self.fetch_mode))
        current = _now(now)
        cutoff = as_of if as_of is not None else current
        local = _observations(cached_observations)
        if cached_observations is None:
            local = read_metric_observations(self.observation_path, invalid=[])
        reusable: dict[tuple[str, str], MetricObservation] = {}
        stale: dict[tuple[str, str], MetricObservation] = {}
        pending: list[MetricRequest] = []
        fresh_hits = 0
        for request in model.requests:
            identity = (request.asset, request.metric_key)
            candidate = latest_usable_observation(
                request.asset,
                request.metric_key,
                as_of=cutoff,
                observations=local,
            )
            if candidate is not None and (selected_mode != FetchMode.REFRESH or not metric_is_mutable(request.metric_key)):
                reusable[identity] = candidate
                fresh_hits += 1
                continue
            if local:
                candidates = [
                    item for item in local
                    if item.asset == request.asset and item.metric_key == request.metric_key
                ]
                if candidates:
                    stale[identity] = max(candidates, key=lambda item: (item.observed_at, item.observation_id))
            pending.append(request)

        provider_requests = self.router.build_requests(pending, as_of=as_of, now=current)
        routed = self.router.collect(provider_requests, mode=selected_mode, as_of=as_of, now=current)
        routed_values = {
            (str(item.get("asset", "")).strip().upper(), str(item.get("metric_key", "")).strip().lower()): item
            for item in routed.observations
        }
        routed_values.update(self._derive_relative_observations(
            model.requests,
            reusable,
            routed_values,
            fetched_at=current,
        ))
        results: list[NormalizedMetricResult] = []
        web_fallbacks: list[WebFallbackRequest] = []
        should_persist = self.persist if persist is None else persist

        def add_web_fallback(request: MetricRequest, reason: str) -> None:
            if selected_mode == FetchMode.CACHE_ONLY or not self.router.allow_web:
                return
            chain = provider_chain(request.metric_key)
            web_fallbacks.append(WebFallbackRequest(
                request.asset,
                request.metric_key,
                reason,
                (*chain, "official/current sources") if chain else ("official/current sources",),
            ))

        for request in model.requests:
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
                reason = "no configured provider returned a usable observation"
                if old is not None:
                    reason = f"last observation is stale as of {cutoff}"
                add_web_fallback(request, reason)
                normalized = self._failure(request, current, reason, stale=old is not None)
            if should_persist:
                persist_metric_result(
                    normalized,
                    observation_path=self.observation_path,
                    event_path=self.event_path,
                )
            results.append(normalized)

        events = tuple(item.event for item in results)
        summary = collection_summary(events)
        summary.update({
            "metrics_requested": len(model.requests),
            "fresh_observation_hits": fresh_hits,
            "provider_cache_hits": routed.provider_cache_hits,
            "api_requests": routed.api_requests,
            "api_derived_metrics": routed.api_derived_metrics,
            "provider_fallbacks": routed.provider_fallbacks,
            "web_fallbacks": len(web_fallbacks),
            "failed_after_fallbacks": sum(item.status in {"FAILED", "STALE", "CONFLICT"} for item in events),
            "fetch_mode": selected_mode.value,
        })
        return AcquisitionResult(
            model,
            tuple(results),
            tuple(web_fallbacks),
            summary,
            tuple(attempt.as_dict() for attempt in routed.attempts),
        )

    collect = run
    acquire = run

    @staticmethod
    def _failure(request: MetricRequest, timestamp: str, reason: str, *, stale: bool = False) -> NormalizedMetricResult:
        return normalize_metric_result({
            "timestamp": timestamp,
            "asset": request.asset,
            "metric_key": request.metric_key,
            "status": "STALE" if stale else "FAILED",
            "reason": reason,
            "source": "provider-router",
        }, now=timestamp)

    @staticmethod
    def _derive_relative_observations(
        requests: Iterable[MetricRequest],
        reusable: Mapping[tuple[str, str], MetricObservation],
        routed: Mapping[tuple[str, str], Mapping[str, Any]],
        *,
        fetched_at: str,
    ) -> dict[tuple[str, str], Mapping[str, Any]]:
        """Derive BTC-relative returns from the same Python-owned OHLCV outputs."""
        result: dict[tuple[str, str], Mapping[str, Any]] = {}

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
            if (
                isinstance(asset_value, bool) or not isinstance(asset_value, (int, float))
                or isinstance(btc_value, bool) or not isinstance(btc_value, (int, float))
                or not math.isfinite(float(asset_value)) or not math.isfinite(float(btc_value))
                or float(asset_value) - float(btc_value) < -1
                or asset_observed is None or btc_observed is None
                or asset_source is None or btc_source is None or asset_source != btc_source
                or parse_timestamp(asset_observed).date() != parse_timestamp(btc_observed).date()
            ):
                continue
            result[(request.asset, key)] = {
                "asset": request.asset,
                "metric_key": key,
                "value": float(asset_value) - float(btc_value),
                "unit": "fraction",
                "period": horizon,
                "observed_at": max(asset_observed, btc_observed),
                "fetched_at": fetched_at,
                "source": "python-derived",
                "confidence": "HIGH",
                "metadata": {
                    "source_mode": "DERIVED",
                    "calculation": "asset return minus BTC return",
                    "source_observation_ids": [item for item in (asset_id, btc_id) if item],
                    "source_metadata": {"asset": asset_metadata, "btc": btc_metadata},
                },
            }
        return result


MetricAcquisitionManager = AcquisitionManager


__all__ = [
    "AcquisitionManager",
    "AcquisitionResult",
    "FetchMode",
    "MetricAcquisitionManager",
    "WebFallbackRequest",
    "format_acquisition_summary",
    "resolve_fetch_mode",
    "fetch_mode_from_env",
]
