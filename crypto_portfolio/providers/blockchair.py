"""Structured Blockchair provider for Ethereum rolling transfer volume."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from typing import Any, Mapping

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderCapabilities,
    ProviderDataError,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient


BASE_URL = "https://api.blockchair.com"
ETHEREUM_STATS_PATH = "/ethereum/stats"
SUPPORTED_ASSETS = ("ETH",)
SUPPORTED_METRICS = ("onchain.transfer_volume",)
WEI_PER_ETH = Decimal("1000000000000000000")


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        raise ProviderDataError(f"Blockchair {field} is not numeric")
    if isinstance(value, str) and not value.strip():
        raise ProviderDataError(f"Blockchair {field} is not numeric")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ProviderDataError(f"Blockchair {field} is not numeric") from exc
    if not result.is_finite():
        raise ProviderDataError(f"Blockchair {field} is not finite")
    return result


def _observed_at(value: Any, fetched_at: str) -> str:
    if value is None:
        return fetched_at
    if not isinstance(value, str) or not value.strip():
        raise ProviderDataError("Blockchair best_block_time is invalid")
    text = value.strip().replace(" ", "T", 1)
    if len(text) < 11 or text[10] not in {"T", "t"}:
        raise ProviderDataError("Blockchair best_block_time is invalid")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            text += "Z"
        return normalize_timestamp(text, "Blockchair best_block_time")
    except ValueError as exc:
        raise ProviderDataError("Blockchair best_block_time is invalid") from exc


def _context(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("context")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ProviderResponseError("Blockchair context must be an object")
    code = value.get("code")
    if code is not None:
        try:
            successful = not isinstance(code, bool) and int(str(code)) == 200
        except (TypeError, ValueError):
            successful = False
        if not successful:
            raise ProviderResponseError("Blockchair response context code is not successful")
    return value


def _nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderDataError(f"Blockchair {field} is invalid")
    return value


def _metadata_scalar(value: Any) -> Any:
    if isinstance(value, bool) or isinstance(value, str) or isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def parse_stats_payload(
    payload: Any,
    *,
    fetched_at: str,
) -> Mapping[str, Any]:
    """Normalize one current Blockchair Ethereum stats response."""
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("Blockchair response must be an object")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ProviderResponseError("Blockchair response must contain a data object")
    context = _context(payload)
    fetched = normalize_timestamp(fetched_at, "fetched_at")

    for field in ("volume_24h_approximate", "market_price_usd"):
        if field not in data or data[field] is None:
            raise ProviderDataError(f"Blockchair {field} is missing")
    volume_wei = _decimal(data["volume_24h_approximate"], "volume_24h_approximate")
    if volume_wei < 0:
        raise ProviderDataError("Blockchair volume_24h_approximate is negative")
    market_price_usd = _decimal(data["market_price_usd"], "market_price_usd")
    if market_price_usd <= 0:
        raise ProviderDataError("Blockchair market_price_usd must be positive")

    transfer_volume_usd = volume_wei / WEI_PER_ETH * market_price_usd
    if not transfer_volume_usd.is_finite():
        raise ProviderDataError("Blockchair normalized transfer volume is not finite")
    value = float(transfer_volume_usd)
    if not math.isfinite(value):
        raise ProviderDataError("Blockchair normalized transfer volume is not finite")

    observed_at = _observed_at(data.get("best_block_time"), fetched)
    # /ethereum/stats is a rolling current gauge: best_block_time advances
    # every ~12s and is inherently later than the collection-start clock stamp
    # the acquisition layer passes as as_of, so as_of must not reject here.
    # The anti-lookahead anchor for this endpoint is fetched_at; anchoring a
    # historical as_of stays enforced by metric normalization at the
    # persistence boundary.
    if parse_timestamp(observed_at) > parse_timestamp(fetched):
        raise ProviderDataError("Blockchair best_block_time is after fetched_at")

    metadata: dict[str, Any] = {
        "source_dataset": "ethereum/stats",
        "source_metric": "volume_24h_approximate",
        "price_metric": "market_price_usd",
        "raw_unit": "wei",
        "window": "rolling_24h",
        "conversion": "volume_24h_approximate / 1e18 * market_price_usd",
    }
    if "best_block_height" in data and data["best_block_height"] is not None:
        metadata["best_block_height"] = _nonnegative_integer(data["best_block_height"], "best_block_height")
    if "best_block_hash" in data and data["best_block_hash"] is not None:
        block_hash = data["best_block_hash"]
        if not isinstance(block_hash, str) or not block_hash.strip():
            raise ProviderDataError("Blockchair best_block_hash is invalid")
        metadata["best_block_hash"] = block_hash.strip()
    if "best_block_time" not in data:
        metadata["observed_timestamp_source"] = "fetched_at"

    for source_field, metadata_field in (
        ("code", "context_code"),
        ("state", "context_state"),
        ("request_cost", "request_cost"),
    ):
        scalar = _metadata_scalar(context.get(source_field))
        if scalar is not None:
            metadata[metadata_field] = scalar
    cache = context.get("cache")
    if isinstance(cache, Mapping):
        for source_field, metadata_field in (
            ("live", "cache_live"),
            ("since", "cache_since"),
            ("until", "cache_until"),
        ):
            scalar = _metadata_scalar(cache.get(source_field))
            if scalar is not None:
                metadata[metadata_field] = scalar
    api = context.get("api")
    if isinstance(api, Mapping):
        scalar = _metadata_scalar(api.get("version"))
        if scalar is not None:
            metadata["api_version"] = scalar

    metric_key = SUPPORTED_METRICS[0]
    return {
        "asset": SUPPORTED_ASSETS[0],
        "metric_key": metric_key,
        "value": value,
        "unit": metric_definition(metric_key).unit,
        "period": "1d",
        "observed_at": observed_at,
        "fetched_at": fetched,
        "source": "blockchair",
        "confidence": "MEDIUM",
        "metadata": metadata,
    }


class BlockchairProvider:
    name = "blockchair"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=SUPPORTED_METRICS,
            historical_series=(),
            supports_batching=False,
            requires_api_key=False,
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if (
            request.asset not in SUPPORTED_ASSETS
            or request.dataset != "onchain"
            or request.metric_keys != SUPPORTED_METRICS
        ):
            raise ProviderUnsupportedMetric(
                "Blockchair only supports ETH onchain.transfer_volume"
            )
        payload = self.client.get_json(BASE_URL + ETHEREUM_STATS_PATH)
        return ProviderResponse(
            observations=(parse_stats_payload(
                payload,
                fetched_at=_now(self.clock),
            ),),
            network_requests=1,
        )


__all__ = [
    "BASE_URL",
    "BlockchairProvider",
    "ETHEREUM_STATS_PATH",
    "SUPPORTED_ASSETS",
    "SUPPORTED_METRICS",
    "WEI_PER_ETH",
    "parse_stats_payload",
]
