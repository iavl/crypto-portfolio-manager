"""Market/structural family scoring (Strategy V2.1 Phase C).

The two family sub-scores reuse the composite's exact semantics — reliability
shrinkage toward neutral, coverage as weight x reliability, and the
coverage-normalized rescale above the minimum gate — under the preregistered
per-profile family weights.
"""

import unittest

from crypto_portfolio.engine.scoring_v3 import (
    asset_family_scores,
    comparison_score_space,
    family_score,
    scoring_v3_families,
)
from crypto_portfolio.models.policy import load_policy


_STRONG_MARKET = {
    "trend": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
    "relative_strength_btc": {"score": 80, "availability": "AVAILABLE", "reliability": 1.0},
    "capital_flows": {"score": 75, "availability": "AVAILABLE", "reliability": 1.0},
}


class FamilyConfigurationTests(unittest.TestCase):
    def test_default_profile_uses_the_preregistered_market_weights(self):
        families = scoring_v3_families("SOL", load_policy())
        self.assertEqual(families["market"], {
            "trend": 0.45, "relative_strength_btc": 0.30, "capital_flows": 0.25,
        })
        self.assertEqual(families["structural"], {
            "valuation": 0.30, "fundamentals": 0.45, "onchain": 0.25,
        })

    def test_eth_asset_override_adds_onchain_demand_to_the_market_family(self):
        families = scoring_v3_families("ETH", load_policy())
        self.assertEqual(families["market"], {
            "trend": 0.40, "relative_strength_btc": 0.25,
            "capital_flows": 0.20, "onchain": 0.15,
        })

    def test_defi_protocol_weights_protocol_fundamentals_highest(self):
        families = scoring_v3_families("AAVE", load_policy())
        self.assertEqual(families["market"]["fundamentals"] if False else families["structural"], {
            "fundamentals": 0.55, "valuation": 0.25, "onchain": 0.20,
        })
        self.assertEqual(families["market"]["trend"], 0.55)

    def test_btc_family_uses_only_btc_profile_factors(self):
        families = scoring_v3_families("BTC", load_policy())
        self.assertNotIn("relative_strength_btc", families["market"])
        self.assertIn("btc_valuation", families["structural"])


class FamilyScoreTests(unittest.TestCase):
    def test_full_evidence_family_scores_stay_in_raw_space(self):
        market = family_score(
            _STRONG_MARKET,
            {"trend": 0.45, "relative_strength_btc": 0.30, "capital_flows": 0.25},
            minimum_coverage=0.60,
        )
        self.assertAlmostEqual(market["score"], 0.45 * 85 + 0.30 * 80 + 0.25 * 75)
        self.assertAlmostEqual(market["coverage"], 1.0)
        self.assertAlmostEqual(market["normalized_score"], market["score"])

    def test_missing_factor_shrinks_coverage_not_the_observation(self):
        # One market factor missing at 70% family coverage: the effective
        # score shrinks toward neutral but the normalized score recovers what
        # was actually observed.
        factors = dict(_STRONG_MARKET)
        factors["capital_flows"] = None
        market = family_score(
            factors,
            {"trend": 0.45, "relative_strength_btc": 0.30, "capital_flows": 0.25},
            minimum_coverage=0.60,
        )
        self.assertAlmostEqual(market["coverage"], 0.75)
        self.assertAlmostEqual(market["score"], 0.45 * 85 + 0.30 * 80 + 0.25 * 50)
        self.assertAlmostEqual(market["normalized_score"], 50 + (market["score"] - 50) / 0.75)

    def test_family_below_the_normalization_gate_is_unavailable(self):
        factors = dict(_STRONG_MARKET)
        factors["trend"] = None
        factors["capital_flows"] = None
        market = family_score(
            factors,
            {"trend": 0.45, "relative_strength_btc": 0.30, "capital_flows": 0.25},
            minimum_coverage=0.60,
        )
        self.assertAlmostEqual(market["coverage"], 0.30)
        self.assertIsNone(market["normalized_score"])
        self.assertFalse(market["available"])

    def test_structural_family_with_no_factors_is_unavailable(self):
        result = asset_family_scores(
            {"factor_scores": dict(_STRONG_MARKET)}, policy=load_policy(), symbol="SOL"
        )
        self.assertIsNone(result["structural"]["normalized_score"])
        self.assertFalse(result["structural"]["available"])
        self.assertAlmostEqual(result["structural"]["coverage"], 0.0)

    def test_market_family_reads_from_assessment_factor_scores(self):
        assessment = {
            "factor_scores": dict(_STRONG_MARKET),
            "score_coverage": 1.0,
            "critical_data_complete": True,
        }
        result = asset_family_scores(assessment, policy=load_policy(), symbol="SOL")
        self.assertAlmostEqual(result["market"]["coverage"], 1.0)
        self.assertAlmostEqual(result["market"]["normalized_score"], 81.0)


class ComparisonScoreSpaceTests(unittest.TestCase):
    def test_space_is_explicit_about_fallbacks(self):
        self.assertEqual(comparison_score_space(72.0, 60.0), "NORMALIZED")
        self.assertEqual(comparison_score_space(None, 60.0), "EFFECTIVE_DIAGNOSTIC_ONLY")
        self.assertEqual(comparison_score_space(None, None), "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
