import unittest

from scripts.portfolio_snapshot import classify, normalize, resolve_config


class PortfolioSnapshotTests(unittest.TestCase):
    def test_classify_accepts_partial_config(self):
        self.assertEqual(classify("alpha", {"core_symbols": ["ALPHA"]}), "core")

    def test_defaults_classify_assets(self):
        result = normalize(
            {
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

    def test_custom_config_replaces_defaults_and_explicit_type_wins(self):
        result = normalize(
            {
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
                    {"symbol": "BTC", "value_usd": 20, "asset_type": "core"},
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
                "BTC": "core",
                "ETH": "other",
                "USDT": "stablecoin",
            },
        )
        self.assertEqual(result["config"]["core_symbols"], ["SOL"])
        self.assertEqual(result["config"]["min_stablecoin_weight"], 0.30)
        self.assertNotIn("stablecoin weight", " ".join(result["warnings"]))

    def test_risk_warnings_and_invalid_config(self):
        result = normalize(
            {
                "config": {"min_stablecoin_weight": 0.50, "max_portfolio_drawdown": 0.10},
                "portfolio_peak_value": 100,
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
