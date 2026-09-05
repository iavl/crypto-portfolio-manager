import unittest

from crypto_portfolio.engine.cash_flow import cash_flow_adjusted_performance, detect_external_cash_flow
from scripts.portfolio_snapshot import classify, normalize, resolve_config


class PortfolioSnapshotTests(unittest.TestCase):
    def test_unresolved_material_change_does_not_become_nav_return(self):
        previous = {
            "timestamp": "2026-09-01T00:00:00Z",
            "positions": [{"symbol": "BTC", "value_usd": 10000}, {"symbol": "USDT", "value_usd": 5000}],
        }
        current = {
            "timestamp": "2026-09-02T00:00:00Z",
            "positions": [{"symbol": "BTC", "value_usd": 10000}, {"symbol": "USDT", "value_usd": 15000}],
        }
        flagged = detect_external_cash_flow(previous, current)
        self.assertEqual(flagged["status"], "UNRESOLVED")
        self.assertTrue(flagged["requires_confirmation"])
        self.assertIsNone(cash_flow_adjusted_performance((previous, current))["return"])
        confirmed = {
            **current,
            "external_cash_flow_usd": 10000,
            "external_cash_flow_type": "DEPOSIT",
        }
        self.assertEqual(detect_external_cash_flow(previous, confirmed)["status"], "CONFIRMED")
        self.assertAlmostEqual(cash_flow_adjusted_performance((previous, confirmed))["return"], 0.0)

    def test_classify_accepts_partial_config(self):
        self.assertEqual(classify("alpha", {"core_symbols": ["ALPHA"]}), "core")

    def test_legacy_snapshot_without_timestamp_remains_normalizable(self):
        result = normalize({"positions": [{"symbol": "BTC", "value_usd": 100}]})
        self.assertIsNone(result["timestamp"])
        self.assertTrue(any("timestamp is missing" in warning for warning in result["warnings"]))

    def test_defaults_classify_assets(self):
        result = normalize(
            {
                "timestamp": "2026-09-01T00:00:00Z",
                "positions": [
                    {"symbol": "BTC", "value_usd": 40},
                    {"symbol": "AAVE", "value_usd": 20},
                    {"symbol": "ARB", "value_usd": 20},
                    {"symbol": "USDC", "value_usd": 20},
                ]
            }
        )

        types = {
            position["symbol"]: position["asset_type"]
            for position in result["positions"]
        }
        self.assertEqual(
            types,
            {"BTC": "core", "AAVE": "satellite", "ARB": "other", "USDC": "stablecoin"},
        )
        self.assertEqual(result["config"]["min_stablecoin_weight"], 0.10)
        self.assertEqual(result["config"]["max_portfolio_drawdown"], 0.20)

    def test_custom_config_replaces_defaults_and_classifies_deterministically(self):
        result = normalize(
            {
                "timestamp": "2026-09-01T00:00:00Z",
                "config": {
                    "core_symbols": ["sol"],
                    "satellite_symbols": ["avax"],
                    "stable_symbols": ["usdt"],
                    "min_stablecoin_weight": 0.30,
                    "max_portfolio_drawdown": 0.25,
                },
                "positions": [
                    {"symbol": "SOL", "value_usd": 20},
                    {"symbol": "AVAX", "value_usd": 20},
                    {"symbol": "BTC", "value_usd": 20},
                    {"symbol": "ETH", "value_usd": 10},
                    {"symbol": "USDT", "value_usd": 40},
                ],
            }
        )

        types = {
            position["symbol"]: position["asset_type"]
            for position in result["positions"]
        }
        self.assertEqual(
            types,
            {
                "SOL": "core",
                "AVAX": "satellite",
                "BTC": "other",
                "ETH": "other",
                "USDT": "stablecoin",
            },
        )
        self.assertEqual(result["config"]["core_symbols"], ["SOL"])
        self.assertEqual(result["config"]["min_stablecoin_weight"], 0.30)
        self.assertNotIn("stablecoin weight", " ".join(result["warnings"]))

    def test_conflicting_asset_type_hint_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize(
                {
                    "timestamp": "2026-09-01T00:00:00Z",
                    "positions": [
                        {"symbol": "BTC", "value_usd": 100, "asset_type_hint": "satellite"}
                    ],
                }
            )

    def test_duplicate_symbols_after_normalization_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize(
                {
                    "timestamp": "2026-09-01T00:00:00Z",
                    "positions": [
                        {"symbol": "btc", "value_usd": 50},
                        {"symbol": " BTC ", "value_usd": 50},
                    ],
                }
            )

    def test_risk_warnings_and_invalid_config(self):
        result = normalize(
            {
                "config": {"min_stablecoin_weight": 0.50, "max_portfolio_drawdown": 0.10},
                "portfolio_peak_value": 100,
                "timestamp": "2026-09-01T00:00:00Z",
                "positions": [{"symbol": "BTC", "value_usd": 80}],
            }
        )
        warnings = " ".join(result["warnings"])
        self.assertIn("stablecoin weight", warnings)
        self.assertIn("exceeds configured maximum", warnings)

        with self.assertRaises(ValueError):
            resolve_config({"core_symbols": ["BTC"], "satellite_symbols": ["btc"]})
        with self.assertRaises(ValueError):
            resolve_config({"max_portfolio_drawdown": 0})
        with self.assertRaises(ValueError):
            resolve_config({"min_stablecoin_weight": "0.20"})
        with self.assertRaises(ValueError):
            resolve_config({"unknown": True})


if __name__ == "__main__":
    unittest.main()
