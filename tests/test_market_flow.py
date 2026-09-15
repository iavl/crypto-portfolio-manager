"""Market-level regime flow aggregation (BTC + ETH ETF, not a BTC alias).

The 2026-09-15 review reported regime flows NEGATIVE from BTC's 7d ETF
outflow while the same review showed ETH 30d strongly positive; the regime
flow domain must represent broad market liquidity.  Dollar flows aggregate
against combined AUM; pre-normalized ratios aggregate by AUM weight; a
single available component is an explicit fallback with reduced
confidence — never a silent BTC substitution.
"""

import unittest

from crypto_portfolio.engine.regime_inputs import aggregate_market_flow


class MarketFlowAggregationTests(unittest.TestCase):
    def test_btc_negative_eth_positive_aggregate_is_not_negative(self):
        # BTC -2B/1000B = -0.002 (NEGATIVE alone); ETH +8B/1000B = +0.008.
        # Aggregate (+6B / 2000B = +0.003) is POSITIVE above the +0.001
        # threshold, so the market regime flow must not be NEGATIVE.
        result = aggregate_market_flow(
            {"net_flow_usd": -2e9, "aum_usd": 1000e9},
            {"net_flow_usd": 8e9, "aum_usd": 1000e9},
        )
        self.assertEqual(result["state"], "POSITIVE")
        self.assertEqual(result["method"], "dollar_aum_aggregation")
        self.assertEqual(result["confidence"], "HIGH")
        self.assertAlmostEqual(result["aggregate_flow_ratio"], 0.003)
        self.assertEqual(result["component_states"]["BTC"], "NEGATIVE")
        self.assertEqual(result["component_states"]["ETH"], "POSITIVE")

    def test_both_negative_aggregate_is_negative(self):
        result = aggregate_market_flow(
            {"net_flow_usd": -3e9, "aum_usd": 1000e9},
            {"net_flow_usd": -4e9, "aum_usd": 1000e9},
        )
        self.assertEqual(result["state"], "NEGATIVE")
        self.assertAlmostEqual(result["aggregate_flow_ratio"], -0.0035)

    def test_ratio_only_components_aggregate_by_aum_weight(self):
        result = aggregate_market_flow(
            {"flow_ratio": -0.002, "aum_usd": 1500e9},
            {"flow_ratio": 0.006, "aum_usd": 500e9},
        )
        # AUM weights 0.75/0.25 -> (-0.0015 + 0.0015) = 0.0 -> NEUTRAL.
        self.assertEqual(result["method"], "aum_weighted_ratios")
        self.assertEqual(result["confidence"], "HIGH")
        self.assertAlmostEqual(result["aggregate_flow_ratio"], 0.0)
        self.assertEqual(result["state"], "NEUTRAL")

    def test_equal_weight_fallback_without_aum_reduces_confidence(self):
        result = aggregate_market_flow(
            {"flow_ratio": -0.002},
            {"flow_ratio": 0.006},
        )
        self.assertEqual(result["method"], "equal_weighted_ratios")
        self.assertEqual(result["confidence"], "MEDIUM")
        self.assertIn("COMPONENT_AUM_MISSING", result["fallback_provenance"])
        self.assertAlmostEqual(result["aggregate_flow_ratio"], 0.002)
        self.assertEqual(result["state"], "POSITIVE")

    def test_single_component_is_an_explicit_fallback(self):
        result = aggregate_market_flow(
            {"net_flow_usd": -2e9, "aum_usd": 1000e9},
            None,
        )
        self.assertEqual(result["method"], "single_asset_fallback")
        self.assertEqual(result["confidence"], "MEDIUM")
        self.assertIn("ONLY_BTC_AVAILABLE", result["fallback_provenance"])
        self.assertEqual(result["state"], "NEGATIVE")
        # The missing component is recorded as unknown, not aliased.
        self.assertEqual(result["component_states"]["ETH"], "UNKNOWN")

    def test_no_components_is_unknown_fail_closed(self):
        result = aggregate_market_flow(None, None)
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertEqual(result["method"], "unavailable")
        self.assertEqual(result["confidence"], "LOW")
        self.assertIn("NO_MARKET_FLOW_COMPONENTS", result["fallback_provenance"])

    def test_inconsistent_component_basis_fails_defensive(self):
        # BTC supplies dollars without AUM (no derivable ratio), ETH only a
        # bare ratio: there is no common aggregation basis, so the result is
        # UNKNOWN with explicit provenance instead of an invented blend.
        result = aggregate_market_flow(
            {"net_flow_usd": 1e9},
            {"flow_ratio": 0.004},
        )
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertIn("INCONSISTENT_COMPONENT_BASIS", result["fallback_provenance"])

    def test_dollar_component_with_aum_derives_ratio_for_weighted_blends(self):
        # BTC dollars+AUM derive a deterministic ratio; ETH supplies a ratio
        # without AUM, so the blend falls back to equal weight at MEDIUM
        # confidence and records the missing-AUM provenance.
        result = aggregate_market_flow(
            {"net_flow_usd": 1e9, "aum_usd": 1000e9},
            {"flow_ratio": 0.004},
        )
        self.assertEqual(result["method"], "equal_weighted_ratios")
        self.assertEqual(result["confidence"], "MEDIUM")
        self.assertAlmostEqual(result["aggregate_flow_ratio"], 0.0025)
        self.assertEqual(result["state"], "POSITIVE")

    def test_invalid_components_are_rejected(self):
        with self.assertRaises(ValueError):
            aggregate_market_flow({"net_flow_usd": float("nan")}, None)
        with self.assertRaises(ValueError):
            aggregate_market_flow({"flow_ratio": 1.5}, None)
        with self.assertRaises(ValueError):
            aggregate_market_flow({"unexpected": 1}, None)

    def test_aum_only_component_counts_as_unavailable(self):
        # AUM without any flow direction carries no signal: the component is
        # recorded unavailable and the aggregate fails closed to UNKNOWN.
        result = aggregate_market_flow({"aum_usd": 100.0}, None)
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertIn("NO_MARKET_FLOW_COMPONENTS", result["fallback_provenance"])


if __name__ == "__main__":
    unittest.main()
