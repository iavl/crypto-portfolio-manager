"""Benchmark contract and objective hierarchy (Strategy V2 Phase 5)."""

import unittest

from crypto_portfolio.engine.benchmark import benchmark_return
from crypto_portfolio.models.policy import (
    PolicyError,
    load_policy,
    policy_from_mapping,
)


class BenchmarkContractTests(unittest.TestCase):
    def test_policy_declares_the_risk_matched_primary(self):
        benchmarks = load_policy().benchmarks
        self.assertEqual(
            benchmarks["risk_matched_primary"], {"type": "VOL_MATCHED_BTC_CASH"}
        )
        self.assertEqual(benchmarks["opportunity_cost_btc"], {"BTC": 1.0})
        self.assertEqual(
            benchmarks["secondary_static"], {"BTC": 0.7, "ETH": 0.3}
        )

    def test_contract_round_trips_through_as_dict(self):
        policy = load_policy()
        self.assertEqual(
            policy_from_mapping(policy.as_dict()).benchmarks, policy.benchmarks
        )

    def test_legacy_primary_secondary_keys_are_rejected(self):
        data = load_policy().as_dict()
        data["benchmarks"] = {
            "primary": {"BTC": 1.0},
            "secondary": {"BTC": 0.7, "ETH": 0.3},
        }
        with self.assertRaisesRegex(PolicyError, "risk_matched_primary"):
            policy_from_mapping(data)

    def test_marker_must_be_the_exact_type(self):
        data = load_policy().as_dict()
        data["benchmarks"]["risk_matched_primary"] = {"type": "SOMETHING_ELSE"}
        with self.assertRaisesRegex(PolicyError, "VOL_MATCHED_BTC_CASH"):
            policy_from_mapping(data)

    def test_static_weight_consumers_reject_the_marker(self):
        with self.assertRaisesRegex(ValueError, "static weight map"):
            benchmark_return({"BTC": 0.1}, benchmark="risk_matched_primary")

    def test_default_per_period_anchor_is_the_btc_opportunity_cost(self):
        self.assertAlmostEqual(benchmark_return({"BTC": 0.1}), 0.1)


class ComparisonMetricTests(unittest.TestCase):
    def test_benchmark_comparison_carries_the_excess_metric_family(self):
        from crypto_portfolio.research.orchestrator import _benchmark_comparison

        strategy = {
            "total_return": 0.20, "cagr": 0.10, "annualized_volatility": 0.12,
            "sharpe_rf_zero": 0.5, "sortino_target_zero": 0.8,
            "maximum_drawdown": -0.10,
        }
        benchmark = {
            "total_return": 0.15, "cagr": 0.08, "annualized_volatility": 0.12,
            "sharpe_rf_zero": 0.6, "sortino_target_zero": 0.9,
            "maximum_drawdown": -0.16,
        }
        result = _benchmark_comparison(strategy, benchmark)
        self.assertAlmostEqual(result["tracking_difference"], 0.05)
        self.assertAlmostEqual(result["excess_return"], 0.05)
        self.assertAlmostEqual(result["excess_return_annualized"], 0.02)
        self.assertAlmostEqual(result["sortino_delta"], -0.1)
        self.assertAlmostEqual(result["drawdown_delta"], 0.06)


class StrategyAttributionTests(unittest.TestCase):
    def test_attribution_block_exists_on_real_runs(self):
        # Exercise the real replay fixture used by the benchmark tests.
        from tests.test_benchmarks import OrchestratorBenchmarkWiringTests

        result = OrchestratorBenchmarkWiringTests._result()
        attribution = result["strategy_attribution"]
        self.assertIn("methodology", attribution)
        self.assertIn("average_weights", attribution)
        if attribution.get("average_weights"):
            self.assertIn("average_weights_hold", attribution)
            self.assertIn("risk_scaling_effect", attribution)
            hold = attribution["average_weights_hold"]
            self.assertAlmostEqual(
                sum(attribution["average_weights"].values()), 1.0, places=6
            )
            self.assertIn("total_return", hold)
            self.assertIsInstance(attribution["risk_scaling_effect"], float)
        self.assertIn("vol_matched_excess_return_annualized", attribution)


class VolMatchedBtcEthBenchmarkTests(unittest.TestCase):
    """Ablation benchmark B (Phase 6.8): two-asset volatility targeting."""

    def test_benchmark_b_joins_the_comparison_set(self):
        from tests.test_benchmarks import OrchestratorBenchmarkWiringTests

        result = OrchestratorBenchmarkWiringTests._result()
        benchmarks = result["benchmarks"]
        self.assertIn("vol_matched_btc_eth_70_30_cash_investable", benchmarks)
        comparison = result["benchmark_comparison"]["vol_matched_btc_eth_70_30_cash_investable"]
        self.assertIn("excess_return_annualized", comparison)
        # The solved mix carries the strategy's risk or the closest reachable
        # risk below it (the closed-form solve caps at the riskiest mix when
        # the strategy out-volatilizes both legs - the report's risk-match
        # warning covers that case for readers).
        strategy_vol = result["metrics"]["annualized_volatility"]
        benchmark_vol = benchmarks["vol_matched_btc_eth_70_30_cash_investable"]["metrics"]["annualized_volatility"]
        self.assertLessEqual(benchmark_vol, strategy_vol + 0.02 + 1e-9)
        # The internal split of the risky sleeve stays 70/30.
        weights = benchmarks["vol_matched_btc_eth_70_30_cash_investable"]["valuations"][-1]["weights"]
        btc, eth = weights.get("BTC", 0.0), weights.get("ETH", 0.0)
        if btc + eth > 1e-9:
            # Mark drift between rebalances moves the realized split a few
            # basis points off the 70/30 target; only the gross shape is
            # being asserted here.
            self.assertAlmostEqual(btc / (btc + eth), 0.7, places=2)
        self.assertIn("70/30 BTC/ETH sleeve weight", benchmarks["vol_matched_btc_eth_70_30_cash_investable"]["methodology"])


if __name__ == "__main__":
    unittest.main()
