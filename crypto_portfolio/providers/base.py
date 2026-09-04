"""Provider contracts shared by the on-demand acquisition layer."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from ..metrics_registry import normalize_metric_key
from ..models.market import OHLCVSeries, SpotPrice


_SECRET_PARAMETER_NAMES = {"api_key", "apikey", "api_secret", "authorization", "cookie", "password", "secret", "token"}


def _public_parameters(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _public_parameters(item)
            for key, item in value.items()
            if str(key).strip().lower().replace("-", "_") not in _SECRET_PARAMETER_NAMES
            and "api_key" not in str(key).strip().lower().replace("-", "_")
        }
    if isinstance(value, (list, tuple)):
        return [_public_parameters(item) for item in value]
    return value


class FetchMode(str, Enum):
    """Controls whether acquisition may reuse or refresh local data."""

    AUTO = "AUTO"
    CACHE_ONLY = "CACHE_ONLY"
    REFRESH = "REFRESH"

    @classmethod
    def parse(cls, value: Any = None) -> "FetchMode":
        if value is None:
            return cls.AUTO
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError("fetch mode must be AUTO, CACHE_ONLY, or REFRESH")
        try:
            return cls(value.strip().upper())
        except ValueError as exc:
            raise ValueError("fetch mode must be AUTO, CACHE_ONLY, or REFRESH") from exc


class ProviderError(RuntimeError):
    """Base class for a provider failure that can be handled by the router."""


class ProviderUnavailable(ProviderError):
    """The provider or its network endpoint is temporarily unavailable."""


class ProviderRateLimited(ProviderError):
    """The provider rejected a request because of rate limiting."""


class ProviderAuthenticationError(ProviderError):
    """Configured provider credentials are missing or rejected."""


class ProviderResponseError(ProviderError):
    """The provider returned an invalid or unexpected response."""


class ProviderDataError(ProviderError):
    """The response shape or values cannot be normalized safely."""


class ProviderUnsupportedMetric(ProviderError):
    """The provider does not support a requested metric."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    metric_keys: tuple[str, ...] = ()
    historical_series: tuple[str, ...] = ()
    supports_batching: bool = False
    requires_api_key: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _text(self.provider, "provider").lower())
        keys = tuple(dict.fromkeys(normalize_metric_key(key) for key in self.metric_keys))
        series = tuple(dict.fromkeys(normalize_metric_key(key) for key in self.historical_series))
        if not isinstance(self.supports_batching, bool) or not isinstance(self.requires_api_key, bool):
            raise ValueError("provider capability flags must be boolean")
        object.__setattr__(self, "metric_keys", keys)
        object.__setattr__(self, "historical_series", series)

    def supports(self, metric_key: str) -> bool:
        return normalize_metric_key(metric_key) in self.metric_keys

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "metric_keys": list(self.metric_keys),
            "historical_series": list(self.historical_series),
            "supports_batching": self.supports_batching,
            "requires_api_key": self.requires_api_key,
        }


@dataclass(frozen=True)
class ProviderRequest:
    """Deterministic, secret-free description of one provider bundle."""

    provider: str
    dataset: str
    asset: str
    parameters: Mapping[str, Any]
    metric_keys: tuple[str, ...]
    mutable: bool = True
    freshness_seconds: int | None = None

    def __post_init__(self) -> None:
        provider = _text(self.provider, "provider").lower()
        dataset = _text(self.dataset, "dataset").lower()
        asset = _text(self.asset, "asset").upper()
        if not isinstance(self.parameters, Mapping):
            raise ValueError("provider request parameters must be an object")
        keys = tuple(dict.fromkeys(normalize_metric_key(key) for key in self.metric_keys))
        if not keys:
            raise ValueError("provider request must contain at least one metric key")
        if not isinstance(self.mutable, bool):
            raise ValueError("provider request mutable must be boolean")
        if self.freshness_seconds is not None:
            if isinstance(self.freshness_seconds, bool) or not isinstance(self.freshness_seconds, int):
                raise ValueError("provider request freshness_seconds must be a positive integer or null")
            if self.freshness_seconds <= 0:
                raise ValueError("provider request freshness_seconds must be a positive integer or null")
        try:
            parameters = _public_parameters(dict(self.parameters))
            # Reject non-finite values before the request reaches cache identity.
            import json

            json.dumps(parameters, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("provider request parameters must be finite JSON") from exc
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "dataset", dataset)
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "metric_keys", keys)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "dataset": self.dataset,
            "asset": self.asset,
            "parameters": dict(self.parameters),
            "metric_keys": list(self.metric_keys),
            "mutable": self.mutable,
            "freshness_seconds": self.freshness_seconds,
        }


@dataclass(frozen=True)
class ProviderResponse:
    """Optional richer provider return value; lists remain supported."""

    observations: tuple[Mapping[str, Any], ...]
    payload: Any = None
    observed_range: Mapping[str, Any] | None = None
    network_requests: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.observations, (str, bytes)):
            raise ValueError("provider observations must be a sequence")
        if isinstance(self.network_requests, bool) or not isinstance(self.network_requests, int) or self.network_requests < 0:
            raise ValueError("provider network_requests must be a non-negative integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "observations": [dict(item) for item in self.observations],
            "payload": self.payload,
            "observed_range": dict(self.observed_range) if self.observed_range else None,
            "network_requests": self.network_requests,
        }


class MarketDataProvider(Protocol):
    def candles(
        self,
        symbol: str,
        *,
        timeframe: str = "1D",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> OHLCVSeries:
        """Return normalized OHLCV candles without portfolio-side effects."""

    def spot_price(self, symbol: str) -> SpotPrice:
        """Return a normalized timestamped spot observation."""

    def prices(
        self,
        symbols: Sequence[str],
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """Return normalized spot observations without portfolio-side effects."""


class FundamentalDataProvider(Protocol):
    def fundamentals(self, symbols: Sequence[str]) -> Mapping[str, Mapping[str, Any]]:
        """Return normalized asset fundamental observations."""


class OnchainDataProvider(Protocol):
    def onchain(self, symbols: Sequence[str]) -> Mapping[str, Mapping[str, Any]]:
        """Return normalized on-chain observations."""


class EventDataProvider(Protocol):
    def events(self, symbols: Sequence[str]) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """Return normalized security, governance, and regulatory events."""


class MetricDataProvider(Protocol):
    def collect(self, collection_plan: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        """Return one structured result for each requested metric."""


class DerivativesDataProvider(Protocol):
    def derivatives(self, symbols: Sequence[str]) -> Mapping[str, Mapping[str, Any]]:
        """Return normalized, provenance-preserving derivatives observations."""


class SocialDataProvider(Protocol):
    def sentiment(self, symbols: Sequence[str]) -> Mapping[str, Mapping[str, Any]]:
        """Return structured social observations with quality metadata."""


class CycleDataProvider(Protocol):
    def cycle(self, symbol: str = "BTC") -> Mapping[str, Any]:
        """Return normalized BTC cycle/on-chain observations."""


__all__ = [
    "EventDataProvider",
    "CycleDataProvider",
    "DerivativesDataProvider",
    "FetchMode",
    "FundamentalDataProvider",
    "MarketDataProvider",
    "MetricDataProvider",
    "OnchainDataProvider",
    "ProviderAuthenticationError",
    "ProviderCapabilities",
    "ProviderDataError",
    "ProviderError",
    "ProviderRateLimited",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderResponseError",
    "ProviderUnavailable",
    "ProviderUnsupportedMetric",
    "SocialDataProvider",
]
