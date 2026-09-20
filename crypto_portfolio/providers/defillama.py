"""Structured DeFiLlama protocol/fundamental provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from ..metrics_registry import (
    BNB_BLOCKSPACE_FEES_METHODOLOGY,
    BNB_NETWORK_FEES_DATA_TYPE,
    BNB_NETWORK_FEES_METHODOLOGY,
    BNB_NETWORK_FEES_PREFIX,
    BNB_NETWORK_FEES_SOURCE_DATASET,
    BNB_NETWORK_FEE_WINDOW_DAYS,
    BNB_SUPPLY_CHANGE_HORIZON_DAYS,
    BNB_SUPPLY_CHANGE_METHODOLOGY,
    BNB_SUPPLY_CHANGE_PREFIX,
    BNB_SUPPLY_MIN_SAMPLES,
    BNB_SUPPLY_SAMPLE_WINDOW_DAYS,
    metric_definition,
)
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
CHAIN_FEES_PATH = "/overview/fees"
CHAIN_DAILY_FEES_PATH = "/summary/fees"
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
STABLECOIN_SUPPLY_FIELD = "totalCirculating.peggedUSD"
_STABLE_DAYS = 3


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


def _error_details(exc: BaseException) -> dict[str, Any]:
    diagnostic = getattr(exc, "diagnostic", None)
    if hasattr(diagnostic, "as_dict"):
        return dict(diagnostic.as_dict())
    if isinstance(diagnostic, Mapping):
        return dict(diagnostic)
    return {
        "error_code": classify_transport_error(exc),
        "exception_class": exc.__class__.__name__,
        "detail": redact_secrets(str(exc)),
    }


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


def parse_chain_fees(
    payload: Any,
    *,
    asset: str,
    metric_key: str,
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str,
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("totalDataChart"), list):
        raise ProviderResponseError("DeFiLlama chain-fees response has no totalDataChart")
    cutoff = parse_timestamp(as_of) if as_of else datetime.now(timezone.utc)
    rows: list[tuple[str, float]] = []
    for index, row in enumerate(payload["totalDataChart"]):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ProviderDataError(f"DeFiLlama chain-fees row {index} is malformed")
        observed = _timestamp(row[0], "DeFiLlama chain-fees timestamp", fetched_at)
        if parse_timestamp(observed).date() >= cutoff.date():
            continue
        rows.append((observed, _number(row[1], "DeFiLlama chain fees")))
    if not rows:
        raise ProviderInsufficientHistory("DeFiLlama chain-fees history has no completed day")
    observed, value = max(rows, key=lambda item: parse_timestamp(item[0]))
    return {
        "asset": asset.strip().upper(),
        "metric_key": metric_key,
        "value": value,
        "unit": metric_definition(metric_key).unit,
        "period": "1d",
        "observed_at": observed,
        "fetched_at": fetched_at,
        "source": "defillama",
        "confidence": "MEDIUM",
        "metadata": {
            "source_dataset": "overview/fees",
            "source_url": endpoint,
            "methodology": "totalDataChart_latest_completed_utc_day",
            "chain_scope": CHAIN_NAMES.get(asset.strip().upper()),
        },
    }


def _utc_midnight(value: Any, field: str, fetched_at: str) -> str:
    """Return an exact UTC-day timestamp or fail closed."""
    timestamp = parse_timestamp(_timestamp(value, field, fetched_at))
    if (timestamp.hour, timestamp.minute, timestamp.second, timestamp.microsecond) != (0, 0, 0, 0):
        raise ProviderDataError(f"{field} must be an exact UTC day boundary")
    return normalize_timestamp(timestamp.isoformat(), field)


def _series_hash(rows: Iterable[tuple[str, float]]) -> str:
    payload = [[day, value] for day, value in rows]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _complete_daily_rows(
    payload: Any,
    *,
    field: str,
    fetched_at: str,
    cutoff: datetime | None = None,
) -> tuple[tuple[str, float], ...]:
    """Validate, de-duplicate and sort one daily series.

    Conflicting values for the same UTC day are a hard failure; a repeated
    identical day is collapsed once.  Rows are never forward-filled, and a row
    dated after ``cutoff`` (an observation from the future) is rejected rather
    than silently dropped.
    """
    if not isinstance(payload, list):
        raise ProviderResponseError(f"DeFiLlama {field} response must be a list")
    collected: dict[str, float] = {}
    for index, row in enumerate(payload):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ProviderDataError(f"DeFiLlama {field} row {index} is malformed")
        day = _utc_midnight(row[0], f"DeFiLlama {field} timestamp", fetched_at)
        if cutoff is not None and parse_timestamp(day) > cutoff:
            raise ProviderDataError(f"DeFiLlama {field} contains a future observation for {day[:10]}")
        value = _number(row[1], f"DeFiLlama {field} value")
        if day in collected:
            if not math.isclose(collected[day], value, rel_tol=1e-12, abs_tol=1e-9):
                raise ProviderDataError(f"DeFiLlama {field} has conflicting values for {day}")
            continue
        collected[day] = value
    if not collected:
        raise ProviderInsufficientHistory(f"DeFiLlama {field} history has no usable day")
    return tuple(sorted(collected.items()))


def _anchor_day(rows: tuple[tuple[str, float], ...], cutoff: datetime, field: str) -> str:
    """Return the latest fully completed UTC day, rejecting stale history."""
    completed = [day for day, _ in rows if parse_timestamp(day) + timedelta(days=1) <= cutoff]
    if not completed:
        raise ProviderInsufficientHistory(f"DeFiLlama {field} history has no completed UTC day")
    anchor = max(completed, key=parse_timestamp)
    if cutoff - parse_timestamp(anchor) > timedelta(days=_STABLE_DAYS):
        raise ProviderInsufficientHistory(f"DeFiLlama {field} history is stale")
    return anchor


def _percentile(current: float, samples: tuple[float, ...]) -> float:
    """Fraction of the historical magnitudes strictly below ``current``.

    Exact ties count half.  The current value never enters its own sample.
    """
    target = abs(current)
    below = 0
    equal = 0
    for sample in samples:
        magnitude = abs(sample)
        if math.isclose(magnitude, target, rel_tol=1e-12, abs_tol=0.0):
            equal += 1
        elif magnitude < target:
            below += 1
    return (below + 0.5 * equal) / len(samples)


def parse_stablecoin_supply_changes(
    payload: Any,
    *,
    asset: str,
    metric_keys: Iterable[str],
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str,
) -> tuple[Mapping[str, Any], ...]:
    """Turn one chain stablecoin history into 7d/30d/90d supply changes.

    The scored input is the *nominal USD-pegged supply* (``totalCirculating.peggedUSD``),
    never the price-adjusted ``totalCirculatingUSD``.  Each horizon carries its
    own change, the percentile of its absolute magnitude inside the preceding
    365 days, and the sample count, so the flow factor never has to re-derive a
    distribution it cannot verify.
    """
    cutoff = parse_timestamp(as_of) if as_of else datetime.now(timezone.utc)
    symbol = asset.strip().upper()
    rows = _stablecoin_supply_rows(payload, fetched_at=fetched_at, cutoff=cutoff)
    anchor = _anchor_day(rows, cutoff, "stablecoin supply")
    supplies = dict(rows)
    horizons = {
        key: BNB_SUPPLY_CHANGE_HORIZON_DAYS[key[len(BNB_SUPPLY_CHANGE_PREFIX):]]
        for key in dict.fromkeys(metric_keys)
        if key.startswith(BNB_SUPPLY_CHANGE_PREFIX)
        and key[len(BNB_SUPPLY_CHANGE_PREFIX):] in BNB_SUPPLY_CHANGE_HORIZON_DAYS
    }
    if not horizons:
        raise ProviderUnsupportedMetric("no supported stablecoin supply-change horizon was requested")
    anchor_value = supplies[anchor]
    anchor_day = parse_timestamp(anchor)
    days = sorted(supplies)
    observations: list[Mapping[str, Any]] = []
    for metric_key, window_days in sorted(horizons.items(), key=lambda item: item[1]):
        base_day = normalize_timestamp((anchor_day - timedelta(days=window_days)).isoformat(), "base_date")
        if base_day not in supplies:
            raise ProviderInsufficientHistory(
                f"DeFiLlama stablecoin supply history has no exact {window_days}-day endpoint"
            )
        base_value = supplies[base_day]
        if base_value <= 0:
            raise ProviderDataError("DeFiLlama stablecoin supply base value must be > 0")
        change = anchor_value / base_value - 1.0
        if not math.isfinite(change):
            raise ProviderDataError("DeFiLlama stablecoin supply change is invalid")
        samples, calibration_reason = _supply_change_samples(
            supplies, days, anchor_day=anchor_day, anchor_index=days.index(anchor), window_days=window_days,
        )
        if samples and change != 0.0 and all(sample == 0.0 for sample in samples):
            # A non-zero change against a history where every magnitude is zero
            # has no rank; it is uncalibrated rather than awarded full strength.
            samples = ()
            calibration_reason = "every historical absolute change is zero"
        calibration_state = "CALIBRATED" if samples else "UNCALIBRATED"
        percentile = _percentile(change, samples) if samples else None
        metadata = {
            "source_dataset": "stablecoincharts",
            "source_url": endpoint,
            "source_field": STABLECOIN_SUPPLY_FIELD,
            "methodology": BNB_SUPPLY_CHANGE_METHODOLOGY,
            "chain_scope": CHAIN_NAMES.get(symbol),
            "signal_interpretation": "usd_pegged_supply_expansion_proxy",
            "window_days": window_days,
            "sample_window_days": BNB_SUPPLY_SAMPLE_WINDOW_DAYS,
            "min_samples_required": BNB_SUPPLY_MIN_SAMPLES,
            "supply_change_ratio": change,
            "abs_change_percentile": percentile,
            "sample_count": len(samples),
            "calibration_state": calibration_state,
            "anchor_date": anchor,
            "base_date": base_day,
            "anchor_supply_usd": anchor_value,
            "base_supply_usd": base_value,
            "source_series_hash": _series_hash(rows),
            "source_confidence": "MEDIUM",
        }
        if calibration_state == "UNCALIBRATED":
            metadata["calibration_reason"] = calibration_reason
        observations.append({
            "asset": symbol,
            "metric_key": metric_key,
            "value": change,
            "unit": "fraction",
            "period": f"{window_days}d",
            "observed_at": anchor,
            "fetched_at": fetched_at,
            "source": "defillama",
            "confidence": "MEDIUM",
            "summary": (
                f"{CHAIN_NAMES.get(symbol, symbol)} USD-pegged stablecoin supply {window_days}d change "
                f"{change:+.2%} (expansion/contraction proxy; historical magnitude percentile "
                f"{percentile:.0%}, n={len(samples)})" if percentile is not None else
                f"{CHAIN_NAMES.get(symbol, symbol)} USD-pegged stablecoin supply {window_days}d change "
                f"{change:+.2%} (uncalibrated: {calibration_reason})"
            ),
            "metadata": metadata,
        })
    return tuple(observations)


def _supply_change_samples(
    supplies: Mapping[str, float],
    days: list[str],
    *,
    anchor_day: datetime,
    anchor_index: int,
    window_days: int,
) -> tuple[tuple[float, ...], str]:
    """Collect the same-horizon changes of the 365 days before the anchor.

    Returns the sample magnitudes and, when the horizon cannot be calibrated, a
    reason.  Fewer than the required number of exact endpoints leaves the
    horizon uncallibrated rather than awarding it a full score.
    """
    earliest = anchor_day - timedelta(days=BNB_SUPPLY_SAMPLE_WINDOW_DAYS)
    samples: list[float] = []
    for index in range(anchor_index):
        day = days[index]
        day_time = parse_timestamp(day)
        if day_time < earliest:
            continue
        base_day = normalize_timestamp((day_time - timedelta(days=window_days)).isoformat(), "sample_base")
        base_value = supplies.get(base_day)
        current_value = supplies[day]
        if base_value is None or base_value <= 0 or not math.isfinite(current_value):
            continue
        change = current_value / base_value - 1.0
        if math.isfinite(change):
            samples.append(change)
    if len(samples) < BNB_SUPPLY_MIN_SAMPLES:
        return (), (
            f"only {len(samples)} of {BNB_SUPPLY_MIN_SAMPLES} required {window_days}-day samples "
            f"in the preceding {BNB_SUPPLY_SAMPLE_WINDOW_DAYS} days"
        )
    return tuple(samples), ""


def _stablecoin_supply_rows(payload: Any, *, fetched_at: str, cutoff: datetime) -> tuple[tuple[str, float], ...]:
    """Extract the nominal USD-pegged supply series.

    ``totalCirculating.peggedUSD`` is the dollar-anchored nominal supply, so a
    pure price move in ``totalCirculatingUSD`` cannot masquerade as an inflow.
    ``totalMintedUSD``/``totalBridgedToUSD`` are not added back on top of an
    already-aggregated total.
    """
    if not isinstance(payload, list):
        raise ProviderResponseError("DeFiLlama stablecoin chart response must be a list")
    rows: list[list[Any]] = []
    for index, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise ProviderDataError(f"DeFiLlama stablecoin row {index} is malformed")
        day = _utc_midnight(row.get("date"), "DeFiLlama stablecoin date", fetched_at)
        circulating = row.get("totalCirculating")
        if not isinstance(circulating, Mapping) or circulating.get("peggedUSD") is None:
            raise ProviderDataError("DeFiLlama stablecoin row has no totalCirculating.peggedUSD supply")
        rows.append([day, _number(circulating["peggedUSD"], "DeFiLlama stablecoin supply")])
    return _complete_daily_rows(
        rows, field="stablecoin supply", fetched_at=fetched_at, cutoff=cutoff
    )


def parse_chain_daily_fees(
    payload: Any,
    *,
    asset: str,
    metric_keys: Iterable[str],
    fetched_at: str,
    as_of: str | None = None,
    endpoint: str,
) -> tuple[tuple[Mapping[str, Any], ...], Mapping[str, Mapping[str, Any]]]:
    """Turn one dailyFees series into BNB network-fee observations.

    ``onchain.blockspace_fees`` is the latest complete UTC day; the 30/90-day
    metrics are the complete-window totals and their window-over-window change.
    A missing day inside either window fails that horizon explicitly.
    """
    cutoff = parse_timestamp(as_of) if as_of else datetime.now(timezone.utc)
    symbol = asset.strip().upper()
    if not isinstance(payload, Mapping) or not isinstance(payload.get("totalDataChart"), list):
        raise ProviderResponseError("DeFiLlama chain daily-fees response has no totalDataChart")
    rows = _complete_daily_rows(
        payload["totalDataChart"],
        field="chain daily fees",
        fetched_at=fetched_at,
        cutoff=cutoff,
    )
    anchor = _anchor_day(rows, cutoff, "chain daily fees")
    fees = dict(rows)
    anchor_value = fees[anchor]
    anchor_day = parse_timestamp(anchor)
    series_digest = _series_hash(rows)
    chain_scope = CHAIN_NAMES.get(symbol)
    common = {
        "source_dataset": BNB_NETWORK_FEES_SOURCE_DATASET,
        "source_url": endpoint,
        "fees_data_type": BNB_NETWORK_FEES_DATA_TYPE,
        "methodology": BNB_NETWORK_FEES_METHODOLOGY,
        "chain_scope": chain_scope,
        "anchor_date": anchor,
        "source_series_hash": series_digest,
    }
    observations: list[Mapping[str, Any]] = []
    diagnostics: dict[str, Mapping[str, Any]] = {}
    if "onchain.blockspace_fees" in set(metric_keys):
        observations.append({
            "asset": symbol,
            "metric_key": "onchain.blockspace_fees",
            "value": anchor_value,
            "unit": "USD",
            "period": "1d",
            "observed_at": anchor,
            "fetched_at": fetched_at,
            "source": "defillama",
            "confidence": "MEDIUM",
            "summary": f"{chain_scope} network fees paid by users on {anchor[:10]}: {anchor_value:,.0f} USD",
            "metadata": {
                **common,
                "methodology": BNB_BLOCKSPACE_FEES_METHODOLOGY,
                "window": "latest_complete_utc_day",
                "usd_denominated": True,
                "price_effects": "USD fees move with both gas usage and the BNB price",
            },
        })
    for key in dict.fromkeys(metric_keys):
        if key == "onchain.blockspace_fees":
            continue
        suffix = key[len(BNB_NETWORK_FEES_PREFIX):] if key.startswith(BNB_NETWORK_FEES_PREFIX) else ""
        horizon = suffix.split("_", 1)[0]
        window_days = BNB_NETWORK_FEE_WINDOW_DAYS.get(horizon)
        if window_days is None:
            raise ProviderUnsupportedMetric(f"DeFiLlama daily fees cannot supply {key}")
        try:
            detail = _fee_window(
                fees,
                anchor_day=anchor_day,
                window_days=window_days,
                chain_scope=chain_scope,
            )
        except (ProviderDataError, ProviderInsufficientHistory) as exc:
            diagnostic = getattr(exc, "diagnostic", None)
            diagnostics[key] = dict(diagnostic.as_dict()) if hasattr(diagnostic, "as_dict") else {
                "error_code": classify_transport_error(exc),
                "detail": redact_secrets(str(exc)),
            }
            continue
        metadata = {
            **common,
            "window_days": window_days,
            "complete_utc_days": detail["complete_utc_days"],
            "window_start_date": detail["window_start_date"],
            "window_total_usd": detail["window_total_usd"],
            "previous_window_total_usd": detail["previous_window_total_usd"],
            "window_change_ratio": detail["window_change_ratio"],
            "usd_denominated": True,
            "price_effects": "USD fees move with both gas usage and the BNB price",
        }
        if key.endswith("_change"):
            value = detail["window_change_ratio"]
            summary = (
                f"{chain_scope} network fees last {window_days} complete days "
                f"{detail['window_total_usd']:,.0f} USD vs prior window {detail['window_change_ratio']:+.2%}"
            )
            period = f"{window_days}d_change"
        else:
            value = detail["window_total_usd"]
            summary = (
                f"{chain_scope} network fees over the last {window_days} complete days: "
                f"{detail['window_total_usd']:,.0f} USD"
            )
            period = f"{window_days}d"
        observations.append({
            "asset": symbol,
            "metric_key": key,
            "value": value,
            "unit": "fraction" if key.endswith("_change") else "USD",
            "period": period,
            "observed_at": anchor,
            "fetched_at": fetched_at,
            "source": "defillama",
            "confidence": "MEDIUM",
            "summary": summary,
            "metadata": metadata,
        })
    return tuple(observations), diagnostics


def _fee_window(
    fees: Mapping[str, float],
    *,
    anchor_day: datetime,
    window_days: int,
    chain_scope: str | None,
) -> dict[str, Any]:
    """Sum two adjacent complete windows of ``window_days`` and their change."""
    def window(end: datetime) -> tuple[float, str]:
        days = [
            normalize_timestamp((end - timedelta(days=offset)).isoformat(), "window day")
            for offset in range(window_days)
        ]
        missing = [day for day in days if day not in fees]
        if missing:
            raise ProviderInsufficientHistory(
                f"{chain_scope} daily fees are missing {len(missing)} of {window_days} days "
                f"ending {days[0][:10]}"
            )
        return sum(fees[day] for day in days), days[-1]

    total, start = window(anchor_day)
    previous_total, previous_start = window(anchor_day - timedelta(days=window_days))
    if previous_total <= 0:
        raise ProviderDataError(f"{chain_scope} prior {window_days}-day fee window must be > 0")
    change = total / previous_total - 1.0
    if not math.isfinite(change):
        raise ProviderDataError(f"{chain_scope} {window_days}-day fee change is invalid")
    return {
        "window_total_usd": total,
        "previous_window_total_usd": previous_total,
        "window_change_ratio": change,
        "complete_utc_days": window_days,
        "window_start_date": start,
        "previous_window_start_date": previous_start,
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
                "onchain.blockspace_fees",
                "onchain.bnb_network_fees_30d_usd", "onchain.bnb_network_fees_90d_usd",
                "onchain.bnb_network_fees_30d_change", "onchain.bnb_network_fees_90d_change",
                "flows.bnb_stablecoin_supply_change_7d", "flows.bnb_stablecoin_supply_change_30d",
                "flows.bnb_stablecoin_supply_change_90d",
            ),
            historical_series=(
                "fundamentals.tvl", "fundamentals.fees_30d", "fundamentals.revenue_30d",
                "fundamentals.stablecoin_liquidity", "market.stablecoin_supply", "onchain.blockspace_fees",
                "onchain.bnb_network_fees_30d_usd", "onchain.bnb_network_fees_90d_usd",
                "onchain.bnb_network_fees_30d_change", "onchain.bnb_network_fees_90d_change",
                "flows.bnb_stablecoin_supply_change_7d", "flows.bnb_stablecoin_supply_change_30d",
                "flows.bnb_stablecoin_supply_change_90d",
            ),
            supports_batching=True,
            requires_api_key=False,
        )

    def validate_cached_observations(self, request: ProviderRequest, values: Any) -> bool:
        """Reject cached observations produced by a superseded source contract.

        The BNB network-fee source moved from the application-inclusive
        ``/overview/fees`` aggregate to ``/summary/fees?dataType=dailyFees``.
        A cache entry written by the old method must never be scored as network
        gas demand, so the source, scope and methodology are re-checked before
        any cached value is reused.  Nothing is deleted or migrated.
        """
        if request.asset.strip().upper() != "BNB":
            return True
        for value in values:
            if not isinstance(value, Mapping):
                return False
            key = str(value.get("metric_key", "")).strip().lower()
            metadata = value.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            if key == "onchain.blockspace_fees":
                if metadata.get("source_dataset") != BNB_NETWORK_FEES_SOURCE_DATASET:
                    return False
                if metadata.get("fees_data_type") != BNB_NETWORK_FEES_DATA_TYPE:
                    return False
                if metadata.get("methodology") != BNB_BLOCKSPACE_FEES_METHODOLOGY:
                    return False
            elif key.startswith(BNB_NETWORK_FEES_PREFIX) or key.startswith(BNB_SUPPLY_CHANGE_PREFIX):
                if metadata.get("methodology") not in {BNB_NETWORK_FEES_METHODOLOGY, BNB_SUPPLY_CHANGE_METHODOLOGY}:
                    return False
        return True

    def _bnb_network_fees(self, request: ProviderRequest) -> ProviderResponse:
        asset = request.asset.strip().upper()
        chain = CHAIN_NAMES.get(asset)
        if chain is None:
            raise ProviderUnsupportedMetric("DeFiLlama chain daily fees require a BNB chain scope")
        keys = tuple(request.metric_keys)
        if any(key != "onchain.blockspace_fees" and not key.startswith(BNB_NETWORK_FEES_PREFIX) for key in keys):
            raise ProviderUnsupportedMetric(
                "DeFiLlama chain daily fees serve only BNB network-fee metrics in one request"
            )
        endpoint = BASE_URL + CHAIN_DAILY_FEES_PATH + "/" + quote(chain.lower(), safe="")
        fetched_at = _now(self.clock)
        try:
            observations, diagnostics = parse_chain_daily_fees(
                self.client.get_json(endpoint, params={"dataType": BNB_NETWORK_FEES_DATA_TYPE}),
                asset=asset,
                metric_keys=keys,
                fetched_at=fetched_at,
                as_of=request.parameters.get("as_of"),
                endpoint=endpoint,
            )
        except (ProviderDataError, ProviderResponseError, ProviderInsufficientHistory) as exc:
            return ProviderResponse((), diagnostics={
                key: _error_details(exc) for key in keys
            }, network_requests=1)
        if not observations and not diagnostics:
            raise ProviderUnsupportedMetric("DeFiLlama chain daily fees did not supply a requested metric")
        return ProviderResponse(observations, diagnostics=diagnostics or None, network_requests=1)

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        asset = request.asset.strip().upper()
        keys = tuple(request.metric_keys)
        if asset == "BNB" and any(
            key == "onchain.blockspace_fees" or key.startswith(BNB_NETWORK_FEES_PREFIX) for key in keys
        ):
            return self._bnb_network_fees(request)
        if "onchain.blockspace_fees" in keys:
            # The legacy aggregate stays as the ETH fallback only: it mixes
            # application protocol fees into what would otherwise be read as
            # network gas demand.
            chain = CHAIN_NAMES.get(asset)
            if chain is None or any(key != "onchain.blockspace_fees" for key in keys):
                raise ProviderUnsupportedMetric("DeFiLlama chain fees require one ETH blockspace metric")
            endpoint = BASE_URL + CHAIN_FEES_PATH + "/" + quote(chain, safe="")
            fetched_at = _now(self.clock)
            try:
                observation = parse_chain_fees(
                    self.client.get_json(endpoint),
                    asset=asset,
                    metric_key="onchain.blockspace_fees",
                    fetched_at=fetched_at,
                    as_of=request.parameters.get("as_of"),
                    endpoint=endpoint,
                )
            except (ProviderDataError, ProviderResponseError, ProviderInsufficientHistory) as exc:
                return ProviderResponse((), diagnostics={"onchain.blockspace_fees": _error_details(exc)}, network_requests=1)
            return ProviderResponse((observation,), network_requests=1)
        if request.dataset == "stablecoin":
            asset = request.asset.strip().upper()
            keys = tuple(request.metric_keys)
            change_keys = tuple(key for key in keys if key.startswith(BNB_SUPPLY_CHANGE_PREFIX))
            value_keys = tuple(key for key in keys if key in {"market.stablecoin_supply", "fundamentals.stablecoin_liquidity"})
            if len(change_keys) + len(value_keys) != len(keys):
                raise ProviderUnsupportedMetric("DeFiLlama stablecoin API does not support the requested metrics")
            if change_keys and asset != "BNB":
                raise ProviderUnsupportedMetric("stablecoin supply changes are defined only for the BNB chain scope")
            if asset == "MARKET":
                if change_keys:
                    raise ProviderNotApplicable("stablecoin supply changes require a chain scope")
                scope = "all"
            else:
                scope = CHAIN_NAMES.get(asset)
                if scope is None:
                    raise ProviderNotApplicable("stablecoin supply is defined only for global or chain scope")
            endpoint = STABLECOINS_BASE_URL + STABLECOIN_CHARTS_PATH + "/" + scope
            fetched_at = _now(self.clock)
            payload = self.client.get_json(endpoint)
            observations: list[Mapping[str, Any]] = []
            # One request returns the whole daily history; it serves the scale
            # observation and every supply-change horizon together.
            for key in value_keys:
                observations.append(parse_stablecoin_chart(
                    payload,
                    asset=asset,
                    metric_key=key,
                    fetched_at=fetched_at,
                    as_of=request.parameters.get("as_of"),
                    endpoint=endpoint,
                ))
            if change_keys:
                try:
                    observations.extend(parse_stablecoin_supply_changes(
                        payload,
                        asset=asset,
                        metric_keys=change_keys,
                        fetched_at=fetched_at,
                        as_of=request.parameters.get("as_of"),
                        endpoint=endpoint,
                    ))
                except (ProviderDataError, ProviderResponseError, ProviderInsufficientHistory) as exc:
                    return ProviderResponse(
                        tuple(observations),
                        diagnostics={key: _error_details(exc) for key in change_keys},
                        network_requests=1,
                    )
            return ProviderResponse(tuple(observations), network_requests=1)
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
    "STABLECOIN_SUPPLY_FIELD",
    "DeFiLlamaProvider",
    "CHAIN_DAILY_FEES_PATH",
    "CHAIN_FEES_PATH",
    "identifier_for_asset",
    "parse_chain_daily_fees",
    "parse_chain_fees",
    "parse_stablecoin_chart",
    "parse_stablecoin_supply_changes",
    "parse_protocol_payload",
]
