"""File-based provider response and incremental series caches."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from ..models.market import OHLCVSeries
from ..models.time import normalize_timestamp, parse_timestamp
from ..state.market_data import _atomic_write, cache_ohlcv, load_ohlcv
from ..state.snapshots import runtime_data_dir
from .base import ProviderDataError, ProviderRequest


_SAFE_COMPONENT = re.compile(r"^[a-z0-9_.-]+$")
_SECRET_NAMES = {"api_key", "apikey", "api_secret", "authorization", "cookie", "password", "secret", "token"}


def _is_secret_name(value: Any) -> bool:
    name = str(value).strip().lower().replace("-", "_")
    return name in _SECRET_NAMES or "api_key" in name or name.endswith(("_secret", "_token")) or "authorization" in name
_CACHE_VERSION = 1


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("cache value must be finite JSON") from exc


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _contains_secret_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_is_secret_name(key) or _contains_secret_field(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_field(item) for item in value)
    return False


def _safe_component(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"{field} contains unsafe path characters")
    return value


def _public_parameters(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _public_parameters(item)
            for key, item in value.items()
            if not _is_secret_name(key)
        }
    if isinstance(value, (list, tuple)):
        return [_public_parameters(item) for item in value]
    return value


def request_identity(request: ProviderRequest | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(request, ProviderRequest):
        provider = request.provider
        dataset = request.dataset
        asset = request.asset
        parameters = request.parameters
        metric_keys = request.metric_keys
    elif isinstance(request, Mapping):
        provider = str(request.get("provider", "")).strip().lower()
        dataset = str(request.get("dataset", "")).strip().lower()
        asset = str(request.get("asset", "")).strip().upper()
        parameters = request.get("parameters", {})
        metric_keys = tuple(request.get("metric_keys", ()))
    else:
        raise ValueError("request must be a ProviderRequest or mapping")
    return {
        "provider": provider,
        "dataset": dataset,
        "asset": asset,
        "parameters": _public_parameters(parameters),
        "metric_keys": list(dict.fromkeys(str(key).strip().lower() for key in metric_keys)),
    }


def request_hash(request: ProviderRequest | Mapping[str, Any]) -> str:
    return content_hash(request_identity(request))


request_cache_key = request_hash
hash_request = request_hash


class CacheError(ValueError):
    """Base class for a cache entry that cannot be used."""


class CacheCorruption(CacheError):
    """A cache file is malformed or fails its content identity."""


class CacheExpired(CacheError):
    """A mutable cache entry is past its TTL."""


def _timestamp(value: Any, field: str) -> str:
    if isinstance(value, datetime):
        value = value.isoformat()
    return normalize_timestamp(value, field)


def _now(value: str | datetime | None = None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return _timestamp(value.isoformat() if isinstance(value, datetime) else value, "now")


def _range_end(value: Mapping[str, Any] | None) -> str | None:
    if not value:
        return None
    raw = value.get("end", value.get("end_timestamp"))
    return None if raw is None else _timestamp(raw, "observed_range.end")


class ProviderCache:
    """Content-safe cache under the configured local runtime directory."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        market_data_directory: str | Path | None = None,
    ) -> None:
        self.root = Path(root).expanduser() if root is not None else runtime_data_dir() / "provider-cache"
        if market_data_directory is None:
            data_root = runtime_data_dir() if root is None else (
                self.root.parent if self.root.name == "provider-cache" else self.root
            )
            market_data_directory = data_root / "market-data" / "sha256"
        self.market_data_directory = Path(market_data_directory).expanduser()

    request_identity = staticmethod(request_identity)
    request_hash = staticmethod(request_hash)

    def response_path(self, request: ProviderRequest | Mapping[str, Any]) -> Path:
        identity = request_identity(request)
        provider = _safe_component(identity["provider"], "provider")
        return self.root / "responses" / provider / "sha256" / f"{request_hash(request)}.json"

    def save_response(
        self,
        request: ProviderRequest | Mapping[str, Any],
        payload: Any,
        *,
        fetched_at: str | datetime | None = None,
        observed_range: Mapping[str, Any] | None = None,
        expires_at: str | datetime | None = None,
        parser_version: int = 1,
    ) -> Path:
        if _contains_secret_field(payload):
            raise ValueError("provider cache payload must not contain credentials")
        fetched = _now(fetched_at)
        if expires_at is None:
            expires = None
            if isinstance(request, ProviderRequest) and request.mutable:
                ttl = request.freshness_seconds or 3600
                expires = _timestamp(parse_timestamp(fetched) + timedelta(seconds=ttl), "expires_at")
        else:
            expires = _timestamp(expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at, "expires_at")
        if observed_range is None:
            values = payload.get("observations", ()) if isinstance(payload, Mapping) else payload
            if isinstance(values, Mapping) and "observed_at" in values:
                values = (values,)
            if isinstance(values, (list, tuple)):
                observed = sorted(
                    str(item.get("observed_at"))
                    for item in values
                    if isinstance(item, Mapping) and item.get("observed_at") is not None
                )
                if observed:
                    observed_range = {"start": observed[0], "end": observed[-1]}
        record = {
            "cache_version": _CACHE_VERSION,
            "parser_version": parser_version,
            "provider": request_identity(request)["provider"],
            "dataset": request_identity(request)["dataset"],
            "asset": request_identity(request)["asset"],
            "request_identity": request_identity(request),
            "fetched_at": fetched,
            "observed_range": dict(observed_range) if observed_range else None,
            "expires_at": expires,
            "mutable": bool(request.mutable) if isinstance(request, ProviderRequest) else True,
            "content_hash": content_hash(payload),
            "payload": payload,
        }
        encoded = canonical_json(record) + "\n"
        destination = self.response_path(request)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(destination, encoded)
        return destination

    cache_response = save_response

    def _read_response_record(self, request: ProviderRequest | Mapping[str, Any]) -> dict[str, Any] | None:
        path = self.response_path(request)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CacheCorruption(f"invalid provider cache entry {path}") from exc
        if not isinstance(record, Mapping):
            raise CacheCorruption(f"provider cache entry {path} is not an object")
        identity = request_identity(request)
        if record.get("cache_version") != _CACHE_VERSION or record.get("request_identity") != identity:
            raise CacheCorruption(f"provider cache identity mismatch {path}")
        try:
            if record.get("content_hash") != content_hash(record.get("payload")):
                raise CacheCorruption(f"provider cache content hash mismatch {path}")
            if _contains_secret_field(record.get("payload")):
                raise CacheCorruption(f"provider cache entry contains credentials {path}")
            _timestamp(record.get("fetched_at"), "cache.fetched_at")
            if record.get("expires_at") is not None:
                _timestamp(record.get("expires_at"), "cache.expires_at")
        except ValueError as exc:
            raise CacheCorruption(f"provider cache entry {path} is invalid") from exc
        return dict(record)

    def load_response(
        self,
        request: ProviderRequest | Mapping[str, Any],
        *,
        now: str | datetime | None = None,
        as_of: str | datetime | None = None,
        allow_expired: bool = False,
    ) -> Any | None:
        record = self._read_response_record(request)
        if record is None:
            return None
        now_time = parse_timestamp(_now(now))
        if as_of is not None:
            cutoff = parse_timestamp(_timestamp(as_of.isoformat() if isinstance(as_of, datetime) else as_of, "as_of"))
            range_end = _range_end(record.get("observed_range"))
            if range_end is None:
                if parse_timestamp(record["fetched_at"]) > cutoff:
                    return None
            elif parse_timestamp(range_end) > cutoff:
                return None
        if record.get("mutable", True) and record.get("expires_at") and parse_timestamp(record["expires_at"]) < now_time:
            if not allow_expired:
                raise CacheExpired(f"provider cache entry expired for {record['provider']}/{record['dataset']}")
        return record.get("payload")

    get_response = load_response

    def load_response_record(
        self,
        request: ProviderRequest | Mapping[str, Any],
        *,
        now: str | datetime | None = None,
        as_of: str | datetime | None = None,
        allow_expired: bool = False,
    ) -> dict[str, Any] | None:
        payload = self.load_response(request, now=now, as_of=as_of, allow_expired=allow_expired)
        record = self._read_response_record(request)
        if record is None or payload is None:
            return None
        return record

    get_response_record = load_response_record

    def quarantine(self, request: ProviderRequest | Mapping[str, Any]) -> Path | None:
        path = self.response_path(request)
        if not path.exists():
            return None
        target = path.with_suffix(path.suffix + ".corrupt")
        index = 1
        while target.exists():
            target = path.with_suffix(path.suffix + f".corrupt.{index}")
            index += 1
        os.replace(path, target)
        return target

    @staticmethod
    def series_key(
        provider: str,
        symbol: str,
        timeframe: str,
        *,
        market: str = "spot",
        quote_currency: str = "USDT",
    ) -> dict[str, str]:
        return {
            "provider": str(provider).strip().lower(),
            "symbol": str(symbol).strip().upper(),
            "timeframe": str(timeframe).strip().upper(),
            "market": str(market).strip().lower(),
            "quote_currency": str(quote_currency).strip().upper(),
        }

    @classmethod
    def series_key_hash(cls, provider: str, symbol: str, timeframe: str, *, market: str = "spot", quote_currency: str = "USDT") -> str:
        return content_hash(cls.series_key(provider, symbol, timeframe, market=market, quote_currency=quote_currency))

    def series_directory(self, provider: str, symbol: str, timeframe: str, *, market: str = "spot", quote_currency: str = "USDT") -> Path:
        return self.root / "series" / _safe_component(provider.strip().lower(), "provider") / self.series_key_hash(provider, symbol, timeframe, market=market, quote_currency=quote_currency)

    def manifest_path(self, provider: str, symbol: str, timeframe: str, *, market: str = "spot", quote_currency: str = "USDT") -> Path:
        return self.series_directory(provider, symbol, timeframe, market=market, quote_currency=quote_currency) / "manifest.json"

    series_manifest_path = manifest_path

    def load_manifest(self, provider: str, symbol: str, timeframe: str, *, market: str = "spot", quote_currency: str = "USDT") -> dict[str, Any] | None:
        path = self.manifest_path(provider, symbol, timeframe, market=market, quote_currency=quote_currency)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CacheCorruption(f"invalid series manifest {path}") from exc
        if (
            not isinstance(value, dict)
            or value.get("manifest_version") != 1
            or value.get("series_key") != self.series_key(provider, symbol, timeframe, market=market, quote_currency=quote_currency)
        ):
            raise CacheCorruption(f"series manifest identity mismatch {path}")
        if not isinstance(value.get("latest_content_hash"), str):
            raise CacheCorruption(f"series manifest has no latest content hash {path}")
        try:
            start = parse_timestamp(_timestamp(value.get("start"), "series manifest start"))
            end = parse_timestamp(_timestamp(value.get("end"), "series manifest end"))
            completed_through = parse_timestamp(_timestamp(value.get("completed_through"), "series manifest completed_through"))
        except (TypeError, ValueError) as exc:
            raise CacheCorruption(f"series manifest has invalid range {path}") from exc
        if start > end or completed_through < start or completed_through > end:
            raise CacheCorruption(f"series manifest range is inconsistent {path}")
        return value

    def load_series(self, provider: str, symbol: str, timeframe: str, *, market: str = "spot", quote_currency: str = "USDT") -> OHLCVSeries | None:
        manifest = self.load_manifest(provider, symbol, timeframe, market=market, quote_currency=quote_currency)
        if manifest is None:
            return None
        try:
            series = load_ohlcv(manifest["latest_content_hash"], self.market_data_directory)
        except ValueError as exc:
            raise CacheCorruption(f"series manifest points to invalid OHLCV content: {exc}") from exc
        expected = self.series_key(provider, symbol, timeframe, market=market, quote_currency=quote_currency)
        if series.symbol != expected["symbol"] or series.timeframe != expected["timeframe"]:
            raise CacheCorruption("series manifest content has a mismatched symbol or timeframe")
        return series

    def store_series(
        self,
        series: OHLCVSeries,
        *,
        provider: str | None = None,
        market: str | None = None,
        quote_currency: str | None = None,
        completed_through: str | None = None,
    ) -> Path:
        provider_name = (provider or series.source).strip().lower()
        _safe_component(provider_name, "provider")
        market_name = (market or series.market or "spot").strip().lower()
        quote = (quote_currency or series.quote_currency or "USDT").strip().upper()
        completed_candles = tuple(candle for candle in series.candles if candle.completed)
        if not completed_candles:
            raise ValueError("provider series must contain a completed candle")
        if len(completed_candles) != len(series.candles):
            series = OHLCVSeries(
                symbol=series.symbol,
                timeframe=series.timeframe,
                candles=completed_candles,
                source=series.source,
                fetched_at=series.fetched_at,
                venue=series.venue,
                market=series.market,
                quote_currency=series.quote_currency,
            )
        completed = _timestamp(completed_through, "completed_through") if completed_through is not None else series.candles[-1].timestamp
        if parse_timestamp(completed) > parse_timestamp(series.candles[-1].timestamp):
            raise ValueError("completed_through must not be after the series end")
        cache_ohlcv(series, self.market_data_directory)
        directory = self.series_directory(provider_name, series.symbol, series.timeframe, market=market_name, quote_currency=quote)
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            "manifest_version": 1,
            "series_key": self.series_key(provider_name, series.symbol, series.timeframe, market=market_name, quote_currency=quote),
            "latest_content_hash": series.ohlcv_hash,
            "start": series.candles[0].timestamp,
            "end": series.candles[-1].timestamp,
            "completed_through": completed,
        }
        _atomic_write(directory / "manifest.json", canonical_json(manifest) + "\n")
        return directory / "manifest.json"

    cache_series = store_series
    put_series = store_series
    get_series = load_series

    def quarantine_series(
        self,
        provider: str,
        symbol: str,
        timeframe: str,
        *,
        market: str = "spot",
        quote_currency: str = "USDT",
    ) -> Path | None:
        path = self.manifest_path(provider, symbol, timeframe, market=market, quote_currency=quote_currency)
        if not path.exists():
            return None
        target = path.with_suffix(path.suffix + ".corrupt")
        index = 1
        while target.exists():
            target = path.with_suffix(path.suffix + f".corrupt.{index}")
            index += 1
        os.replace(path, target)
        return target

    def stats(self) -> dict[str, int]:
        responses = list((self.root / "responses").glob("**/*.json")) if (self.root / "responses").exists() else []
        manifests = list((self.root / "series").glob("**/manifest.json")) if (self.root / "series").exists() else []
        return {"response_entries": len(responses), "series_manifests": len(manifests)}

    def prune_expired(self, *, now: str | datetime | None = None) -> int:
        current = parse_timestamp(_now(now))
        removed = 0
        for path in ((self.root / "responses").glob("**/*.json") if (self.root / "responses").exists() else ()):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, Mapping) and record.get("mutable", True) and record.get("expires_at"):
                try:
                    expired = parse_timestamp(record["expires_at"]) < current
                except ValueError:
                    expired = False
                if expired:
                    path.unlink()
                    removed += 1
        return removed


ProviderResponseCache = ProviderCache


def merge_ohlcv_series(existing: OHLCVSeries | None, incoming: OHLCVSeries) -> OHLCVSeries:
    if not isinstance(incoming, OHLCVSeries):
        raise ValueError("incoming must be an OHLCVSeries")
    if existing is None:
        return incoming
    if any(
        getattr(existing, field) != getattr(incoming, field)
        for field in ("symbol", "timeframe", "source", "venue", "market", "quote_currency")
    ):
        raise ProviderDataError("cannot merge OHLCV series with different provenance or identity")
    by_timestamp = {candle.timestamp: candle for candle in existing.candles}
    for candle in incoming.candles:
        previous = by_timestamp.get(candle.timestamp)
        if previous is not None and previous.as_dict() != candle.as_dict():
            raise ProviderDataError(f"conflicting OHLCV candle at {candle.timestamp}")
        by_timestamp[candle.timestamp] = candle
    return OHLCVSeries(
        symbol=existing.symbol,
        timeframe=existing.timeframe,
        candles=tuple(by_timestamp[key] for key in sorted(by_timestamp)),
        source=existing.source,
        fetched_at=incoming.fetched_at or existing.fetched_at,
        venue=existing.venue,
        market=existing.market,
        quote_currency=existing.quote_currency,
    )


def missing_series_range(
    existing: OHLCVSeries | None,
    *,
    start: str | datetime | None,
    end: str | datetime | None,
) -> tuple[str | None, str | None] | None:
    """Return one missing prefix/tail range, or ``None`` when covered."""
    if existing is None:
        return (
            None if start is None else _timestamp(start.isoformat() if isinstance(start, datetime) else start, "start"),
            None if end is None else _timestamp(end.isoformat() if isinstance(end, datetime) else end, "end"),
        )
    requested_start = None if start is None else parse_timestamp(_timestamp(start.isoformat() if isinstance(start, datetime) else start, "start"))
    requested_end = None if end is None else parse_timestamp(_timestamp(end.isoformat() if isinstance(end, datetime) else end, "end"))
    first = parse_timestamp(existing.candles[0].timestamp)
    last = parse_timestamp(existing.candles[-1].timestamp)
    interval = timedelta(seconds=existing.interval_seconds)
    if requested_start is not None and requested_start < first:
        prefix_end = min(requested_end or first - interval, first - interval)
        return requested_start.isoformat().replace("+00:00", "Z"), prefix_end.isoformat().replace("+00:00", "Z")
    coverage_start = max(requested_start or first, first)
    coverage_end = min(requested_end or last, last)
    timestamps = {parse_timestamp(candle.timestamp) for candle in existing.candles}
    cursor = coverage_start
    while cursor <= coverage_end:
        if cursor not in timestamps:
            missing_start = cursor
            while cursor <= coverage_end and cursor not in timestamps:
                cursor += interval
            missing_end = cursor - interval
            return missing_start.isoformat().replace("+00:00", "Z"), missing_end.isoformat().replace("+00:00", "Z")
        cursor += interval
    if requested_end is not None and requested_end > last:
        tail_start = max(requested_start or last + interval, last + interval)
        return tail_start.isoformat().replace("+00:00", "Z"), requested_end.isoformat().replace("+00:00", "Z")
    return None


def provider_cache_stats(root: str | Path | None = None) -> dict[str, int]:
    return ProviderCache(root).stats()


__all__ = [
    "CacheCorruption",
    "CacheError",
    "CacheExpired",
    "ProviderCache",
    "ProviderResponseCache",
    "canonical_json",
    "content_hash",
    "merge_ohlcv_series",
    "missing_series_range",
    "provider_cache_stats",
    "request_cache_key",
    "hash_request",
    "request_hash",
    "request_identity",
]
