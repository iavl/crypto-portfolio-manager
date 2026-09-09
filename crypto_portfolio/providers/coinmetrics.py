"""Catalog-aware Coin Metrics Community and optional authenticated provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderAuthenticationError,
    ProviderCapabilities,
    ProviderDataError,
    ProviderError,
    ProviderInsufficientHistory,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient, redact_secrets


COMMUNITY_BASE_URL = "https://community-api.coinmetrics.io"
AUTHENTICATED_BASE_URL = "https://api.coinmetrics.io"
COINMETRICS_ASSETS = {
    "BTC": "btc",
    "ETH": "eth",
    "BNB": "bnb",
    "AAVE": "aave",
}
COINMETRICS_GENERIC_NETWORK_METRICS = {
    "onchain.active_addresses": "AdrActCnt",
    "onchain.transfer_volume": "TxTfrValAdjUSD",
    "onchain.blockspace_fees": "FeeTotUSD",
    "onchain.transaction_count": "TxCnt",
}
COINMETRICS_MARKET_VALUATION_METRICS = {
    "valuation.market_cap": "CapMrktEstUSD",
}
COINMETRICS_BTC_CYCLE_METRICS = {
    "onchain.btc.mvrv": "CapMVRVCur",
    "onchain.btc.mvrv_zscore": "CapMVRVZ",
    "onchain.btc.realized_price": "PriceRealizedUSD",
    "onchain.btc.market_to_realized_price": "CapMVRVCur",
    "onchain.btc.sopr": "SOPR",
    "onchain.btc.lth_supply_pct": "SplyLTHPct",
    "onchain.btc.lth_net_position_change": "SplyLTHNetChange",
    "onchain.btc.sth_realized_price": "PriceRealizedSthUSD",
    "onchain.btc.lth_realized_price": "PriceRealizedLthUSD",
    "onchain.btc.nupl": "NUPL",
}
COINMETRICS_BTC_VALUATION_METRICS = {
    "btc_valuation.mvrv": "CapMVRVCur",
    "btc_valuation.mvrv_zscore": "CapMVRVZ",
    "btc_valuation.realized_price": "PriceRealizedUSD",
    "btc_valuation.realized_cap_usd": "CapRealUSD",
}
COINMETRICS_BTC_NETWORK_METRICS = {
    "btc_network.hashrate": "HashRate",
    "btc_network.difficulty": "DiffMean",
}
COINMETRICS_BTC_VALUATION_INPUTS = {
    "btc_valuation.mvrv": ("CapMVRVCur", "CapMrktCurUSD", "CapRealUSD"),
    "btc_valuation.mvrv_zscore": ("CapMVRVZ", "CapMrktCurUSD", "CapRealUSD"),
    "btc_valuation.realized_price": ("PriceRealizedUSD", "CapRealUSD", "SplyCur", "CapMVRVCur", "CapMrktCurUSD"),
    "btc_valuation.realized_cap_usd": ("CapRealUSD", "CapMVRVCur", "CapMrktCurUSD"),
}
COINMETRICS_TOKENOMICS_INPUTS = {
    "tokenomics.annualized_emissions": ("IssTotNtv", "SplyCur"),
    "tokenomics.supply_growth": ("SplyCur",),
}
COINMETRICS_EXCHANGE_FLOW_INPUTS = ("FlowInExUSD", "FlowOutExUSD")
COINMETRICS_ETH_INPUTS = {
    "eth.monetary.current_supply_eth": ("SplyCur",),
    "eth.monetary.issuance_30d_eth": ("IssTotNtv",),
    "eth.monetary.issuance_365d_eth": ("IssTotNtv",),
    "eth.monetary.net_supply_growth_30d": ("SplyCur",),
    "eth.monetary.net_supply_growth_90d": ("SplyCur",),
    "eth.monetary.net_supply_growth_365d": ("SplyCur",),
    "eth_valuation.mvrv": ("CapMVRVCur", "CapMrktCurUSD", "CapRealUSD"),
    "eth_valuation.realized_price": ("PriceRealizedUSD", "CapRealUSD", "SplyCur", "CapMVRVCur", "CapMrktCurUSD"),
    "eth_valuation.realized_cap_usd": ("CapRealUSD", "CapMVRVCur", "CapMrktCurUSD"),
}
COINMETRICS_ETH_SUPPORTED_METRICS = tuple(COINMETRICS_ETH_INPUTS)
COINMETRICS_METRIC_MAP = {
    **COINMETRICS_GENERIC_NETWORK_METRICS,
    **COINMETRICS_MARKET_VALUATION_METRICS,
    **COINMETRICS_BTC_CYCLE_METRICS,
    **COINMETRICS_BTC_VALUATION_METRICS,
    **COINMETRICS_BTC_NETWORK_METRICS,
}
COINMETRICS_SUPPORTED_METRICS = tuple(dict.fromkeys((
    *COINMETRICS_METRIC_MAP,
    *COINMETRICS_TOKENOMICS_INPUTS,
    "flows.exchange_netflow",
    *COINMETRICS_ETH_SUPPORTED_METRICS,
)))
SUPPLY_LOOKBACK_DAYS = 365
SUPPLY_LOOKBACK_TOLERANCE_DAYS = 7
CATALOG_TTL_SECONDS = 86400
MAX_HISTORY_PAGES = 20


def _metric_diagnostic(error: BaseException, secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    diagnostic = getattr(error, "diagnostic", None)
    if hasattr(diagnostic, "as_dict"):
        return dict(redact_secrets(diagnostic.as_dict(), secrets))
    if isinstance(diagnostic, Mapping):
        return dict(redact_secrets(dict(diagnostic), secrets))
    if isinstance(error, ProviderInsufficientHistory):
        code = "PROVIDER_INSUFFICIENT_HISTORY"
    elif isinstance(error, ProviderUnsupportedMetric):
        code = "PROVIDER_UNSUPPORTED"
    elif isinstance(error, ProviderResponseError):
        code = "PROVIDER_SCHEMA_ERROR"
    elif isinstance(error, ProviderDataError):
        code = "PROVIDER_SCHEMA_ERROR"
    elif isinstance(error, ProviderError):
        code = "PROVIDER_SCHEMA_ERROR"
    else:
        code = "PROVIDER_SCHEMA_ERROR"
    return {"error_code": code, "detail": redact_secrets(str(error), secrets) or error.__class__.__name__}


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        value = value.isoformat()
    return normalize_timestamp(value, "fetched_at")


def _timestamp(value: Any, field: str) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            raise ProviderDataError(f"{field} is not finite")
        if number > 100_000_000_000:
            number /= 1000
        return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), field)
    return normalize_timestamp(value, field)


def _catalog_rows(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("Coin Metrics catalog response must be an object")
    raw = payload.get("metrics", payload.get("data", payload.get("available_metrics")))
    if isinstance(raw, Mapping):
        raw = raw.values()
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        raise ProviderResponseError("Coin Metrics catalog has no metric list")
    return tuple(raw)


def _catalog_metric(item: Any) -> str | None:
    if isinstance(item, str):
        return item.strip().lower() or None
    if isinstance(item, Mapping):
        for field in ("metric", "metric_name", "name"):
            if item.get(field):
                return str(item[field]).strip().lower()
    return None


def catalog_metrics(payload: Mapping[str, Any]) -> frozenset[str]:
    result: set[str] = set()
    for item in _catalog_rows(payload):
        metric = _catalog_metric(item)
        if metric:
            result.add(metric)
    return frozenset(item for item in result if item)


def catalog_metrics_by_asset(payload: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """Parse the official catalog's per-asset frequency declarations."""
    by_asset: dict[str, set[str]] = {}
    global_metrics: set[str] = set()
    for item in _catalog_rows(payload):
        metric = _catalog_metric(item)
        if metric is None:
            continue
        if not isinstance(item, Mapping):
            global_metrics.add(metric)
            continue
        frequencies = item.get("frequencies")
        if isinstance(frequencies, (list, tuple)):
            found_asset = False
            for frequency in frequencies:
                if not isinstance(frequency, Mapping) or str(frequency.get("frequency", "")).lower() != "1d":
                    continue
                assets = frequency.get("assets", ())
                if isinstance(assets, str):
                    assets = (assets,)
                for asset in assets:
                    if isinstance(asset, str) and asset.strip():
                        by_asset.setdefault(asset.strip().lower(), set()).add(metric)
                        found_asset = True
            if found_asset:
                continue
            # A catalog entry with only non-daily frequencies is not a valid
            # source for this provider's 1D acquisition contract.
            continue
        assets = item.get("assets", item.get("asset"))
        if isinstance(assets, str):
            assets = (assets,)
        if isinstance(assets, (list, tuple, set, frozenset)):
            found_asset = False
            for asset in assets:
                if isinstance(asset, str) and asset.strip():
                    by_asset.setdefault(asset.strip().lower(), set()).add(metric)
                    found_asset = True
            if found_asset:
                continue
        global_metrics.add(metric)
    if global_metrics:
        by_asset["*"] = global_metrics
    return {asset: frozenset(metrics) for asset, metrics in by_asset.items()}


def _rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("Coin Metrics response must be an object")
    rows = payload.get("data", payload.get("rows"))
    if not isinstance(rows, list):
        raise ProviderResponseError("Coin Metrics response has no data rows")
    if any(not isinstance(item, Mapping) for item in rows):
        raise ProviderResponseError("Coin Metrics response contains a malformed data row")
    return list(rows)


def _append_history_pages(
    client: Any,
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str],
    full_history: bool,
) -> tuple[Mapping[str, Any], int]:
    """Follow only the provider's explicit bounded pagination link."""
    if not full_history:
        return payload, 1
    rows = list(_rows(payload))
    next_url = payload.get("next_page_url")
    requests = 1
    while next_url is not None:
        if requests >= MAX_HISTORY_PAGES or not isinstance(next_url, str) or not next_url.startswith((COMMUNITY_BASE_URL, AUTHENTICATED_BASE_URL)):
            raise ProviderResponseError("Coin Metrics full-history pagination exceeded the bounded contract")
        page = client.get_json(next_url, headers=headers)
        rows.extend(_rows(page))
        requests += 1
        next_url = page.get("next_page_url")
    return {**dict(payload), "data": rows}, requests


def parse_timeseries(
    payload: Mapping[str, Any],
    metric_keys: Iterable[str],
    *,
    asset: str = "BTC",
    source: str = "coinmetrics_community",
    fetched_at: str,
    as_of: str | None = None,
    metric_map: Mapping[str, str] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    rows = _rows(payload)
    cutoff = parse_timestamp(as_of) if as_of else None
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    mapping = metric_map or COINMETRICS_METRIC_MAP
    selected: dict[str, tuple[str, float]] = {}
    for row in rows:
        observed = _timestamp(row.get("time", row.get("timestamp")), "Coin Metrics observation time")
        if cutoff is not None and parse_timestamp(observed) > cutoff:
            continue
        for key in requested:
            metric = mapping.get(key)
            if metric is None or metric not in row:
                continue
            try:
                value = float(row[metric])
            except (TypeError, ValueError) as exc:
                raise ProviderDataError(f"Coin Metrics value for {metric} is not numeric") from exc
            if not math.isfinite(value):
                raise ProviderDataError(f"Coin Metrics value for {metric} is not finite")
            prior = selected.get(key)
            if prior is None or parse_timestamp(observed) > parse_timestamp(prior[0]):
                selected[key] = (observed, value)
    result = []
    for key in requested:
        if key not in selected:
            raise ProviderUnsupportedMetric(f"Coin Metrics returned no value for {key}")
        observed, value = selected[key]
        result.append({
            "asset": asset.strip().upper(),
            "metric_key": key,
            "value": value,
            "unit": metric_definition(key).unit,
            "period": "1d",
            "observed_at": observed,
            "fetched_at": fetched_at,
            "source": source,
            "confidence": "MEDIUM",
            "metadata": {
                "source_dataset": "timeseries/asset-metrics",
                "coinmetrics_metric": mapping[key],
                **(
                    {"methodology": "coinmetrics_estimated_circulating_supply_market_cap"}
                    if key == "valuation.market_cap" else {}
                ),
            },
        })
    return tuple(result)


def _parse_btc_valuation(
    payload: Mapping[str, Any],
    asset: str,
    requested: Iterable[str],
    input_plan: Mapping[str, tuple[str, ...]],
    *,
    fetched_at: str,
    as_of: str | None,
    source: str,
) -> tuple[Mapping[str, Any], ...]:
    """Parse direct BTC valuation metrics or derive them from catalog primitives."""
    rows = _rows(payload)
    cutoff = parse_timestamp(as_of) if as_of else None
    selected: tuple[str, Mapping[str, Any]] | None = None
    for row in rows:
        observed = _timestamp(row.get("time", row.get("timestamp")), "Coin Metrics observation time")
        if cutoff is not None and parse_timestamp(observed) > cutoff:
            continue
        if selected is None or parse_timestamp(observed) > parse_timestamp(selected[0]):
            selected = (observed, row)
    if selected is None:
        raise ProviderUnsupportedMetric("Coin Metrics returned no BTC valuation row at or before as_of")
    observed, row = selected
    result: list[Mapping[str, Any]] = []
    for key in tuple(dict.fromkeys(str(item).strip().lower() for item in requested)):
        inputs = input_plan.get(key)
        if not inputs:
            raise ProviderUnsupportedMetric(f"Coin Metrics has no plan for {key}")
        value: float | None = None
        mode = "DIRECT"
        methodology = None
        if key == "btc_valuation.mvrv":
            if "CapMVRVCur" in inputs and row.get("CapMVRVCur") is not None:
                value = float(row["CapMVRVCur"])
            else:
                market_cap = row.get("CapMrktCurUSD")
                realized_cap = row.get("CapRealUSD")
                if market_cap is not None and realized_cap is not None and float(realized_cap) > 0:
                    value = float(market_cap) / float(realized_cap)
                    mode = "DERIVED"
                    methodology = "CapMrktCurUSD / CapRealUSD"
        elif key == "btc_valuation.mvrv_zscore":
            direct = row.get("CapMVRVZ")
            if "CapMVRVZ" in inputs and direct is not None:
                value = float(direct)
            elif {"CapMrktCurUSD", "CapRealUSD"} <= set(inputs):
                market = row.get("CapMrktCurUSD")
                realized = row.get("CapRealUSD")
                history: list[float] = []
                for history_row in rows:
                    candidate = history_row.get("CapMrktCurUSD")
                    try:
                        candidate_value = float(candidate)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(candidate_value) and candidate_value > 0:
                        history.append(candidate_value)
                if market is not None and realized is not None and len(history) >= 2:
                    mean = sum(history) / len(history)
                    standard_deviation = math.sqrt(sum((item - mean) ** 2 for item in history) / len(history))
                    if standard_deviation > 0:
                        value = (float(market) - float(realized)) / standard_deviation
                        mode = "DERIVED"
                        methodology = "(CapMrktCurUSD - CapRealUSD) / population_std(CapMrktCurUSD)"
            if value is None:
                raise ProviderInsufficientHistory("Coin Metrics MVRV Z-score requires aligned market-cap history and realized cap")
        elif key == "btc_valuation.realized_price":
            if "PriceRealizedUSD" in inputs and row.get("PriceRealizedUSD") is not None:
                value = float(row["PriceRealizedUSD"])
            else:
                realized_cap = row.get("CapRealUSD")
                supply = row.get("SplyCur")
                if realized_cap is not None and supply is not None and float(supply) > 0:
                    value = float(realized_cap) / float(supply)
                    mode = "DERIVED"
                    methodology = "CapRealUSD / SplyCur"
                elif all(row.get(item) is not None for item in ("CapMVRVCur", "CapMrktCurUSD", "SplyCur")):
                    value = float(row["CapMrktCurUSD"]) / float(row["CapMVRVCur"]) / float(row["SplyCur"])
                    mode = "DERIVED"
                    methodology = "CapMrktCurUSD / CapMVRVCur / SplyCur"
        elif key == "btc_valuation.realized_cap_usd":
            if row.get("CapRealUSD") is not None:
                value = float(row["CapRealUSD"])
            elif all(row.get(item) is not None for item in ("CapMVRVCur", "CapMrktCurUSD")) and float(row["CapMVRVCur"]) > 0:
                value = float(row["CapMrktCurUSD"]) / float(row["CapMVRVCur"])
                mode = "DERIVED"
                methodology = "CapMrktCurUSD / CapMVRVCur"
        else:
            metric = inputs[0]
            if row.get(metric) is not None:
                value = float(row[metric])
        if value is None or not math.isfinite(value):
            raise ProviderUnsupportedMetric(f"Coin Metrics returned no usable value for {key}")
        if key != "btc_valuation.mvrv_zscore" and value <= 0:
            raise ProviderDataError(f"Coin Metrics BTC valuation denominator/value is not positive for {key}")
        result.append({
            "asset": asset,
            "metric_key": key,
            "value": value,
            "unit": metric_definition(key).unit,
            "period": "1d",
            "observed_at": observed,
            "fetched_at": fetched_at,
            "source": source if mode == "DIRECT" else "python-derived",
            "confidence": "MEDIUM",
            "metadata": {
                "source_dataset": "timeseries/asset-metrics",
                "coinmetrics_metrics": list(inputs),
                "source_mode": mode,
                **({"methodology": methodology} if methodology else {}),
                **(
                    {
                        "history_start": min(
                            _timestamp(item.get("time", item.get("timestamp")), "Coin Metrics observation time")
                            for item in rows
                            if item.get("CapMrktCurUSD") is not None
                        ),
                        "history_end": observed,
                        "rows_used": len(rows),
                        "std_semantics": "population",
                    }
                    if key == "btc_valuation.mvrv_zscore" and mode == "DERIVED"
                    else {}
                ),
            },
        })
    return tuple(result)


def _series_rows(
    payload: Mapping[str, Any],
    *,
    fields: Iterable[str],
    as_of: str | None,
) -> tuple[Mapping[str, Any], ...]:
    cutoff = parse_timestamp(as_of) if as_of else None
    result = []
    for row in _rows(payload):
        observed = _timestamp(row.get("time", row.get("timestamp")), "Coin Metrics observation time")
        if cutoff is not None and parse_timestamp(observed) > cutoff:
            continue
        values: dict[str, float] = {}
        for field in fields:
            if row.get(field) is None:
                continue
            try:
                value = float(row[field])
            except (TypeError, ValueError) as exc:
                raise ProviderDataError(f"Coin Metrics value for {field} is not numeric") from exc
            if not math.isfinite(value):
                raise ProviderDataError(f"Coin Metrics value for {field} is not finite")
            values[field] = value
        if values:
            result.append({"observed_at": observed, "values": values})
    return tuple(sorted(result, key=lambda item: parse_timestamp(item["observed_at"])))


def parse_tokenomics(
    payload: Mapping[str, Any],
    asset: str,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    source: str = "coinmetrics_community",
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    rows = _series_rows(payload, fields=("IssTotNtv", "SplyCur"), as_of=as_of)
    supply_rows = [row for row in rows if "SplyCur" in row["values"] and row["values"]["SplyCur"] > 0]
    if not supply_rows:
        raise ProviderUnsupportedMetric("Coin Metrics returned no current supply")
    current = supply_rows[-1]
    current_time = parse_timestamp(current["observed_at"])
    cutoff = current_time - timedelta(days=SUPPLY_LOOKBACK_DAYS)
    prior = [row for row in supply_rows if parse_timestamp(row["observed_at"]) <= cutoff]
    if not prior or (cutoff - parse_timestamp(prior[-1]["observed_at"])).total_seconds() > SUPPLY_LOOKBACK_TOLERANCE_DAYS * 86400:
        raise ProviderUnsupportedMetric("Coin Metrics supply history is insufficient for a 365d calculation")
    values: dict[str, tuple[float, str, str]] = {}
    if "tokenomics.supply_growth" in requested:
        values["tokenomics.supply_growth"] = (
            current["values"]["SplyCur"] / prior[-1]["values"]["SplyCur"] - 1,
            current["observed_at"],
            "current_supply / supply_at_or_before_365d - 1",
        )
    if "tokenomics.annualized_emissions" in requested:
        issuance_rows = [
            row for row in rows
            if "IssTotNtv" in row["values"] and parse_timestamp(row["observed_at"]) >= cutoff
        ]
        if (
            not issuance_rows
            or (parse_timestamp(issuance_rows[0]["observed_at"]) - cutoff).total_seconds()
            > SUPPLY_LOOKBACK_TOLERANCE_DAYS * 86400
        ):
            raise ProviderUnsupportedMetric("Coin Metrics issuance history is insufficient for a 365d calculation")
        values["tokenomics.annualized_emissions"] = (
            sum(row["values"]["IssTotNtv"] for row in issuance_rows) / current["values"]["SplyCur"],
            current["observed_at"],
            "gross_issuance_trailing_365d / current_supply",
        )
    return tuple({
        "asset": asset.strip().upper(),
        "metric_key": key,
        "value": value,
        "unit": metric_definition(key).unit,
        "period": "365d",
        "observed_at": observed,
        "fetched_at": fetched_at,
        "source": source,
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "timeseries/asset-metrics",
            "coinmetrics_metrics": list(COINMETRICS_TOKENOMICS_INPUTS[key]),
            "methodology": methodology,
            "window": "365d",
        },
    } for key, (value, observed, methodology) in values.items())


def _eth_prior(rows: tuple[Mapping[str, Any], ...], field: str, cutoff: datetime) -> Mapping[str, Any] | None:
    candidates = [
        row for row in rows
        if field in row["values"] and parse_timestamp(row["observed_at"]) <= cutoff
    ]
    return candidates[-1] if candidates else None


def parse_eth_metrics(
    payload: Mapping[str, Any],
    asset: str,
    metric_keys: Iterable[str],
    input_plan: Mapping[str, tuple[str, ...]],
    *,
    fetched_at: str,
    as_of: str | None = None,
    source: str = "coinmetrics_community",
) -> tuple[Mapping[str, Any], ...]:
    """Parse catalog-supported ETH monetary, staking, and realized-value metrics."""
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    fields = tuple(dict.fromkeys(field for key in requested for field in input_plan.get(key, ())))
    rows = _series_rows(payload, fields=fields, as_of=as_of)
    if not rows:
        raise ProviderUnsupportedMetric("Coin Metrics returned no ETH history")
    current = rows[-1]
    current_time = parse_timestamp(current["observed_at"])
    values: dict[str, tuple[float, str, str, tuple[str, ...]]] = {}

    def add(key: str, value: float | None, methodology: str, *, observed: str | None = None) -> None:
        if value is None or not math.isfinite(value):
            raise ProviderUnsupportedMetric(f"Coin Metrics returned no usable value for {key}")
        values[key] = (value, observed or current["observed_at"], methodology, input_plan.get(key, ()))

    for key in requested:
        inputs = input_plan.get(key, ())
        if not inputs:
            raise ProviderUnsupportedMetric(f"Coin Metrics catalog does not support {key}")
        if key == "eth.monetary.current_supply_eth":
            add(key, current["values"].get(inputs[0]), "latest SplyCur")
        elif key.startswith("eth.monetary.issuance_"):
            days = int(key.rsplit("_", 2)[-2].removesuffix("d"))
            start = current_time - timedelta(days=days)
            selected = [row for row in rows if inputs[0] in row["values"] and parse_timestamp(row["observed_at"]) >= start]
            if not selected or parse_timestamp(selected[0]["observed_at"]) > start + timedelta(days=SUPPLY_LOOKBACK_TOLERANCE_DAYS):
                raise ProviderInsufficientHistory(f"Coin Metrics issuance history is insufficient for {days}d")
            add(key, sum(row["values"][inputs[0]] for row in selected), f"sum {inputs[0]} over trailing {days}d")
        elif key.startswith("eth.monetary.net_supply_growth_"):
            days = int(key.rsplit("_", 1)[-1].removesuffix("d"))
            prior = _eth_prior(rows, inputs[0], current_time - timedelta(days=days))
            current_supply = current["values"].get(inputs[0])
            prior_supply = prior["values"].get(inputs[0]) if prior else None
            aligned = (
                prior is not None
                and current_time - parse_timestamp(prior["observed_at"]) - timedelta(days=days)
                <= timedelta(days=SUPPLY_LOOKBACK_TOLERANCE_DAYS)
            )
            add(
                key,
                current_supply / prior_supply - 1
                if aligned and current_supply is not None and prior_supply and prior_supply > 0
                else None,
                f"SplyCur / SplyCur at trailing {days}d - 1",
            )
        elif key == "eth_valuation.mvrv":
            direct = current["values"].get("CapMVRVCur")
            market = current["values"].get("CapMrktCurUSD")
            realized = current["values"].get("CapRealUSD")
            add(key, direct if direct is not None else market / realized if market is not None and realized and realized > 0 else None, "CapMVRVCur or CapMrktCurUSD / CapRealUSD")
        elif key == "eth_valuation.realized_price":
            direct = current["values"].get("PriceRealizedUSD")
            realized = current["values"].get("CapRealUSD")
            supply = current["values"].get("SplyCur")
            mvrv = current["values"].get("CapMVRVCur")
            market = current["values"].get("CapMrktCurUSD")
            add(
                key,
                direct
                if direct is not None
                else realized / supply if realized is not None and supply and supply > 0
                else market / mvrv / supply if market is not None and mvrv and mvrv > 0 and supply and supply > 0
                else None,
                "PriceRealizedUSD or CapRealUSD / SplyCur or CapMrktCurUSD / CapMVRVCur / SplyCur",
            )
        elif key == "eth_valuation.realized_cap_usd":
            realized = current["values"].get("CapRealUSD")
            mvrv = current["values"].get("CapMVRVCur")
            market = current["values"].get("CapMrktCurUSD")
            add(key, realized if realized is not None else market / mvrv if market is not None and mvrv and mvrv > 0 else None, "CapRealUSD or CapMrktCurUSD / CapMVRVCur")
        else:
            raise ProviderUnsupportedMetric(f"Coin Metrics does not have an ETH parser for {key}")

    return tuple({
        "asset": asset.strip().upper(),
        "metric_key": key,
        "value": value,
        "unit": metric_definition(key).unit,
        "period": "1d" if key.startswith("eth_valuation.") or key.endswith("current_supply_eth") or key.startswith("eth.staking.") and "change" not in key else "365d" if "365d" in key else "30d",
        "observed_at": observed,
        "fetched_at": fetched_at,
        "source": source,
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "timeseries/asset-metrics",
            "coinmetrics_metrics": list(metrics),
            "methodology": methodology,
            "asset_scope": "ETH",
        },
    } for key, (value, observed, methodology, metrics) in values.items())


def parse_exchange_netflow(
    payload: Mapping[str, Any],
    asset: str,
    *,
    fetched_at: str,
    as_of: str | None = None,
    source: str = "coinmetrics_community",
) -> Mapping[str, Any]:
    rows = _series_rows(payload, fields=COINMETRICS_EXCHANGE_FLOW_INPUTS, as_of=as_of)
    if not rows or not all(field in rows[-1]["values"] for field in COINMETRICS_EXCHANGE_FLOW_INPUTS):
        raise ProviderUnsupportedMetric("Coin Metrics exchange-flow history has no complete current row")
    row = rows[-1]
    inflow = row["values"]["FlowInExUSD"]
    outflow = row["values"]["FlowOutExUSD"]
    return {
        "asset": asset.strip().upper(),
        "metric_key": "flows.exchange_netflow",
        "value": inflow - outflow,
        "unit": "USD",
        "period": "1d",
        "observed_at": row["observed_at"],
        "fetched_at": fetched_at,
        "source": source,
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "timeseries/asset-metrics",
            "coinmetrics_metrics": list(COINMETRICS_EXCHANGE_FLOW_INPUTS),
            "methodology": "exchange_inflow_usd_minus_exchange_outflow_usd",
        },
    }


class CoinMetricsProvider:
    name = "coinmetrics_community"

    def __init__(
        self,
        *,
        client: HttpClient | Any | None = None,
        clock: Any | None = None,
        authenticated: bool = False,
        api_key: str | None = None,
    ) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.authenticated = authenticated
        self.api_key = api_key
        self.name = "coinmetrics_pro" if authenticated else "coinmetrics_community"
        self.base_url = AUTHENTICATED_BASE_URL if authenticated else COMMUNITY_BASE_URL
        self._catalog: frozenset[str] | None = None
        self._catalog_by_asset: dict[str, frozenset[str]] | None = None
        self._catalog_fetched_at: datetime | None = None
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=COINMETRICS_SUPPORTED_METRICS,
            historical_series=tuple(dict.fromkeys((
                *COINMETRICS_METRIC_MAP,
                *COINMETRICS_TOKENOMICS_INPUTS,
                *COINMETRICS_ETH_INPUTS,
                "flows.exchange_netflow",
            ))),
            supports_batching=True,
            requires_api_key=authenticated,
        )

    def _headers(self) -> Mapping[str, str]:
        if self.authenticated:
            if not self.api_key:
                raise ProviderAuthenticationError("Coin Metrics API key is not configured")
            return {"X-API-Key": self.api_key}
        return {}

    def catalog(self) -> frozenset[str]:
        now = datetime.now(timezone.utc)
        if (
            self._catalog_by_asset is None
            or self._catalog_fetched_at is None
            or (now - self._catalog_fetched_at).total_seconds() >= CATALOG_TTL_SECONDS
        ):
            payload = self.client.get_json(
                self.base_url + "/v4/catalog/asset-metrics",
                headers=self._headers(),
            )
            self._catalog = catalog_metrics(payload)
            self._catalog_by_asset = catalog_metrics_by_asset(payload)
            self._catalog_fetched_at = now
        return self._catalog

    def available_metrics_for_asset(self, asset: str) -> frozenset[str]:
        self.catalog()
        normalized = asset.strip().lower()
        if self._catalog_by_asset and normalized in self._catalog_by_asset:
            return self._catalog_by_asset[normalized]
        return self._catalog_by_asset.get("*", frozenset()) if self._catalog_by_asset else frozenset()

    def parse_cached_payload(
        self,
        request: ProviderRequest,
        payload: Mapping[str, Any],
        *,
        fetched_at: str | None = None,
    ) -> ProviderResponse:
        """Reparse a cached full-history payload without another network call."""
        _rows(payload)
        timestamp = fetched_at or _now(self.clock)
        fields = {str(field) for row in _rows(payload) for field in row}
        diagnostics: dict[str, Mapping[str, Any]] = {}
        values: list[Mapping[str, Any]] = []
        for key in request.metric_keys:
            try:
                if key in COINMETRICS_BTC_VALUATION_INPUTS:
                    candidates = COINMETRICS_BTC_VALUATION_INPUTS[key]
                    inputs = (
                        (candidates[0],)
                        if candidates[0] in fields
                        else tuple(item for item in candidates[1:] if item in fields)
                    )
                    if key == "btc_valuation.mvrv_zscore" and set(inputs) not in (
                        {"CapMVRVZ"},
                        {"CapMrktCurUSD", "CapRealUSD"},
                    ):
                        raise ProviderUnsupportedMetric(f"cached payload has no exact inputs for {key}")
                    if not inputs:
                        raise ProviderUnsupportedMetric(f"cached payload has no inputs for {key}")
                    values.extend(_parse_btc_valuation(
                        payload, request.asset, (key,), {key: inputs},
                        fetched_at=timestamp, as_of=request.parameters.get("as_of"), source=self.name,
                    ))
                elif key in COINMETRICS_METRIC_MAP:
                    values.extend(parse_timeseries(
                        payload, (key,), asset=request.asset, source=self.name,
                        fetched_at=timestamp, as_of=request.parameters.get("as_of"),
                    ))
                elif key in COINMETRICS_TOKENOMICS_INPUTS:
                    values.extend(parse_tokenomics(
                        payload, request.asset, (key,), source=self.name,
                        fetched_at=timestamp, as_of=request.parameters.get("as_of"),
                    ))
                elif key in COINMETRICS_ETH_INPUTS:
                    candidates = COINMETRICS_ETH_INPUTS[key]
                    inputs = tuple(item for item in candidates if item in fields)
                    if key == "eth_valuation.mvrv" and "CapMVRVCur" in fields:
                        inputs = ("CapMVRVCur",)
                    elif key == "eth_valuation.realized_price" and "PriceRealizedUSD" in fields:
                        inputs = ("PriceRealizedUSD",)
                    elif key == "eth_valuation.realized_cap_usd" and "CapRealUSD" in fields:
                        inputs = ("CapRealUSD",)
                    if not inputs:
                        raise ProviderUnsupportedMetric(f"cached payload has no inputs for {key}")
                    values.extend(parse_eth_metrics(
                        payload, request.asset, (key,), {key: inputs},
                        source=self.name, fetched_at=timestamp, as_of=request.parameters.get("as_of"),
                    ))
                elif key == "flows.exchange_netflow":
                    values.append(parse_exchange_netflow(
                        payload, request.asset, source=self.name,
                        fetched_at=timestamp, as_of=request.parameters.get("as_of"),
                    ))
                else:
                    raise ProviderUnsupportedMetric(f"cached full-history payload does not support {key}")
            except (ProviderError, ValueError) as exc:
                diagnostics[key] = _metric_diagnostic(exc, (self.api_key,) if self.api_key else ())
        return ProviderResponse(tuple(values), payload=payload, diagnostics=diagnostics, network_requests=0)

    def collect(self, request: ProviderRequest) -> ProviderResponse | list[Mapping[str, Any]]:
        asset = request.asset.strip().upper()
        try:
            asset_id = COINMETRICS_ASSETS[asset]
        except KeyError as exc:
            raise ProviderUnsupportedMetric(f"Coin Metrics has no approved asset mapping for {asset}") from exc
        requested = tuple(dict.fromkeys(request.metric_keys))
        unsupported_keys = {
            key for key in requested if key not in self.capabilities.metric_keys
        }
        diagnostics: dict[str, Mapping[str, Any]] = {
            key: {"error_code": "PROVIDER_UNSUPPORTED", "detail": "metric is not supported by this adapter"}
            for key in unsupported_keys
        }
        requested = tuple(key for key in requested if key not in unsupported_keys)
        if not requested:
            return ProviderResponse(observations=(), diagnostics=diagnostics, network_requests=0)
        available = self.available_metrics_for_asset(asset)
        required_inputs: dict[str, tuple[str, ...]] = {}
        input_plans: dict[str, tuple[str, ...]] = {}
        for key in requested:
            if key in COINMETRICS_BTC_VALUATION_INPUTS:
                candidates = COINMETRICS_BTC_VALUATION_INPUTS[key]
                if key == "btc_valuation.mvrv" and candidates[0].lower() in available:
                    selected_inputs = (candidates[0],)
                elif key == "btc_valuation.mvrv_zscore" and candidates[0].lower() in available:
                    selected_inputs = (candidates[0],)
                elif key == "btc_valuation.mvrv_zscore" and all(item.lower() in available for item in candidates[1:]):
                    selected_inputs = candidates[1:]
                elif key == "btc_valuation.realized_price" and candidates[0].lower() in available:
                    selected_inputs = (candidates[0],)
                elif key == "btc_valuation.mvrv" and all(item.lower() in available for item in candidates[1:]):
                    selected_inputs = candidates[1:]
                elif key == "btc_valuation.realized_price" and {"caprealusd", "splycur"} <= available:
                    selected_inputs = ("CapRealUSD", "SplyCur")
                elif key == "btc_valuation.realized_price" and {"capmvrvcur", "capmrktcurusd", "splycur"} <= available:
                    selected_inputs = ("CapMVRVCur", "CapMrktCurUSD", "SplyCur")
                elif key == "btc_valuation.realized_cap_usd" and {"capmvrvcur", "capmrktcurusd"} <= available:
                    selected_inputs = ("CapMVRVCur", "CapMrktCurUSD")
                elif all(item.lower() in available for item in candidates[:1]):
                    selected_inputs = candidates[:1]
                else:
                    selected_inputs = ()
                input_plans[key] = selected_inputs
                required_inputs[key] = selected_inputs
            elif key in COINMETRICS_METRIC_MAP:
                required_inputs[key] = (COINMETRICS_METRIC_MAP[key],)
            elif key in COINMETRICS_ETH_INPUTS:
                available_fields = {item.lower() for item in available}
                candidates = COINMETRICS_ETH_INPUTS[key]
                if key == "eth_valuation.mvrv":
                    selected_inputs = (
                        ("CapMVRVCur",)
                        if "capmvrvcur" in available_fields
                        else ("CapMrktCurUSD", "CapRealUSD")
                        if {"capmrktcurusd", "caprealusd"} <= available_fields
                        else ()
                    )
                elif key == "eth_valuation.realized_price":
                    selected_inputs = (
                        ("PriceRealizedUSD",)
                        if "pricerealizedusd" in available_fields
                        else ("CapRealUSD", "SplyCur")
                        if {"caprealusd", "splycur"} <= available_fields
                        else ("CapMVRVCur", "CapMrktCurUSD", "SplyCur")
                        if {"capmvrvcur", "capmrktcurusd", "splycur"} <= available_fields
                        else ()
                    )
                elif key == "eth_valuation.realized_cap_usd":
                    selected_inputs = (
                        ("CapRealUSD",)
                        if "caprealusd" in available_fields
                        else ("CapMVRVCur", "CapMrktCurUSD")
                        if {"capmvrvcur", "capmrktcurusd"} <= available_fields
                        else ()
                    )
                else:
                    selected_inputs = candidates if all(item.lower() in available_fields for item in candidates) else ()
                input_plans[key] = selected_inputs
                required_inputs[key] = selected_inputs
            elif key in COINMETRICS_TOKENOMICS_INPUTS:
                required_inputs[key] = COINMETRICS_TOKENOMICS_INPUTS[key]
            else:
                required_inputs[key] = COINMETRICS_EXCHANGE_FLOW_INPUTS
        available_requested = tuple(
            key for key in requested
            if required_inputs[key] and all(input_metric.lower() in available for input_metric in required_inputs[key])
        )
        diagnostics.update({
            key: {
                "error_code": "PROVIDER_UNSUPPORTED",
                "detail": f"catalog does not include the required 1d primitive for {key}",
            }
            for key in requested
            if key not in available_requested
        })
        if not available_requested:
            return ProviderResponse(observations=(), diagnostics=diagnostics, network_requests=0)
        input_metrics = tuple(dict.fromkeys(
            input_metric
            for key in available_requested
            for input_metric in required_inputs[key]
        ))
        params: dict[str, Any] = {
            "assets": asset_id,
            "metrics": ",".join(input_metrics),
            "frequency": "1d",
            "page_size": 1000,
        }
        if request.parameters.get("start") is not None:
            params["start_time"] = request.parameters["start"]
        if request.parameters.get("end") is not None:
            params["end_time"] = request.parameters["end"]
        payload = self.client.get_json(
            self.base_url + "/v4/timeseries/asset-metrics",
            params=params,
            headers=self._headers(),
        )
        payload, network_requests = _append_history_pages(
            self.client,
            payload,
            headers=self._headers(),
            full_history=request.parameters.get("history_mode") == "FULL_AVAILABLE",
        )
        _rows(payload)
        fetched_at = _now(self.clock)
        direct = tuple(
            key for key in available_requested
            if key in COINMETRICS_METRIC_MAP and key not in COINMETRICS_BTC_VALUATION_INPUTS
        )
        result: list[Mapping[str, Any]] = []
        for key in direct:
            try:
                result.extend(parse_timeseries(
                    payload,
                    (key,),
                    asset=asset,
                    source=self.name,
                    fetched_at=fetched_at,
                    as_of=request.parameters.get("as_of"),
                ))
            except (ProviderError, ValueError) as exc:
                diagnostics[key] = _metric_diagnostic(exc, (self.api_key,) if self.api_key else ())
        btc_valuation = tuple(key for key in available_requested if key in COINMETRICS_BTC_VALUATION_INPUTS)
        for key in btc_valuation:
            try:
                result.extend(_parse_btc_valuation(
                    payload,
                    asset,
                    (key,),
                    input_plans,
                    fetched_at=fetched_at,
                    as_of=request.parameters.get("as_of"),
                    source=self.name,
                ))
            except (ProviderError, ValueError) as exc:
                diagnostics[key] = _metric_diagnostic(exc, (self.api_key,) if self.api_key else ())
        tokenomics = tuple(key for key in available_requested if key in COINMETRICS_TOKENOMICS_INPUTS)
        for key in tokenomics:
            try:
                result.extend(parse_tokenomics(
                    payload,
                    asset,
                    (key,),
                    source=self.name,
                    fetched_at=fetched_at,
                    as_of=request.parameters.get("as_of"),
                ))
            except (ProviderError, ValueError) as exc:
                diagnostics[key] = _metric_diagnostic(exc, (self.api_key,) if self.api_key else ())
        eth_metrics = tuple(key for key in available_requested if key in COINMETRICS_ETH_INPUTS)
        for key in eth_metrics:
            try:
                result.extend(parse_eth_metrics(
                    payload,
                    asset,
                    (key,),
                    input_plans,
                    source=self.name,
                    fetched_at=fetched_at,
                    as_of=request.parameters.get("as_of"),
                ))
            except (ProviderError, ValueError) as exc:
                diagnostics[key] = _metric_diagnostic(exc)
        if "flows.exchange_netflow" in available_requested:
            try:
                result.append(parse_exchange_netflow(
                    payload,
                    asset,
                    source=self.name,
                    fetched_at=fetched_at,
                    as_of=request.parameters.get("as_of"),
                ))
            except (ProviderError, ValueError) as exc:
                diagnostics["flows.exchange_netflow"] = _metric_diagnostic(exc, (self.api_key,) if self.api_key else ())
        return ProviderResponse(
            observations=tuple(dict(item) for item in result),
            payload=payload,
            diagnostics=diagnostics,
            network_requests=network_requests,
        )


class CoinMetricsAuthenticatedProvider(CoinMetricsProvider):
    name = "coinmetrics_pro"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None, api_key: str | None = None) -> None:
        super().__init__(client=client, clock=clock, authenticated=True, api_key=api_key)


__all__ = [
    "AUTHENTICATED_BASE_URL",
    "CATALOG_TTL_SECONDS",
    "COINMETRICS_ASSETS",
    "COINMETRICS_BTC_CYCLE_METRICS",
    "COINMETRICS_BTC_NETWORK_METRICS",
    "COINMETRICS_BTC_VALUATION_INPUTS",
    "COINMETRICS_BTC_VALUATION_METRICS",
    "COINMETRICS_GENERIC_NETWORK_METRICS",
    "COINMETRICS_ETH_INPUTS",
    "COINMETRICS_ETH_SUPPORTED_METRICS",
    "COINMETRICS_MARKET_VALUATION_METRICS",
    "COINMETRICS_METRIC_MAP",
    "COINMETRICS_SUPPORTED_METRICS",
    "COMMUNITY_BASE_URL",
    "MAX_HISTORY_PAGES",
    "CoinMetricsAuthenticatedProvider",
    "CoinMetricsProvider",
    "catalog_metrics",
    "catalog_metrics_by_asset",
    "parse_exchange_netflow",
    "parse_eth_metrics",
    "parse_tokenomics",
    "parse_timeseries",
]
