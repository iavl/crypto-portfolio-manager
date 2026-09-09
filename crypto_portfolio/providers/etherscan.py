"""Optional Etherscan v2 Ethereum supply/cumulative-burn adapter."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp
from .base import (
    ProviderAuthenticationError,
    ProviderCapabilities,
    ProviderDataError,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient, redact_secrets


BASE_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID = "1"
ACTION = "ethsupply2"
_CURRENT_SUPPLY = "eth.monetary.current_supply_eth"
_CUMULATIVE_BURN = "eth.monetary.cumulative_burn_eth"
WEI_PER_ETH = 10**18


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")


def _wei(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProviderDataError(f"Etherscan {field} is not numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"Etherscan {field} is not numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise ProviderDataError(f"Etherscan {field} is invalid")
    return result / WEI_PER_ETH


def parse_ethsupply2(
    payload: Any,
    metric_keys: tuple[str, ...] | list[str],
    *,
    fetched_at: str,
    endpoint: str = BASE_URL,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("Etherscan response is not an object")
    if str(payload.get("status", "")).strip() != "1":
        message = redact_secrets(str(payload.get("message", "")))
        raise ProviderResponseError(f"Etherscan response is not successful{': ' + message if message else ''}")
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ProviderResponseError("Etherscan result is not an object")
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    values: list[Mapping[str, Any]] = []
    for key in requested:
        if key == _CURRENT_SUPPLY:
            field = "EthSupply"
        elif key == _CUMULATIVE_BURN:
            field = "BurntFees"
        else:
            raise ProviderUnsupportedMetric(f"Etherscan does not support {key}")
        if field not in result:
            raise ProviderDataError(f"Etherscan result is missing {field}")
        values.append({
            "asset": "ETH",
            "metric_key": key,
            "value": _wei(result[field], field),
            "unit": metric_definition(key).unit,
            "period": "current",
            "observed_at": normalize_timestamp(fetched_at, "observed_at"),
            "fetched_at": normalize_timestamp(fetched_at, "fetched_at"),
            "source": "etherscan",
            "confidence": "MEDIUM",
            "metadata": {
                "source_dataset": "stats/ethsupply2",
                "source_url": endpoint,
                "chain_id": CHAIN_ID,
                "methodology": "Etherscan documented wei counter converted to ETH",
                "field": field,
            },
        })
    return tuple(values)


def _diagnostic(error: BaseException) -> Mapping[str, Any]:
    value = getattr(error, "diagnostic", None)
    if hasattr(value, "as_dict"):
        return dict(redact_secrets(value.as_dict()))
    if isinstance(value, Mapping):
        return dict(redact_secrets(dict(value)))
    return {
        "error_code": "PROVIDER_UNSUPPORTED" if isinstance(error, ProviderUnsupportedMetric) else "PROVIDER_SCHEMA_ERROR",
        "detail": redact_secrets(str(error)) or error.__class__.__name__,
    }


class EtherscanProvider:
    name = "etherscan"

    def __init__(self, *, client: HttpClient | Any | None = None, api_key: str | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.api_key = api_key
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=(_CURRENT_SUPPLY, _CUMULATIVE_BURN),
            historical_series=(_CURRENT_SUPPLY, _CUMULATIVE_BURN),
            supports_batching=True,
            requires_api_key=True,
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.asset != "ETH":
            raise ProviderUnsupportedMetric("Etherscan supply metrics require ETH scope")
        if not self.api_key:
            raise ProviderAuthenticationError("Etherscan API key is not configured")
        try:
            payload = self.client.get_json(
                BASE_URL,
                params={
                    "module": "stats",
                    "action": ACTION,
                    "chainid": CHAIN_ID,
                    "apikey": self.api_key,
                },
            )
        except ProviderError as exc:
            diagnostic = exc.diagnostic
            if hasattr(diagnostic, "as_dict"):
                from .base import ProviderDiagnostic

                diagnostic = ProviderDiagnostic(**redact_secrets(diagnostic.as_dict(), (self.api_key,)))
            elif isinstance(diagnostic, Mapping):
                diagnostic = redact_secrets(dict(diagnostic), (self.api_key,))
            raise exc.__class__(redact_secrets(str(exc), (self.api_key,)), diagnostic=diagnostic) from None
        except Exception as exc:
            raise ProviderResponseError(redact_secrets(str(exc), (self.api_key,))) from None
        fetched_at = _now(self.clock)
        observations: list[Mapping[str, Any]] = []
        diagnostics: dict[str, Mapping[str, Any]] = {}
        for key in request.metric_keys:
            try:
                observations.extend(parse_ethsupply2(payload, (key,), fetched_at=fetched_at))
            except (ProviderError, ValueError) as exc:
                diagnostics[key] = _diagnostic(exc)
        return ProviderResponse(observations=tuple(observations), diagnostics=diagnostics, network_requests=1)


__all__ = [
    "ACTION",
    "BASE_URL",
    "CHAIN_ID",
    "EtherscanProvider",
    "parse_ethsupply2",
]
