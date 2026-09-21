import unittest

from crypto_portfolio.engine.funding import funding_readiness
from crypto_portfolio.importers.binance_balance import (
    BinanceAccountFetch,
    snapshot_from_binance_account,
)
from crypto_portfolio.models.funding import FundingAvailability
from crypto_portfolio.models.portfolio import Position, PortfolioSnapshot
from crypto_portfolio.providers.binance_account import WalletBalance


NOW = "2026-09-22T00:00:00Z"


class FundingAvailabilityTests(unittest.TestCase):
    def test_binance_keeps_economic_value_but_only_spot_free_is_available(self):
        fetch = BinanceAccountFetch(
            captured_at=NOW,
            spot=(WalletBalance("USDT", 100, "spot", available_quantity=40),),
            flexible=(WalletBalance("USDT", 20, "earn_flexible"),),
            locked=(WalletBalance("USDT", 30, "earn_locked"),),
            prices={"USDT": 1},
        )
        snapshot, _, _, _ = snapshot_from_binance_account(fetch)
        position = snapshot.positions[0]
        self.assertEqual(position.value_usd, 150)
        self.assertEqual(position.funding_availability.available_value_usd, 40)
        self.assertEqual(position.funding_availability.restricted_value_usd, 110)
        self.assertEqual(position.funding_availability.unknown_value_usd, 0)

    def test_missing_wallet_availability_stays_unknown(self):
        fetch = BinanceAccountFetch(
            captured_at=NOW,
            spot=(WalletBalance("USDT", 100, "spot"),),
            prices={"USDT": 1},
        )
        snapshot, _, _, _ = snapshot_from_binance_account(fetch)
        facts = snapshot.positions[0].funding_availability
        self.assertEqual(facts.available_value_usd, 0)
        self.assertEqual(facts.unknown_value_usd, 100)

    def test_generic_snapshot_defaults_missing_funding_facts_to_unknown(self):
        snapshot = PortfolioSnapshot(
            NOW,
            positions=(Position("BTC", value_usd=100, resolved_asset_type="core"),),
            external_cash_flow=0,
            external_cash_flow_type="NONE",
        )
        facts = snapshot.positions[0].funding_availability
        self.assertEqual(facts.available_value_usd, 0)
        self.assertEqual(facts.restricted_value_usd, 0)
        self.assertEqual(facts.unknown_value_usd, 100)
        self.assertEqual(facts.source, "UNSPECIFIED_INPUT")

    def test_readiness_requires_release_and_conversion_without_executing_them(self):
        snapshot = PortfolioSnapshot(
            NOW,
            positions=(
                Position(
                    "USDC",
                    value_usd=100,
                    resolved_asset_type="stablecoin",
                    funding_availability=FundingAvailability(25, 75, 0, NOW, "fixture"),
                ),
            ),
            external_cash_flow=0,
            external_cash_flow_type="NONE",
        )
        operation = {
            "execution_actions": [
                {
                    "symbol": "USDC",
                    "strategic_action": "REDUCE",
                    "proposed_amount_usd": 50,
                    "purpose": "BUY_FUNDING",
                }
            ]
        }
        result = funding_readiness(snapshot, operation, settlement_asset="USDT")
        self.assertEqual(result["status"], "CONDITIONAL")
        self.assertEqual(
            result["rows"][0]["conditions"],
            ["RELEASE_RESTRICTIONS_OR_REFRESH_REQUIRED", "CONVERSION_REQUIRED"],
        )
        self.assertIsNone(result["fees_usd"])
        self.assertIsNone(result["slippage_usd"])

    def test_funding_values_must_reconcile_position_value(self):
        with self.assertRaisesRegex(ValueError, "reconcile"):
            Position(
                "USDT",
                value_usd=100,
                resolved_asset_type="stablecoin",
                funding_availability=FundingAvailability(80, 30, 0, NOW, "fixture"),
            )


if __name__ == "__main__":
    unittest.main()
