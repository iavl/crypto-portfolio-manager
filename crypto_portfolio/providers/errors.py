"""Compatibility import surface for handled provider errors."""

from .base import (
    ProviderAuthenticationError,
    ProviderDataError,
    ProviderError,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderUnavailable,
    ProviderUnsupportedMetric,
)

__all__ = [
    "ProviderAuthenticationError",
    "ProviderDataError",
    "ProviderError",
    "ProviderRateLimited",
    "ProviderResponseError",
    "ProviderUnavailable",
    "ProviderUnsupportedMetric",
]
