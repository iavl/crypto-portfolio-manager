"""Small, attribution-preserving growthepie public-data adapter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import time
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderCapabilities,
    ProviderDataError,
    ProviderInsufficientHistory,
    ProviderRequest,
    ProviderRateLimited,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient, classify_transport_error


BASE_URL = "https://api.growthepie.com/v1"
RENT_PATH = "/fundamentals.json"
FUNDAMENTALS_PATH = "/fundamentals.json"
EXPORT_RENT_PATH = "/export/rent_paid.json"
DA_OVERVIEW_PATH = "/daoverview.json"
DA_TIMESERIES_PATH = "/datimeseries.json"
MASTER_PATH = "/master.json"
LANDING_PAGE_PATH = "/landing_page.json"
ATTRIBUTION = "growthepie / orbal GmbH"
LICENSE = "CC BY 4.0"
MAX_CALLS_PER_MINUTE = 10

_RENT_KEYS = {"eth.l2.rent_paid_30d_usd", "eth.l2.rent_paid_90d_usd"}
_DA_KEYS = {
    "eth.da.ethereum_blob_data_30d_mb",
    "eth.da.ethereum_blob_fees_30d_usd",
    "eth.da.ethereum_share_of_tracked_da_bytes_30d",
    "eth.da.ethereum_share_of_tracked_da_fees_30d",
}
_LANDING_KEYS = {"eth.l2.tvs_usd", "eth.l2.activity_30d", "onchain.blockspace_fees"}


def _diagnostic(error: Exception) -> Mapping[str, Any]:
    value = getattr(error, "diagnostic", None)
    if hasattr(value, "as_dict"):
        return dict(value.as_dict())
    if isinstance(value, Mapping):
        return dict(value)
    return {
        "error_code": classify_transport_error(error),
        "exception_class": error.__class__.__name__,
        "detail": str(error) or error.__class__.__name__,
    }


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProviderDataError(f"{field} is not numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise ProviderDataError(f"{field} is invalid")
    return result


def _timestamp(value: Any, field: str) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = _number(value, field)
        if number > 100_000_000_000:
            number /= 1000
        return normalize_timestamp(datetime.fromtimestamp(number, timezone.utc).isoformat(), field)
    if not isinstance(value, str) or not value.strip():
        raise ProviderDataError(f"{field} is missing")
    if isinstance(value, str) and len(value) == 10:
        value += "T00:00:00Z"
    return normalize_timestamp(value, field)


def _rows(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, Mapping):
        raw = payload.get("data", payload.get("rows", payload.get("results")))
        if isinstance(raw, Mapping):
            raw = raw.get("data", raw.get("rows", raw.get("results")))
    else:
        raw = None
    if not isinstance(raw, list):
        raise ProviderResponseError("growthepie response has no row list")
    if any(not isinstance(item, Mapping) for item in raw):
        raise ProviderDataError("growthepie response contains a malformed row")
    return tuple(raw)


def _row_date(row: Mapping[str, Any]) -> str:
    return _timestamp(row.get("date", row.get("timestamp", row.get("time"))), "growthepie row timestamp")


def _value(row: Mapping[str, Any], names: Iterable[str]) -> float | None:
    for name in names:
        if name in row and row[name] is not None:
            return _number(row[name], f"growthepie {name}")
    for container_name in ("metrics", "values", "value"):
        nested = row.get(container_name)
        if isinstance(nested, Mapping):
            found = _value(nested, names)
            if found is not None:
                return found
    return None


def _observation(
    asset: str,
    key: str,
    value: float,
    *,
    observed_at: str,
    fetched_at: str,
    period: str,
    endpoint: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "asset": asset,
        "metric_key": key,
        "value": value,
        "unit": metric_definition(key).unit,
        "period": period,
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "source": "growthepie",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": endpoint.rsplit("/", 1)[-1],
            "source_url": BASE_URL + endpoint,
            "attribution": ATTRIBUTION,
            "license": LICENSE,
            **dict(metadata or {}),
        },
    }


def _filtered_rows(payload: Any, as_of: str | None) -> list[tuple[str, Mapping[str, Any]]]:
    cutoff = parse_timestamp(as_of) if as_of else None
    values: list[tuple[str, Mapping[str, Any]]] = []
    for row in _rows(payload):
        timestamp = _row_date(row)
        if cutoff is not None and parse_timestamp(timestamp) > cutoff:
            continue
        values.append((timestamp, row))
    values.sort(key=lambda item: parse_timestamp(item[0]))
    return values


def parse_master_payload(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("chains"), Mapping):
        raise ProviderResponseError("growthepie master response has no chains object")
    chains = payload["chains"]
    if not isinstance(payload.get("metrics"), Mapping):
        raise ProviderResponseError("growthepie master response has no metrics object")
    return {"chains": dict(chains), "metrics": dict(payload["metrics"]), "last_updated_utc": payload.get("last_updated_utc")}


def _daily_metric_rows(payload: Any, chain: str, metric: str) -> tuple[tuple[str, float], ...]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
        raise ProviderResponseError("growthepie landing page response has no data object")
    chain_data = payload["data"].get(chain)
    metrics = chain_data.get("metrics") if isinstance(chain_data, Mapping) else None
    metric_data = metrics.get(metric) if isinstance(metrics, Mapping) else None
    daily = metric_data.get("daily") if isinstance(metric_data, Mapping) else None
    if not isinstance(daily, Mapping) or not isinstance(daily.get("types"), list) or not isinstance(daily.get("data"), list):
        raise ProviderResponseError(f"growthepie landing page is missing {chain}/{metric} daily data")
    types = tuple(str(item) for item in daily["types"])
    if not types or types[0] != "unix":
        raise ProviderResponseError(f"growthepie {chain}/{metric} daily schema has no unix timestamp")
    rows: list[tuple[str, float]] = []
    for index, row in enumerate(daily["data"]):
        if not isinstance(row, list) or len(row) != len(types):
            raise ProviderResponseError(f"growthepie {chain}/{metric} row {index} is malformed")
        timestamp = _timestamp(row[0], f"growthepie {chain}/{metric} timestamp")
        value_index = 1
        if metric in {"fees", "tvl"}:
            try:
                value_index = types.index("usd")
            except ValueError as exc:
                raise ProviderResponseError(f"growthepie {chain}/{metric} has no USD value") from exc
        rows.append((timestamp, _number(row[value_index], f"growthepie {chain}/{metric} value")))
    return tuple(rows)


def _completed_daily_rows(rows: Iterable[tuple[str, float]], as_of: str | None) -> list[tuple[str, float]]:
    cutoff = parse_timestamp(as_of) if as_of else datetime.now(timezone.utc)
    latest_by_day: dict[str, tuple[str, float]] = {}
    for timestamp, value in rows:
        if parse_timestamp(timestamp).date() >= cutoff.date():
            continue
        day = timestamp[:10]
        previous = latest_by_day.get(day)
        if previous is not None and previous[1] != value:
            raise ProviderDataError(f"growthepie has conflicting duplicate landing row for {day}")
        latest_by_day[day] = (timestamp, value)
    return [latest_by_day[day] for day in sorted(latest_by_day)]


def parse_landing_page_payload(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str = LANDING_PAGE_PATH,
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if not requested or any(key not in _LANDING_KEYS for key in requested):
        raise ProviderUnsupportedMetric("growthepie landing page does not support the requested metrics")
    result: list[Mapping[str, Any]] = []
    for key in requested:
        if key == "eth.l2.activity_30d":
            rows = _completed_daily_rows(_daily_metric_rows(payload, "all_l2s", "txcount"), as_of)
            if len(rows) < 30:
                raise ProviderInsufficientHistory("growthepie all_l2s transaction history is insufficient for 30d")
            latest_day = parse_timestamp(rows[-1][0]).date()
            start_day = latest_day - timedelta(days=29)
            selected = [item for item in rows if start_day <= parse_timestamp(item[0]).date() <= latest_day]
            if len(selected) != 30:
                raise ProviderInsufficientHistory("growthepie all_l2s transaction history has missing completed days")
            result.append(_observation(
                "ETH", key, sum(value for _, value in selected),
                observed_at=selected[-1][0], fetched_at=fetched_at, period="30d", endpoint=endpoint,
                metadata={
                    "source_metric": "txcount",
                    "methodology": "growthepie_all_l2s_daily_transaction_count_sum",
                    "chain_scope": "all_l2s",
                    "window": "30d",
                    "rows_used": len(selected),
                    "excludes_ethereum_l1": True,
                },
            ))
            continue
        chain = "ethereum" if key == "onchain.blockspace_fees" else "all_l2s"
        metric = "fees" if key == "onchain.blockspace_fees" else "tvl"
        rows = _completed_daily_rows(_daily_metric_rows(payload, chain, metric), as_of)
        if not rows:
            raise ProviderInsufficientHistory(f"growthepie {chain}/{metric} has no completed daily observation")
        observed_at, value = rows[-1]
        result.append(_observation(
            "ETH", key, value,
            observed_at=observed_at, fetched_at=fetched_at, period="1d", endpoint=endpoint,
            metadata={
                "source_metric": metric,
                "methodology": "fees_paid_by_users" if key == "onchain.blockspace_fees" else "growthepie_tvl",
                "chain_scope": chain,
                "window": "latest_completed_utc_day" if key == "onchain.blockspace_fees" else "stock_latest_completed_utc_day",
            },
        ))
    return tuple(result)


def _eligible_chain_keys(master: Mapping[str, Any], metric_name: str) -> set[str]:
    metric = master.get("metrics", {}).get(metric_name, {})
    supported = metric.get("supported_chains", ()) if isinstance(metric, Mapping) else ()
    if isinstance(supported, str):
        supported = (supported,)
    result = set()
    for chain in supported:
        row = master["chains"].get(chain)
        if not isinstance(row, Mapping) or row.get("deployment") in {"DEV", "ARCHIVED"}:
            continue
        if row.get("chain_type") == "l1":
            continue
        result.add(str(chain).strip().lower())
    return result


def _ethereum_da_chain_keys(master: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    eligible: set[str] = set()
    tracked: set[str] = set()
    for chain, row in master["chains"].items():
        if not isinstance(row, Mapping) or row.get("deployment") in {"DEV", "ARCHIVED"}:
            continue
        layer = str(row.get("da_layer") or "").strip().lower()
        if not layer:
            continue
        tracked.add(str(chain).strip().lower())
        if "ethereum" in layer and "blob" in layer:
            eligible.add(str(chain).strip().lower())
    return eligible, tracked


def parse_fundamentals_payload(
    payload: Any,
    master_payload: Any,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str = FUNDAMENTALS_PATH,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(payload, list) or any(not isinstance(row, Mapping) for row in payload):
        raise ProviderResponseError("growthepie fundamentals response must be an array of rows")
    master = parse_master_payload(master_payload)
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    allowed = _RENT_KEYS | _DA_KEYS
    if not requested or any(key not in allowed for key in requested):
        raise ProviderUnsupportedMetric("growthepie fundamentals does not support the requested metrics")
    cutoff = parse_timestamp(as_of) if as_of else None
    rows: list[tuple[str, str, str, float]] = []
    source_metrics = {"rent_paid_usd", "blob_size_bytes", "costs_blobs_usd"}
    for index, row in enumerate(payload):
        if set(row) != {"metric_key", "origin_key", "date", "value"}:
            raise ProviderResponseError(f"growthepie fundamentals row {index} does not match the documented schema")
        metric_name = str(row["metric_key"]).strip()
        if metric_name not in source_metrics:
            continue
        timestamp = _timestamp(row["date"], "growthepie fundamentals date")
        if cutoff is not None and parse_timestamp(timestamp) > cutoff:
            continue
        if row["value"] is None:
            continue
        rows.append((metric_name, str(row["origin_key"]).strip().lower(), timestamp, _number(row["value"], "growthepie fundamentals value")))
    if not rows:
        raise ProviderInsufficientHistory("growthepie fundamentals has no rows at or before as_of")
    rent_chains = _eligible_chain_keys(master, "rent_paid")
    da_chains, tracked_da_chains = _ethereum_da_chain_keys(master)
    result: list[Mapping[str, Any]] = []

    def window(metric: str, chains: set[str], days: int) -> tuple[float, str, int, set[str]]:
        selected = [(date, origin, value) for key, origin, date, value in rows if key == metric and origin in chains]
        if not selected:
            raise ProviderUnsupportedMetric(f"growthepie fundamentals has no {metric} rows for the eligible chain set")
        latest = max(item[0] for item in selected)
        start = parse_timestamp(latest[:10] + "T00:00:00Z") - timedelta(days=days - 1)
        within = [item for item in selected if parse_timestamp(item[0]) >= start]
        if not within or parse_timestamp(min(item[0] for item in within)) > start + timedelta(days=7):
            raise ProviderInsufficientHistory(f"growthepie fundamentals history is insufficient for {days}d")
        return sum(item[2] for item in within), latest, len(within), {item[1] for item in within}

    for key in requested:
        if key in _RENT_KEYS:
            days = 30 if key.endswith("30d_usd") else 90
            value, observed, rows_used, chains = window("rent_paid_usd", rent_chains, days)
            result.append(_observation(
                "ETH", key, value, observed_at=observed, fetched_at=fetched_at,
                period=f"{days}d", endpoint=endpoint,
                metadata={"window": f"{days}d", "rows_used": rows_used, "chain_set": sorted(chains), "source_contract": "master.json + fundamentals.json"},
            ))
            continue
        metric = "blob_size_bytes" if key.endswith("blob_data_30d_mb") or key.endswith("share_of_tracked_da_bytes_30d") else "costs_blobs_usd"
        numerator, observed, rows_used, chains = window(metric, da_chains, 30)
        if key.endswith("share_of_tracked_da_bytes_30d") or key.endswith("share_of_tracked_da_fees_30d"):
            denominator, _, _, _ = window(metric, tracked_da_chains, 30)
            if denominator <= 0:
                raise ProviderUnsupportedMetric(f"growthepie cannot derive {key} without a tracked DA denominator")
            value = numerator / denominator
        elif key.endswith("blob_data_30d_mb"):
            value = numerator / 1_000_000
        else:
            value = numerator
        result.append(_observation(
            "ETH", key, value, observed_at=observed, fetched_at=fetched_at,
            period="30d", endpoint=endpoint,
            metadata={"window": "30d", "rows_used": rows_used, "chain_set": sorted(chains), "source_contract": "master.json + fundamentals.json", "metric_key": metric},
        ))
    return tuple(result)


def parse_rent_payload(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str = RENT_PATH,
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if not requested or any(key not in _RENT_KEYS for key in requested):
        raise ProviderUnsupportedMetric("growthepie rent endpoint does not support the requested metrics")
    rows = _filtered_rows(payload, as_of)
    if not rows:
        raise ProviderInsufficientHistory("growthepie rent history is empty")
    numeric: list[tuple[str, float]] = []
    seen: dict[str, float] = {}
    for timestamp, row in rows:
        value = _value(row, ("rent_paid_usd", "rentPaidUsd", "rent_paid", "value", "total"))
        if value is None:
            raise ProviderDataError("growthepie rent row has no rent value")
        day = timestamp[:10]
        if day in seen and seen[day] != value:
            raise ProviderDataError(f"growthepie has conflicting duplicate rent row for {day}")
        seen[day] = value
    numeric = sorted((day, value) for day, value in seen.items())
    latest = parse_timestamp(numeric[-1][0] + "T00:00:00Z")
    result: list[Mapping[str, Any]] = []
    for key in requested:
        days = 30 if key.endswith("30d_usd") else 90
        start = latest - timedelta(days=days - 1)
        selected = [(day, value) for day, value in numeric if parse_timestamp(day + "T00:00:00Z") >= start]
        if not selected or parse_timestamp(selected[0][0] + "T00:00:00Z") > start + timedelta(days=7):
            raise ProviderInsufficientHistory(f"growthepie rent history is insufficient for {days}d")
        result.append(_observation(
            "ETH", key, sum(value for _, value in selected),
            observed_at=latest.isoformat().replace("+00:00", "Z"),
            fetched_at=fetched_at,
            period=f"{days}d",
            endpoint=endpoint,
            metadata={"window": f"{days}d", "rows_used": len(selected)},
        ))
    return tuple(result)


def _layer(row: Mapping[str, Any]) -> str:
    value = row.get("layer", row.get("da_layer", row.get("data_layer", row.get("chain", ""))))
    return str(value).strip().lower().replace(" ", "_")


def _is_ethereum(row: Mapping[str, Any]) -> bool:
    if row.get("is_ethereum") is True or row.get("isEthereum") is True:
        return True
    return _layer(row) in {"ethereum", "ethereum_l1", "ethereum_mainnet", "ethereum_blob"}


def parse_da_payload(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str = DA_TIMESERIES_PATH,
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if not requested or any(key not in _DA_KEYS for key in requested):
        raise ProviderUnsupportedMetric("growthepie DA endpoint does not support the requested metrics")
    rows = _filtered_rows(payload, as_of)
    if not rows:
        raise ProviderInsufficientHistory("growthepie DA history is empty")
    seen_layers: dict[tuple[str, str], Mapping[str, Any]] = {}
    for timestamp, row in rows:
        identity = (timestamp[:10], _layer(row))
        previous = seen_layers.get(identity)
        if previous is not None and previous != row:
            raise ProviderDataError(f"growthepie has conflicting duplicate DA row for {identity[0]}:{identity[1]}")
        seen_layers[identity] = row
    latest = rows[-1][0]
    start = parse_timestamp(latest[:10] + "T00:00:00Z") - timedelta(days=29)
    selected = [(timestamp, row) for timestamp, row in rows if parse_timestamp(timestamp) >= start]
    ethereum_rows = [(timestamp, row) for timestamp, row in selected if _is_ethereum(row)]
    if not ethereum_rows:
        # Explicit ethereum_* fields are already classified by the source.
        ethereum_rows = [(timestamp, row) for timestamp, row in selected if any(str(key).lower().startswith("ethereum_") for key in row)]
    if not ethereum_rows:
        raise ProviderUnsupportedMetric("growthepie DA rows have no explicit Ethereum layer")
    data_values = [
        _value(row, ("ethereum_blob_data_mb", "ethereum_data_mb", "data_mb"))
        for _, row in ethereum_rows
    ]
    byte_values = [_value(row, ("data_bytes", "blob_data_bytes", "ethereum_data_bytes")) for _, row in ethereum_rows]
    fee_values = [_value(row, ("ethereum_blob_fees_usd", "ethereum_fees_usd", "fees_usd", "fees")) for _, row in ethereum_rows]
    eth_bytes = sum(value for value in byte_values if value is not None)
    eth_mb = sum(value for value in data_values if value is not None) + eth_bytes / 1_000_000
    eth_fees = sum(value for value in fee_values if value is not None)
    has_data = any(value is not None for value in data_values + byte_values)
    has_fees = any(value is not None for value in fee_values)
    all_bytes = sum(
        value for _, row in selected
        for value in (_value(row, ("data_bytes", "blob_data_bytes", "ethereum_data_bytes")),)
        if value is not None
    )
    all_fees = sum(
        value for _, row in selected
        for value in (_value(row, ("fees_usd", "fees")),)
        if value is not None
    )
    result: list[Mapping[str, Any]] = []
    for key in requested:
        if key.endswith("blob_data_30d_mb"):
            value = eth_mb if has_data else None
        elif key.endswith("blob_fees_30d_usd"):
            value = eth_fees if has_fees else None
        elif key.endswith("share_of_tracked_da_bytes_30d"):
            value = eth_bytes / all_bytes if all_bytes > 0 else None
        else:
            value = eth_fees / all_fees if all_fees > 0 else None
        if value is None:
            raise ProviderUnsupportedMetric(f"growthepie cannot derive {key}")
        result.append(_observation(
            "ETH", key, value,
            observed_at=latest,
            fetched_at=fetched_at,
            period="30d",
            endpoint=endpoint,
            metadata={"window": "30d", "rows_used": len(ethereum_rows), "da_layer": "ethereum"},
        ))
    return tuple(result)


class GrowthepieProvider:
    name = "growthepie"

    def __init__(self, *, client: HttpClient | Any | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self._master_payload: Mapping[str, Any] | None = None
        self._request_times: list[float] = []
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=tuple(sorted(_RENT_KEYS | _DA_KEYS | _LANDING_KEYS)),
            historical_series=tuple(sorted(_RENT_KEYS | _DA_KEYS | _LANDING_KEYS)),
            supports_batching=True,
            requires_api_key=False,
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.asset != "ETH":
            raise ProviderUnsupportedMetric("growthepie metrics require ETH scope")
        requested = tuple(dict.fromkeys(request.metric_keys))
        fetched_at = _now(self.clock)
        values: list[Mapping[str, Any]] = []
        diagnostics: dict[str, Mapping[str, Any]] = {}
        as_of = request.parameters.get("as_of")

        def get_json(url: str) -> Any:
            now = time.monotonic()
            self._request_times[:] = [stamp for stamp in self._request_times if now - stamp < 60]
            if len(self._request_times) >= MAX_CALLS_PER_MINUTE:
                raise ProviderRateLimited("growthepie request budget exceeded: 10 calls per minute")
            self._request_times.append(now)
            return self.client.get_json(url)

        landing_requested = tuple(key for key in requested if key in _LANDING_KEYS)
        fundamentals_requested = tuple(key for key in requested if key in _RENT_KEYS or key in _DA_KEYS)
        unsupported_requested = tuple(
            key for key in requested if key not in _LANDING_KEYS and key not in _RENT_KEYS and key not in _DA_KEYS
        )
        for key in unsupported_requested:
            diagnostics[key] = {
                "error_code": "PROVIDER_UNSUPPORTED",
                "detail": "growthepie capability does not include metric",
            }

        network_requests = 0
        if landing_requested:
            try:
                landing = get_json(BASE_URL + LANDING_PAGE_PATH)
                network_requests += 1
                for key in landing_requested:
                    try:
                        values.extend(parse_landing_page_payload(
                            landing, (key,), fetched_at=fetched_at, as_of=as_of,
                        ))
                    except Exception as exc:
                        diagnostics[key] = _diagnostic(exc)
            except Exception as exc:
                network_requests += 1
                for key in landing_requested:
                    diagnostics[key] = _diagnostic(exc)
        if fundamentals_requested:
            try:
                if self._master_payload is None:
                    self._master_payload = parse_master_payload(get_json(BASE_URL + MASTER_PATH))
                    network_requests += 1
                fundamentals = get_json(BASE_URL + FUNDAMENTALS_PATH)
                network_requests += 1
                for key in fundamentals_requested:
                    try:
                        values.extend(parse_fundamentals_payload(
                            fundamentals,
                            self._master_payload,
                            (key,),
                            fetched_at=fetched_at,
                            as_of=as_of,
                        ))
                    except Exception as exc:
                        diagnostics[key] = _diagnostic(exc)
            except Exception as exc:
                network_requests += 1
                for key in fundamentals_requested:
                    diagnostics[key] = _diagnostic(exc)
        return ProviderResponse(tuple(values), diagnostics=diagnostics, network_requests=network_requests)


__all__ = [
    "ATTRIBUTION",
    "BASE_URL",
    "DA_OVERVIEW_PATH",
    "DA_TIMESERIES_PATH",
    "EXPORT_RENT_PATH",
    "FUNDAMENTALS_PATH",
    "LANDING_PAGE_PATH",
    "MAX_CALLS_PER_MINUTE",
    "GrowthepieProvider",
    "LICENSE",
    "MASTER_PATH",
    "RENT_PATH",
    "parse_da_payload",
    "parse_fundamentals_payload",
    "parse_landing_page_payload",
    "parse_master_payload",
    "parse_rent_payload",
]
