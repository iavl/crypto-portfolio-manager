"""Rated Free-tier Ethereum staking aggregate adapter."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderAuthenticationError,
    ProviderCapabilities,
    ProviderDataError,
    ProviderDiagnostic,
    ProviderInsufficientHistory,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from .http import HttpClient, classify_transport_error


BASE_URL = "https://api.rated.network"
DAILY_REWARDS_PATH = "/v0/eth/network/dailyRewards"
GWEI_PER_ETH = Decimal(10**9)
_ACTIVE_STAKE = "eth.staking.active_effective_stake_eth"
_KEYS = {_ACTIVE_STAKE}


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")


def _number(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ProviderDataError(f"{field} is not numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not result.is_finite() or result < 0:
        raise ProviderDataError(f"{field} is invalid")
    return result


def _source_date(value: Any) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ProviderDataError("Rated daily row is missing date")
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise ProviderDataError("Rated daily row date is invalid") from exc


def _observed_at(day: date) -> str:
    return normalize_timestamp(
        datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc).isoformat(),
        "observed_at",
    )


def _rows(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, Mapping):
        raw = payload.get("data", payload.get("results"))
    else:
        raw = None
    if not isinstance(raw, list) or any(not isinstance(row, Mapping) for row in raw):
        raise ProviderResponseError("Rated response has no row list")
    return tuple(raw)


def _completed_daily_rows(payload: Any, as_of: str | None) -> list[tuple[date, Mapping[str, Any]]]:
    cutoff = parse_timestamp(as_of).date() if as_of else datetime.now(timezone.utc).date()
    by_day: dict[date, Mapping[str, Any]] = {}
    for row in _rows(payload):
        day = _source_date(row.get("date"))
        if day >= cutoff:
            continue
        if day in by_day and by_day[day] != row:
            raise ProviderDataError(f"Rated has conflicting duplicate daily row for {day.isoformat()}")
        by_day[day] = row
    result = sorted(by_day.items())
    if not result:
        raise ProviderInsufficientHistory("Rated has no completed daily rewards row")
    return result


def _gwei_to_eth(value: Any, field: str) -> float:
    result = _number(value, field) / GWEI_PER_ETH
    if not result.is_finite():
        raise ProviderDataError(f"{field} is invalid")
    return float(result)


def parse_daily_rewards(
    payload: Any,
    metric_keys: Iterable[str],
    *,
    fetched_at: str,
    as_of: str | None = None,
) -> tuple[Mapping[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(key).strip().lower() for key in metric_keys))
    if not requested or any(key not in _KEYS for key in requested):
        raise ProviderUnsupportedMetric("Rated daily rewards does not support the requested metrics")
    rows = _completed_daily_rows(payload, as_of)
    latest_day, latest = rows[-1]
    result: list[Mapping[str, Any]] = []
    if _ACTIVE_STAKE in requested:
        result.append({
            "asset": "ETH",
            "metric_key": _ACTIVE_STAKE,
            "value": _gwei_to_eth(latest.get("sumEffectiveBalance"), "sumEffectiveBalance"),
            "unit": metric_definition(_ACTIVE_STAKE).unit,
            "period": "1d",
            "observed_at": _observed_at(latest_day),
            "fetched_at": normalize_timestamp(fetched_at, "fetched_at"),
            "source": "rated",
            "confidence": "MEDIUM",
            "metadata": {
                "source_dataset": "network/dailyRewards",
                "source_url": BASE_URL + DAILY_REWARDS_PATH,
                "source_metric": "sumEffectiveBalance",
                "methodology": "rated_sum_effective_balance",
                "raw_unit": "Gwei",
                "network": "mainnet",
            },
        })
    return tuple(result)


def _diagnostic(error: Exception) -> Mapping[str, Any]:
    value = getattr(error, "diagnostic", None)
    if hasattr(value, "as_dict"):
        result = dict(value.as_dict())
    elif isinstance(value, Mapping):
        result = dict(value)
    else:
        result = {"error_code": classify_transport_error(error), "detail": str(error) or error.__class__.__name__}
    if (
        result.get("status_code") == 401
        and "subscription is not active" in str(result.get("detail", "")).lower()
    ):
        result["error_code"] = "RATED_SUBSCRIPTION_INACTIVE"
        result["detail"] = "Subscription is not active."
    return result


def _subscription_inactive(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    return any(
        isinstance(payload.get(key), str)
        and "subscription is not active" in payload[key].lower()
        for key in ("detail", "message", "error")
    )


class RatedProvider:
    name = "rated"

    def __init__(self, *, client: HttpClient | Any | None = None, api_key: str | None = None, clock: Any | None = None) -> None:
        self.client = client or HttpClient()
        self.api_key = api_key
        self.clock = clock
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=tuple(sorted(_KEYS)),
            historical_series=(_ACTIVE_STAKE,),
            supports_batching=True,
            requires_api_key=True,
        )

    def _headers(self) -> Mapping[str, str]:
        if not self.api_key:
            raise ProviderAuthenticationError("Rated API key is not configured")
        return {"Authorization": f"Bearer {self.api_key}"}

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.asset != "ETH" or any(key not in _KEYS for key in request.metric_keys):
            raise ProviderUnsupportedMetric("Rated only supports active effective ETH stake")
        fetched_at = _now(self.clock)
        requested = tuple(dict.fromkeys(request.metric_keys))
        daily_keys = requested
        values: list[Mapping[str, Any]] = []
        diagnostics: dict[str, Mapping[str, Any]] = {}
        network_requests = 0
        if daily_keys:
            anchor = request.parameters.get("as_of") or request.parameters.get("end") or fetched_at
            anchor_day = parse_timestamp(anchor).date()
            max_days = 1
            try:
                payload = self.client.get_json(
                    BASE_URL + DAILY_REWARDS_PATH,
                    params={"from": (anchor_day - timedelta(days=max_days + 1)).isoformat(), "size": max_days + 1},
                    headers=self._headers(),
                )
                network_requests += 1
                if _subscription_inactive(payload):
                    raise ProviderAuthenticationError(
                        "Rated subscription is not active",
                        diagnostic=ProviderDiagnostic(
                            error_code="RATED_SUBSCRIPTION_INACTIVE",
                            detail="Subscription is not active.",
                            retryable=False,
                        ),
                    )
                values.extend(parse_daily_rewards(payload, daily_keys, fetched_at=fetched_at, as_of=request.parameters.get("as_of")))
            except Exception as exc:
                for key in daily_keys:
                    diagnostics[key] = _diagnostic(exc)
        return ProviderResponse(tuple(values), diagnostics=diagnostics, network_requests=network_requests)


__all__ = [
    "BASE_URL",
    "DAILY_REWARDS_PATH",
    "GWEI_PER_ETH",
    "RatedProvider",
    "parse_daily_rewards",
]
