"""TACTICAL_ONLY allocation behavior (Strategy V2.1 Phase C).

The phase's key behavior: a satellite with a strong normalized market score
but no usable structural evidence earns ``satellite envelope x
tactical_fraction`` — a small, real strategic target — instead of a
deployment of exactly zero.
"""

import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
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


def _sol(structural: bool, **fields):
    return {
        "factor_scores": _factors(structural),
        "weighted_score": 60 if not structural else 78,
        "normalized_score": 78 if structural else None,
        "confidence": "HIGH",
        "critical_data_complete": True,
        "score_coverage": 1.0 if structural else 0.55,
        "relative_strength_vs_btc": "OUTPERFORM",
        **fields,
    }


class TacticalOnlyTests(unittest.TestCase):
    def test_case_a_unheld_tactical_satellite_gets_a_real_target(self):
        policy = load_policy()
        tactical_fraction = policy.allocation["tactical_fraction"]
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": _sol(structural=False)},
        )
        allowance = result.deployment_allowances["SOL"]
        self.assertEqual(allowance["conviction_state"], "TACTICAL_ONLY")
        self.assertEqual(allowance["final_score_authority"], "TACTICAL_FRACTION")
        # NORMAL regime envelope 25%, tactical slice 30% of it.
        self.assertAlmostEqual(result.target_weights["SOL"], 0.25 * tactical_fraction)
        self.assertGreater(allowance["max_immediate_increase_weight"], 0)

    def test_case_b_full_conviction_uses_the_curve_not_the_tactical_slice(self):
        policy = load_policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": _sol(structural=True)},
        )
        allowance = result.deployment_allowances["SOL"]
        self.assertEqual(allowance["conviction_state"], "FULL_CONVICTION")
        self.assertEqual(allowance["final_score_authority"], "COMPOSITE_NORMALIZED")
        # Full evidence at ~78 composite normalized rides the curve above the
        # tactical slice: the structural case is worth more than 30% of the
        # envelope.
        self.assertGreater(result.target_weights["SOL"], 0.25 * 0.30)
        self.assertAlmostEqual(allowance["tactical_fraction"], None)

    def test_tactical_slice_is_monotone_in_the_envelope_not_the_score(self):
        # The tactical slice does not chase the market score: any strong
        # market without structure earns exactly the same fixed fraction.
        policy = load_policy()
        stronger = _sol(structural=False)
        for factor in ("trend", "relative_strength_btc", "capital_flows"):
            stronger["factor_scores"][factor] = {
                "score": 98, "availability": "AVAILABLE", "reliability": 1.0,
            }
        baseline = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": _sol(structural=False)},
        )
        boosted = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": stronger},
        )
        self.assertAlmostEqual(
            boosted.target_weights["SOL"], baseline.target_weights["SOL"]
        )

    def test_tactical_needs_the_btc_relative_case(self):
        policy = load_policy()
        missing_relative = _sol(structural=False, relative_strength_vs_btc=None)
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"SOL": missing_relative},
            current_weights={"BTC": 0.5, "ETH": 0.3, "SOL": 0.02, "USDT": 0.18},
        )
        # Missing BTC-relative comparison is HOLD_OR_REDUCE: the held slice is
        # preserved and no tactical entry happens.
        allowance = result.deployment_allowances["SOL"]
        self.assertNotEqual(allowance["eligibility_state"], "ELIGIBLE_INCREASE")
        self.assertAlmostEqual(result.target_weights["SOL"], 0.02)

    def test_attribution_block_carries_every_scoring_v3_field(self):
        result = build_target_allocation(
            assessments={"SOL": _sol(structural=False)},
        )
        allowance = result.deployment_allowances["SOL"]
        for field in (
            "market_score", "market_coverage", "structural_score", "structural_coverage",
            "conviction_state", "normalized_market_score", "normalized_structural_score",
            "comparison_score_space", "tactical_fraction", "final_score_authority",
            "evidence_class", "evidence_deployment_factor",
        ):
            self.assertIn(field, allowance)


if __name__ == "__main__":
    unittest.main()
