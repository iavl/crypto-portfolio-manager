"""Bounded public BSC RPC transaction-count provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from ..state.market_data import _atomic_write
from .base import (
    ProviderCapabilities,
    ProviderDataError,
    ProviderDiagnostic,
    ProviderInsufficientHistory,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .cache import ProviderCache, canonical_json
from .http import HttpClient, redact_url
from .rpc import find_block_at_or_before_timestamp


BASE_URL = "https://bsc-dataseed.bnbchain.org"
PUBLIC_ENDPOINTS = (BASE_URL, "https://bsc-dataseed-public.bnbchain.org")
TRANSACTION_COUNT_METRIC = "onchain.transaction_count"
CONFIRMATION_BLOCKS = 15
MAX_COUNT_BATCH_BLOCKS = 100
MAX_COUNT_BATCH_REQUESTS = 64
HISTORY_SCHEMA_VERSION = 1


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")


def _hex_int(value: Any, field: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ProviderDataError(f"{field} is malformed")
    try:
        result = int(value, 16)
    except ValueError as exc:
        raise ProviderDataError(f"{field} is malformed") from exc
    if result < 0:
        raise ProviderDataError(f"{field} is malformed")
    return result


def _url(value: str) -> str:
    if not isinstance(value, str) or urlsplit(value).scheme not in {"http", "https"} or not urlsplit(value).netloc:
        raise ValueError("BNB RPC URL must be an http or https URL")
    return value


def _day(value: str | datetime) -> tuple[str, datetime]:
    timestamp = parse_timestamp(value.isoformat() if isinstance(value, datetime) else value)
    start = datetime(timestamp.year, timestamp.month, timestamp.day, tzinfo=timezone.utc)
    return start.date().isoformat(), start


def _history_path(cache: ProviderCache) -> Path:
    return cache.root / "series" / "bnb_rpc" / "daily.json"


def _validate_history(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != HISTORY_SCHEMA_VERSION:
        raise ProviderDataError("BNB RPC history cache has an unsupported schema")
    if value.get("provider") != "bnb_rpc" or value.get("asset") != "BNB":
        raise ProviderDataError("BNB RPC history cache identity mismatch")
    daily = value.get("daily")
    if not isinstance(daily, Mapping):
        raise ProviderDataError("BNB RPC history cache has no daily mapping")
    normalized: dict[str, Any] = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "provider": "bnb_rpc",
        "asset": "BNB",
        "daily": {},
        "last_completed_block": value.get("last_completed_block"),
        "last_completed_block_hash": value.get("last_completed_block_hash"),
        "last_completed_timestamp": value.get("last_completed_timestamp"),
    }
    last_block = normalized["last_completed_block"]
    if last_block is not None and (isinstance(last_block, bool) or not isinstance(last_block, int) or last_block < 0):
        raise ProviderDataError("BNB RPC history last block is invalid")
    if normalized["last_completed_block_hash"] is not None and not isinstance(normalized["last_completed_block_hash"], str):
        raise ProviderDataError("BNB RPC history last block hash is invalid")
    if normalized["last_completed_timestamp"] is not None:
        try:
            normalized["last_completed_timestamp"] = normalize_timestamp(normalized["last_completed_timestamp"], "last_completed_timestamp")
        except (TypeError, ValueError) as exc:
            raise ProviderDataError("BNB RPC history last timestamp is invalid") from exc
    for raw_day, raw_value in daily.items():
        if not isinstance(raw_day, str):
            raise ProviderDataError("BNB RPC history day is invalid")
        try:
            start = datetime.fromisoformat(raw_day).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ProviderDataError("BNB RPC history day is invalid") from exc
        if start.date().isoformat() != raw_day or not isinstance(raw_value, Mapping):
            raise ProviderDataError("BNB RPC history daily entry is invalid")
        count = raw_value.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ProviderDataError("BNB RPC history transaction count is invalid")
        for field in ("start_block", "end_block"):
            number = raw_value.get(field)
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                raise ProviderDataError(f"BNB RPC history {field} is invalid")
        block_hash = raw_value.get("end_block_hash")
        if not isinstance(block_hash, str) or not block_hash.strip():
            raise ProviderDataError("BNB RPC history end block hash is invalid")
        try:
            completed = normalize_timestamp(raw_value.get("completed_through"), "completed_through")
        except (TypeError, ValueError) as exc:
            raise ProviderDataError("BNB RPC history completed boundary is invalid") from exc
        if parse_timestamp(completed) != start + timedelta(days=1):
            raise ProviderDataError("BNB RPC history completed boundary is invalid")
        normalized["daily"][raw_day] = {
            "count": count,
            "start_block": raw_value["start_block"],
            "end_block": raw_value["end_block"],
            "end_block_hash": block_hash.strip(),
            "completed_through": completed,
        }
    return normalized


class BNBRPCProvider:
    """Count canonical BSC block transactions by completed UTC day."""

    name = "bnb_rpc"

    def __init__(
        self,
        *,
        client: HttpClient | Any | None = None,
        rpc_url: str = BASE_URL,
        cache: ProviderCache | None = None,
        clock: Any | None = None,
        confirmation_blocks: int = CONFIRMATION_BLOCKS,
    ) -> None:
        self.client = client or HttpClient()
        self.rpc_url = _url(rpc_url)
        self.cache = cache or ProviderCache()
        self.clock = clock
        if isinstance(confirmation_blocks, bool) or not isinstance(confirmation_blocks, int) or confirmation_blocks < 0:
            raise ValueError("confirmation_blocks must be a non-negative integer")
        self.confirmation_blocks = confirmation_blocks
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=(TRANSACTION_COUNT_METRIC,),
            historical_series=(TRANSACTION_COUNT_METRIC,),
            supports_batching=True,
            requires_api_key=False,
        )

    def _rpc(self, method: str, params: list[Any], request_id: int) -> Any:
        response = self.client.post_json(
            self.rpc_url,
            json_body={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            idempotent=True,
        )
        if not isinstance(response, Mapping):
            raise ProviderResponseError("BNB RPC response is malformed")
        if response.get("error") is not None:
            error = response["error"] if isinstance(response["error"], Mapping) else {}
            raise ProviderResponseError(f"BNB RPC request rejected ({error.get('code', 'unknown')})")
        if "result" not in response:
            raise ProviderResponseError("BNB RPC response has no result")
        return response["result"]

    def _batch_counts(self, start_block: int, end_block: int) -> tuple[int, int]:
        batch_count = (end_block - start_block) // MAX_COUNT_BATCH_BLOCKS + 1
        if batch_count > MAX_COUNT_BATCH_REQUESTS:
            raise ProviderInsufficientHistory(
                "BNB RPC daily range exceeds the bounded block-count request budget",
                diagnostic=ProviderDiagnostic(
                    endpoint=self.rpc_url,
                    method="POST",
                    error_code="PROVIDER_INSUFFICIENT_HISTORY",
                    detail=f"daily range requires {batch_count} RPC batches; limit is {MAX_COUNT_BATCH_REQUESTS}",
                ),
            )
        total = 0
        requests = 0
        for chunk_start in range(start_block, end_block + 1, MAX_COUNT_BATCH_BLOCKS):
            if requests >= MAX_COUNT_BATCH_REQUESTS:
                raise ProviderInsufficientHistory("BNB RPC block-count request budget exhausted")
            chunk_end = min(end_block, chunk_start + MAX_COUNT_BATCH_BLOCKS - 1)
            payload = [
                {
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "eth_getBlockTransactionCountByNumber",
                    "params": [hex(block)],
                }
                for index, block in enumerate(range(chunk_start, chunk_end + 1), start=1)
            ]
            response = self.client.post_json(self.rpc_url, json_body=payload, idempotent=True)
            if not isinstance(response, list) or len(response) != len(payload):
                raise ProviderResponseError("BNB RPC batch response is malformed")
            by_id = {item.get("id"): item for item in response if isinstance(item, Mapping)}
            for item in payload:
                result = by_id.get(item["id"])
                if not isinstance(result, Mapping) or result.get("error") is not None:
                    raise ProviderResponseError("BNB RPC block count request failed")
                total += _hex_int(result.get("result"), "BNB transaction count")
            requests += 1
        return total, requests

    def _load_history(self) -> dict[str, Any]:
        path = _history_path(self.cache)
        if not path.is_file():
            return {
                "schema_version": HISTORY_SCHEMA_VERSION,
                "provider": self.name,
                "asset": "BNB",
                "daily": {},
                "last_completed_block": None,
                "last_completed_block_hash": None,
                "last_completed_timestamp": None,
            }
        try:
            return _validate_history(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderDataError("BNB RPC history cache is unreadable") from exc

    def _save_history(self, history: Mapping[str, Any]) -> None:
        path = _history_path(self.cache)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, canonical_json(history) + "\n")

    def _observation(self, day: str, value: Mapping[str, Any], fetched_at: str) -> Mapping[str, Any]:
        return {
            "asset": "BNB",
            "metric_key": TRANSACTION_COUNT_METRIC,
            "value": value["count"],
            "unit": metric_definition(TRANSACTION_COUNT_METRIC).unit,
            "period": "1d",
            "observed_at": value["completed_through"],
            "fetched_at": fetched_at,
            "freshness_reference_at": value["completed_through"],
            "source": self.name,
            "confidence": "MEDIUM",
            "metadata": {
                "source_url": redact_url(self.rpc_url),
                "source_dataset": "bsc_canonical_blocks",
                "methodology": "sum eth_getBlockTransactionCountByNumber over canonical blocks in one completed UTC day",
                "day": day,
                "start_block": value["start_block"],
                "end_block": value["end_block"],
                "end_block_hash": value["end_block_hash"],
                "confirmation_blocks": self.confirmation_blocks,
                "completed_through": value["completed_through"],
            },
        }

    def probe(self) -> Mapping[str, Any]:
        latest = _hex_int(self._rpc("eth_blockNumber", [], 1), "BNB latest block number")
        safe = max(0, latest - self.confirmation_blocks)
        block = self._rpc("eth_getBlockByNumber", [hex(safe), True], 2)
        if not isinstance(block, Mapping) or not isinstance(block.get("transactions"), list):
            raise ProviderDataError("BNB RPC probe block has no transactions array")
        number = _hex_int(block.get("number"), "BNB probe block number")
        timestamp = _hex_int(block.get("timestamp"), "BNB probe block timestamp")
        block_hash = block.get("hash")
        if number != safe or not isinstance(block_hash, str) or not block_hash.strip():
            raise ProviderDataError("BNB RPC probe block identity is malformed")
        observed = normalize_timestamp(datetime.fromtimestamp(timestamp, timezone.utc).isoformat(), "BNB probe timestamp")
        return {
            "provider": self.name,
            "rpc_method": "eth_getBlockByNumber",
            "latest_block_number": latest,
            "checked_block_number": number,
            "checked_block_timestamp": observed,
            "transactions": len(block["transactions"]),
            "confirmation_blocks": self.confirmation_blocks,
            "methodology": "canonical block transactions",
            "endpoint": redact_url(self.rpc_url),
        }

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.asset != "BNB" or request.metric_keys != (TRANSACTION_COUNT_METRIC,):
            raise ProviderUnsupportedMetric("BNB RPC only supports BNB onchain.transaction_count")
        fetched_at = _now(self.clock)
        cutoff = request.parameters.get("as_of") or fetched_at
        cutoff_timestamp = parse_timestamp(cutoff)
        day, start = _day(cutoff_timestamp - timedelta(days=1))
        history = self._load_history()
        cached = history["daily"].get(day)
        if cached is not None:
            return ProviderResponse(
                observations=(self._observation(day, cached, fetched_at),),
                payload=history,
                network_requests=0,
            )

        latest = _hex_int(self._rpc("eth_blockNumber", [], 1), "BNB latest block number")
        safe_latest = max(0, latest - self.confirmation_blocks)
        start_candidate = find_block_at_or_before_timestamp(
            self.rpc_url,
            normalize_timestamp(start.isoformat(), "day start target"),
            safe_latest,
            client=self.client,
        )
        start_metadata = self._rpc("eth_getBlockByNumber", [hex(start_candidate), False], 2)
        if not isinstance(start_metadata, Mapping):
            raise ProviderDataError("BNB RPC start block metadata is malformed")
        start_timestamp = _hex_int(start_metadata.get("timestamp"), "BNB RPC start block timestamp")
        start_block = start_candidate + int(start_timestamp < int(start.timestamp()))
        end_block = find_block_at_or_before_timestamp(
            self.rpc_url,
            normalize_timestamp((start + timedelta(days=1) - timedelta(seconds=1)).isoformat(), "day end target"),
            safe_latest,
            client=self.client,
        )
        if start_block > end_block:
            raise ProviderInsufficientHistory("BNB RPC has no complete block range for the requested UTC day")
        previous_block = history.get("last_completed_block")
        if previous_block is not None and start_block <= previous_block + 1:
            previous = self._rpc("eth_getBlockByNumber", [hex(previous_block), False], 2)
            if not isinstance(previous, Mapping) or previous.get("hash") != history.get("last_completed_block_hash"):
                raise ProviderDataError("BNB RPC cached continuity mismatch")
        count, batch_requests = self._batch_counts(start_block, end_block)
        end_metadata = self._rpc("eth_getBlockByNumber", [hex(end_block), False], 3)
        if not isinstance(end_metadata, Mapping) or end_metadata.get("hash") is None:
            raise ProviderDataError("BNB RPC end block metadata is malformed")
        end_hash = end_metadata["hash"]
        if not isinstance(end_hash, str) or not end_hash.strip():
            raise ProviderDataError("BNB RPC end block hash is malformed")
        completed_through = normalize_timestamp((start + timedelta(days=1)).isoformat(), "completed_through")
        value = {
            "count": count,
            "start_block": start_block,
            "end_block": end_block,
            "end_block_hash": end_hash.strip(),
            "completed_through": completed_through,
        }
        if day in history["daily"] and history["daily"][day] != value:
            raise ProviderDataError("BNB RPC history contains a conflicting daily value")
        history["daily"][day] = value
        if previous_block is None or end_block >= previous_block:
            history.update({
                "last_completed_block": end_block,
                "last_completed_block_hash": end_hash.strip(),
                "last_completed_timestamp": completed_through,
            })
        self._save_history(history)
        return ProviderResponse(
            observations=(self._observation(day, value, fetched_at),),
            payload=history,
            observed_range={"start": start.isoformat().replace("+00:00", "Z"), "end": completed_through},
            network_requests=1 + batch_requests + 1,
        )


BnbRPCProvider = BNBRPCProvider


__all__ = [
    "BASE_URL",
    "BnbRPCProvider",
    "BNBRPCProvider",
    "CONFIRMATION_BLOCKS",
    "MAX_COUNT_BATCH_BLOCKS",
    "MAX_COUNT_BATCH_REQUESTS",
    "PUBLIC_ENDPOINTS",
    "TRANSACTION_COUNT_METRIC",
]
