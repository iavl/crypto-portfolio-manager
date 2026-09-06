"""Structured CoinGecko market-cap and FDV provider."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderAuthenticationError,
    ProviderCapabilities,
    ProviderDataError,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient


BASE_URL = "https://api.coingecko.com/api/v3"
CURRENT_MARKETS_PATH = "/coins/markets"
COINGECKO_API_KEY_HEADER = "x-cg-demo-api-key"
COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "LINK": "chainlink",
    "AAVE": "aave",
}
VALUATION_METRICS = ("valuation.market_cap", "valuation.fdv")


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        value = value.isoformat()
    return normalize_timestamp(value, "fetched_at")


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderDataError(f"CoinGecko {field} is not numeric")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ProviderDataError(f"CoinGecko {field} is invalid")
    return result


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderDataError(f"CoinGecko {field} is missing")
    try:
        return normalize_timestamp(value, field)
    except ValueError as exc:
        raise ProviderDataError(f"CoinGecko {field} is invalid") from exc


def _diagnostic(code: str, detail: str) -> dict[str, str]:
    return {"error_code": code, "detail": detail}


def _observation(
    asset: str,
    metric_key: str,
    value: float,
    *,
    observed_at: str,
    fetched_at: str,
    source_dataset: str,
    provider_asset_id: str,
    methodology: str,
    period: str,
) -> dict[str, Any]:
    return {
        "asset": asset,
        "metric_key": metric_key,
        "value": value,
        "unit": metric_definition(metric_key).unit,
        "period": period,
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "source": "coingecko",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": source_dataset,
            "provider_asset_id": provider_asset_id,
            "quote_currency": "USD",
            "methodology": methodology,
        },
    }


def _exact_market_row(payload: Any, provider_asset_id: str) -> Mapping[str, Any]:
    if not isinstance(payload, list):
        raise ProviderResponseError("CoinGecko markets response must be an array")
    if any(not isinstance(item, Mapping) for item in payload):
        raise ProviderDataError("CoinGecko markets response contains a non-object row")
    matches = [item for item in payload if item.get("id") == provider_asset_id]
    if len(matches) != 1:
        raise ProviderDataError("CoinGecko markets response must contain exactly one requested asset")
    return matches[0]


def _historical_market_data(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("CoinGecko history response must be an object")
    market_data = payload.get("market_data")
    if not isinstance(market_data, Mapping):
        raise ProviderDataError("CoinGecko history response has no market_data object")
    return market_data


def _usd_value(market_data: Mapping[str, Any], field: str) -> Any:
    value = market_data.get(field)
    if isinstance(value, Mapping):
        return value.get("usd")
    return None


def parse_market_payload(
    payload: Any,
    asset: str,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    provider_asset_id: str,
    as_of: str | None = None,
) -> ProviderResponse:
    """Parse one current or historical CoinGecko response without dropping partial values."""
    asset = asset.strip().upper()
    keys = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    unsupported = set(keys) - set(VALUATION_METRICS)
    if unsupported:
        raise ProviderUnsupportedMetric(f"CoinGecko does not support: {', '.join(sorted(unsupported))}")

    diagnostics: dict[str, Mapping[str, Any]] = {}
    if as_of is None:
        row = _exact_market_row(payload, provider_asset_id)
        observed_at = _timestamp(row.get("last_updated"), "last_updated")
        values = {
            "valuation.market_cap": row.get("market_cap"),
            "valuation.fdv": row.get("fully_diluted_valuation"),
        }
        source_dataset = "coins/markets"
        period = "current"
        methodologies = {
            "valuation.market_cap": "coingecko_reported_circulating_supply_market_cap",
            "valuation.fdv": "coingecko_reported_fully_diluted_valuation",
        }
    else:
        market_data = _historical_market_data(payload)
        cutoff = parse_timestamp(as_of)
        observed_at = cutoff.date().isoformat() + "T00:00:00Z"
        values = {
            "valuation.market_cap": _usd_value(market_data, "market_cap"),
            "valuation.fdv": _usd_value(market_data, "fully_diluted_valuation"),
        }
        source_dataset = "coins/{id}/history".format(id=provider_asset_id)
        period = "1d"
        methodologies = {
            "valuation.market_cap": "coingecko_reported_historical_market_cap",
            "valuation.fdv": "coingecko_reported_historical_fully_diluted_valuation",
        }

    observations: list[Mapping[str, Any]] = []
    for key in keys:
        raw = values.get(key)
        if key == "valuation.fdv" and (raw is None or isinstance(raw, bool)):
            diagnostics[key] = _diagnostic("COINGECKO_NO_FDV", "CoinGecko did not return FDV")
            continue
        if raw is None:
            diagnostics[key] = _diagnostic(
                "HISTORICAL_RANGE_UNAVAILABLE" if as_of is not None else "COINGECKO_SCHEMA",
                "CoinGecko did not return market cap",
            )
            continue
        try:
            number = _number(raw, key.rsplit(".", 1)[-1], positive=True)
        except ProviderDataError as exc:
            diagnostics[key] = _diagnostic(
                "COINGECKO_NO_FDV" if key == "valuation.fdv" else "COINGECKO_SCHEMA",
                str(exc),
            )
            continue
        observations.append(_observation(
            asset,
            key,
            number,
            observed_at=observed_at,
            fetched_at=fetched_at,
            source_dataset=source_dataset,
            provider_asset_id=provider_asset_id,
            methodology=methodologies[key],
            period=period,
        ))
    return ProviderResponse(tuple(observations), diagnostics=diagnostics or None)


class CoinGeckoProvider:
    name = "coingecko"

    def __init__(self, *, client: HttpClient | Any | None = None, api_key: str | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.api_key = api_key
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=VALUATION_METRICS,
            historical_series=("valuation.market_cap",),
            supports_batching=True,
            requires_api_key=True,
        )

    def _headers(self) -> Mapping[str, str]:
        if not self.api_key:
            raise ProviderAuthenticationError("CoinGecko API key is not configured")
        return {COINGECKO_API_KEY_HEADER: self.api_key}

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        asset = request.asset.strip().upper()
        try:
            provider_asset_id = COINGECKO_IDS[asset]
        except KeyError as exc:
            raise ProviderUnsupportedMetric(f"CoinGecko has no approved asset mapping for {asset}") from exc
        keys = tuple(dict.fromkeys(request.metric_keys))
        if any(key not in VALUATION_METRICS for key in keys):
            raise ProviderUnsupportedMetric("CoinGecko supports only market cap and FDV")
        as_of = request.parameters.get("as_of")
        if as_of is None:
            payload = self.client.get_json(
                BASE_URL + CURRENT_MARKETS_PATH,
                params={"vs_currency": "usd", "ids": provider_asset_id, "localization": "false"},
                headers=self._headers(),
            )
        else:
            cutoff = parse_timestamp(as_of)
            date = cutoff.strftime("%d-%m-%Y")
            payload = self.client.get_json(
                BASE_URL + "/coins/" + quote(provider_asset_id, safe="") + "/history",
                params={"date": date, "localization": "false"},
                headers=self._headers(),
            )
        return parse_market_payload(
            payload,
            asset,
            keys,
            fetched_at=_now(self.clock),
            provider_asset_id=provider_asset_id,
            as_of=as_of,
        )


__all__ = [
    "BASE_URL",
    "COINGECKO_API_KEY_HEADER",
    "COINGECKO_IDS",
    "CURRENT_MARKETS_PATH",
    "CoinGeckoProvider",
    "VALUATION_METRICS",
    "parse_market_payload",
]
