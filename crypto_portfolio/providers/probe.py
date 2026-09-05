"""Opt-in, read-only provider network probes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from .alternative_me import BASE_URL as ALTERNATIVE_BASE_URL
from .base import ProviderRequest
from .binance import SPOT_BASE_URL
from .bybit import BASE_URL as BYBIT_BASE_URL
from .coinmetrics import AUTHENTICATED_BASE_URL, COMMUNITY_BASE_URL, catalog_metrics
from .coinglass import CoinGlassProvider
from .defillama import BASE_URL as DEFILLAMA_BASE_URL
from .http import classify_transport_error, redact_secrets, redact_url
from .router import ProviderRouter


_NETWORK_FAILURES = {
    "TLS_CERTIFICATE_VERIFY_FAILED", "TLS_HANDSHAKE_FAILED", "DNS_RESOLUTION_FAILED",
    "CONNECT_TIMEOUT", "READ_TIMEOUT", "CONNECTION_REFUSED", "CONNECTION_RESET",
    "PROXY_ERROR", "HTTP_5XX", "UNKNOWN_NETWORK_ERROR",
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
    authenticated: bool = False,
    validate: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider": provider,
        "endpoint": redact_url(endpoint),
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
        raise ValueError("probe response schema is not an object")


def _require_list(value: Any) -> None:
    if not isinstance(value, Mapping) or not isinstance(value.get("data"), list):
        raise ValueError("probe response schema has no data list")


def _coinglass_probes(provider: CoinGlassProvider) -> tuple[dict[str, Any], ...]:
    # Probe through the existing adapter so endpoint/auth/parsing semantics do not diverge.
    etf = _probe_call(
        "coinglass",
        "https://open-api-v4.coinglass.com/api/etf/bitcoin/flow-history",
        lambda: provider.collect(ProviderRequest("coinglass", "etf", "MARKET", {}, ("flows.etf_net_1d",))),
        authenticated=True,
        validate=lambda value: _require_list({"data": value}) if not isinstance(value, list) else None,
    )
    liquidation = _probe_call(
        "coinglass",
        "https://open-api-v4.coinglass.com/api/futures/liquidation/aggregated-history",
        lambda: provider.collect(ProviderRequest("coinglass", "liquidations", "BTC", {}, ("derivatives.total_liquidations_24h_usd",))),
        authenticated=True,
        validate=lambda value: _require_list({"data": value}) if not isinstance(value, list) else None,
    )
    etf["endpoint_name"] = "ETF flow history"
    liquidation["endpoint_name"] = "Aggregated liquidation history"
    transport = getattr(provider.client, "transport_metadata", lambda: {})()
    for result in (etf, liquidation):
        result.update({
            "python_ssl": transport.get("python_ssl"),
            "ca_file": transport.get("ca_source"),
            "proxy": transport.get("proxy"),
            "tls_verification": transport.get("verify_mode") == "CERT_REQUIRED" and transport.get("check_hostname") is True,
        })
    return etf, liquidation


def probe_provider(router: ProviderRouter, provider_name: str) -> tuple[dict[str, Any], ...]:
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
    if client is None or not hasattr(client, "get_json"):
        return ({"provider": name, "config": "READY", "network": "SKIPPED", "error_code": "PROVIDER_UNSUPPORTED"},)
    if name == "coinglass" and isinstance(provider, CoinGlassProvider):
        return tuple({"config": "READY", **item} for item in _coinglass_probes(provider))
    if name == "binance":
        endpoint = SPOT_BASE_URL + "/api/v3/ticker/price"
        return (_with_config(_probe_call(name, endpoint, lambda: client.get_json(endpoint, params={"symbol": "BTCUSDT"}), validate=lambda value: _require_mapping(value)), client),)
    if name == "defillama":
        endpoint = DEFILLAMA_BASE_URL + "/protocol/aave"
        return (_with_config(_probe_call(name, endpoint, lambda: client.get_json(endpoint), validate=lambda value: _require_mapping(value)), client),)
    if name == "bybit":
        endpoint = BYBIT_BASE_URL + "/v5/market/tickers"
        return (_with_config(_probe_call(name, endpoint, lambda: client.get_json(endpoint, params={"category": "spot", "symbol": "BTCUSDT"}), validate=lambda value: _require_mapping(value)), client),)
    if name == "alternative_me":
        endpoint = ALTERNATIVE_BASE_URL + "/fng/"
        return (_with_config(_probe_call(name, endpoint, lambda: client.get_json(endpoint, params={"limit": 1, "format": "json"}), validate=_require_list), client),)
    if name in {"coinmetrics_community", "coinmetrics_pro"}:
        base_url = AUTHENTICATED_BASE_URL if name == "coinmetrics_pro" else COMMUNITY_BASE_URL
        endpoint = base_url + "/v4/catalog/asset-metrics"
        return (_with_config(_probe_call(name, endpoint, lambda: client.get_json(endpoint, params={"assets": "btc"}), validate=lambda value: catalog_metrics(value), authenticated=name == "coinmetrics_pro"), client),)
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


def probe_providers(router: ProviderRouter, providers: Iterable[str] | str = "all") -> tuple[dict[str, Any], ...]:
    names = tuple(sorted(router.providers)) if providers == "all" else (providers,) if isinstance(providers, str) else tuple(providers)
    result: list[dict[str, Any]] = []
    for name in names:
        result.extend(probe_provider(router, name))
    return tuple(result)


__all__ = ["probe_provider", "probe_providers"]
