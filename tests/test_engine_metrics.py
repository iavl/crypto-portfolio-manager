import unittest

from crypto_portfolio.engine.benchmark import (
    benchmark_return,
    benchmark_return_from_prices,
    benchmark_return_with_cash_flows,
    build_aligned_benchmark_result,
    compare_portfolio_to_benchmark,
    require_aligned_period,
    secondary_benchmark_return,
)
from crypto_portfolio.engine.ledger import PortfolioSnapshot, cash_flow_adjusted_return
from crypto_portfolio.engine.metrics import portfolio_weighted_return


class EngineMetricsTests(unittest.TestCase):
    def test_missing_held_asset_return_fails(self):
        with self.assertRaisesRegex(ValueError, "missing returns"):
            portfolio_weighted_return({"BTC": 0.5, "ETH": 0.5}, {"BTC": 0.1})

        with self.assertRaisesRegex(ValueError, "sum to 1"):
            portfolio_weighted_return({"BTC": 0.5}, {"BTC": 0.1})

    def test_primary_and_secondary_benchmarks(self):
        self.assertAlmostEqual(benchmark_return({"BTC": 0.1}), 0.1)
        self.assertAlmostEqual(secondary_benchmark_return(0.1, 0.2), 0.13)
        self.assertAlmostEqual(benchmark_return({"BTC": 0.1, "ETH": 0.2}, benchmark="secondary"), 0.13)

    def test_benchmark_cash_flow_is_neutral(self):
        self.assertAlmostEqual(
            benchmark_return_with_cash_flows([{"BTC": 0.0}], [0.5]),
            0.0,
        )
        self.assertAlmostEqual(
            benchmark_return_with_cash_flows([{"BTC": 0.1}], [0.5]),
            0.1,
        )
        self.assertAlmostEqual(
            benchmark_return_with_cash_flows([{"BTC": 0.0}], [-0.5]),
            0.0,
        )
        self.assertAlmostEqual(
            benchmark_return_from_prices({"BTC": [100, 110]}, cash_flows=[0]),
            0.1,
        )

    def test_portfolio_and_benchmark_share_ending_snapshot_flow_timing(self):
        timestamps = ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"]
        portfolio = cash_flow_adjusted_return(
            [PortfolioSnapshot(timestamps[0], 100), PortfolioSnapshot(timestamps[1], 160, 50)]
        )
        benchmark = benchmark_return_with_cash_flows(
            [{"BTC": 0.10}], [0.50], timestamps=timestamps
        )
        self.assertAlmostEqual(portfolio, benchmark)

    def test_secondary_benchmark_is_buy_and_hold(self):
        result = benchmark_return_from_prices(
            {"BTC": [100, 110, 121], "ETH": [100, 200, 200]},
            benchmark="secondary",
        )
        self.assertAlmostEqual(result, 0.447)

    def test_benchmark_contribution_is_invested_at_the_flow_boundary(self):
        result = benchmark_return_with_cash_flows(
            [{"BTC": 0.10}, {"BTC": 0.10}],
            [0.50, 0.0],
        )
        self.assertAlmostEqual(result, 0.21)

    def test_secondary_benchmark_flow_must_be_scaled_by_initial_value(self):
        # Periods: BTC +100% then 0%; ETH -50% then +100%; $5k deposit after
        # period 1. Sized to a $20k portfolio the flow is small relative to
        # value; with the default unit start value it would dwarf the sleeve
        # and silently rebalance the drifted composition back to exactly 70/30.
        prices = {"BTC": [100, 200, 200], "ETH": [100, 50, 100]}
        scaled = benchmark_return_from_prices(
            prices, benchmark="secondary", cash_flows=[5000.0, 0.0], initial_value=20000.0,
        )
        self.assertAlmostEqual(scaled, 40500 / (20000 + 5000 / 1.55) - 1, places=10)
        unscaled = benchmark_return_from_prices(
            prices, benchmark="secondary", cash_flows=[5000.0, 0.0],
        )
        self.assertAlmostEqual(unscaled, 6501.7 / (1 + 5000 / 1.55) - 1, places=10)
        self.assertNotAlmostEqual(scaled, unscaled, places=3)

    def test_aligned_benchmark_sizes_flows_with_the_portfolio(self):
        snapshots = (
            PortfolioSnapshot("2026-01-01T00:00:00Z", 20000),
            PortfolioSnapshot("2026-01-02T00:00:00Z", 36000, 5000),
            PortfolioSnapshot("2026-01-03T00:00:00Z", 40500),
        )
        result = build_aligned_benchmark_result(
            snapshots, [100, 200, 200], eth_prices=[100, 50, 100],
        )
        self.assertEqual(result.benchmark_status, "AVAILABLE")
        self.assertAlmostEqual(result.btc_return, 1.0, places=9)
        self.assertAlmostEqual(
            result.secondary_benchmark_return,
            40500 / (20000 + 5000 / 1.55) - 1,
            places=9,
        )

    def test_aligned_benchmark_survives_a_routine_withdrawal(self):
        # A $5k withdrawal from a $20k portfolio must not exhaust a benchmark
        # that is now scaled to the same initial value.
        snapshots = (
            PortfolioSnapshot("2026-01-01T00:00:00Z", 20000),
            PortfolioSnapshot("2026-01-02T00:00:00Z", 15500, -5000),
        )
        result = build_aligned_benchmark_result(snapshots, [200, 205])
        self.assertEqual(result.benchmark_status, "AVAILABLE")
        self.assertAlmostEqual(result.btc_return, 0.025, places=9)

    def test_benchmark_requires_aligned_periods(self):
        require_aligned_period("2026-01-01", "2026-02-01", "2026-01-01", "2026-02-01")
        with self.assertRaises(ValueError):
            require_aligned_period("2026-01-01", "2026-02-01", "2026-01-02", "2026-02-01")
        result = compare_portfolio_to_benchmark(
            0.2,
            0.1,
            portfolio_start="2026-01-01",
            portfolio_end="2026-02-01",
            benchmark_start="2026-01-01",
            benchmark_end="2026-02-01",
        )
        self.assertAlmostEqual(result["excess_return"], 0.1)


if __name__ == "__main__":
    unittest.main()
