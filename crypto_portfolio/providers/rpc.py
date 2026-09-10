"""Small shared helpers for bounded EVM JSON-RPC range scans."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ..models.time import normalize_timestamp, parse_timestamp
from .base import ProviderResponseError


MAX_RPC_TIMESTAMP_SEARCH = 32


def _block_number(value: Any, field: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ProviderResponseError(f"{field} is malformed")
    try:
        number = int(value, 16)
    except ValueError as exc:
        raise ProviderResponseError(f"{field} is malformed") from exc
    if number < 0:
        raise ProviderResponseError(f"{field} is malformed")
    return number


def _block_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ProviderResponseError("RPC block timestamp is malformed")
    try:
        timestamp = int(value, 16)
        return normalize_timestamp(datetime.fromtimestamp(timestamp, timezone.utc).isoformat(), "block timestamp")
    except (OverflowError, OSError, TypeError, ValueError) as exc:
        raise ProviderResponseError("RPC block timestamp is malformed") from exc


def find_block_at_or_before_timestamp(
    rpc_endpoint: str,
    target_timestamp: str,
    latest_block: int,
    *,
    client: Any,
    max_calls: int = MAX_RPC_TIMESTAMP_SEARCH,
) -> int:
    """Find the greatest block whose canonical timestamp is at or before a target."""
    if not isinstance(latest_block, int) or isinstance(latest_block, bool) or latest_block < 0:
        raise ValueError("latest_block must be a non-negative integer")
    if not isinstance(max_calls, int) or isinstance(max_calls, bool) or max_calls < 1:
        raise ValueError("max_calls must be a positive integer")
    target = int(parse_timestamp(normalize_timestamp(target_timestamp, "target_timestamp")).timestamp())
    low, high = 0, latest_block
    best = 0
    calls = 0
    while low <= high:
        if calls >= max_calls:
            raise ProviderResponseError("RPC_BLOCK_TIMESTAMP_SEARCH_LIMIT")
        midpoint = (low + high) // 2
        response = client.post_json(
            rpc_endpoint,
            json_body={
                "jsonrpc": "2.0",
                "id": 10 + calls,
                "method": "eth_getBlockByNumber",
                "params": [hex(midpoint), False],
            },
            idempotent=True,
        )
        if (
            not isinstance(response, Mapping)
            or response.get("error") is not None
            or not isinstance(response.get("result"), Mapping)
        ):
            raise ProviderResponseError("RPC block timestamp response is malformed")
        block = response["result"]
        block_number = _block_number(block.get("number", hex(midpoint)), "RPC block number")
        if block_number != midpoint or block_number > latest_block:
            raise ProviderResponseError("RPC block timestamp response has an unexpected block number")
        timestamp = int(parse_timestamp(_block_timestamp(block.get("timestamp"))).timestamp())
        calls += 1
        if timestamp <= target:
            best = midpoint
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


__all__ = ["MAX_RPC_TIMESTAMP_SEARCH", "find_block_at_or_before_timestamp"]
