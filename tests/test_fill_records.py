"""Trade-fill records: canonical model, append-only store, provider fetch."""

import tempfile
import unittest
from pathlib import Path
from typing import Any

from crypto_portfolio.models.fill import TradeFill
from crypto_portfolio.providers.binance_account import AccountTrade, BinanceAccountClient
from crypto_portfolio.state.fills import (
    append_fill,
    append_fills,
    existing_fill_keys,
    fills_by_symbol,
    read_fills,
)

from test_binance_account_client import API_KEY, API_SECRET, FakeTransport


def _fill(**overrides) -> TradeFill:
    values = {
        "fill_id": "101",
        "symbol": "BTC",
        "side": "BUY",
        "quantity": 0.01,
        "price": 84000.0,
        "notional": 840.0,
        "fee": 0.00001,
        "fee_asset": "BTC",
        "executed_at": "2026-09-23T10:00:00Z",
    }
    values.update(overrides)
    return TradeFill(**values)


class TradeFillModelTests(unittest.TestCase):
    def test_rejects_invalid_records(self):
        with self.assertRaisesRegex(ValueError, "side"):
            _fill(side="HOLD")
        with self.assertRaisesRegex(ValueError, "quantity"):
            _fill(quantity=0)
        with self.assertRaisesRegex(ValueError, "notional"):
            _fill(notional=999.0)
        with self.assertRaisesRegex(ValueError, "fee"):
            _fill(fee=-1.0)
        with self.assertRaisesRegex(ValueError, "executed_at"):
            _fill(executed_at="2026-09-23 10:00")
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            TradeFill.from_mapping({**_fill().as_dict(), "surprise": 1})
        with self.assertRaisesRegex(ValueError, "missing fields"):
            TradeFill.from_mapping({"symbol": "BTC"})

    def test_roundtrip_and_dedup_key(self):
        model = _fill()
        self.assertEqual(TradeFill.from_mapping(model.as_dict()), model)
        self.assertEqual(model.dedup_key, "BTC:101")
        stamped = _fill(fetched_at="2026-09-23T12:00:00Z")
        self.assertEqual(
            TradeFill.from_mapping(stamped.as_dict()).fetched_at, "2026-09-23T12:00:00Z"
        )


class FillStoreTests(unittest.TestCase):
    def test_append_read_and_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fills" / "trades.jsonl"
            append_fill(_fill(), path)
            with self.assertRaisesRegex(ValueError, "already recorded"):
                append_fill(_fill(), path)
            append_fills([_fill(), _fill(fill_id="102", symbol="ETH")], path)
            self.assertEqual([fill.fill_id for fill in read_fills(path)], ["101", "102"])
            self.assertEqual(existing_fill_keys(path), {"BTC:101", "ETH:102"})

    def test_fills_by_symbol_marks_requested_empty_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.jsonl"
            append_fills([_fill()], path)
            grouped = fills_by_symbol(path, symbols=("BTC", "ETH"))
            self.assertEqual([fill.fill_id for fill in grouped["BTC"]], ["101"])
            self.assertEqual(grouped["ETH"], [])
            self.assertNotIn("AAVE", grouped)


def _trade(trade_id: int, *, is_buyer: bool = True, price: str = "84000", qty: str = "0.01",
           time: int = 1_000_000) -> dict[str, Any]:
    return {
        "symbol": "BTCUSDT",
        "id": trade_id,
        "orderId": trade_id * 10,
        "price": price,
        "qty": qty,
        "quoteQty": str(float(price) * float(qty)),
        "commission": "0.00001",
        "commissionAsset": "BTC",
        "time": time,
        "isBuyer": is_buyer,
        "isMaker": False,
    }


class MyTradesTests(unittest.TestCase):
    def test_normalizes_and_filters_to_window(self):
        transport = FakeTransport(
            {
                "/api/v3/time": [{"serverTime": 0}],
                "/api/v3/myTrades": [[_trade(1), _trade(2, time=5_000_000)]],
            }
        )
        trades = BinanceAccountClient(transport, API_KEY, API_SECRET, clock=lambda: 0.0).my_trades(
            "BTC", 0, 1_000_000
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].trade_id, 1)
        self.assertEqual(trades[0].side, "BUY")
        self.assertEqual(trades[0].quote_quantity, 840.0)
        self.assertEqual(trades[0].commission_asset, "BTC")
        fill = TradeFill.from_account_trade(trades[0], fetched_at="2026-09-23T12:00:00Z")
        self.assertEqual(fill.executed_at, "1970-01-01T00:16:40Z")
        self.assertEqual(fill.order_id, "10")

    def test_paginates_by_from_id_and_stops_past_window(self):
        page_1 = [_trade(value) for value in range(1, 1001)]
        page_2 = [_trade(1001, time=9_000_000), _trade(1002, time=9_500_000)]
        transport = FakeTransport(
            {
                "/api/v3/time": [{"serverTime": 0}],
                "/api/v3/myTrades": [page_1, page_2],
            }
        )
        trades = BinanceAccountClient(transport, API_KEY, API_SECRET, clock=lambda: 0.0).my_trades(
            "BTC", 0, 10_000_000
        )
        self.assertEqual(len(trades), 1002)
        last_params = [params for path, params, _ in transport.requests if path == "/api/v3/myTrades"][-1]
        self.assertEqual(last_params["fromId"], "1001")
        self.assertNotIn("startTime", last_params)

    def test_account_trade_rejects_bad_entries(self):
        with self.assertRaisesRegex(Exception, "must be > 0"):
            AccountTrade(
                symbol="BTC", trade_id=1, order_id=10, side="BUY", price=0.0,
                quantity=0.01, quote_quantity=0.0, commission=0.0,
                commission_asset="BNB", timestamp_ms=1, is_maker=False,
            )


if __name__ == "__main__":
    unittest.main()
