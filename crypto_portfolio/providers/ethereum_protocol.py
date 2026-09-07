"""Standard Ethereum execution-data helpers; no web3 dependency."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping

from ..models.time import normalize_timestamp
from .base import (
    ProviderCapabilities,
    ProviderDataError,
    ProviderInsufficientHistory,
    ProviderRequest,
    ProviderResponse,
)
from .http import HttpClient


BASE_URLS = (
    "https://ethereum-rpc.publicnode.com",
    "https://rpc.flashbots.net",
)
MIN_BLOB_BASE_FEE = 1
BLOB_BASE_FEE_UPDATE_FRACTION = 3_338_477
WEI_PER_ETH = 10**18


def _number(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ProviderDataError(f"{field} must be an integer or hex string")
    try:
        result = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} must be an integer or hex string") from exc
    if result < minimum:
        raise ProviderDataError(f"{field} must be >= {minimum}")
    return result


def fake_exponential(factor: int, numerator: int, denominator: int) -> int:
    """EIP-4844 fake exponential using integer arithmetic."""
    factor = _number(factor, "factor")
    numerator = _number(numerator, "numerator")
    denominator = _number(denominator, "denominator", minimum=1)
    output = 0
    accumulator = factor * denominator
    index = 1
    while accumulator:
        output += accumulator
        accumulator = accumulator * numerator // (denominator * index)
        index += 1
    return output // denominator


def execution_base_fee_burn(block: Mapping[str, Any]) -> int:
    return _number(block.get("baseFeePerGas"), "baseFeePerGas") * _number(block.get("gasUsed"), "gasUsed")


def blob_base_fee(block: Mapping[str, Any]) -> int:
    return fake_exponential(
        MIN_BLOB_BASE_FEE,
        _number(block.get("excessBlobGas", 0), "excessBlobGas"),
        BLOB_BASE_FEE_UPDATE_FRACTION,
    )


def blob_fee_burn(block: Mapping[str, Any]) -> int:
    return blob_base_fee(block) * _number(block.get("blobGasUsed", 0), "blobGasUsed")


def block_burn_eth(block: Mapping[str, Any]) -> float:
    wei = execution_base_fee_burn(block) + blob_fee_burn(block)
    result = wei / WEI_PER_ETH
    if not math.isfinite(result):
        raise ProviderDataError("block burn is not finite")
    return result


def parse_block_burns(
    blocks: Iterable[Mapping[str, Any]],
    metric_key: str,
    *,
    fetched_at: str,
    source: str = "ethereum_rpc",
) -> Mapping[str, Any]:
    values = tuple(blocks)
    if not values:
        raise ProviderInsufficientHistory("Ethereum block history is empty")
    burns = [block_burn_eth(block) for block in values]
    timestamps = [block.get("timestamp") for block in values if block.get("timestamp") is not None]
    observed_at = normalize_timestamp(datetime.now(timezone.utc).isoformat(), "observed_at")
    if timestamps:
        timestamp = _number(timestamps[-1], "block timestamp")
        observed_at = normalize_timestamp(datetime.fromtimestamp(timestamp, timezone.utc).isoformat(), "observed_at")
    return {
        "asset": "ETH",
        "metric_key": metric_key,
        "value": sum(burns),
        "unit": "ETH",
        "period": "30d" if "30d" in metric_key else "365d",
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "source": source,
        "confidence": "HIGH",
        "metadata": {
            "source_dataset": "ethereum_execution_block",
            "methodology": "baseFeePerGas * gasUsed + EIP-4844 blob base fee * blobGasUsed",
            "block_count": len(values),
        },
    }


class EthereumProtocolProvider:
    """Protocol parser/provider for bounded, caller-supplied block batches."""

    name = "ethereum_protocol"

    def __init__(self, *, client: HttpClient | Any | None = None, rpc_url: str = BASE_URLS[0]) -> None:
        self.client = client or HttpClient()
        self.rpc_url = rpc_url
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=("eth.monetary.burn_30d_eth", "eth.monetary.burn_365d_eth"),
            historical_series=("eth.monetary.burn_30d_eth", "eth.monetary.burn_365d_eth"),
            supports_batching=True,
            requires_api_key=False,
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        blocks = request.parameters.get("blocks")
        if not isinstance(blocks, list):
            raise ProviderInsufficientHistory(
                "Ethereum RPC block history must be supplied by a bounded local/range collector"
            )
        fetched_at = normalize_timestamp(datetime.now(timezone.utc).isoformat(), "fetched_at")
        observations = tuple(
            parse_block_burns(blocks, key, fetched_at=fetched_at)
            for key in request.metric_keys
        )
        return ProviderResponse(observations=observations, network_requests=0)


__all__ = [
    "BASE_URLS",
    "BLOB_BASE_FEE_UPDATE_FRACTION",
    "EthereumProtocolProvider",
    "blob_base_fee",
    "blob_fee_burn",
    "block_burn_eth",
    "execution_base_fee_burn",
    "fake_exponential",
    "parse_block_burns",
]
