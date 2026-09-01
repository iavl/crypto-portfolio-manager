import unittest

from crypto_portfolio.engine.benchmark import (
    benchmark_return,
    benchmark_return_from_prices,
    benchmark_return_with_cash_flows,
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
