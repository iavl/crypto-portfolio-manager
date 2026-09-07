"""Opt-in, read-only provider network probes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from .alternative_me import BASE_URL as ALTERNATIVE_BASE_URL
from .base import ProviderRequest, ProviderResponseError
from .binance import SPOT_BASE_URL
from .bybit import BASE_URL as BYBIT_BASE_URL
from .coinmetrics import AUTHENTICATED_BASE_URL, COMMUNITY_BASE_URL, CoinMetricsProvider, catalog_metrics
from .coingecko import BASE_URL as COINGECKO_BASE_URL, COINGECKO_IDS, CoinGeckoProvider
from .chain_liveness import CHAIN_NATIVE_ASSETS, ChainLivenessProvider
from .defillama import BASE_URL as DEFILLAMA_BASE_URL
from .github_activity import BASE_URL as GITHUB_BASE_URL, GitHubActivityProvider, REPOSITORY_ALLOWLIST
from .http import classify_transport_error, redact_secrets, redact_url
from .fred import FREDProvider, FRED_SERIES, BASE_URL as FRED_BASE_URL, OBSERVATIONS_PATH
from .router import ProviderRouter
from .sosovalue import BASE_URL as SOSOVALUE_BASE_URL, ETF_HISTORICAL_INFLOW_PATH, SoSoValueProvider


_NETWORK_FAILURES = {
    "TLS_CERTIFICATE_VERIFY_FAILED", "TLS_HANDSHAKE_FAILED", "DNS_RESOLUTION_FAILED",
    "CONNECT_TIMEOUT", "READ_TIMEOUT", "CONNECTION_REFUSED", "CONNECTION_RESET",
    "PROXY_ERROR", "HTTP_5XX", "UNKNOWN_NETWORK_ERROR",
    "HTTP_403_RATE_LIMIT", "HTTP_429",
}
_SCHEMA_FAILURES = {"INVALID_JSON", "RESPONSE_TOO_LARGE", "PROVIDER_SCHEMA_ERROR"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _diagnostic(error: BaseException, endpoint: str) -> dict[str, Any]:
    value = getattr(error, "diagnostic", None)
    if hasattr(value, "as_dict"):
        result = dict(value.as_dict())
    elif isinstance(value, Mapping):
        result = dict(value)
    else:
        result = {}
    result.setdefault("endpoint", redact_url(endpoint))
    result.setdefault("method", "GET")
    result.setdefault("attempt", 1)
    result.setdefault("error_code", classify_transport_error(error))
    result.setdefault("exception_class", error.__class__.__name__)
    result.setdefault("detail", redact_secrets(str(error)) or error.__class__.__name__)
    result.setdefault("retryable", False)
    return result


def _probe_call(
    provider: str,
    endpoint: str,
    call: Callable[[], Any],
    *,
    method: str = "GET",
    authenticated: bool = False,
    validate: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider": provider,
        "endpoint": redact_url(endpoint),
        "method": method,
        "checked_at": _now(),
        "network": "OK",
        "auth": "OK" if authenticated else "NOT_REQUIRED",
        "plan_access": "OK",
        "schema": "OK",
    }
    try:
        value = call()
        if validate is not None:
            validate(value)
    except Exception as exc:  # probes report failures instead of aborting all providers
        diagnostic = _diagnostic(exc, endpoint)
        code = str(diagnostic["error_code"]).upper()
        detail = str(diagnostic.get("detail", ""))
        result.update({
            "network": "FAILED" if code in _NETWORK_FAILURES else "OK",
            "auth": "REJECTED" if code in {"HTTP_401", "HTTP_403"} else "NOT_TESTED" if code in _NETWORK_FAILURES else result["auth"],
            "plan_access": "RESTRICTED" if code == "PROVIDER_PLAN_RESTRICTED" else "NOT_TESTED" if code in _NETWORK_FAILURES or code in {"HTTP_401", "HTTP_403"} else "OK",
            "schema": "ERROR" if code in _SCHEMA_FAILURES else "NOT_TESTED" if code in _NETWORK_FAILURES or code in {"HTTP_401", "HTTP_403", "PROVIDER_PLAN_RESTRICTED"} else result["schema"],
            "error_code": code,
            "exception_class": diagnostic.get("exception_class"),
            "detail": redact_secrets(detail),
        })
        if "history is insufficient" in detail.lower() or "no data at or before" in detail.lower():
            result["history"] = "INSUFFICIENT"
        return result
    return result


def _require_mapping(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ProviderResponseError("probe response schema is not an object")


def _require_list(value: Any) -> None:
    if not isinstance(value, Mapping) or not isinstance(value.get("data"), list):
        raise ProviderResponseError("probe response schema has no data list")


def _require_number(value: Any) -> None:
    if isinstance(value, bool):
        raise ProviderResponseError("probe response schema is not numeric")
    try:
        float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderResponseError("probe response schema is not numeric") from exc


def _require_observations(value: Any) -> None:
    observations = getattr(value, "observations", value)
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Iterable) or not tuple(observations):
        raise ProviderResponseError("probe response schema has no normalized observations")


def _sosovalue_probe(provider: SoSoValueProvider, asset: str = "BTC") -> dict[str, Any]:
    asset = asset.strip().upper()
    if asset not in {"BTC", "ETH"}:
        raise ValueError("SoSoValue probe asset must be BTC or ETH")
    endpoint = SOSOVALUE_BASE_URL + ETF_HISTORICAL_INFLOW_PATH
    captured: dict[str, Any] = {}

    def call() -> Any:
        value = provider.collect(ProviderRequest(
            "sosovalue",
            "etf",
            asset,
            {"as_of": _now()},
            (
                "flows.etf_net_1d",
                "flows.btc_etf_net_to_aum_7d",
                "flows.btc_etf_net_to_aum_30d",
                "flows.btc_etf_aum_usd",
            ) if asset.strip().upper() == "BTC" else (
                "flows.etf_net_1d",
                "flows.eth_etf_net_to_aum_7d",
                "flows.eth_etf_net_to_aum_30d",
                "flows.eth_etf_aum_usd",
            ) if asset.strip().upper() == "ETH" else ("flows.etf_net_1d",),
        ))
        captured["value"] = value
        return value

    result = _probe_call(
        "sosovalue",
        endpoint,
        call,
        method="POST",
        authenticated=True,
        validate=_require_observations,
    )
    result["endpoint_name"] = "ETF historical inflow chart"
    result["asset"] = asset
    if "error_code" not in result:
        observations = tuple(getattr(captured["value"], "observations", ()))
        metadata = observations[0].get("metadata", {}) if observations else {}
        result["history_rows"] = metadata.get("history_rows", len(observations))
        result["latest_source_date"] = metadata.get("source_end_date")
        result["normalized_7d"] = any(
            item.get("metric_key") in {"flows.btc_etf_net_to_aum_7d", "flows.eth_etf_net_to_aum_7d"} for item in observations
        )
        result["normalized_30d"] = any(
            item.get("metric_key") in {"flows.btc_etf_net_to_aum_30d", "flows.eth_etf_net_to_aum_30d"} for item in observations
        )
        result["aum"] = any(item.get("metric_key") in {"flows.btc_etf_aum_usd", "flows.eth_etf_aum_usd"} for item in observations)
    transport = getattr(provider.client, "transport_metadata", lambda: {})()
    result.update({
        "python_ssl": transport.get("python_ssl"),
        "ca_file": transport.get("ca_source"),
        "proxy": transport.get("proxy"),
        "tls_verification": transport.get("verify_mode") == "CERT_REQUIRED" and transport.get("check_hostname") is True,
    })
    return result


def _chain_liveness_probe(
    provider: ChainLivenessProvider,
    asset: str,
) -> dict[str, Any]:
    normalized_asset = asset.strip().upper()
    if normalized_asset not in CHAIN_NATIVE_ASSETS:
        raise ValueError(f"chain liveness asset must be one of {CHAIN_NATIVE_ASSETS}")
    assessment = provider.assess(normalized_asset)
    result: dict[str, Any] = {
        "provider": "chain_liveness",
        "asset": normalized_asset,
        "network": "OK" if assessment.sources_healthy else "FAILED",
        "checked_at": assessment.checked_at,
        "assessment": assessment.status,
        "confidence": assessment.confidence,
        "head_height_or_slot": assessment.head_height_or_slot,
        "head_hash": assessment.head_hash,
        "head_observed_at": assessment.head_observed_at,
        "head_age_seconds": assessment.head_age_seconds,
        "finalized_height_or_slot": assessment.finalized_height_or_slot,
        "finalized_observed_at": assessment.finalized_observed_at,
        "finalized_age_seconds": assessment.finalized_age_seconds,
        "head_finalized_distance": (
            assessment.head_height_or_slot - assessment.finalized_height_or_slot
            if assessment.head_height_or_slot is not None
            and assessment.finalized_height_or_slot is not None
            else None
        ),
        "sources_checked": list(assessment.sources_checked),
        "sources_healthy": list(assessment.sources_healthy),
    }
    if assessment.source_failures:
        result["source_failures"] = [dict(item) for item in assessment.source_failures]
        result["error_code"] = str(assessment.source_failures[-1].get("error_code", "UNKNOWN_NETWORK_ERROR"))
    return result


def probe_provider(
    router: ProviderRouter,
    provider_name: str,
    *,
    asset: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Probe one registered provider; callers must opt in explicitly."""
    name = provider_name.strip().lower()
    statuses = {item.provider: item for item in router.provider_runtime_status()}
    status = statuses.get(name)
    if status is None or not status.runtime_ready:
        return ({
            "provider": name,
            "config": "NOT_READY",
            "network": "SKIPPED",
            "reason": status.reason if status else "provider is not registered",
        },)
    provider = router.providers.get(name)
    client = getattr(provider, "client", None)
    if name == "chain_liveness" and isinstance(provider, ChainLivenessProvider):
        assets = (asset.strip().upper(),) if asset is not None else CHAIN_NATIVE_ASSETS
        return tuple(_with_config(_chain_liveness_probe(provider, item), client) for item in assets)
    if client is None or not hasattr(client, "get_json"):
        return ({"provider": name, "config": "READY", "network": "SKIPPED", "error_code": "PROVIDER_UNSUPPORTED"},)
    if name == "sosovalue" and isinstance(provider, SoSoValueProvider):
        return (_with_config(_sosovalue_probe(provider, asset or "BTC"), client),)
    if name == "fred" and isinstance(provider, FREDProvider):
        endpoint = FRED_BASE_URL + OBSERVATIONS_PATH
        captured: dict[str, Any] = {}

        def call() -> Any:
            response = provider.collect(ProviderRequest(
                "fred", "macro", "BTC", {"as_of": _now()},
                tuple(FRED_SERIES),
            ))
            captured["value"] = response
            return response

        result = _probe_call(
            "fred", endpoint, call, authenticated=True, validate=_require_observations,
        )
        result["series"] = list(FRED_SERIES)
        if "error_code" not in result:
            result["available_series"] = sorted({
                str(item.get("metadata", {}).get("series_id"))
                for item in getattr(captured["value"], "observations", ())
                if item.get("metadata", {}).get("series_id")
            })
        return (_with_config(result, client),)
    if name == "coingecko" and isinstance(provider, CoinGeckoProvider):
        endpoint = COINGECKO_BASE_URL + "/coins/markets"
        target = (asset or "BTC").strip().upper()
        if target not in COINGECKO_IDS:
            raise ValueError(f"CoinGecko probe asset must be one of {tuple(COINGECKO_IDS)}")
        captured: dict[str, Any] = {}

        def call() -> Any:
            response = provider.collect(ProviderRequest(
                "coingecko", "valuation", target, {"as_of": None},
                ("valuation.market_cap", "valuation.fdv"),
            ))
            captured["value"] = response
            return response

        result = _probe_call(
            "coingecko",
            endpoint,
            call,
            authenticated=True,
            validate=_require_observations,
        )
        if "error_code" not in result:
            observations = tuple(getattr(captured["value"], "observations", ()))
            result.update({
                "asset": target,
                "market_cap": "present" if any(item.get("metric_key") == "valuation.market_cap" for item in observations) else "missing",
                "fdv": "present" if any(item.get("metric_key") == "valuation.fdv" for item in observations) else "missing",
                "last_updated": next((item.get("observed_at") for item in observations if item.get("observed_at")), None),
            })
        result["endpoint_name"] = "coins/markets"
        return (_with_config(result, client),)
    if name == "github" and isinstance(provider, GitHubActivityProvider):
        target = (asset or "ETH").strip().upper()
        if target not in REPOSITORY_ALLOWLIST:
            raise ValueError(f"GitHub probe asset must be one of {tuple(REPOSITORY_ALLOWLIST)}")
        endpoint = f"{GITHUB_BASE_URL}/repos/{REPOSITORY_ALLOWLIST[target][0]}/commits"
        captured: dict[str, Any] = {}

        def call() -> Any:
            response = provider.collect(ProviderRequest(
                "github", "github", target, {}, ("fundamentals.developer_activity",),
            ))
            captured["value"] = response
            return response

        result = _probe_call(
            "github",
            endpoint,
            call,
            authenticated=True,
            validate=_require_observations,
        )
        if "error_code" not in result:
            observations = tuple(getattr(captured["value"], "observations", ()))
            result.update({
                "asset": target,
                "auth_method": "bearer_token",
                "repositories": list(REPOSITORY_ALLOWLIST[target]),
                "developer_activity": observations[0].get("value") if observations else None,
            })
        result["endpoint_name"] = "repository commits"
        return (_with_config(result, client),)
    if name == "binance":
        endpoint = SPOT_BASE_URL + "/api/v3/ticker/price"
        return (_with_config(_probe_call(name, endpoint, lambda: client.get_json(endpoint, params={"symbol": "BTCUSDT"}), validate=lambda value: _require_mapping(value)), client),)
    if name == "defillama":
        endpoint = DEFILLAMA_BASE_URL + "/tvl/aave"
        return (_with_config(_probe_call(name, endpoint, lambda: client.get_json(endpoint), validate=_require_number), client),)
    if name == "bybit":
        endpoint = BYBIT_BASE_URL + "/v5/market/tickers"
        return (_with_config(_probe_call(name, endpoint, lambda: client.get_json(endpoint, params={"category": "spot", "symbol": "BTCUSDT"}), validate=lambda value: _require_mapping(value)), client),)
    if name == "alternative_me":
        endpoint = ALTERNATIVE_BASE_URL + "/fng/"
        return (_with_config(_probe_call(name, endpoint, lambda: client.get_json(endpoint, params={"limit": 1, "format": "json"}), validate=_require_list), client),)
    if name in {"coinmetrics_community", "coinmetrics_pro"}:
        base_url = AUTHENTICATED_BASE_URL if name == "coinmetrics_pro" else COMMUNITY_BASE_URL
        endpoint = base_url + "/v4/catalog/asset-metrics"
        def call() -> Any:
            return client.get_json(endpoint)

        result = _probe_call(name, endpoint, call, validate=lambda value: catalog_metrics(value), authenticated=name == "coinmetrics_pro")
        if "error_code" not in result and isinstance(provider, CoinMetricsProvider):
            available = provider.available_metrics_for_asset("BTC")
            result["btc_1d_available"] = sorted(
                item for item in (
                    "CapMVRVCur", "CapMVRVZ", "CapRealUSD", "CapMrktCurUSD", "SplyCur",
                    "PriceRealizedUSD", "SOPR", "NUPL", "HashRate", "DiffMean",
                ) if item.lower() in available
            )
        return (_with_config(result, client),)
    return ({"provider": name, "config": "READY", "network": "SKIPPED", "error_code": "PROVIDER_UNSUPPORTED"},)


def _with_config(result: Mapping[str, Any], client: Any | None = None) -> dict[str, Any]:
    output = {"config": "READY", **dict(result)}
    metadata = getattr(client, "transport_metadata", lambda: {})()
    output.update({
        "python_ssl": metadata.get("python_ssl"),
        "ca_file": metadata.get("ca_source"),
        "proxy": metadata.get("proxy"),
        "tls_verification": metadata.get("verify_mode") == "CERT_REQUIRED" and metadata.get("check_hostname") is True,
    })
    return output


def probe_providers(
    router: ProviderRouter,
    providers: Iterable[str] | str = "all",
    *,
    asset: str | None = None,
) -> tuple[dict[str, Any], ...]:
    names = tuple(sorted(router.providers)) if providers == "all" else (providers,) if isinstance(providers, str) else tuple(providers)
    result: list[dict[str, Any]] = []
    for name in names:
        result.extend(probe_provider(router, name, asset=asset))
    return tuple(result)


__all__ = ["probe_provider", "probe_providers"]
