import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.engine.position_pnl import (
    calculate_portfolio_position_performance,
    calculate_position_performance,
)
from crypto_portfolio.importers.binance_screenshot import (
    BinancePortfolioObservation,
    BinancePositionObservation,
    normalize_binance_observation,
)
from crypto_portfolio.models.portfolio import Position
from crypto_portfolio.state.context import (
    build_position_pnl_context,
    latest_position_performance,
    position_performance_history,
)
from crypto_portfolio.state.snapshots import append_snapshot, read_snapshots


class PositionPnlTests(unittest.TestCase):
    def test_deterministic_calculation_and_fallbacks(self):
        performance = calculate_position_performance(
            Position(
                "aaa",
                quantity=2,
                value_usd=180,
                average_cost_price_usd=100,
                exchange_unrealized_pnl_usd=-20,
            ),
            portfolio_total_usd=280,
        )
        self.assertEqual(performance.current_price_usd, 90)
        self.assertEqual(performance.average_cost_price_usd, 100)
        self.assertEqual(performance.cost_basis_usd, 200)
        self.assertEqual(performance.unrealized_pnl_usd, -20)
        self.assertEqual(performance.unrealized_return_pct, -0.1)
        self.assertEqual(performance.pnl_status, "AVAILABLE")

        explicit = calculate_position_performance(
            Position("AAA", quantity=2, value_usd=180, cost_basis_usd=200),
            portfolio_total_usd=280,
        )
        self.assertEqual(explicit.average_cost_price_usd, 100)
        self.assertEqual(explicit.current_price_usd, 90)

    def test_missing_and_zero_cost_are_not_zero_return(self):
        missing = calculate_position_performance(
            Position("USDT", quantity=100, value_usd=100, current_price_usd=1),
            portfolio_total_usd=100,
        )
        self.assertEqual(missing.pnl_status, "COST_UNKNOWN")
        self.assertIsNone(missing.unrealized_pnl_usd)
        self.assertIsNone(missing.unrealized_return_pct)

        zero = calculate_position_performance(
            Position("AAA", quantity=1, value_usd=10, average_cost_price_usd=0),
            portfolio_total_usd=10,
        )
        self.assertEqual(zero.pnl_status, "ZERO_COST")
        self.assertEqual(zero.unrealized_pnl_usd, 10)
        self.assertIsNone(zero.unrealized_return_pct)

        inconsistent_quantity = calculate_position_performance(
            Position("AAA", quantity=0, value_usd=1, current_price_usd=1),
            portfolio_total_usd=1,
        )
        self.assertEqual(inconsistent_quantity.pnl_status, "MATERIAL_MISMATCH")

    def test_position_observations_reject_invalid_numbers(self):
        for field in ("current_price_usd", "average_cost_price_usd", "exchange_unrealized_pnl_usd"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    Position("AAA", value_usd=1, **{field: float("nan")})

    def test_rounding_material_mismatch_and_low_price(self):
        rounded = calculate_position_performance(
            Position(
                "AAA",
                quantity=2,
                value_usd=180.01,
                current_price_usd=90,
                average_cost_price_usd=100,
                exchange_unrealized_pnl_usd=-19.99,
            ),
            portfolio_total_usd=180.01,
        )
        self.assertEqual(rounded.validation_status, "ROUNDING_WARNING")
        self.assertEqual(rounded.pnl_status, "CROSSCHECK_WARNING")
        self.assertAlmostEqual(rounded.unrealized_pnl_usd, -19.99)

        material = calculate_position_performance(
            Position(
                "AAA",
                quantity=2,
                value_usd=180,
                current_price_usd=20,
                average_cost_price_usd=100,
                exchange_unrealized_pnl_usd=-20,
            ),
            portfolio_total_usd=180,
        )
        self.assertEqual(material.validation_status, "MATERIAL_MISMATCH")
        self.assertEqual(material.pnl_status, "MATERIAL_MISMATCH")
        self.assertIsNone(material.unrealized_pnl_usd)

        low_price = calculate_position_performance(
            Position(
                "AAA",
                quantity=100_000,
                value_usd=8,
                current_price_usd=0,
                average_cost_price_usd=0.0001,
                exchange_unrealized_pnl_usd=-2,
            ),
            portfolio_total_usd=8,
        )
        self.assertAlmostEqual(low_price.current_price_usd, 0.00008)
        self.assertAlmostEqual(low_price.unrealized_return_pct, -0.2)
        self.assertIn("$0.00", " ".join(low_price.validation_notes))

    def test_portfolio_aggregation_uses_known_cost_basis_only(self):
        summary = calculate_portfolio_position_performance(
            {
                "timestamp": "2026-09-01T00:00:00Z",
                "positions": [
                    {
                        "symbol": "AAA",
                        "quantity": 2,
                        "value_usd": 180,
                        "current_price_usd": 90,
                        "average_cost_price_usd": 100,
                    },
                    {"symbol": "USDT", "quantity": 100, "value_usd": 100, "current_price_usd": 1},
                ],
            }
        )
        self.assertEqual(summary.cost_known_current_value_usd, 180)
        self.assertEqual(summary.cost_known_cost_basis_usd, 200)
        self.assertEqual(summary.total_unrealized_pnl_known_usd, -20)
        self.assertEqual(summary.aggregate_unrealized_return_pct, -0.1)
        self.assertAlmostEqual(summary.pnl_value_coverage_ratio, 180 / 280)

    def test_binance_observation_normalizes_usd_and_unknown_cost(self):
        observation = BinancePortfolioObservation(
            "2026-09-01T00:00:00Z",
            "USD",
            300,
            (
                BinancePositionObservation("AAA", 2, 180, 90, 100, -20),
                BinancePositionObservation("USDT", 100, 100, 1, "--", "--"),
            ),
        )
        result = normalize_binance_observation(observation)
        positions = {item["symbol"]: item for item in result["positions"]}
        self.assertEqual(positions["AAA"]["unrealized_pnl_usd"], -20)
        self.assertEqual(positions["AAA"]["unrealized_return_pct"], -0.1)
        self.assertEqual(positions["USDT"]["pnl_status"], "COST_UNKNOWN")
        self.assertIsNone(positions["USDT"]["unrealized_return_pct"])
        self.assertFalse(observation.positions[1].cost_available)
        self.assertAlmostEqual(result["visible_value_coverage_ratio"], 280 / 300)
        with self.assertRaises(ValueError):
            BinancePortfolioObservation(
                "2026-09-01T00:00:00Z",
                "CNY",
                300,
                (BinancePositionObservation("AAA", 2, 180),),
            )

    def test_persistence_and_historical_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshots.jsonl"
            base = {
                "positions": [
                    {
                        "symbol": "AAA",
                        "quantity": 2,
                        "value_usd": 180,
                        "current_price_usd": 90,
                        "average_cost_price_usd": 100,
                    },
                    {"symbol": "USDT", "quantity": 100, "value_usd": 100, "current_price_usd": 1},
                ],
            }
            append_snapshot({**base, "timestamp": "2026-09-01T00:00:00Z"}, path)
            append_snapshot(
                {
                    **base,
                    "timestamp": "2026-09-02T00:00:00Z",
                    "positions": [
                        {
                            "symbol": "AAA",
                            "quantity": 2,
                            "value_usd": 200,
                            "current_price_usd": 100,
                            "average_cost_price_usd": 100,
                        },
                        {"symbol": "USDT", "quantity": 100, "value_usd": 100, "current_price_usd": 1},
                    ],
                },
                path,
            )
            record = read_snapshots(path)[0]["positions"][0]
            self.assertEqual(record["average_cost_price_usd"], 100)
            self.assertEqual(record["unrealized_pnl_usd"], -20)
            self.assertEqual(record["performance"]["unrealized_return_pct"], -0.1)
            snapshot_record = read_snapshots(path)[0]
            self.assertEqual(snapshot_record["visible_positions_value_usd"], 280)
            self.assertIsNone(snapshot_record["visible_value_coverage_ratio"])
            self.assertEqual(latest_position_performance("aaa", path).unrealized_return_pct, 0)
            history = position_performance_history("AAA", path)
            self.assertEqual(len(history), 2)
            context = build_position_pnl_context(path)
            self.assertEqual(context["AAA"]["unrealized_return_change_pp"], 10)

            with self.assertRaises(ValueError):
                append_snapshot(
                    {
                        "timestamp": "2026-09-03T00:00:00Z",
                        "positions": [
                            {
                                "symbol": "AAA",
                                "quantity": 2,
                                "value_usd": 180,
                                "current_price_usd": 20,
                                "average_cost_price_usd": 100,
                            }
                        ],
                    },
                    path,
                )

    def test_legacy_cost_basis_remains_calculable(self):
        summary = calculate_portfolio_position_performance(
            {
                "timestamp": "2026-09-01T00:00:00Z",
                "positions": [
                    {"symbol": "AAA", "quantity": 2, "value_usd": 180, "cost_basis_usd": 200}
                ],
            }
        )
        result = summary.positions[0]
        self.assertEqual(result.average_cost_price_usd, 100)
        self.assertEqual(result.current_price_usd, 90)
        self.assertEqual(result.unrealized_pnl_usd, -20)


if __name__ == "__main__":
    unittest.main()
