"""Deterministic provider routing, fallback, and cache coordination."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ..models.market import OHLCVSeries
from ..models.time import normalize_timestamp
from .base import (
    FetchMode,
    ProviderCapabilities,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderRuntimeStatus,
    ProviderUnavailable,
    ProviderUnsupportedMetric,
)
from .cache import CacheCorruption, CacheExpired, ProviderCache, merge_ohlcv_series, missing_series_range, request_hash
from .config import load_provider_config, provider_api_key, provider_enabled
from .http import HttpClient, classify_transport_error, redact_secrets
from .routes import BASIS_METHODOLOGY, build_provider_requests, current_delivery_basis, provider_chain


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    dataset: str
    asset: str
    metric_keys: tuple[str, ...]
    status: str
    source_mode: str
    reason: str | None = None
    request_hash: str | None = None
    network_requests: int = 0
    endpoint: str | None = None
    method: str = "GET"
    attempt: int = 1
    error_code: str | None = None
    exception_class: str | None = None
    detail: str | None = None
    retryable: bool | None = None
    status_code: int | None = None

    @property
    def success(self) -> bool:
        return self.status == "SUCCESS"

    @property
    def final(self) -> bool:
        # HttpClient aggregates its bounded retries before the router records one outcome.
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "dataset": self.dataset,
            "asset": self.asset,
            "metric_keys": list(self.metric_keys),
            "status": self.status,
            "success": self.success,
            "final": self.final,
            "source_mode": self.source_mode,
            "reason": self.reason,
            "request_hash": self.request_hash,
            "network_requests": self.network_requests,
            "endpoint": self.endpoint,
            "method": self.method,
            "attempt": self.attempt,
            "error_code": self.error_code,
            "exception_class": self.exception_class,
            "detail": self.detail,
            "retryable": self.retryable,
            "status_code": self.status_code,
        }


@dataclass(frozen=True)
class RouterResult:
    observations: tuple[Mapping[str, Any], ...] = ()
    attempts: tuple[ProviderAttempt, ...] = ()
    unresolved: tuple[tuple[str, str], ...] = ()
    provider_cache_hits: int = 0
    api_requests: int = 0
    api_derived_metrics: int = 0
    provider_fallbacks: int = 0
    unresolved_details: tuple[Mapping[str, Any], ...] = ()

    @property
    def network_requests(self) -> int:
        return self.api_requests

    def as_dict(self) -> dict[str, Any]:
        return {
            "observations": [dict(item) for item in self.observations],
            "attempts": [item.as_dict() for item in self.attempts],
            "unresolved": [list(item) for item in self.unresolved],
            "unresolved_details": [dict(item) for item in self.unresolved_details],
            "provider_cache_hits": self.provider_cache_hits,
            "api_requests": self.api_requests,
            "api_derived_metrics": self.api_derived_metrics,
            "provider_fallbacks": self.provider_fallbacks,
        }


def _now(value: str | datetime | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "now")


def _mapping_observations(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, ProviderResponse):
        value = value.observations
    elif isinstance(value, Mapping):
        if "observations" in value:
            value = value["observations"]
        elif "metric_key" in value:
            value = (value,)
        else:
            value = ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ProviderUnavailable("provider did not return a sequence of observations")
    result: list[Mapping[str, Any]] = []
    for item in value:
        if hasattr(item, "as_dict"):
            item = item.as_dict()
        if not isinstance(item, Mapping):
            raise ProviderUnavailable("provider returned a non-object observation")
        result.append(dict(item))
    return tuple(result)


def _with_source_mode(value: Mapping[str, Any], mode: str) -> dict[str, Any]:
    result = dict(value)
    metadata = dict(result.get("metadata") or {})
    metadata["source_mode"] = mode
    result["metadata"] = metadata
    return result


class ProviderRouter:
    """Route bundles through configured providers without model participation."""

    def __init__(
        self,
        providers: Mapping[str, Any] | None = None,
        *,
        config: Mapping[str, Any] | None = None,
        cache: ProviderCache | None = None,
        http_client: Any | None = None,
    ) -> None:
        self.config = dict(config or load_provider_config())
        self.cache = cache or ProviderCache()
        self.http_client = http_client
        self.providers = {str(name).strip().lower(): provider for name, provider in (providers or {}).items()}
        if providers is None:
            self.providers = self._default_providers()
        self._review_requests = 0
        self._provider_requests: dict[str, int] = {}
        self._last_network_requests = 0
        self._last_metric_diagnostics: dict[str, Mapping[str, Any]] = {}

    def _default_providers(self) -> dict[str, Any]:
        from .alternative_me import AlternativeMeProvider
        from .binance import BinanceProvider
        from .bybit import BybitProvider
        from .coinmetrics import CoinMetricsProvider
        from .defillama import DeFiLlamaProvider
        from .sosovalue import SoSoValueProvider

        client = self.http_client or HttpClient()
        self.http_client = client
        providers = {
            "binance": BinanceProvider(client=client),
            "bybit": BybitProvider(client=client),
            "alternative_me": AlternativeMeProvider(client=client),
            "defillama": DeFiLlamaProvider(client=client),
            "coinmetrics_community": CoinMetricsProvider(client=client, authenticated=False),
        }
        if provider_enabled("coinmetrics_pro", self.config):
            from .coinmetrics import CoinMetricsAuthenticatedProvider

            providers["coinmetrics_pro"] = CoinMetricsAuthenticatedProvider(
                client=client,
                api_key=provider_api_key("coinmetrics_pro", self.config),
            )
        if provider_enabled("sosovalue", self.config):
            providers["sosovalue"] = SoSoValueProvider(
                client=client,
                api_key=provider_api_key("sosovalue", self.config),
            )
        return providers

    @property
    def allow_web(self) -> bool:
        return bool(self.config.get("fallback", {}).get("allow_web", True))

    def provider_status(self) -> tuple[dict[str, Any], ...]:
        from .config import provider_status

        return provider_status(self.config, adapters=self.providers)

    def provider_runtime_status(self) -> tuple[ProviderRuntimeStatus, ...]:
        from .config import provider_runtime_status

        return provider_runtime_status(self.config, adapters=self.providers)

    runtime_status = provider_runtime_status

    def probe(self, provider: str = "all") -> tuple[dict[str, Any], ...]:
        """Run an explicit network probe; normal collection remains unchanged."""
        from .probe import probe_providers

        return probe_providers(self, provider)

    def capabilities(self, provider: str) -> ProviderCapabilities | None:
        value = self.providers.get(provider.strip().lower())
        capabilities = getattr(value, "capabilities", None)
        if callable(capabilities):
            capabilities = capabilities()
        return capabilities if isinstance(capabilities, ProviderCapabilities) else None

    def build_requests(
        self,
        requests: Iterable[Any],
        *,
        as_of: str | datetime | None = None,
        now: str | datetime | None = None,
        history_days: int = 365,
    ) -> tuple[ProviderRequest, ...]:
        ttl = self.config.get("cache_ttl_seconds", {})
        return build_provider_requests(
            requests,
            as_of=as_of,
            now=now,
            history_days=history_days,
            ttl_seconds=dict(ttl) if isinstance(ttl, Mapping) else None,
        )

    resolve_provider_chain = staticmethod(provider_chain)

    def collect(
        self,
        requests: Iterable[ProviderRequest],
        *,
        mode: FetchMode | str = FetchMode.AUTO,
        fetch_mode: FetchMode | str | None = None,
        as_of: str | datetime | None = None,
        now: str | datetime | None = None,
    ) -> RouterResult:
        selected_mode = FetchMode.parse(fetch_mode if fetch_mode is not None else mode)
        current = _now(now)
        supplied = tuple(requests)
        if any(not isinstance(item, ProviderRequest) for item in supplied):
            raise ValueError("router requests must be ProviderRequest objects")
        self._review_requests = 0
        self._provider_requests = {}

        pending: dict[tuple[str, str], dict[str, Any]] = {}
        for request in supplied:
            if request.dataset == "basis" and request.provider == "binance":
                cutoff = as_of if as_of is not None else request.parameters.get("as_of")
                request = replace(request, parameters={
                    **request.parameters, "market": "delivery", "methodology": BASIS_METHODOLOGY,
                    "as_of": _now(cutoff) if cutoff is not None else None,
                })
            for key in request.metric_keys:
                if (request.asset, key) in pending:
                    raise ValueError("router requests contain duplicate asset/metric keys")
                chain = tuple(dict.fromkeys((request.provider, *provider_chain(key))))
                pending[(request.asset, key)] = {"request": request, "chain": chain, "index": 0}

        observations: dict[tuple[str, str], Mapping[str, Any]] = {}
        attempts: list[ProviderAttempt] = []
        exhausted: dict[tuple[str, str], dict[str, Any]] = {}
        cache_hits = api_requests = api_derived = fallbacks = 0

        while pending:
            groups: dict[tuple[str, str, str, str], list[tuple[str, str]]] = {}
            for identity, item in pending.items():
                request = item["request"]
                provider = item["chain"][item["index"]]
                parameters_key = repr(sorted((str(key), repr(value)) for key, value in request.parameters.items()))
                group = (provider, request.dataset, request.asset, parameters_key)
                groups.setdefault(group, []).append(identity)
            progressed = False
            for (_, _, _, _), identities in groups.items():
                first = pending[identities[0]]
                original: ProviderRequest = first["request"]
                provider_name = first["chain"][first["index"]]
                request = ProviderRequest(
                    provider=provider_name,
                    dataset=original.dataset,
                    asset=original.asset,
                    parameters=original.parameters,
                    metric_keys=tuple(identity[1] for identity in identities),
                    mutable=original.mutable,
                    freshness_seconds=original.freshness_seconds,
                )
                provider = self.providers.get(provider_name)
                if provider is None or not self._enabled(provider_name):
                    attempts.append(ProviderAttempt(
                        provider_name, request.dataset, request.asset, request.metric_keys,
                        "DISABLED", "NONE", "provider disabled or unavailable", request_hash(request),
                    ))
                    self._advance(
                        pending, identities, exhausted=exhausted, provider=provider_name,
                        status="DISABLED", reason="provider disabled or unavailable",
                    )
                    progressed = True
                    continue
                capabilities = self.capabilities(provider_name)
                if capabilities:
                    unsupported = tuple(identity for identity in identities if not capabilities.supports(identity[1]))
                    supported = tuple(identity for identity in identities if identity not in unsupported)
                else:
                    unsupported = ()
                    supported = tuple(identities)
                if unsupported:
                    attempts.append(ProviderAttempt(
                        provider_name, request.dataset, request.asset, tuple(identity[1] for identity in unsupported),
                        "UNSUPPORTED", "NONE", "capability declaration does not include metric", request_hash(request),
                        error_code="PROVIDER_UNSUPPORTED",
                    ))
                    self._advance(
                        pending, unsupported, exhausted=exhausted, provider=provider_name,
                        status="UNSUPPORTED", reason="capability declaration does not include metric",
                        diagnostic={"error_code": "PROVIDER_UNSUPPORTED", "detail": "capability declaration does not include metric"},
                    )
                    progressed = True
                if not supported:
                    continue
                identities = list(supported)
                request = ProviderRequest(
                    provider=provider_name,
                    dataset=original.dataset,
                    asset=original.asset,
                    parameters=original.parameters,
                    metric_keys=tuple(identity[1] for identity in identities),
                    mutable=original.mutable,
                    freshness_seconds=original.freshness_seconds,
                )
                try:
                    values, source_mode, hit, network_count = self._collect_one(
                        provider_name, provider, request, selected_mode, as_of=as_of, now=current
                    )
                    matched = {
                        (str(value.get("asset", request.asset)).strip().upper(), str(value.get("metric_key", "")).strip().lower()): value
                        for value in values
                        if value.get("status", "SUCCESS") == "SUCCESS"
                    }
                    found = []
                    for identity in identities:
                        value = matched.get(identity)
                        if value is not None:
                            observations[identity] = _with_source_mode(value, source_mode)
                            found.append(identity)
                            pending.pop(identity, None)
                    missing = [identity for identity in identities if identity not in found]
                    status = "SUCCESS" if not missing else "PARTIAL"
                    partial_diagnostic = {}
                    partial_reason = None
                    if missing:
                        partial_diagnostic = next(
                            (
                                self._last_metric_diagnostics.get(identity[1])
                                for identity in missing
                                if self._last_metric_diagnostics.get(identity[1])
                            ),
                            {},
                        )
                        partial_reason = next(
                            (
                                self._format_diagnostic(self._last_metric_diagnostics.get(identity[1]))
                                for identity in missing
                                if self._last_metric_diagnostics.get(identity[1])
                            ),
                            "provider returned no usable value for some metrics",
                        )
                    attempts.append(ProviderAttempt(
                        provider_name, request.dataset, request.asset, request.metric_keys,
                        status, source_mode,
                        None if not missing else partial_reason,
                        request_hash(request), network_count,
                        endpoint=partial_diagnostic.get("endpoint"),
                        method=str(partial_diagnostic.get("method", "GET")),
                        attempt=int(partial_diagnostic.get("attempt", 1)),
                        error_code=partial_diagnostic.get("error_code"),
                        exception_class=partial_diagnostic.get("exception_class"),
                        detail=partial_diagnostic.get("detail"),
                        retryable=partial_diagnostic.get("retryable"),
                        status_code=partial_diagnostic.get("status_code"),
                    ))
                    cache_hits += int(hit)
                    api_requests += network_count
                    api_derived += len(found)
                    if first["index"] > 0:
                        fallbacks += 1
                    if missing:
                        for identity in missing:
                            diagnostic = self._last_metric_diagnostics.get(identity[1])
                            self._advance(
                                pending, (identity,), exhausted=exhausted, provider=provider_name,
                                status=status,
                                reason=self._format_diagnostic(diagnostic) if diagnostic else "provider returned no usable value for some metrics",
                                diagnostic=diagnostic,
                            )
                    progressed = True
                except Exception as exc:  # provider boundaries must not abort an entire review
                    secret = provider_api_key(provider_name, self.config)
                    diagnostic = self._diagnostic_for(exc)
                    if not diagnostic:
                        diagnostic = {"error_code": classify_transport_error(exc), "detail": redact_secrets(str(exc), (secret,) if secret else ())}
                    reason = self._format_diagnostic(diagnostic, fallback=redact_secrets(str(exc), (secret,) if secret else ()) or exc.__class__.__name__)
                    failed_network_requests = self._last_network_requests
                    attempts.append(ProviderAttempt(
                        provider_name, request.dataset, request.asset, request.metric_keys,
                        self._error_status(exc), "NONE", reason, request_hash(request), failed_network_requests,
                        endpoint=diagnostic.get("endpoint"),
                        method=str(diagnostic.get("method", "GET")),
                        attempt=int(diagnostic.get("attempt", 1)),
                        error_code=str(diagnostic.get("error_code")) if diagnostic.get("error_code") else classify_transport_error(exc),
                        exception_class=str(diagnostic.get("exception_class", exc.__class__.__name__)),
                        detail=str(diagnostic.get("detail", reason)),
                        retryable=diagnostic.get("retryable"),
                        status_code=diagnostic.get("status_code"),
                    ))
                    api_requests += failed_network_requests
                    if first["index"] > 0:
                        fallbacks += 1
                    self._advance(
                        pending, identities, exhausted=exhausted, provider=provider_name,
                        status=self._error_status(exc), reason=reason, diagnostic=diagnostic,
                    )
                    progressed = True
            if not progressed:
                for identity, item in tuple(pending.items()):
                    exhausted[identity] = self._unresolved_detail(
                        identity,
                        item,
                        reason="router made no progress",
                    )
                break

        unresolved = tuple(sorted(exhausted))
        unresolved_details = tuple(exhausted[identity] for identity in unresolved)
        return RouterResult(
            observations=tuple(observations.values()),
            attempts=tuple(attempts),
            unresolved=unresolved,
            provider_cache_hits=cache_hits,
            api_requests=api_requests,
            api_derived_metrics=api_derived,
            provider_fallbacks=fallbacks,
            unresolved_details=unresolved_details,
        )

    route = collect
    fetch = collect
    resolve = collect

    def _enabled(self, name: str) -> bool:
        if name not in self.config.get("providers", {}):
            return name in self.providers
        return provider_enabled(name, self.config)

    @staticmethod
    def _error_status(error: Exception) -> str:
        if isinstance(error, CacheExpired):
            return "CACHE_EXPIRED"
        if isinstance(error, CacheCorruption):
            return "CACHE_CORRUPT"
        if isinstance(error, ProviderUnsupportedMetric):
            return "UNSUPPORTED"
        if isinstance(error, ProviderError):
            return error.__class__.__name__.replace("Provider", "").upper()
        return "FAILED"

    @staticmethod
    def _unresolved_detail(
        identity: tuple[str, str],
        item: Mapping[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        attempted = tuple(dict.fromkeys(str(provider) for provider in item.get("attempted", ())))
        result = {
            "asset": identity[0],
            "metric_key": identity[1],
            "reason": reason,
            "providers_attempted": list(attempted),
        }
        if attempted:
            result["provider"] = attempted[-1]
        diagnostic = item.get("last_diagnostic")
        if isinstance(diagnostic, Mapping):
            for field in ("endpoint", "method", "attempt", "error_code", "exception_class", "detail", "retryable", "status_code"):
                if diagnostic.get(field) is not None:
                    result[field] = diagnostic[field]
        return result

    @classmethod
    def _advance(
        cls,
        pending: dict[tuple[str, str], dict[str, Any]],
        identities: Iterable[tuple[str, str]],
        *,
        exhausted: dict[tuple[str, str], dict[str, Any]],
        provider: str,
        status: str,
        reason: str,
        diagnostic: Mapping[str, Any] | None = None,
    ) -> None:
        for identity in identities:
            item = pending.get(identity)
            if item is None:
                continue
            attempted = item.setdefault("attempted", [])
            attempted.append(provider)
            item["last_status"] = status
            item["last_reason"] = reason
            item["last_diagnostic"] = dict(diagnostic or {})
            item["index"] += 1
            if item["index"] >= len(item["chain"]):
                pending.pop(identity, None)
                exhausted[identity] = cls._unresolved_detail(identity, item, reason=reason)

    def _budget(self, provider: str) -> None:
        network = self.config.get("network", {})
        maximum = int(network.get("max_requests_per_review", 60))
        per_provider = int(network.get("max_requests_per_provider", 30))
        if self._review_requests >= maximum or self._provider_requests.get(provider, 0) >= per_provider:
            raise ProviderUnavailable("provider request budget exhausted")

    def _collect_one(
        self,
        provider_name: str,
        provider: Any,
        request: ProviderRequest,
        mode: FetchMode,
        *,
        as_of: str | datetime | None,
        now: str,
    ) -> tuple[tuple[Mapping[str, Any], ...], str, bool, int]:
        self._last_network_requests = 0
        self._last_metric_diagnostics = {}
        if request.dataset == "ohlcv" and hasattr(provider, "candles"):
            return self._collect_ohlcv(provider_name, provider, request, mode, as_of=as_of, now=now)
        if request.dataset == "basis" and as_of is None:
            as_of = request.parameters.get("as_of")
        if mode != FetchMode.REFRESH or not request.mutable:
            try:
                cached = self.cache.load_response(request, now=now, as_of=as_of)
            except CacheExpired:
                cached = None
            except CacheCorruption:
                if mode == FetchMode.CACHE_ONLY:
                    raise
                self.cache.quarantine(request)
                cached = None
            if cached is not None:
                values = _mapping_observations(cached)
                if request.dataset != "basis" or all(
                    current_delivery_basis(value.get("metadata"), as_of or now) for value in values
                ):
                    return values, "CACHE_PROVIDER", True, 0
        if mode == FetchMode.CACHE_ONLY:
            raise ProviderUnavailable("CACHE_ONLY has no usable provider cache")
        self._budget(provider_name)
        self._review_requests += 1
        self._provider_requests[provider_name] = self._provider_requests.get(provider_name, 0) + 1
        if not hasattr(provider, "collect"):
            raise ProviderUnsupportedMetric(f"{provider_name} has no bundle collector")
        self._last_network_requests = 0
        client = getattr(provider, "client", None)
        before = getattr(client, "request_count", None)
        try:
            raw = provider.collect(request)
        finally:
            after = getattr(client, "request_count", None)
            if isinstance(before, int) and isinstance(after, int):
                self._last_network_requests = max(0, after - before)
        values = _mapping_observations(raw)
        if isinstance(raw, ProviderResponse) and raw.diagnostics:
            self._last_metric_diagnostics = {
                str(key).strip().lower(): redact_secrets(dict(value))
                for key, value in raw.diagnostics.items()
            }
        network_count = self._last_network_requests or (raw.network_requests if isinstance(raw, ProviderResponse) else 1)
        observed = sorted(
            str(value.get("observed_at"))
            for value in values
            if value.get("observed_at") is not None
        )
        observed_range = {"start": observed[0], "end": observed[-1]} if observed else None
        self.cache.save_response(
            request,
            [dict(value) for value in values],
            fetched_at=now,
            observed_range=observed_range,
        )
        return values, "API", False, network_count

    def _collect_ohlcv(
        self,
        provider_name: str,
        provider: Any,
        request: ProviderRequest,
        mode: FetchMode,
        *,
        as_of: str | datetime | None,
        now: str,
    ) -> tuple[tuple[Mapping[str, Any], ...], str, bool, int]:
        self._last_metric_diagnostics = {}
        parameters = request.parameters
        effective_as_of = as_of if as_of is not None else parameters.get("as_of")
        timeframe = str(parameters.get("timeframe", "1D")).upper()
        market = str(parameters.get("market", "spot")).lower()
        quote = str(parameters.get("quote_currency", "USDT")).upper()
        try:
            existing = self.cache.load_series(provider_name, request.asset, timeframe, market=market, quote_currency=quote)
        except CacheCorruption:
            if mode == FetchMode.CACHE_ONLY:
                raise
            self.cache.quarantine_series(provider_name, request.asset, timeframe, market=market, quote_currency=quote)
            existing = None
        missing = missing_series_range(
            existing,
            start=parameters.get("start"),
            end=parameters.get("end"),
        )
        if existing is not None and missing is None:
            values = self._series_values(provider, request, existing, as_of=effective_as_of)
            return values, "CACHE_PROVIDER", True, 0
        if mode == FetchMode.CACHE_ONLY:
            if existing is None:
                raise ProviderUnavailable("CACHE_ONLY has no cached OHLCV series")
            values = self._series_values(provider, request, existing, as_of=effective_as_of)
            return values, "CACHE_PROVIDER", True, 0
        self._budget(provider_name)
        self._review_requests += 1
        self._provider_requests[provider_name] = self._provider_requests.get(provider_name, 0) + 1
        start, end = missing or (parameters.get("start"), parameters.get("end"))
        self._last_network_requests = 0
        client = getattr(provider, "client", None)
        before = getattr(client, "request_count", None)
        try:
            incoming = provider.candles(
                request.asset,
                timeframe=timeframe,
                start=start,
                end=end,
            )
        finally:
            after = getattr(client, "request_count", None)
            if isinstance(before, int) and isinstance(after, int):
                self._last_network_requests = max(0, after - before)
        if not hasattr(incoming, "candles"):
            incoming = OHLCVSeries.from_mapping(incoming)
        completed = tuple(candle for candle in incoming.candles if candle.completed)
        if not completed:
            raise ProviderUnavailable("provider returned no completed OHLCV candle")
        if len(completed) != len(incoming.candles):
            incoming = OHLCVSeries(
                symbol=incoming.symbol,
                timeframe=incoming.timeframe,
                candles=completed,
                source=incoming.source,
                fetched_at=incoming.fetched_at,
                venue=incoming.venue,
                market=incoming.market,
                quote_currency=incoming.quote_currency,
            )
        merged = merge_ohlcv_series(existing, incoming)
        self.cache.store_series(merged, provider=provider_name, market=market, quote_currency=quote)
        values = self._series_values(provider, request, merged, as_of=effective_as_of)
        return values, "API", False, self._last_network_requests or 1

    @staticmethod
    def _series_values(provider: Any, request: ProviderRequest, series: Any, *, as_of: str | datetime | None) -> tuple[Mapping[str, Any], ...]:
        method = getattr(provider, "observations_from_series", None) or getattr(provider, "metrics_from_series", None)
        if method is not None:
            try:
                value = method(series, request.metric_keys, as_of=as_of)
            except TypeError:
                value = method(series, request.metric_keys)
            return _mapping_observations(value)
        from .binance import observations_from_ohlcv

        return observations_from_ohlcv(series, request.metric_keys, as_of=as_of)

    @staticmethod
    def _diagnostic_for(error: BaseException) -> dict[str, Any]:
        diagnostic = getattr(error, "diagnostic", None)
        if hasattr(diagnostic, "as_dict"):
            return dict(redact_secrets(diagnostic.as_dict()))
        if isinstance(diagnostic, Mapping):
            return dict(redact_secrets(dict(diagnostic)))
        return {}

    @staticmethod
    def _format_diagnostic(
        diagnostic: Mapping[str, Any] | None,
        *,
        fallback: str = "provider returned no usable value",
    ) -> str:
        if not diagnostic:
            return fallback
        code = str(diagnostic.get("error_code", "UNKNOWN_NETWORK_ERROR"))
        endpoint = diagnostic.get("endpoint")
        detail = str(diagnostic.get("detail", "")).strip()
        location = f" at {endpoint}" if endpoint else ""
        return f"{code}{location}: {detail}" if detail else f"{code}{location}"


__all__ = ["ProviderAttempt", "ProviderRouter", "RouterResult"]
