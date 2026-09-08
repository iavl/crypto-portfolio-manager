"""Structured, read-only blockchain liveness observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from ..metrics_registry import CHAIN_NATIVE_ASSETS, metric_definition
from ..models.policy import Policy, resolve_policy
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderCapabilities,
    ProviderDataError,
    ProviderDiagnostic,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnavailable,
    ProviderUnsupportedMetric,
)
from .http import HttpClient, classify_transport_error, redact_secrets, redact_url


HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
HALTED = "HALTED"
UNKNOWN = "UNKNOWN"
CONFLICT = "CONFLICT"
LIVENESS_STATUSES = (HEALTHY, DEGRADED, HALTED, UNKNOWN)

_DEFAULT_THRESHOLDS: dict[str, dict[str, int]] = {
    "BTC": {
        "healthy_head_age_seconds": 3600,
        "degraded_head_age_seconds": 10800,
        "halted_head_age_seconds": 10800,
        "halted_minimum_independent_sources": 2,
    },
    "ETH": {
        "healthy_head_age_seconds": 120,
        "degraded_head_age_seconds": 600,
        "halted_head_age_seconds": 600,
        "healthy_finalized_age_seconds": 1200,
        "degraded_finalized_age_seconds": 2700,
        "halted_finalized_age_seconds": 2700,
        "halted_minimum_independent_sources": 2,
    },
    "BNB": {
        "healthy_head_age_seconds": 120,
        "degraded_head_age_seconds": 600,
        "halted_head_age_seconds": 600,
        "halted_minimum_independent_sources": 2,
    },
    "SOL": {
        "healthy_finalized_age_seconds": 120,
        "degraded_finalized_age_seconds": 600,
        "halted_finalized_age_seconds": 600,
        "halted_minimum_independent_sources": 2,
    },
}

_DEFAULT_SOURCES = {
    "BTC": (
        ("btc-blockstream", "BTC", "https://blockstream.info/api/blocks", "bitcoin_esplora", 10, "blockstream"),
        ("btc-mempool", "BTC", "https://mempool.space/api/v1/blocks", "bitcoin_mempool", 20, "mempool"),
    ),
    "ETH": (
        ("eth-publicnode", "ETH", "https://ethereum-rpc.publicnode.com", "evm_json_rpc", 10, "publicnode"),
        ("eth-flashbots", "ETH", "https://rpc.flashbots.net", "evm_json_rpc", 20, "flashbots"),
    ),
    "BNB": (
        ("bnb-dataseed", "BNB", "https://bsc-dataseed.bnbchain.org", "evm_json_rpc", 10, "bnb-dataseed"),
        ("bnb-dataseed-public", "BNB", "https://bsc-dataseed-public.bnbchain.org", "evm_json_rpc", 20, "bnb-dataseed-public"),
    ),
    "SOL": (
        ("solana-mainnet", "SOL", "https://api.mainnet-beta.solana.com", "solana_json_rpc", 10, "solana-mainnet"),
        ("solana-publicnode", "SOL", "https://solana-rpc.publicnode.com", "solana_json_rpc", 20, "publicnode"),
    ),
}

_OVERRIDE_ENV = {
    "BTC": "CRYPTO_PORTFOLIO_BTC_LIVENESS_URL",
    "ETH": "CRYPTO_PORTFOLIO_ETH_RPC_URL",
    "BNB": "CRYPTO_PORTFOLIO_BNB_RPC_URL",
    "SOL": "CRYPTO_PORTFOLIO_SOL_RPC_URL",
}
_MAX_PROVIDER_CLOCK_SKEW_SECONDS = 60.0


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _url(value: Any, field: str) -> str:
    result = _text(value, field)
    parts = urlsplit(result)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"{field} must be an http or https URL")
    return result


def _optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or null")
    return value


def _optional_age(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a non-negative finite number or null")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a non-negative finite number or null")
    return result


def _safe_json_mapping(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(redact_secrets(dict(value)))
    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite JSON") from exc
    return result


@dataclass(frozen=True)
class ChainLivenessSource:
    """One structured chain-liveness source and its independence group."""

    id: str
    asset: str
    url: str
    source_type: str
    priority: int
    independent_group: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "source.id"))
        asset = _text(self.asset, "source.asset").upper()
        if asset not in CHAIN_NATIVE_ASSETS:
            raise ValueError(f"source.asset must be one of {CHAIN_NATIVE_ASSETS}")
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "url", _url(self.url, "source.url"))
        object.__setattr__(self, "source_type", _text(self.source_type, "source.source_type").lower())
        if isinstance(self.priority, bool) or not isinstance(self.priority, int) or self.priority < 0:
            raise ValueError("source.priority must be a non-negative integer")
        object.__setattr__(self, "independent_group", _text(self.independent_group, "source.independent_group"))

    @property
    def safe_url(self) -> str:
        return redact_url(self.url)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "asset": self.asset,
            "url": self.safe_url,
            "source_type": self.source_type,
            "priority": self.priority,
            "independent_group": self.independent_group,
        }


@dataclass(frozen=True)
class ChainLivenessAssessment:
    """Deterministic liveness result from structured source observations."""

    asset: str
    status: str
    checked_at: str
    confidence: str
    head_height_or_slot: int | None
    head_hash: str | None
    head_observed_at: str | None
    head_age_seconds: float | None
    finalized_height_or_slot: int | None
    finalized_observed_at: str | None
    finalized_age_seconds: float | None
    sources_checked: tuple[str, ...]
    sources_healthy: tuple[str, ...]
    source_failures: tuple[Mapping[str, Any], ...] = ()
    evidence: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        asset = _text(self.asset, "assessment.asset").upper()
        if asset not in CHAIN_NATIVE_ASSETS:
            raise ValueError(f"assessment.asset must be one of {CHAIN_NATIVE_ASSETS}")
        object.__setattr__(self, "asset", asset)
        status = _text(self.status, "assessment.status").upper()
        if status not in (*LIVENESS_STATUSES, CONFLICT):
            raise ValueError(f"assessment.status must be one of {(*LIVENESS_STATUSES, CONFLICT)}")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "checked_at", normalize_timestamp(self.checked_at, "checked_at"))
        confidence = _text(self.confidence, "assessment.confidence").upper()
        if confidence not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("assessment.confidence must be HIGH, MEDIUM, or LOW")
        object.__setattr__(self, "confidence", confidence)
        for name in ("head_height_or_slot", "finalized_height_or_slot"):
            object.__setattr__(self, name, _optional_int(getattr(self, name), f"assessment.{name}"))
        for name in ("head_observed_at", "finalized_observed_at"):
            value = getattr(self, name)
            object.__setattr__(self, name, None if value is None else normalize_timestamp(value, name))
        for name in ("head_age_seconds", "finalized_age_seconds"):
            object.__setattr__(self, name, _optional_age(getattr(self, name), f"assessment.{name}"))
        checked = tuple(_text(item, "assessment.source_id") for item in self.sources_checked)
        healthy = tuple(_text(item, "assessment.source_id") for item in self.sources_healthy)
        if len(checked) != len(set(checked)) or len(healthy) != len(set(healthy)):
            raise ValueError("assessment source IDs must be unique")
        if any(item not in checked for item in healthy):
            raise ValueError("assessment healthy sources must be checked sources")
        object.__setattr__(self, "sources_checked", checked)
        object.__setattr__(self, "sources_healthy", healthy)
        failures = tuple(
            _safe_json_mapping(item, "assessment.source_failures")
            for item in self.source_failures
        )
        object.__setattr__(self, "source_failures", failures)
        evidence = {} if self.evidence is None else self.evidence
        if not isinstance(evidence, Mapping):
            raise ValueError("assessment.evidence must be an object")
        object.__setattr__(self, "evidence", _safe_json_mapping(evidence, "assessment.evidence"))
        if self.head_hash is not None:
            object.__setattr__(self, "head_hash", _text(self.head_hash, "assessment.head_hash"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "status": self.status,
            "checked_at": self.checked_at,
            "confidence": self.confidence,
            "head_height_or_slot": self.head_height_or_slot,
            "head_hash": self.head_hash,
            "head_observed_at": self.head_observed_at,
            "head_age_seconds": self.head_age_seconds,
            "finalized_height_or_slot": self.finalized_height_or_slot,
            "finalized_observed_at": self.finalized_observed_at,
            "finalized_age_seconds": self.finalized_age_seconds,
            "sources_checked": list(self.sources_checked),
            "sources_healthy": list(self.sources_healthy),
            "source_failures": [dict(item) for item in self.source_failures],
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class _Progress:
    source: ChainLivenessSource
    head_height_or_slot: int
    head_hash: str | None
    head_observed_at: str
    head_age_seconds: float
    finalized_height_or_slot: int | None = None
    finalized_observed_at: str | None = None
    finalized_age_seconds: float | None = None
    finalized_supported: bool = True
    finalized_error: Mapping[str, Any] | None = None


def _epoch_timestamp(value: Any, field: str) -> str:
    if isinstance(value, bool):
        raise ProviderDataError(f"{field} is invalid")
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith("0x"):
            try:
                value = int(text, 16)
            except ValueError as exc:
                raise ProviderDataError(f"{field} is invalid") from exc
        else:
            try:
                value = float(text)
            except ValueError:
                return normalize_timestamp(value, field)
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ProviderDataError(f"{field} is invalid")
        if number > 100_000_000_000:
            number /= 1000
        try:
            return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), field)
        except (OverflowError, OSError, ValueError) as exc:
            raise ProviderDataError(f"{field} is invalid") from exc
    return normalize_timestamp(value, field)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ProviderDataError(f"{field} is invalid")
    try:
        if isinstance(value, str):
            text = value.strip().lower()
            result = int(text, 16) if text.startswith("0x") else int(text)
        else:
            result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProviderDataError(f"{field} is invalid") from exc
    if result < 0 or (isinstance(value, float) and result != value):
        raise ProviderDataError(f"{field} is invalid")
    return result


def _block_age(observed_at: str, checked_at: str, field: str) -> float:
    age = (parse_timestamp(checked_at) - parse_timestamp(observed_at)).total_seconds()
    if age < 0:
        if age >= -_MAX_PROVIDER_CLOCK_SKEW_SECONDS:
            return 0.0
        raise ProviderDataError(f"{field} is in the future")
    return age


def _thresholds(policy: Policy | Mapping[str, Any] | None, asset: str) -> dict[str, Any]:
    result = dict(_DEFAULT_THRESHOLDS[asset])
    raw: Any = None
    if isinstance(policy, Mapping):
        raw = policy.get("chain_liveness")
    elif policy is not None:
        raw = getattr(policy, "chain_liveness", None)
    if isinstance(raw, Mapping):
        raw_asset = raw.get(asset)
        if isinstance(raw_asset, Mapping):
            result.update(raw_asset)
    for key, value in tuple(result.items()):
        if key == "halted_minimum_independent_sources":
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise ValueError("halted_minimum_independent_sources must be an integer >= 2")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{key} must be a positive finite number")
            result[key] = float(value)
    return result


def _source_from_value(value: ChainLivenessSource | Mapping[str, Any]) -> ChainLivenessSource:
    if isinstance(value, ChainLivenessSource):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("chain liveness sources must contain ChainLivenessSource objects or mappings")
    return ChainLivenessSource(**dict(value))


def chain_liveness_sources(
    environ: Mapping[str, str] | None = None,
) -> tuple[ChainLivenessSource, ...]:
    """Return the vetted source catalog with optional local endpoint overrides."""
    environment = environ if environ is not None else os.environ
    result: list[ChainLivenessSource] = []
    for asset in CHAIN_NATIVE_ASSETS:
        override = str(environment.get(_OVERRIDE_ENV[asset], "")).strip()
        if override:
            first = _DEFAULT_SOURCES[asset][0]
            result.append(ChainLivenessSource(
                id=f"{asset.lower()}-override",
                asset=asset,
                url=override,
                source_type=first[3],
                priority=0,
                independent_group=f"{asset.lower()}-override",
            ))
        result.extend(ChainLivenessSource(*item) if not isinstance(item, ChainLivenessSource) else item for item in _DEFAULT_SOURCES[asset])
    return tuple(result)


class ChainLivenessProvider:
    """Collect current chain progress without treating transport failure as a halt."""

    name = "chain_liveness"

    def __init__(
        self,
        *,
        client: HttpClient | Any | None = None,
        clock: Any | None = None,
        policy: Policy | Mapping[str, Any] | None = None,
        environ: Mapping[str, str] | None = None,
        sources: Mapping[str, Iterable[ChainLivenessSource | Mapping[str, Any]]] | Iterable[ChainLivenessSource | Mapping[str, Any]] | None = None,
        thresholds: Mapping[str, Any] | None = None,
    ) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.policy = policy or resolve_policy()
        self.environ = dict(environ if environ is not None else os.environ)
        self._threshold_overrides = dict(thresholds or {})
        self._network_requests = 0
        catalog = chain_liveness_sources(self.environ) if sources is None else sources
        self._sources = self._normalize_sources(catalog)
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=("risk.chain_liveness_status",),
            requires_api_key=False,
        )

    @staticmethod
    def _normalize_sources(
        value: Mapping[str, Iterable[ChainLivenessSource | Mapping[str, Any]]] | Iterable[ChainLivenessSource | Mapping[str, Any]],
    ) -> dict[str, tuple[ChainLivenessSource, ...]]:
        if isinstance(value, Mapping):
            def mapped_sources():
                for sources in value.values():
                    if isinstance(sources, (ChainLivenessSource, Mapping)):
                        yield sources
                    else:
                        yield from sources

            items = mapped_sources()
        else:
            items = iter(value)
        grouped: dict[str, list[ChainLivenessSource]] = {asset: [] for asset in CHAIN_NATIVE_ASSETS}
        for item in items:
            source = _source_from_value(item)
            grouped[source.asset].append(source)
        return {
            asset: tuple(sorted(values, key=lambda source: (source.priority, source.id)))
            for asset, values in grouped.items()
        }

    def sources_for(self, asset: str) -> tuple[ChainLivenessSource, ...]:
        normalized = _text(asset, "asset").upper()
        if normalized not in CHAIN_NATIVE_ASSETS:
            raise ValueError(f"chain liveness asset must be one of {CHAIN_NATIVE_ASSETS}")
        return self._sources[normalized]

    def _now(self, value: Any | None = None) -> str:
        current = value if value is not None else (self.clock() if callable(self.clock) else datetime.now(timezone.utc))
        if isinstance(current, datetime):
            current = current.isoformat()
        return normalize_timestamp(current, "checked_at")

    def _settings(self, asset: str) -> dict[str, Any]:
        settings = _thresholds(self.policy, asset)
        raw = self._threshold_overrides.get(asset)
        if isinstance(raw, Mapping):
            settings.update(raw)
        return _thresholds({"chain_liveness": {asset: settings}}, asset)

    def _get(self, source: ChainLivenessSource) -> Any:
        self._network_requests += 1
        return self.client.get_json(source.url)

    def _post(self, source: ChainLivenessSource, method: str, params: list[Any]) -> Any:
        self._network_requests += 1
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        post = getattr(self.client, "post_json", None)
        if callable(post):
            return post(source.url, json_body=body, idempotent=True)
        request_json = getattr(self.client, "request_json", None)
        if callable(request_json):
            return request_json("POST", source.url, json_body=body, idempotent=True)
        raise ProviderUnsupportedMetric("chain liveness client has no JSON POST method")

    @staticmethod
    def _rpc_result(payload: Any, method: str) -> Any:
        if method.startswith("eth_getBlockByNumber") and isinstance(payload, Mapping) and "number" in payload:
            return payload
        if not isinstance(payload, Mapping):
            raise ProviderResponseError(f"{method} RPC response must be an object")
        if payload.get("error") is not None:
            raise ProviderResponseError(f"{method} RPC response contains an error")
        if "result" not in payload:
            raise ProviderResponseError(f"{method} RPC response has no result")
        return payload["result"]

    def _btc_progress(self, source: ChainLivenessSource, checked_at: str) -> _Progress:
        payload = self._get(source)
        rows: Any = payload.get("blocks") if isinstance(payload, Mapping) and "blocks" in payload else payload
        if isinstance(rows, Mapping):
            rows = [rows]
        if not isinstance(rows, list) or not rows:
            raise ProviderResponseError("Bitcoin block response must contain a non-empty block list")
        parsed: list[tuple[int, Mapping[str, Any]]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ProviderDataError("Bitcoin block response contains a malformed row")
            parsed.append((_integer(row.get("height"), "Bitcoin tip height"), row))
        height, row = max(parsed, key=lambda item: item[0])
        block_hash = row.get("id", row.get("hash"))
        block_hash = _text(block_hash, "Bitcoin tip hash")
        observed_at = _epoch_timestamp(row.get("timestamp", row.get("time")), "Bitcoin tip timestamp")
        return _Progress(
            source=source,
            head_height_or_slot=height,
            head_hash=block_hash,
            head_observed_at=observed_at,
            head_age_seconds=_block_age(observed_at, checked_at, "Bitcoin tip timestamp"),
        )

    def _evm_progress(self, source: ChainLivenessSource, checked_at: str) -> _Progress:
        latest = self._rpc_result(self._post(source, "eth_getBlockByNumber", ["latest", False]), "eth_getBlockByNumber latest")
        if not isinstance(latest, Mapping):
            raise ProviderDataError("latest EVM block result must be an object")
        head_height = _integer(latest.get("number"), "EVM latest block number")
        head_hash = latest.get("hash")
        head_hash = _text(head_hash, "EVM latest block hash")
        head_observed_at = _epoch_timestamp(latest.get("timestamp"), "EVM latest block timestamp")
        head_age = _block_age(head_observed_at, checked_at, "EVM latest block timestamp")
        if source.asset != "ETH":
            return _Progress(
                source=source,
                head_height_or_slot=head_height,
                head_hash=head_hash,
                head_observed_at=head_observed_at,
                head_age_seconds=head_age,
            )
        finalized_height = None
        finalized_observed_at = None
        finalized_age = None
        finalized_supported = True
        finalized_error = None
        try:
            finalized = self._rpc_result(
                self._post(source, "eth_getBlockByNumber", ["finalized", False]),
                "eth_getBlockByNumber finalized",
            )
            if finalized is None:
                raise ProviderUnsupportedMetric("EVM endpoint returned no finalized block")
            if not isinstance(finalized, Mapping):
                raise ProviderDataError("finalized EVM block result must be an object")
            finalized_height = _integer(finalized.get("number"), "EVM finalized block number")
            if finalized_height > head_height:
                raise ProviderDataError("EVM finalized block is ahead of latest block")
            finalized_observed_at = _epoch_timestamp(finalized.get("timestamp"), "EVM finalized block timestamp")
            finalized_age = _block_age(finalized_observed_at, checked_at, "EVM finalized block timestamp")
        except Exception as exc:
            detail = str(exc).lower()
            unsupported = isinstance(exc, ProviderUnsupportedMetric) or any(
                marker in detail
                for marker in ("unsupported", "method not found", "unknown block", "invalid params", "no finalized")
            )
            if not unsupported:
                finalized_error = self._failure(source, exc, phase="finalized")
            finalized_supported = False
        return _Progress(
            source=source,
            head_height_or_slot=head_height,
            head_hash=head_hash,
            head_observed_at=head_observed_at,
            head_age_seconds=head_age,
            finalized_height_or_slot=finalized_height,
            finalized_observed_at=finalized_observed_at,
            finalized_age_seconds=finalized_age,
            finalized_supported=finalized_supported,
            finalized_error=finalized_error,
        )

    def _solana_progress(self, source: ChainLivenessSource, checked_at: str) -> _Progress:
        health = self._rpc_result(self._post(source, "getHealth", []), "getHealth")
        if health != "ok":
            raise ProviderDataError("Solana getHealth did not return ok")
        slot = _integer(
            self._rpc_result(self._post(source, "getSlot", [{"commitment": "finalized"}]), "getSlot"),
            "Solana finalized slot",
        )
        block_time = self._rpc_result(self._post(source, "getBlockTime", [slot]), "getBlockTime")
        observed_at = _epoch_timestamp(block_time, "Solana finalized block time")
        return _Progress(
            source=source,
            head_height_or_slot=slot,
            head_hash=None,
            head_observed_at=observed_at,
            head_age_seconds=_block_age(observed_at, checked_at, "Solana finalized block time"),
            finalized_height_or_slot=slot,
            finalized_observed_at=observed_at,
            finalized_age_seconds=_block_age(observed_at, checked_at, "Solana finalized block time"),
        )

    def _collect_source(self, source: ChainLivenessSource, checked_at: str) -> _Progress:
        if source.source_type in {"bitcoin_esplora", "bitcoin_mempool"}:
            return self._btc_progress(source, checked_at)
        if source.source_type == "evm_json_rpc":
            return self._evm_progress(source, checked_at)
        if source.source_type == "solana_json_rpc":
            return self._solana_progress(source, checked_at)
        raise ProviderUnsupportedMetric(f"unsupported chain liveness source type {source.source_type}")

    @staticmethod
    def _failure(source: ChainLivenessSource, error: BaseException, *, phase: str | None = None) -> dict[str, Any]:
        diagnostic = getattr(error, "diagnostic", None)
        if hasattr(diagnostic, "as_dict"):
            details = dict(diagnostic.as_dict())
        elif isinstance(diagnostic, Mapping):
            details = dict(diagnostic)
        else:
            details = {}
        code = str(details.get("error_code") or classify_transport_error(error, phase=phase)).upper()
        return {
            "source_id": source.id,
            "independent_group": source.independent_group,
            "endpoint": redact_url(source.url),
            "method": "GET" if source.source_type.startswith("bitcoin_") else "POST",
            "error_code": code,
            "exception_class": str(details.get("exception_class", error.__class__.__name__)),
            "detail": redact_secrets(str(details.get("detail", str(error)))) or error.__class__.__name__,
        }

    def _needs_secondary(self, asset: str, progress: _Progress) -> bool:
        settings = self._settings(asset)
        if asset == "SOL":
            return progress.finalized_age_seconds is None or progress.finalized_age_seconds > settings["degraded_finalized_age_seconds"]
        if progress.head_age_seconds > settings["degraded_head_age_seconds"]:
            return True
        return progress.finalized_age_seconds is not None and progress.finalized_age_seconds > settings.get("degraded_finalized_age_seconds", math.inf)

    def _severe(self, asset: str, progress: _Progress) -> bool:
        settings = self._settings(asset)
        if progress.head_age_seconds > settings.get("halted_head_age_seconds", math.inf):
            return True
        return progress.finalized_age_seconds is not None and progress.finalized_age_seconds > settings.get("halted_finalized_age_seconds", math.inf)

    def _degraded(self, asset: str, progress: _Progress) -> bool:
        settings = self._settings(asset)
        if asset == "SOL":
            return progress.finalized_age_seconds is None or progress.finalized_age_seconds > settings["healthy_finalized_age_seconds"]
        if progress.head_age_seconds > settings["healthy_head_age_seconds"]:
            return True
        return progress.finalized_age_seconds is not None and progress.finalized_age_seconds > settings.get("healthy_finalized_age_seconds", math.inf)

    def _conflicts(self, asset: str, progress: tuple[_Progress, ...]) -> bool:
        settings = self._settings(asset)
        recent_limit = max(
            float(settings.get("degraded_head_age_seconds", 0)),
            float(settings.get("degraded_finalized_age_seconds", 0)),
            1.0,
        )
        for left_index, left in enumerate(progress):
            for right in progress[left_index + 1:]:
                both_recent = left.head_age_seconds <= recent_limit and right.head_age_seconds <= recent_limit
                if (
                    left.head_height_or_slot == right.head_height_or_slot
                    and left.head_hash is not None
                    and right.head_hash is not None
                    and left.head_hash != right.head_hash
                ):
                    return True
                if both_recent and abs(left.head_height_or_slot - right.head_height_or_slot) > 6:
                    return True
                if (
                    left.finalized_height_or_slot is not None
                    and right.finalized_height_or_slot is not None
                    and both_recent
                    and abs(left.finalized_height_or_slot - right.finalized_height_or_slot) > 6
                ):
                    return True
        return False

    @staticmethod
    def _best(progress: tuple[_Progress, ...]) -> _Progress:
        return min(
            progress,
            key=lambda item: (
                item.head_age_seconds,
                item.finalized_age_seconds if item.finalized_age_seconds is not None else -1,
                item.source.priority,
            ),
        )

    def _assessment(
        self,
        asset: str,
        checked_at: str,
        attempted: tuple[str, ...],
        progress: tuple[_Progress, ...],
        failures: tuple[Mapping[str, Any], ...],
    ) -> ChainLivenessAssessment:
        settings = self._settings(asset)
        evidence_progress = tuple({
            "source_id": item.source.id,
            "independent_group": item.source.independent_group,
            "head_height_or_slot": item.head_height_or_slot,
            "head_hash": item.head_hash,
            "head_observed_at": item.head_observed_at,
            "head_age_seconds": item.head_age_seconds,
            "finalized_height_or_slot": item.finalized_height_or_slot,
            "finalized_observed_at": item.finalized_observed_at,
            "finalized_age_seconds": item.finalized_age_seconds,
            "head_finalized_distance": (
                item.head_height_or_slot - item.finalized_height_or_slot
                if item.finalized_height_or_slot is not None else None
            ),
            "finalized_supported": item.finalized_supported,
        } for item in progress)
        common_evidence = {
            "progress": evidence_progress,
            "independent_groups": tuple(dict.fromkeys(item.source.independent_group for item in progress)),
            "thresholds": settings,
        }
        if not progress:
            return ChainLivenessAssessment(
                asset, UNKNOWN, checked_at, "LOW", None, None, None, None,
                None, None, None, attempted, (), failures, common_evidence,
            )
        best = self._best(progress)
        conflict = self._conflicts(asset, progress)
        severe = tuple(item for item in progress if self._severe(asset, item))
        severe_groups = {item.source.independent_group for item in severe}
        required_sources = int(settings["halted_minimum_independent_sources"])
        if not conflict and len(severe_groups) >= required_sources:
            status = HALTED
        elif conflict:
            status = CONFLICT
        else:
            status = DEGRADED if self._degraded(asset, best) else HEALTHY
        if status == "HALTED":
            confidence = "HIGH"
        elif status == "CONFLICT" or status == DEGRADED:
            confidence = "LOW"
        elif failures or len(progress) > 1 or not best.finalized_supported:
            confidence = "MEDIUM"
        else:
            confidence = "HIGH"
        if any(item.finalized_error for item in progress):
            common_evidence["finalized_provider_warnings"] = tuple(
                item.finalized_error for item in progress if item.finalized_error
            )
        healthy = tuple(item.source.id for item in progress)
        return ChainLivenessAssessment(
            asset=asset,
            status=status,
            checked_at=checked_at,
            confidence=confidence,
            head_height_or_slot=best.head_height_or_slot,
            head_hash=best.head_hash,
            head_observed_at=best.head_observed_at,
            head_age_seconds=best.head_age_seconds,
            finalized_height_or_slot=best.finalized_height_or_slot,
            finalized_observed_at=best.finalized_observed_at,
            finalized_age_seconds=best.finalized_age_seconds,
            sources_checked=attempted,
            sources_healthy=healthy,
            source_failures=failures,
            evidence=common_evidence,
        )

    def assess(self, asset: str, *, checked_at: str | datetime | None = None) -> ChainLivenessAssessment:
        normalized_asset = _text(asset, "asset").upper()
        if normalized_asset not in CHAIN_NATIVE_ASSETS:
            raise ValueError(f"chain liveness asset must be one of {CHAIN_NATIVE_ASSETS}")
        checked = self._now(checked_at)
        self._network_requests = 0
        attempted: list[str] = []
        progress: list[_Progress] = []
        failures: list[Mapping[str, Any]] = []
        for source in self.sources_for(normalized_asset):
            attempted.append(source.id)
            try:
                current = self._collect_source(source, checked)
            except Exception as exc:
                failures.append(self._failure(source, exc))
                continue
            progress.append(current)
            if len(progress) >= 2 or not self._needs_secondary(normalized_asset, current):
                break
        return self._assessment(
            normalized_asset,
            checked,
            tuple(attempted),
            tuple(progress),
            tuple(failures),
        )

    def _observation(self, assessment: ChainLivenessAssessment) -> Mapping[str, Any]:
        metric_definition("risk.chain_liveness_status")
        metadata: dict[str, Any] = {
            "chain": assessment.asset,
            "confidence": assessment.confidence,
            "provider_source_ids": list(assessment.sources_healthy),
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
            "independent_sources": list(assessment.evidence.get("independent_groups", ())),
        }
        if assessment.source_failures:
            metadata["source_failures"] = [dict(item) for item in assessment.source_failures]
        return {
            "asset": assessment.asset,
            "metric_key": "risk.chain_liveness_status",
            "value": assessment.status,
            "observed_at": assessment.checked_at,
            "fetched_at": assessment.checked_at,
            "source": self.name,
            "confidence": assessment.confidence,
            "summary": "Current canonical chain progress assessed from structured RPC/block sources.",
            "metadata": metadata,
        }

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if not isinstance(request, ProviderRequest):
            raise ValueError("request must be a ProviderRequest")
        if request.dataset != self.name:
            raise ProviderUnsupportedMetric(f"{self.name} does not support dataset {request.dataset}")
        if request.asset not in CHAIN_NATIVE_ASSETS:
            raise ProviderUnsupportedMetric(f"{self.name} does not apply to {request.asset}")
        if request.metric_keys != ("risk.chain_liveness_status",):
            raise ProviderUnsupportedMetric(f"{self.name} supports only risk.chain_liveness_status")
        checked = self._now()
        requested_as_of = request.parameters.get("as_of")
        if requested_as_of is not None and parse_timestamp(requested_as_of) < parse_timestamp(checked):
            raise ProviderUnavailable(
                "historical chain liveness requires a stored observation",
                diagnostic=ProviderDiagnostic(
                    endpoint=None,
                    method="GET",
                    error_code="HISTORICAL_LIVENESS_UNAVAILABLE",
                    detail="current structured chain state is not valid historical evidence",
                    retryable=False,
                ),
            )
        assessment = self.assess(request.asset, checked_at=checked)
        if assessment.status == UNKNOWN:
            detail = "; ".join(
                f"{item.get('source_id')}: {item.get('error_code')}" for item in assessment.source_failures
            ) or "no trustworthy structured chain progress"
            endpoint = assessment.source_failures[-1].get("endpoint") if assessment.source_failures else None
            method = assessment.source_failures[-1].get("method", "GET") if assessment.source_failures else "GET"
            raise ProviderUnavailable(
                f"chain liveness evidence unavailable: {detail}",
                diagnostic=ProviderDiagnostic(
                    endpoint=endpoint,
                    method=method,
                    error_code="CHAIN_LIVENESS_UNAVAILABLE",
                    detail=detail,
                    retryable=False,
                ),
            )
        if assessment.status == CONFLICT:
            raise ProviderDataError(
                "structured chain liveness sources materially conflict",
                diagnostic=ProviderDiagnostic(
                    error_code="CHAIN_LIVENESS_CONFLICT",
                    detail="independent sources reported incompatible canonical state",
                    retryable=False,
                ),
            )
        return ProviderResponse(
            observations=(self._observation(assessment),),
            network_requests=self._network_requests,
        )


__all__ = [
    "CHAIN_NATIVE_ASSETS",
    "CONFLICT",
    "DEGRADED",
    "HALTED",
    "HEALTHY",
    "LIVENESS_STATUSES",
    "UNKNOWN",
    "ChainLivenessAssessment",
    "ChainLivenessProvider",
    "ChainLivenessSource",
    "chain_liveness_sources",
]
