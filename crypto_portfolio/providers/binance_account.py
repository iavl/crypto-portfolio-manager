"""Read-only Binance account client for signed USER_DATA requests.

This module fetches spot balances, Simple Earn positions, ETH staking
(WBETH), and deposit/withdrawal history from Binance with an HMAC-SHA256
signature.  It is intentionally separate from the market-data
``BinanceProvider``: account state is portfolio input, not a scoring
metric, so it never registers with the metric router.

Only GET endpoints are issued; this module must never place, cancel, or
modify orders.  Credentials arrive exclusively through the provider
configuration ``api_key_env`` / ``api_secret_env`` environment variables.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from .base import (
    ProviderAuthenticationError,
    ProviderDataError,
    ProviderResponseError,
)
from .http import HttpClient


DEFAULT_BASE_URL = "https://api.binance.com"
RECV_WINDOW_MS = 5000
_PAGE_SIZE = 1000
_EARN_PAGE_SIZE = 100
_COMPLETED_FLOW_STATUS = 6
# Binance API error codes that mean the key/secret or its permissions are wrong.
_AUTH_ERROR_CODES = (-2014, -2015)
_TIMESTAMP_ERROR_CODE = -1021
# WBETH position payloads have carried different field names across sapi
# revisions; unknown shapes fail closed instead of being guessed.
_WBETH_AMOUNT_FIELDS = ("holding", "wbethAmount", "amount", "wbeth")


def _decimal_string(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProviderDataError(f"{field} is not numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result < minimum:
        raise ProviderDataError(f"{field} is invalid")
    return result


@dataclass(frozen=True)
class WalletBalance:
    """A quantity of one asset held in one named Binance wallet."""

    asset: str
    quantity: float
    wallet: str

    def __post_init__(self) -> None:
        if not isinstance(self.asset, str) or not self.asset.strip():
            raise ProviderDataError("wallet balance asset must be a non-empty string")
        object.__setattr__(self, "asset", self.asset.strip().upper())
        object.__setattr__(
            self, "quantity", _decimal_string(self.quantity, f"{self.asset}.quantity")
        )
        if not isinstance(self.wallet, str) or not self.wallet.strip():
            raise ProviderDataError("wallet balance wallet must be a non-empty string")


@dataclass(frozen=True)
class FlowEvent:
    """One completed or in-flight on-chain deposit/withdrawal record."""

    direction: str
    asset: str
    amount: float
    timestamp_ms: int
    completed: bool
    raw_status: int

    def __post_init__(self) -> None:
        if self.direction not in {"DEPOSIT", "WITHDRAWAL"}:
            raise ProviderDataError("flow event direction is unsupported")
        object.__setattr__(self, "asset", str(self.asset).strip().upper())
        object.__setattr__(
            self, "amount", _decimal_string(self.amount, f"{self.asset}.flow amount")
        )
        if isinstance(self.timestamp_ms, bool) or not isinstance(self.timestamp_ms, int):
            raise ProviderDataError("flow event timestamp_ms must be an integer")
        object.__setattr__(self, "completed", bool(self.completed))
        if isinstance(self.raw_status, bool) or not isinstance(self.raw_status, int):
            raise ProviderDataError("flow event raw_status must be an integer")


def _error_codes(exception: Exception) -> set[int]:
    detail = str(exception)
    diagnostic = getattr(exception, "diagnostic", None)
    if diagnostic is not None:
        detail = f"{detail} {getattr(diagnostic, 'detail', '')}"
    return {int(token) for token in re.findall(r"-\d+", detail)}


class BinanceAccountClient:
    """Signed read-only access to one Binance account."""

    name = "binance_account"

    def __init__(
        self,
        client: HttpClient,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProviderAuthenticationError("binance_account api key is missing")
        if not isinstance(api_secret, str) or not api_secret.strip():
            raise ProviderAuthenticationError("binance_account api secret is missing")
        self.client = client
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.clock = clock or time.time
        self._time_offset_ms: int | None = None

    @classmethod
    def from_environment(
        cls,
        client: HttpClient,
        *,
        api_key: str | None,
        api_secret: str | None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> "BinanceAccountClient | None":
        """Build a client only when both credentials are present."""
        if not (api_key or "").strip() or not (api_secret or "").strip():
            return None
        return cls(client, api_key.strip(), api_secret.strip(), base_url=base_url)

    # -- transport ---------------------------------------------------------

    def _sync_clock(self) -> None:
        payload = self._public_get("/api/v3/time", {})
        server_time = payload.get("serverTime") if isinstance(payload, Mapping) else None
        if isinstance(server_time, bool) or not isinstance(server_time, int):
            raise ProviderDataError("binance server time response is invalid")
        self._time_offset_ms = server_time - int(self.clock() * 1000)

    def _now_ms(self) -> int:
        return int(self.clock() * 1000) + (self._time_offset_ms or 0)

    def _sign(self, query: str) -> str:
        return hmac.new(
            self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _public_get(self, path: str, params: Mapping[str, str]) -> Any:
        return self.client.get_json(self.base_url + path, params=dict(params))

    def _signed_get(self, path: str, params: Mapping[str, str]) -> Any:
        for resync in (False, True):
            if self._time_offset_ms is None or resync:
                self._sync_clock()
            signed_params = {
                **params,
                "recvWindow": str(RECV_WINDOW_MS),
                "timestamp": str(self._now_ms()),
            }
            # HttpClient re-encodes params with urlencode in dict order, so the
            # signed string and the wire query string stay byte-identical.
            signature = self._sign(urlencode(list(signed_params.items())))
            try:
                return self.client.get_json(
                    self.base_url + path,
                    params={**signed_params, "signature": signature},
                    headers={"X-MBX-APIKEY": self.api_key},
                )
            except (ProviderResponseError, ProviderAuthenticationError) as exc:
                codes = _error_codes(exc)
                if codes & set(_AUTH_ERROR_CODES) or (
                    isinstance(exc, ProviderAuthenticationError) and not codes
                ):
                    raise ProviderAuthenticationError(
                        "binance_account credentials were rejected; verify the "
                        "read-only API key and its permissions"
                    ) from exc
                if _TIMESTAMP_ERROR_CODE in codes and not resync:
                    continue
                raise
        raise ProviderResponseError("binance_account signed request failed")

    # -- public market helpers ----------------------------------------------

    def ticker_price(self, symbol: str) -> float:
        """Last traded price for one quoted symbol, e.g. BTCUSDT."""
        pair = symbol.strip().upper()
        if not pair:
            raise ProviderDataError("ticker symbol must be non-empty")
        payload = self._public_get("/api/v3/ticker/price", {"symbol": pair})
        if not isinstance(payload, Mapping) or "price" not in payload:
            raise ProviderDataError(f"ticker response for {pair} is invalid")
        return _decimal_string(payload["price"], f"{pair}.price")

    def daily_close(self, symbol: str, day_start_ms: int) -> float:
        """Close of the 1d candle opening at ``day_start_ms`` (UTC day close).

        For the current UTC day the candle is still forming, so this is the
        latest price; flow valuation is pinned once the snapshot is persisted.
        """
        pair = symbol.strip().upper()
        payload = self._public_get(
            "/api/v3/klines",
            {
                "symbol": pair,
                "interval": "1d",
                "startTime": str(day_start_ms),
                "limit": "1",
            },
        )
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
            raise ProviderDataError(f"daily close response for {pair} is invalid")
        candle = payload[0]
        if len(candle) < 5:
            raise ProviderDataError(f"daily close candle for {pair} is invalid")
        return _decimal_string(candle[4], f"{pair}.daily close")

    # -- signed account endpoints -------------------------------------------

    def spot_balances(self) -> tuple[WalletBalance, ...]:
        payload = self._signed_get("/api/v3/account", {})
        if not isinstance(payload, Mapping) or not isinstance(payload.get("balances"), list):
            raise ProviderDataError("binance account response is invalid")
        balances: list[WalletBalance] = []
        for entry in payload["balances"]:
            if not isinstance(entry, Mapping) or "asset" not in entry:
                raise ProviderDataError("binance account balance entry is invalid")
            quantity = _decimal_string(
                entry.get("free"), f"spot {entry['asset']}.free"
            ) + _decimal_string(entry.get("locked"), f"spot {entry['asset']}.locked")
            if quantity <= 0:
                continue
            balances.append(
                WalletBalance(asset=entry["asset"], quantity=quantity, wallet="spot")
            )
        return tuple(balances)

    def flexible_earn_positions(self) -> tuple[WalletBalance, ...]:
        return tuple(
            self._earn_page("flexible", "totalAmount", "/sapi/v1/simple-earn/flexible/position")
        )

    def locked_earn_positions(self) -> tuple[WalletBalance, ...]:
        return tuple(self._earn_page("locked", "amount", "/sapi/v1/simple-earn/locked/position"))

    def _earn_page(self, wallet: str, amount_field: str, path: str) -> list[WalletBalance]:
        positions: list[WalletBalance] = []
        current = 1
        while True:
            payload = self._signed_get(
                path,
                {"current": str(current), "size": str(_EARN_PAGE_SIZE)},
            )
            if not isinstance(payload, Mapping) or not isinstance(payload.get("rows"), list):
                raise ProviderDataError(f"binance {wallet} earn position response is invalid")
            rows = payload["rows"]
            if not rows:
                break
            for entry in rows:
                if not isinstance(entry, Mapping) or "asset" not in entry:
                    raise ProviderDataError(f"binance {wallet} earn position row is invalid")
                quantity = _decimal_string(
                    entry.get(amount_field), f"{wallet} earn {entry['asset']}.{amount_field}"
                )
                redeeming = _decimal_string(
                    entry.get("redeemingAmt", 0.0),
                    f"{wallet} earn {entry['asset']}.redeemingAmt",
                )
                quantity += redeeming
                if quantity <= 0:
                    continue
                positions.append(
                    WalletBalance(asset=entry["asset"], quantity=quantity, wallet=f"earn_{wallet}")
                )
            if len(rows) < _EARN_PAGE_SIZE or current >= int(payload.get("total", current)):
                break
            current += 1
        return positions

    def simple_earn_totals(self) -> Mapping[str, Any] | None:
        """Binance's own Simple Earn totals, used only for cross-checks."""
        payload = self._signed_get("/sapi/v1/simple-earn/account", {})
        if not isinstance(payload, Mapping):
            raise ProviderDataError("binance simple earn account response is invalid")
        return payload

    def eth_staking_wbeth(self) -> WalletBalance | None:
        """WBETH holding from the ETH staking position endpoint.

        WBETH usually also appears in the spot wallet; the importer treats
        spot as authoritative and uses this only for cross-checking.
        """
        payload = self._signed_get("/sapi/v1/eth-staking/eth/position", {})
        if not isinstance(payload, Mapping):
            raise ProviderDataError("binance eth staking position response is invalid")
        raw = None
        for field in _WBETH_AMOUNT_FIELDS:
            if field in payload and payload[field] is not None:
                raw = payload[field]
                break
        if raw is None:
            raise ProviderDataError(
                "binance eth staking position payload shape is unrecognized; "
                "update _WBETH_AMOUNT_FIELDS for the current sapi contract"
            )
        quantity = _decimal_string(raw, "eth staking WBETH quantity")
        if quantity <= 0:
            return None
        return WalletBalance(asset="WBETH", quantity=quantity, wallet="eth_staking")

    def wbeth_exchange_rate(self) -> float | None:
        """WBETH-to-ETH exchange rate; None when sapi does not expose it."""
        try:
            payload = self._public_get("/sapi/v1/eth-staking/wbeth/exchange-rate", {})
        except (ProviderResponseError, ProviderDataError):
            return None
        if not isinstance(payload, Mapping):
            return None
        raw = payload.get("exchangeRate")
        if raw is None:
            return None
        return _decimal_string(raw, "WBETH exchange rate")

    def deposit_history(self, since_ms: int, until_ms: int) -> tuple[FlowEvent, ...]:
        return self._flow_history(
            "/sapi/v1/capital/deposit/hisrec",
            "DEPOSIT",
            since_ms,
            until_ms,
            time_field="insertTime",
            timestamp_is_ms=True,
        )

    def withdrawal_history(self, since_ms: int, until_ms: int) -> tuple[FlowEvent, ...]:
        return self._flow_history(
            "/sapi/v1/capital/withdraw/history",
            "WITHDRAWAL",
            since_ms,
            until_ms,
            time_field="applyTime",
            timestamp_is_ms=False,
        )

    def _flow_history(
        self,
        path: str,
        direction: str,
        since_ms: int,
        until_ms: int,
        *,
        time_field: str,
        timestamp_is_ms: bool,
    ) -> tuple[FlowEvent, ...]:
        events: list[FlowEvent] = []
        cursor = since_ms
        while cursor < until_ms:
            payload = self._signed_get(
                path,
                {
                    "startTime": str(cursor),
                    "endTime": str(until_ms),
                    "limit": str(_PAGE_SIZE),
                },
            )
            if not isinstance(payload, list):
                raise ProviderDataError(f"binance {direction.lower()} history response is invalid")
            if not payload:
                break
            for entry in payload:
                if (
                    not isinstance(entry, Mapping)
                    or ("asset" not in entry and "coin" not in entry)
                ):
                    raise ProviderDataError(
                        f"binance {direction.lower()} history entry is invalid"
                    )
                asset = entry.get("asset", entry.get("coin"))
                timestamp_ms = self._flow_timestamp_ms(entry.get(time_field), time_field, timestamp_is_ms)
                status = entry.get("status")
                if isinstance(status, bool) or not isinstance(status, int):
                    raise ProviderDataError(
                        f"binance {direction.lower()} history status is invalid"
                    )
                events.append(
                    FlowEvent(
                        direction=direction,
                        asset=asset,
                        amount=_decimal_string(entry.get("amount"), f"{asset}.{direction}.amount"),
                        timestamp_ms=timestamp_ms,
                        completed=status == _COMPLETED_FLOW_STATUS,
                        raw_status=status,
                    )
                )
            if len(payload) < _PAGE_SIZE:
                break
            next_cursor = max(event.timestamp_ms for event in events[-len(payload):]) + 1
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return tuple(events)

    @staticmethod
    def _flow_timestamp_ms(value: Any, field: str, timestamp_is_ms: bool) -> int:
        if timestamp_is_ms:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ProviderDataError(f"flow {field} must be millisecond integers")
            return value
        if not isinstance(value, str):
            raise ProviderDataError(f"flow {field} must be a datetime string")
        try:
            parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError as exc:
            raise ProviderDataError(f"flow {field} is not parseable") from exc
        return int(parsed.timestamp() * 1000)


__all__ = [
    "BinanceAccountClient",
    "DEFAULT_BASE_URL",
    "FlowEvent",
    "WalletBalance",
]
