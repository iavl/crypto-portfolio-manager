"""Open-order domain records and the current-state orders store."""

import json
import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.models.order import OpenOrderRecord
from crypto_portfolio.state.orders import read_open_orders, write_open_orders


ORDER = {
    "order_id": "5738465275",
    "symbol": "AAVE",
    "side": "BUY",
    "order_type": "LIMIT",
    "price": 132.0,
    "orig_quantity": 9.5,
    "executed_quantity": 0.0,
    "status": "NEW",
    "time_in_force": "GTC",
    "created_at": "2026-09-25T02:15:12Z",
}


class OpenOrderRecordTests(unittest.TestCase):
    def test_record_normalizes_and_derives_remaining(self):
        record = OpenOrderRecord.from_mapping({**ORDER, "executed_quantity": 2.5})
        self.assertEqual(record.symbol, "AAVE")
        self.assertEqual(record.side, "BUY")
        self.assertTrue(record.is_resting_limit)
        self.assertAlmostEqual(record.remaining_quantity, 7.0)
        self.assertAlmostEqual(record.remaining_notional_usd, 132.0 * 7.0)
        round_trip = OpenOrderRecord.from_mapping(record.as_dict())
        self.assertEqual(round_trip, record)

    def test_record_rejects_unknown_and_missing_fields(self):
        with self.assertRaises(ValueError):
            OpenOrderRecord.from_mapping({**ORDER, "unexpected": 1})
        incomplete = dict(ORDER)
        del incomplete["price"]
        with self.assertRaises(ValueError):
            OpenOrderRecord.from_mapping(incomplete)

    def test_record_rejects_invalid_quantities_and_sides(self):
        with self.assertRaises(ValueError):
            OpenOrderRecord.from_mapping({**ORDER, "executed_quantity": 10.0})
        with self.assertRaises(ValueError):
            OpenOrderRecord.from_mapping({**ORDER, "price": 0.0})
        with self.assertRaises(ValueError):
            OpenOrderRecord.from_mapping({**ORDER, "side": "swap"})
        with self.assertRaises(ValueError):
            OpenOrderRecord.from_mapping({**ORDER, "price": "132.0"})

    def test_from_account_order_maps_provider_fields(self):
        provider_order = type(
            "ProviderOrder",
            (),
            {
                "order_id": 5738465275,
                "symbol": "aave",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": 132.0,
                "orig_quantity": 9.5,
                "executed_quantity": 0.0,
                "status": "NEW",
                "time_in_force": "GTC",
                "timestamp_ms": 1785896112000,
            },
        )()
        record = OpenOrderRecord.from_account_order(provider_order, fetched_at="2026-09-26T02:40:00Z")
        self.assertEqual(record.order_id, "5738465275")
        self.assertEqual(record.symbol, "AAVE")
        self.assertEqual(record.created_at, "2026-08-05T02:15:12Z")
        self.assertEqual(record.fetched_at, "2026-09-26T02:40:00Z")


class OpenOrdersStoreTests(unittest.TestCase):
    def test_write_then_read_round_trips_with_coverage_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orders" / "open-orders.json"
            write_open_orders(
                {"AAVE": [OpenOrderRecord.from_mapping(ORDER)], "BTC": []},
                fetched_at="2026-09-26T02:41:20Z",
                path=path,
            )
            snapshot = read_open_orders(path)
            self.assertEqual(snapshot["fetched_at"], "2026-09-26T02:41:20Z")
            # An empty list stays a fetched-but-zero symbol, never dropped.
            self.assertEqual(snapshot["orders_by_symbol"]["BTC"], ())
            self.assertEqual(len(snapshot["orders_by_symbol"]["AAVE"]), 1)
            self.assertEqual(snapshot["orders_by_symbol"]["AAVE"][0].price, 132.0)
            # The persisted file itself carries the raw record mappings.
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["orders_by_symbol"]["AAVE"][0]["order_id"], "5738465275")

    def test_write_replaces_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orders" / "open-orders.json"
            write_open_orders({"AAVE": [OpenOrderRecord.from_mapping(ORDER)]}, fetched_at="2026-09-26T02:00:00Z", path=path)
            write_open_orders({"BTC": []}, fetched_at="2026-09-26T03:00:00Z", path=path)
            snapshot = read_open_orders(path)
            self.assertEqual(snapshot["fetched_at"], "2026-09-26T03:00:00Z")
            self.assertNotIn("AAVE", snapshot["orders_by_symbol"])

    def test_missing_file_reads_as_never_fetched(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = read_open_orders(Path(directory) / "orders" / "open-orders.json")
            self.assertIsNone(snapshot["fetched_at"])
            self.assertEqual(snapshot["orders_by_symbol"], {})

    def test_invalid_persisted_state_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "open-orders.json"
            path.write_text('{"orders_by_symbol": {"AAVE": [{"price": "bad"}]}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                read_open_orders(path)


if __name__ == "__main__":
    unittest.main()
