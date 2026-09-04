"""Structured DeFiLlama protocol/fundamental provider."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import ProviderCapabilities, ProviderDataError, ProviderRequest, ProviderResponseError, ProviderUnsupportedMetric
from .http import HttpClient


BASE_URL = "https://api.llama.fi"
ASSET_IDENTIFIERS = {
    "AAVE": "aave",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "bsc",
    "LINK": "chainlink",
}


def identifier_for_asset(asset: str) -> str:
    if not isinstance(asset, str) or not asset.strip():
        raise ValueError("asset must be a non-empty string")
    try:
        return ASSET_IDENTIFIERS[asset.strip().upper()]
    except KeyError as exc:
        raise ProviderUnsupportedMetric(f"DeFiLlama has no explicit identifier for {asset}") from exc


protocol_identifier = identifier_for_asset


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        value = value.isoformat()
    return normalize_timestamp(value, "fetched_at")


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise ProviderDataError(f"{field} is invalid")
    return result


def _timestamp(value: Any, field: str, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = _number(value, field)
        if number > 100_000_000_000:
            number /= 1000
        try:
            return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), field)
        except (OverflowError, OSError, ValueError) as exc:
            raise ProviderDataError(f"{field} is invalid") from exc
    return normalize_timestamp(value, field)


def _latest_series_value(value: Any, *, as_of: str | None, fetched_at: str) -> tuple[float, str] | None:
    if not isinstance(value, list):
        return None
    cutoff = parse_timestamp(as_of) if as_of else None
    rows: list[tuple[str, float]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        raw_date = item.get("date", item.get("timestamp"))
        timestamp = _timestamp(raw_date, "DeFiLlama series timestamp", fetched_at)
        if cutoff is not None and parse_timestamp(timestamp) > cutoff:
            continue
        raw = item.get("totalLiquidityUSD", item.get("totalLiquidity", item.get("value")))
        if raw is None:
            continue
        rows.append((timestamp, _number(raw, "DeFiLlama series value")))
    return max(rows, key=lambda item: item[0]) if rows else None


def _latest_numeric_series(
    value: Any,
    names: tuple[str, ...],
    *,
    as_of: str | None,
    fetched_at: str,
    aggregate_days: int | None = None,
) -> tuple[float, str] | None:
    if not isinstance(value, list):
        return None
    cutoff = parse_timestamp(as_of) if as_of else None
    rows: list[tuple[str, float]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        timestamp = _timestamp(item.get("date", item.get("timestamp")), "DeFiLlama metric timestamp", fetched_at)
        if cutoff is not None and parse_timestamp(timestamp) > cutoff:
            continue
        raw = next((item.get(name) for name in names if item.get(name) is not None), None)
        if raw is None and not names:
            raw = next((candidate for key, candidate in item.items() if key not in {"date", "timestamp"} and isinstance(candidate, (int, float, str))), None)
        if raw is None or isinstance(raw, (list, dict)):
            continue
        rows.append((timestamp, _number(raw, "DeFiLlama metric value")))
    if not rows:
        return None
    latest_timestamp = max(timestamp for timestamp, _ in rows)
    if aggregate_days is None:
        return next(value for timestamp, value in rows if timestamp == latest_timestamp), latest_timestamp
    lower = parse_timestamp(latest_timestamp).timestamp() - aggregate_days * 86400
    return sum(value for timestamp, value in rows if parse_timestamp(timestamp).timestamp() >= lower), latest_timestamp


def parse_protocol_payload(
    payload: Mapping[str, Any],
    asset: str,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    fees_payload: Mapping[str, Any] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("DeFiLlama protocol response must be an object")
    asset = asset.strip().upper()
    keys = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    tvl = _latest_series_value(payload.get("tvl"), as_of=as_of, fetched_at=fetched_at)
    if tvl is None:
        for key in ("tvl", "tvlUsd", "totalLiquidityUSD"):
            if key in payload:
                tvl = (_number(payload[key], f"DeFiLlama {key}"), fetched_at)
                break
    values: dict[str, tuple[float, str]] = {}
    if tvl is not None:
        values["fundamentals.tvl"] = tvl
    scalar_names = {
        "fundamentals.fees_30d": ("fees30d", "fees_30d", "total30d", "fees"),
        "fundamentals.revenue_30d": ("revenue30d", "revenue_30d", "revenue"),
        "fundamentals.stablecoin_liquidity": ("stablecoinLiquidity", "stablecoin_liquidity"),
        "valuation.market_cap": ("mcap", "marketCap", "market_cap"),
        "valuation.fdv": ("fdv", "fullyDilutedValuation"),
    }
    for metric, names in scalar_names.items():
        for source in tuple(item for item in (fees_payload, payload) if isinstance(item, Mapping)):
            for name in names:
                if name not in source or source[name] is None:
                    continue
                if isinstance(source[name], list):
                    aggregate = _latest_numeric_series(
                        source[name], (), as_of=as_of, fetched_at=fetched_at,
                        aggregate_days=30 if metric.endswith("_30d") else None,
                    )
                    if aggregate is not None:
                        values[metric] = aggregate
                        break
                elif not isinstance(source[name], dict):
                    values[metric] = (_number(source[name], f"DeFiLlama {name}"), fetched_at)
                    break
            if metric in values:
                break
    if "valuation.fdv_market_cap_ratio" in keys and "valuation.fdv" in values and "valuation.market_cap" in values and values["valuation.market_cap"][0] > 0:
        values["valuation.fdv_market_cap_ratio"] = (values["valuation.fdv"][0] / values["valuation.market_cap"][0], fetched_at)
    if "valuation.fee_revenue_multiple" in keys and "fundamentals.fees_30d" in values and "fundamentals.revenue_30d" in values and values["fundamentals.revenue_30d"][0] > 0:
        values["valuation.fee_revenue_multiple"] = (values["fundamentals.fees_30d"][0] / values["fundamentals.revenue_30d"][0], fetched_at)
    result = []
    for key in keys:
        if key not in values:
            raise ProviderUnsupportedMetric(f"DeFiLlama response cannot supply {key}")
        value, observed = values[key]
        definition = metric_definition(key)
        result.append({
            "asset": asset,
            "metric_key": key,
            "value": value,
            "unit": definition.unit,
            "observed_at": observed,
            "fetched_at": fetched_at,
            "source": "defillama",
            "confidence": "MEDIUM",
            "metadata": {
                "source_dataset": "protocol",
                "identifier": identifier_for_asset(asset),
            },
        })
    return tuple(result)


class DeFiLlamaProvider:
    name = "defillama"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=(
                "fundamentals.tvl", "fundamentals.fees_30d", "fundamentals.revenue_30d",
                "fundamentals.stablecoin_liquidity", "valuation.market_cap", "valuation.fdv",
                "valuation.fdv_market_cap_ratio", "valuation.fee_revenue_multiple",
            ),
            historical_series=("fundamentals.tvl", "fundamentals.fees_30d", "fundamentals.revenue_30d"),
            supports_batching=True,
            requires_api_key=False,
        )

    def collect(self, request: ProviderRequest) -> list[Mapping[str, Any]]:
        identifier = identifier_for_asset(request.asset)
        url = BASE_URL + "/protocol/" + quote(identifier, safe="")
        fetched = _now(self.clock)
        payload = self.client.get_json(url)
        fees_payload = None
        if any(key in request.metric_keys for key in ("fundamentals.fees_30d", "fundamentals.revenue_30d", "valuation.fee_revenue_multiple")):
            try:
                fees_payload = self.client.get_json(BASE_URL + "/summary/fees/" + quote(identifier, safe=""))
            except Exception:
                # The protocol response is still useful for TVL/valuation.
                fees_payload = None
        return [dict(item) for item in parse_protocol_payload(
            payload,
            request.asset,
            request.metric_keys,
            fetched_at=fetched,
            as_of=request.parameters.get("as_of"),
            fees_payload=fees_payload,
        )]


DefiLlamaProvider = DeFiLlamaProvider


__all__ = [
    "ASSET_IDENTIFIERS",
    "BASE_URL",
    "DeFiLlamaProvider",
    "DefiLlamaProvider",
    "identifier_for_asset",
    "parse_protocol_payload",
    "protocol_identifier",
]
