"""Offline regression coverage for the signed read-only Binance account client."""

import hashlib
import hmac
import unittest
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from crypto_portfolio.providers.base import (
    ProviderAuthenticationError,
    ProviderDiagnostic,
    ProviderResponseError,
)
from crypto_portfolio.providers.binance_account import BinanceAccountClient, FlowEvent, WalletBalance
from crypto_portfolio.providers.http import redact_url


API_KEY = "test-api-key"
API_SECRET = "test-api-secret"


def _error_response(status: int, code: int, msg: str) -> Exception:
    detail = '{"code":%d,"msg":"%s"}' % (code, msg)
    if status == 401:
        return ProviderAuthenticationError(
            f"provider authentication rejected ({status})",
            diagnostic=ProviderDiagnostic(
                endpoint="https://api.binance.com/test",
                error_code="HTTP_401",
                detail=detail,
                status_code=status,
            ),
        )
    return ProviderResponseError(
        f"provider request failed ({status})",
        diagnostic=ProviderDiagnostic(
            endpoint="https://api.binance.com/test",
            error_code=f"HTTP_{status}",
            detail=detail,
            status_code=status,
        ),
    )


class FakeTransport:
    """Stands in for HttpClient.get_json against canned routes."""

    def __init__(self, routes: dict[str, Any]) -> None:
        # Each route is a list of per-call outcomes: a JSON payload or an
        # exception.  The final outcome repeats for any further calls.
        self.routes = {path: list(outcomes) for path, outcomes in routes.items()}
        self.requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def get_json(self, url: str, *, params=None, headers=None, max_response_bytes=None) -> Any:
        path = urlsplit(url).path
        outcomes = self.routes.get(path)
        if outcomes is None:
            raise AssertionError(f"unexpected request path {path}")
        if len(outcomes) > 1:
            outcome = outcomes.pop(0)
        else:
            outcome = outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        self.requests.append((path, dict(params or {}), dict(headers or {})))
        return outcome


def _client(transport: FakeTransport, *, clock=lambda: 1_000_000.0) -> BinanceAccountClient:
    return BinanceAccountClient(
        transport,  # type: ignore[arg-type]
        API_KEY,
        API_SECRET,
        clock=clock,
    )


class SignedRequestTests(unittest.TestCase):
    def _routes(self, server_time: int = 1_000_500) -> dict[str, Any]:
        return {
            "/api/v3/time": [{"serverTime": server_time}],
            "/api/v3/account": [{"balances": []}],
        }

    def test_signed_request_carries_hmac_signature_and_api_key_header(self):
        transport = FakeTransport(self._routes())
        client = _client(transport)
        client.spot_balances()
        path, params, headers = [request for request in transport.requests if request[0] == "/api/v3/account"][0]
        self.assertEqual(headers.get("X-MBX-APIKEY"), API_KEY)
        signature = params["signature"]
        del params["signature"]
        expected = hmac.new(
            API_SECRET.encode(), urlencode(list(params.items())).encode(), hashlib.sha256
        ).hexdigest()
        self.assertEqual(signature, expected)
        # The signed parameters must survive the transport's own re-encoding.
        self.assertEqual(
            dict(parse_qsl(urlencode(list(params.items())))),
            {key: str(value) for key, value in params.items()},
        )

    def test_timestamp_uses_the_synced_server_offset(self):
        transport = FakeTransport(self._routes(server_time=1_000_500))
        client = _client(transport, clock=lambda: 1_000_000.0)
        client.spot_balances()
        _, params, _ = [request for request in transport.requests if request[0] == "/api/v3/account"][0]
        self.assertEqual(params["timestamp"], "1000500")
        self.assertEqual(params["recvWindow"], "5000")

    def test_timestamp_outside_recv_window_resyncs_and_retries_once(self):
        routes = self._routes()
        routes["/api/v3/account"] = [
            _error_response(400, -1021, "Timestamp for this request was outside of the recvWindow."),
            {"balances": []},
        ]
        transport = FakeTransport(routes)
        client = _client(transport)
        self.assertEqual(client.spot_balances(), ())
        syncs = [request for request in transport.requests if request[0] == "/api/v3/time"]
        self.assertEqual(len(syncs), 2)

    def test_rejected_credentials_raise_authentication_error(self):
        for outcome in (
            _error_response(401, 0, "Unauthorized"),
            _error_response(400, -2015, "Invalid API-key, IP, or permissions for action."),
        ):
            routes = self._routes()
            routes["/api/v3/account"] = [outcome]
            with self.assertRaises(ProviderAuthenticationError):
                _client(FakeTransport(routes)).spot_balances()

    def test_signature_values_never_reach_redacted_urls(self):
        url = "https://api.binance.com/api/v3/account?timestamp=1&signature=abc123"
        redacted = redact_url(url)
        self.assertNotIn("abc123", redacted)
        self.assertIn("signature=%5BREDACTED%5D", redacted)

    def test_client_requires_both_credentials(self):
        transport = FakeTransport(self._routes())
        with self.assertRaises(ProviderAuthenticationError):
            BinanceAccountClient(transport, "", API_SECRET)  # type: ignore[arg-type]
        with self.assertRaises(ProviderAuthenticationError):
            BinanceAccountClient(transport, API_KEY, " ")  # type: ignore[arg-type]
        self.assertIsNone(
            BinanceAccountClient.from_environment(transport, api_key="k", api_secret=None)
        )


class AccountEndpointParsingTests(unittest.TestCase):
    def test_spot_balances_merge_free_locked_and_drop_zero_rows(self):
        transport = FakeTransport(
            {
                "/api/v3/time": [{"serverTime": 0}],
                "/api/v3/account": [
                    {
                        "balances": [
                            {"asset": "BTC", "free": "1.5", "locked": "0.5"},
                            {"asset": "USDT", "free": "100.25", "locked": "0"},
                            {"asset": "DUST", "free": "0.00000000", "locked": "0.0"},
                            {"asset": "ETH", "free": "0", "locked": "2"},
                        ]
                    }
                ],
            }
        )
        balances = _client(transport).spot_balances()
        self.assertEqual(
            balances,
            (
                WalletBalance("BTC", 2.0, "spot"),
                WalletBalance("USDT", 100.25, "spot"),
                WalletBalance("ETH", 2.0, "spot"),
            ),
        )

    def test_earn_positions_paginate_and_include_redeeming_amounts(self):
        locked_rows_page_1 = [
            {"asset": "ETH", "amount": "1.0", "redeemingAmt": "0.25", "status": "HOLDING"}
            for _ in range(100)
        ]
        transport = FakeTransport(
            {
                "/api/v3/time": [{"serverTime": 0}],
                "/sapi/v1/simple-earn/locked/position": [
                    {"rows": locked_rows_page_1, "total": 101},
                    {"rows": [{"asset": "USDT", "amount": "50", "redeemingAmt": "0"}], "total": 101},
                ],
            }
        )
        positions = _client(transport).locked_earn_positions()
        self.assertEqual(len(positions), 101)
        self.assertEqual(positions[0], WalletBalance("ETH", 1.25, "earn_locked"))
        self.assertEqual(positions[-1], WalletBalance("USDT", 50.0, "earn_locked"))

class FlowHistoryTests(unittest.TestCase):
    def test_completed_deposit_and_withdrawal_records_normalize(self):
        transport = FakeTransport(
            {
                "/api/v3/time": [{"serverTime": 0}],
                "/sapi/v1/capital/deposit/hisrec": [
                    [
                        {"asset": "USDT", "amount": "500", "insertTime": 1000, "status": 6},
                        {"asset": "ETH", "amount": "1", "insertTime": 2000, "status": 0},
                        {"asset": "USDT", "amount": "21", "insertTime": 3000, "status": 1},
                    ]
                ],
                "/sapi/v1/capital/withdraw/history": [
                    [
                        {
                            "coin": "BTC",
                            "amount": "0.05",
                            "applyTime": "2026-09-18 10:00:00",
                            "status": 6,
                        }
                    ]
                ],
            }
        )
        client = _client(transport)
        deposits = client.deposit_history(0, 5000)
        withdrawals = client.withdrawal_history(0, 5000)
        self.assertEqual(
            deposits,
            (
                FlowEvent("DEPOSIT", "USDT", 500.0, 1000, True, 6),
                FlowEvent("DEPOSIT", "ETH", 1.0, 2000, False, 0),
                # Status 1 ("success") settles without passing through 6 on
                # some networks (e.g. PLASMA) and still credits the balance,
                # so it must count as a completed deposit.
                FlowEvent("DEPOSIT", "USDT", 21.0, 3000, True, 1),
            ),
        )
        self.assertEqual(len(withdrawals), 1)
        self.assertEqual(withdrawals[0].direction, "WITHDRAWAL")
        self.assertEqual(withdrawals[0].asset, "BTC")
        self.assertTrue(withdrawals[0].completed)
        self.assertEqual(
            withdrawals[0].timestamp_ms,
            int(
                datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
            ),
        )

    def test_flow_history_paginates_by_timestamp_cursor(self):
        page_1 = [
            {"asset": "USDT", "amount": "1", "insertTime": value, "status": 6}
            for value in range(1000, 1000 + 1000)
        ]
        transport = FakeTransport(
            {
                "/api/v3/time": [{"serverTime": 0}],
                "/sapi/v1/capital/deposit/hisrec": [
                    page_1,
                    [{"asset": "USDT", "amount": "2", "insertTime": 99999, "status": 6}],
                ],
            }
        )
        events = _client(transport).deposit_history(0, 100000)
        self.assertEqual(len(events), 1001)
        _, second_params, _ = transport.requests[-1]
        self.assertEqual(second_params["startTime"], str(1999 + 1))


if __name__ == "__main__":
    unittest.main()
