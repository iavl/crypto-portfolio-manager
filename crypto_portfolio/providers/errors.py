"""Compatibility import surface for handled provider errors."""

from .base import (
    ProviderAuthenticationError,
    ProviderDataError,
    ProviderDiagnostic,
    ProviderError,
    ProviderInsufficientHistory,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderUnavailable,
    ProviderUnsupportedMetric,
    ProviderNotApplicable,
)

__all__ = [
    "ProviderAuthenticationError",
    "ProviderDataError",
    "ProviderDiagnostic",
    "ProviderError",
    "ProviderInsufficientHistory",
    "ProviderRateLimited",
    "ProviderResponseError",
    "ProviderUnavailable",
    "ProviderUnsupportedMetric",
    "ProviderNotApplicable",
]
