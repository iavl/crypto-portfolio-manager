"""Score-space contract (Strategy V2.1 Phase C).

Threshold comparisons run in the NORMALIZED space only. When the normalized
score is unavailable the effective (reliability-shrunk) score is a diagnostic
aggregate: it must never substitute into an entry/exit threshold comparison,
and it must never manufacture an exit for a held position.
"""

import unittest

from crypto_portfolio.engine.allocation import (
    _core_quality_multiplier,
    build_target_allocation,
    satellite_eligibility,
)
from crypto_portfolio.engine.scoring_v3 import comparison_score_space
from crypto_portfolio.models.policy import load_policy


def _factors(structural: bool):
    factors = {
        "trend": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
        "relative_strength_btc": {"score": 80, "availability": "AVAILABLE", "reliability": 1.0},
        "capital_flows": {"score": 75, "availability": "AVAILABLE", "reliability": 1.0},
    }
    if structural:
        factors.update({
            "valuation": {"score": 70, "availability": "AVAILABLE", "reliability": 1.0},
            "fundamentals": {"score": 72, "availability": "AVAILABLE", "reliability": 1.0},
            "onchain": {"score": 68, "availability": "AVAILABLE", "reliability": 1.0},
        })
    return factors


class NoEffectiveFallbackTests(unittest.TestCase):
    def test_case_c_weighted_score_alone_never_compares_against_thresholds(self):
        policy = load_policy()
        # Market factors deliberately weak, so no conviction path applies,
        # while a high effective aggregate used to sail over the entry score
        # through the fallback.
        assessment = {
            "factor_scores": {
                "trend": {"score": 40, "availability": "AVAILABLE", "reliability": 1.0},
                "relative_strength_btc": {"score": 45, "availability": "AVAILABLE", "reliability": 1.0},
                "capital_flows": {"score": 42, "availability": "AVAILABLE", "reliability": 1.0},
            },
            "weighted_score": 90,
            "confidence": "HIGH",
            "critical_data_complete": True,
            "score_coverage": 0.55,
            "relative_strength_vs_btc": "OUTPERFORM",
        }
        self.assertEqual(comparison_score_space(None, 90), "EFFECTIVE_DIAGNOSTIC_ONLY")
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": assessment},
        )
        self.assertEqual(result.deployment_allowances["SOL"]["comparison_score_space"],
                         "EFFECTIVE_DIAGNOSTIC_ONLY")
        self.assertEqual(result.deployment_allowances["SOL"]["final_score_authority"], "NONE")
        self.assertEqual(result.target_weights.get("SOL", 0), 0)
        self.assertEqual(
            satellite_eligibility(assessment, policy, current_weight=0.0),
            "INELIGIBLE",
        )

    def test_held_without_normalized_is_preserved_not_exited(self):
        policy = load_policy()
        assessment = {
            "factor_scores": {
                "trend": {"score": 40, "availability": "AVAILABLE", "reliability": 1.0},
                "relative_strength_btc": {"score": 45, "availability": "AVAILABLE", "reliability": 1.0},
                "capital_flows": {"score": 42, "availability": "AVAILABLE", "reliability": 1.0},
            },
            "weighted_score": 45,  # would sit below the exit floor if compared
            "confidence": "HIGH",
            "critical_data_complete": True,
            "score_coverage": 0.55,
            "relative_strength_vs_btc": "OUTPERFORM",
        }
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": assessment},
            current_weights={"BTC": 0.5, "ETH": 0.3, "SOL": 0.05, "USDT": 0.15},
        )
        self.assertAlmostEqual(result.target_weights["SOL"], 0.05)
        self.assertTrue(result.deployment_allowances["SOL"].get("preserve_existing"))
        self.assertEqual(result.deployment_allowances["SOL"]["deployment_factor"], 0.0)

    def test_normalized_score_still_compares_normally(self):
        policy = load_policy()
        assessment = {
            "factor_scores": _factors(structural=True),
            "weighted_score": 78,
            "normalized_score": 78,
            "confidence": "HIGH",
            "critical_data_complete": True,
            "score_coverage": 1.0,
            "relative_strength_vs_btc": "OUTPERFORM",
        }
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": assessment},
        )
        allowance = result.deployment_allowances["SOL"]
        self.assertEqual(allowance["comparison_score_space"], "NORMALIZED")
        self.assertEqual(allowance["final_score_authority"], "COMPOSITE_NORMALIZED")
        self.assertGreater(result.target_weights["SOL"], 0)

    def test_unavailable_space_blocks_every_path(self):
        self.assertEqual(comparison_score_space(None, None), "UNAVAILABLE")
        policy = load_policy()
        assessment = {
            "factor_scores": {
                "trend": {"score": 40, "availability": "AVAILABLE", "reliability": 1.0},
                "relative_strength_btc": {"score": 45, "availability": "AVAILABLE", "reliability": 1.0},
                "capital_flows": {"score": 42, "availability": "AVAILABLE", "reliability": 1.0},
            },
            "confidence": "HIGH",
            "critical_data_complete": True,
            "score_coverage": 0.55,
            "relative_strength_vs_btc": "OUTPERFORM",
        }
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": assessment},
        )
        self.assertEqual(result.target_weights.get("SOL", 0), 0)


class CoreAuthorityTests(unittest.TestCase):
    def test_core_quality_multiplier_is_narrowed_to_research_bounds(self):
        # Phase C: unvalidated scores hold [0.85, 1.15] sizing authority.
        self.assertAlmostEqual(_core_quality_multiplier(0), 0.85)
        self.assertAlmostEqual(_core_quality_multiplier(50), 1.00)
        self.assertAlmostEqual(_core_quality_multiplier(100), 1.15)

    def test_core_scores_keep_bounded_relative_influence(self):
        from crypto_portfolio.engine.allocation import _allocate_core

        policy = load_policy()
        btc = {"weighted_score": 80, "normalized_score": 80, "confidence": "HIGH"}
        eth = {"weighted_score": 80, "normalized_score": 80, "confidence": "HIGH",
               "relative_strength_vs_btc": 70}
        strong_btc, _, _ = _allocate_core(policy, 0.30, {"BTC": btc, "ETH": eth}, {}, 0.50)
        weak_btc_eth = _allocate_core(
            policy, 0.30,
            {"BTC": {"weighted_score": 40, "normalized_score": 40, "confidence": "HIGH"},
             "ETH": eth},
            {}, 0.50,
        )
        btc_gain = strong_btc["BTC"] - weak_btc_eth[0]["BTC"]
        # The score moves core shares, but the narrowed multiplier bounds the
        # move: a 40-point score gap shifts the BTC share by well under five
        # points of the 30% budget.
        self.assertGreater(btc_gain, 0.0)
        self.assertLess(btc_gain, 0.05)


if __name__ == "__main__":
    unittest.main()
