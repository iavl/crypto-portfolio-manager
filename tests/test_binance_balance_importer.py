"""Regression coverage: Binance account fetches become canonical snapshots."""

import unittest

from crypto_portfolio.importers.binance_balance import (
    BINANCE_API_ACCOUNT_SOURCE,
    BinanceAccountFetch,
    snapshot_from_binance_account,
)
from crypto_portfolio.models.portfolio import normalize_snapshot
from crypto_portfolio.providers.binance_account import FlowEvent, WalletBalance
from crypto_portfolio.state.snapshots import append_snapshot, read_snapshots


def _fetch(**overrides) -> BinanceAccountFetch:
    base = dict(
        captured_at="2026-09-20T12:00:00Z",
        spot=(
            WalletBalance("BTC", 0.5, "spot"),
            WalletBalance("USDT", 10000.0, "spot"),
        ),
        prices={"BTC": 60000.0},
    )
    base.update(overrides)
    return BinanceAccountFetch(**base)


class MergeAndValuationTests(unittest.TestCase):
    def test_wallets_merge_per_asset_and_stables_default_to_one(self):
        fetch = _fetch(
            flexible=(WalletBalance("BTC", 0.1, "earn_flexible"),),
            locked=(WalletBalance("ETH", 2.0, "earn_locked"),),
            prices={"BTC": 60000.0, "ETH": 3000.0},
        )
        snapshot, _, _, _ = snapshot_from_binance_account(fetch)
        by_symbol = {position.symbol: position for position in snapshot.positions}
        self.assertEqual(by_symbol["BTC"].quantity, 0.6)
        self.assertEqual(by_symbol["USDT"].current_price_usd, 1.0)
        self.assertEqual(by_symbol["BTC"].value_usd, 0.6 * 60000.0)
        self.assertEqual(snapshot.total_value_usd, snapshot.total_value)

    def test_value_price_quantity_stay_consistent_for_position_pnl(self):
        # Cost fields are intentionally absent (the API has no average cost),
        # so overall validation degrades to INSUFFICIENT_DATA while the
        # price/quantity/value identity stays exact.
        _, _, _, imported = snapshot_from_binance_account(_fetch())
        normalized = normalize_snapshot(imported.snapshot_mapping)
        by_symbol = {position["symbol"]: position for position in normalized["positions"]}
        self.assertEqual(by_symbol["BTC"]["validation_status"], "INSUFFICIENT_DATA")
        self.assertAlmostEqual(
            by_symbol["BTC"]["value_usd"],
            by_symbol["BTC"]["quantity"] * by_symbol["BTC"]["current_price_usd"],
        )

    def test_missing_price_fails_closed_naming_the_symbol(self):
        fetch = _fetch(
            spot=(WalletBalance("BTC", 0.5, "spot"), WalletBalance("XYZ", 3.0, "spot")),
            prices={"BTC": 60000.0},
        )
        with self.assertRaises(ValueError) as raised:
            snapshot_from_binance_account(fetch)
        self.assertIn("XYZ", str(raised.exception))

    def test_explicit_exclusion_and_dust_threshold_record_warnings(self):
        fetch = _fetch(
            spot=(
                WalletBalance("BTC", 0.5, "spot"),
                WalletBalance("USDT", 10000.0, "spot"),
                WalletBalance("DUST", 5.0, "spot"),
            ),
            prices={"BTC": 60000.0, "DUST": 0.001},
        )
        _, _, warnings, _ = snapshot_from_binance_account(
            fetch, exclude_symbols=("dust",), min_value_usd=0.01
        )
        self.assertTrue(any("excluded by explicit request" in warning for warning in warnings))
        self.assertTrue(any("DUST" in warning for warning in warnings))

    def test_ld_spot_mirrors_are_counted_exactly_once(self):
        # LD<SYM> spot entries mirror Simple Earn positions; the live sapi
        # amounts are authoritative and must not be double-counted.
        fetch = _fetch(
            spot=(
                WalletBalance("BTC", 0.0, "spot"),
                WalletBalance("LDBTC", 0.42, "spot"),
                WalletBalance("LDUSDT", 700.0, "spot"),
            ),
            flexible=(
                WalletBalance("BTC", 0.43, "earn_flexible"),
                WalletBalance("USDT", 800.0, "earn_flexible"),
            ),
            prices={"BTC": 60000.0},
        )
        snapshot, _, warnings, _ = snapshot_from_binance_account(fetch)
        by_symbol = {position.symbol: position for position in snapshot.positions}
        self.assertAlmostEqual(by_symbol["BTC"].quantity, 0.43)
        self.assertAlmostEqual(by_symbol["USDT"].quantity, 800.0)
        self.assertNotIn("LDBTC", by_symbol)
        self.assertNotIn("LDUSDT", by_symbol)
        self.assertTrue(any("spot mirror" in warning for warning in warnings))

    def test_ld_entry_without_earn_counterpart_folds_into_base(self):
        fetch = _fetch(spot=(WalletBalance("LDETH", 1.5, "spot"),), prices={"ETH": 3000.0})
        snapshot, _, warnings, _ = snapshot_from_binance_account(fetch)
        by_symbol = {position.symbol: position for position in snapshot.positions}
        self.assertAlmostEqual(by_symbol["ETH"].quantity, 1.5)
        self.assertNotIn("LDETH", by_symbol)
        self.assertTrue(any("counted as ETH" in warning for warning in warnings))

    def test_conversion_warnings_survive_the_normalized_report(self):
        fetch = _fetch(
            spot=(
                WalletBalance("USDT", 1000.0, "spot"),
                WalletBalance("LDUSDT", 50.0, "spot"),
            ),
            flexible=(WalletBalance("USDT", 900.0, "earn_flexible"),),
        )
        _, _, _, imported = snapshot_from_binance_account(fetch)
        normalized = imported.normalize()
        self.assertTrue(any("spot mirror" in warning for warning in normalized["warnings"]))

    def test_user_stables_value_at_one_without_a_pair(self):
        fetch = _fetch(
            spot=(WalletBalance("U", 100.0, "spot"), WalletBalance("USD1", 200.0, "spot")),
        )
        snapshot, _, _, _ = snapshot_from_binance_account(fetch)
        by_symbol = {position.symbol: position for position in snapshot.positions}
        self.assertEqual(by_symbol["U"].value_usd, 100.0)
        self.assertEqual(by_symbol["USD1"].value_usd, 200.0)

    def test_empty_valued_portfolio_is_rejected(self):
        with self.assertRaises(ValueError):
            snapshot_from_binance_account(_fetch(spot=(WalletBalance("BTC", 0.0, "spot"),)))


class FlowDerivationTests(unittest.TestCase):
    def _flow_events(self):
        return (
            FlowEvent("DEPOSIT", "USDT", 10000.0, 1700000000000, True, 6),
            FlowEvent("WITHDRAWAL", "BTC", 0.01, 1700000100000, True, 6),
            FlowEvent("DEPOSIT", "ETH", 0.5, 1700000200000, False, 0),
        )

    def test_net_flow_is_exchange_derived_confirmed_amount(self):
        fetch = _fetch(
            flow_events=self._flow_events(),
            flow_price=lambda asset, event: {"BTC": 59000.0}[asset],
        )
        snapshot, _, warnings, imported = snapshot_from_binance_account(fetch)
        self.assertEqual(snapshot.cash_flow_resolution_status, "CONFIRMED_AMOUNT")
        self.assertEqual(snapshot.cash_flow_classification_source, "EXCHANGE_DERIVED")
        self.assertEqual(snapshot.external_cash_flow_type, "DEPOSIT")
        self.assertAlmostEqual(snapshot.external_cash_flow, 10000.0 - 590.0)
        self.assertTrue(any("in flight" in warning for warning in warnings))
        self.assertEqual(len(imported.flow_summary["completed_events"]), 2)
        self.assertEqual(len(imported.flow_summary["in_flight_events"]), 1)

    def test_no_completed_events_confirm_zero_flow(self):
        fetch = _fetch(flow_events=(self._flow_events()[2],))
        snapshot, _, _, imported = snapshot_from_binance_account(fetch)
        self.assertEqual(snapshot.cash_flow_resolution_status, "CONFIRMED_NONE")
        self.assertEqual(snapshot.external_cash_flow, 0.0)
        self.assertEqual(snapshot.external_cash_flow_type, "NONE")
        self.assertEqual(imported.flow_summary["status"], "CONFIRMED_NONE")

    def test_net_zero_deposit_and_withdrawal_confirms_none(self):
        events = (
            FlowEvent("DEPOSIT", "USDT", 500.0, 1700000000000, True, 6),
            FlowEvent("WITHDRAWAL", "USDC", 500.0, 1700000100000, True, 6),
        )
        snapshot, _, _, _ = snapshot_from_binance_account(_fetch(flow_events=events))
        self.assertEqual(snapshot.cash_flow_resolution_status, "CONFIRMED_NONE")

    def test_manual_flow_escape_hatch_marks_unresolved(self):
        fetch = _fetch(flow_events=self._flow_events(), flow_price=lambda asset, event: 1.0)
        snapshot, _, _, _ = snapshot_from_binance_account(fetch, manual_flow=True)
        self.assertEqual(snapshot.cash_flow_resolution_status, "UNRESOLVED")
        self.assertIsNone(snapshot.external_cash_flow)
        self.assertEqual(snapshot.cash_flow_classification_source, "USER_EXPLICIT")

    def test_completed_non_stable_flow_without_price_source_fails_closed(self):
        fetch = _fetch(flow_events=(self._flow_events()[1],))
        with self.assertRaises(ValueError) as raised:
            snapshot_from_binance_account(fetch)
        self.assertIn("flow price source", str(raised.exception))

    def test_stable_flows_value_at_one_without_network(self):
        events = (FlowEvent("DEPOSIT", "USDC", 250.0, 1700000000000, True, 6),)
        snapshot, _, _, _ = snapshot_from_binance_account(_fetch(flow_events=events))
        self.assertEqual(snapshot.external_cash_flow, 250.0)


class PersistenceRoundTripTests(unittest.TestCase):
    def test_api_snapshot_persists_and_reads_back(self):
        import tempfile
        from pathlib import Path

        _, _, _, imported = snapshot_from_binance_account(_fetch())
        self.assertEqual(imported.snapshot_mapping["source"], BINANCE_API_ACCOUNT_SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshots.jsonl"
            append_snapshot(imported.snapshot_mapping, path)
            records = read_snapshots(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source"], BINANCE_API_ACCOUNT_SOURCE)
            self.assertEqual(records[0]["cash_flow_classification_source"], "EXCHANGE_DERIVED")
            self.assertEqual(records[0]["cash_flow_resolution_status"], "CONFIRMED_NONE")


class CommandIntegrationTests(unittest.TestCase):
    """scripts/binance_snapshot.py acquire() against a fake account client."""

    def test_acquire_builds_exchange_derived_snapshot_end_to_end(self):
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        from unittest.mock import patch

        from scripts.binance_snapshot import acquire

        class FakeClient:
            def spot_balances(self):
                return (WalletBalance("BTC", 0.2, "spot"), WalletBalance("USDT", 5000.0, "spot"))

            def flexible_earn_positions(self):
                return ()

            def locked_earn_positions(self):
                return ()

            def simple_earn_totals(self):
                return {}

            def ticker_price(self, symbol):
                return {"BTCUSDT": 60000.0, "USDCUSDT": 1.0001}[symbol]

            def daily_close(self, symbol, day_start_ms):
                return 59000.0

            def deposit_history(self, since_ms, until_ms):
                return (FlowEvent("DEPOSIT", "BTC", 0.01, since_ms + 1000, True, 6),)

            def withdrawal_history(self, since_ms, until_ms):
                return ()

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"BINANCE_API_KEY": "k", "BINANCE_API_SECRET": "s"},
        ):
            data_dir = Path(directory)
            snapshot_path = data_dir / "portfolio" / "snapshots.jsonl"
            snapshot_path.parent.mkdir(parents=True)
            # First run: baseline snapshot, empty flow window.
            mapping, normalized = acquire(
                snapshot_path,
                client_factory=lambda config: FakeClient(),
                now=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(normalized["cash_flow_resolution_status"], "CONFIRMED_NONE")
            append_snapshot(mapping, snapshot_path)
            # Second run: the deposit since the baseline is exchange-confirmed.
            mapping, normalized = acquire(
                snapshot_path,
                client_factory=lambda config: FakeClient(),
                now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(normalized["cash_flow_resolution_status"], "CONFIRMED_AMOUNT")
            self.assertEqual(normalized["cash_flow_classification_source"], "EXCHANGE_DERIVED")
            self.assertAlmostEqual(normalized["external_cash_flow"], 0.01 * 59000.0)
            self.assertEqual(len(normalized["flow_summary"]["completed_events"]), 1)


if __name__ == "__main__":
    unittest.main()
