"""Structured DeFiLlama protocol/fundamental provider."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderCapabilities,
    ProviderDataError,
    ProviderInsufficientHistory,
    ProviderNotApplicable,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient, classify_transport_error, redact_secrets


BASE_URL = "https://api.llama.fi"
STABLECOINS_BASE_URL = "https://stablecoins.llama.fi"
STABLECOIN_CHARTS_PATH = "/stablecoincharts"
ASSET_IDENTIFIERS = {
    "AAVE": "aave",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "bsc",
    "LINK": "chainlink",
}
CHAIN_NAMES = {"ETH": "Ethereum", "SOL": "Solana", "BNB": "BSC"}


def identifier_for_asset(asset: str) -> str:
    if not isinstance(asset, str) or not asset.strip():
        raise ValueError("asset must be a non-empty string")
    try:
        return ASSET_IDENTIFIERS[asset.strip().upper()]
    except KeyError as exc:
        raise ProviderUnsupportedMetric(f"DeFiLlama has no explicit identifier for {asset}") from exc


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
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            pass
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


def parse_stablecoin_chart(
    payload: Any,
    *,
    asset: str,
    metric_key: str,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str,
) -> Mapping[str, Any]:
    if not isinstance(payload, list):
        raise ProviderResponseError("DeFiLlama stablecoin chart response must be a list")
    cutoff = parse_timestamp(as_of) if as_of else None
    rows: list[tuple[str, float]] = []
    for index, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise ProviderDataError(f"DeFiLlama stablecoin row {index} is malformed")
        observed = _timestamp(row.get("date"), "DeFiLlama stablecoin date", fetched_at)
        if cutoff is not None and parse_timestamp(observed) > cutoff:
            continue
        total = row.get("totalCirculatingUSD")
        if not isinstance(total, Mapping) or total.get("peggedUSD") is None:
            raise ProviderDataError("DeFiLlama stablecoin row has no peggedUSD total")
        rows.append((observed, _number(total["peggedUSD"], "DeFiLlama stablecoin supply")))
    if not rows:
        raise ProviderInsufficientHistory("DeFiLlama stablecoin history has no value at or before as_of")
    observed, value = max(rows, key=lambda item: parse_timestamp(item[0]))
    return {
        "asset": asset.strip().upper(),
        "metric_key": metric_key,
        "value": value,
        "unit": metric_definition(metric_key).unit,
        "period": "current",
        "observed_at": observed,
        "fetched_at": fetched_at,
        "source": "defillama",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "stablecoincharts",
            "source_url": endpoint,
            "methodology": "latest_totalCirculatingUSD.peggedUSD",
            "scope": "global" if asset.upper() == "MARKET" else CHAIN_NAMES.get(asset.upper()),
        },
    }


def parse_protocol_payload(
    payload: Mapping[str, Any],
    asset: str,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    fees_payload: Mapping[str, Any] | None = None,
    revenue_payload: Mapping[str, Any] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("DeFiLlama protocol response must be an object")
    asset = asset.strip().upper()
    keys = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    tvl = _latest_series_value(payload.get("tvl"), as_of=as_of, fetched_at=fetched_at)
    if tvl is None:
        for key in ("tvl", "tvlUsd", "totalLiquidityUSD"):
            raw = payload.get(key)
            if key in payload and not isinstance(raw, (list, Mapping)):
                tvl = (_number(raw, f"DeFiLlama {key}"), fetched_at)
                break
    values: dict[str, tuple[float, str]] = {}
    if tvl is not None:
        values["fundamentals.tvl"] = tvl
    scalar_names = {
        "fundamentals.fees_30d": ("fees30d", "fees_30d", "total30d", "fees"),
        "fundamentals.revenue_30d": ("revenue30d", "revenue_30d", "revenue"),
    }
    for metric, names in scalar_names.items():
        if metric == "fundamentals.revenue_30d" and revenue_payload is not None:
            sources = (revenue_payload, payload)
            names = ("total30d", *names)
        else:
            sources = (fees_payload, payload)
        for source in tuple(item for item in sources if isinstance(item, Mapping)):
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
                "fundamentals.stablecoin_liquidity", "market.stablecoin_supply", "valuation.fee_revenue_multiple",
            ),
            historical_series=("fundamentals.tvl", "fundamentals.fees_30d", "fundamentals.revenue_30d", "fundamentals.stablecoin_liquidity", "market.stablecoin_supply"),
            supports_batching=True,
            requires_api_key=False,
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.dataset == "stablecoin":
            key = next((item for item in request.metric_keys if item in {"market.stablecoin_supply", "fundamentals.stablecoin_liquidity"}), None)
            if key is None:
                raise ProviderUnsupportedMetric("DeFiLlama stablecoin API does not support the requested metrics")
            asset = request.asset.strip().upper()
            if asset == "MARKET":
                scope = "all"
            else:
                scope = CHAIN_NAMES.get(asset)
                if scope is None:
                    raise ProviderNotApplicable("stablecoin supply is defined only for global or chain scope")
            endpoint = STABLECOINS_BASE_URL + STABLECOIN_CHARTS_PATH + "/" + scope
            return ProviderResponse((parse_stablecoin_chart(
                self.client.get_json(endpoint),
                asset=asset,
                metric_key=key,
                fetched_at=_now(self.clock),
                as_of=request.parameters.get("as_of"),
                endpoint=endpoint,
            ),))
        identifier = identifier_for_asset(request.asset)
        url = BASE_URL + "/protocol/" + quote(identifier, safe="")
        fetched = _now(self.clock)
        fee_keys = {"fundamentals.fees_30d", "fundamentals.revenue_30d", "valuation.fee_revenue_multiple"}
        payload: Mapping[str, Any] = {}
        payload_error: Exception | None = None
        try:
            if request.asset in CHAIN_NAMES:
                if "fundamentals.tvl" in request.metric_keys:
                    chains = self.client.get_json(BASE_URL + "/v2/chains")
                    if not isinstance(chains, list):
                        raise ProviderResponseError("DeFiLlama chains response must be a list")
                    chain = next((item for item in chains if isinstance(item, Mapping) and item.get("name") == CHAIN_NAMES[request.asset]), {})
                    payload = {"tvl": chain["tvl"]} if chain.get("tvl") is not None else {}
            elif identifier == "aave":
                # Avoid the oversized multi-chain protocol history payload.
                if "fundamentals.tvl" in request.metric_keys:
                    payload = {"tvl": self.client.get_json(BASE_URL + "/tvl/" + quote(identifier, safe=""))}
            elif any(key not in fee_keys for key in request.metric_keys):
                payload = self.client.get_json(url)
        except Exception as exc:
            payload_error = exc
        fees_payload = None
        fees_error: Exception | None = None
        if any(key in request.metric_keys for key in ("fundamentals.fees_30d", "valuation.fee_revenue_multiple")):
            try:
                fees_payload = self.client.get_json(BASE_URL + "/summary/fees/" + quote(identifier, safe=""))
            except Exception as exc:
                # The protocol response is still useful for TVL/fundamentals.
                fees_payload, fees_error = None, exc
        revenue_payload = None
        revenue_error: Exception | None = None
        if any(key in request.metric_keys for key in ("fundamentals.revenue_30d", "valuation.fee_revenue_multiple")):
            try:
                revenue_payload = self.client.get_json(
                    BASE_URL + "/summary/fees/" + quote(identifier, safe=""),
                    params={"dataType": "dailyRevenue"},
                )
            except Exception as exc:
                revenue_error = exc

        values: list[Mapping[str, Any]] = []
        diagnostics: dict[str, Mapping[str, Any]] = {}
        for key in request.metric_keys:
            try:
                values.extend(parse_protocol_payload(
                    payload,
                    request.asset,
                    (key,),
                    fetched_at=fetched,
                    as_of=request.parameters.get("as_of"),
                    fees_payload=fees_payload,
                    revenue_payload=revenue_payload,
                ))
            except (ProviderDataError, ProviderResponseError, ProviderUnsupportedMetric) as exc:
                source_error = (
                    fees_error if key == "fundamentals.fees_30d" else
                    revenue_error if key == "fundamentals.revenue_30d" else
                    fees_error or revenue_error if key == "valuation.fee_revenue_multiple" else
                    payload_error
                ) or exc
                diagnostic = getattr(source_error, "diagnostic", None)
                if hasattr(diagnostic, "as_dict"):
                    details = dict(diagnostic.as_dict())
                elif isinstance(diagnostic, Mapping):
                    details = dict(diagnostic)
                else:
                    details = {
                        "error_code": classify_transport_error(source_error),
                        "exception_class": source_error.__class__.__name__,
                        "detail": redact_secrets(str(source_error)),
                    }
                diagnostics[key] = details
        return ProviderResponse(tuple(values), diagnostics=diagnostics)


__all__ = [
    "ASSET_IDENTIFIERS",
    "BASE_URL",
    "STABLECOINS_BASE_URL",
    "STABLECOIN_CHARTS_PATH",
    "DeFiLlamaProvider",
    "identifier_for_asset",
    "parse_stablecoin_chart",
    "parse_protocol_payload",
]
