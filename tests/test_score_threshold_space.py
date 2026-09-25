"""Unified threshold score space (Strategy V2 Phase 2).

Satellite thresholds (57/62/67/85) and ETH core gates (55/45) compare the
coverage-normalized score; the effective score stays a diagnostic aggregate.
Both gate families must use the same space. The interesting production case
is evidence that is COMPLETE but reliability-degraded (every factor observed,
reliability below 1): the effective score shrinks toward neutral while the
normalized reading keeps the observed attractiveness.
"""

import unittest

from crypto_portfolio.engine.allocation import build_target_allocation, satellite_eligibility
from crypto_portfolio.engine.core_eligibility import eth_core_eligibility
from crypto_portfolio.engine.scoring import score_assessment
from crypto_portfolio.models.evidence import AssetAssessment, FactorScore
from crypto_portfolio.models.policy import load_policy


_DEFAULT_FACTORS = ("trend", "valuation", "fundamentals", "onchain",
                    "capital_flows", "relative_strength_btc")


def _reliability_degraded(symbol, raw_score, reliability, **kwargs):
    """Score an asset whose evidence is complete but reliability-degraded."""
    factors = {
        factor: FactorScore(
            factor=factor, score=raw_score, availability="AVAILABLE",
            reliability=reliability,
        )
        for factor in _DEFAULT_FACTORS
    }
    return score_assessment(AssetAssessment(
        symbol=symbol, factor_scores=factors, **kwargs
    ))[0]


class SatelliteThresholdSpaceTests(unittest.TestCase):
    def test_effective_sub_entry_score_can_enter_on_the_normalized_space(self):
        # Raw 70 observed at reliability 0.8: effective 66 sits below the 67
        # entry threshold after shrinkage, the normalized 75 sits above it.
        # Entry is a question about attractiveness, so the normalized space
        # decides; evidence quality keeps gating deployment separately.
        scored = _reliability_degraded(
            "SOL", 70.0, 0.8, asset_type="satellite",
            relative_strength_vs_btc="OUTPERFORM",
        )
        self.assertLess(scored.weighted_score, 67.0)
        self.assertGreater(scored.normalized_score, 67.0)
        self.assertEqual(
            satellite_eligibility(scored, load_policy(), current_weight=0.0),
            "ELIGIBLE_INCREASE",
        )

    def test_full_coverage_threshold_behavior_is_unchanged(self):
        # At coverage 1.0 the two spaces coincide, so the historical
        # threshold semantics are exactly the old ones.
        scored = _reliability_degraded(
            "SOL", 60.0, 1.0, asset_type="satellite",
            relative_strength_vs_btc="OUTPERFORM",
        )
        self.assertAlmostEqual(scored.normalized_score, scored.weighted_score)
        # A held 60 rides the soft-exit band exactly as before.
        self.assertEqual(
            satellite_eligibility(scored, load_policy(), current_weight=0.10),
            "SOFT_EXIT",
        )

    def test_weak_degraded_evidence_is_saved_from_a_missing_data_exit(self):
        # Raw 63 at reliability 0.8: effective 60.4 reads inside the
        # soft-exit band [57, 62) - a shrinkage artifact - while the
        # normalized 66.25 reads above the exit band. The normalized space
        # keeps the exit decision anchored to the observed attractiveness.
        scored = _reliability_degraded(
            "SOL", 63.0, 0.8, asset_type="satellite",
            relative_strength_vs_btc="OUTPERFORM",
        )
        self.assertLess(scored.weighted_score, 62.0)
        self.assertGreater(scored.normalized_score, 62.0)
        self.assertEqual(
            satellite_eligibility(scored, load_policy(), current_weight=0.10),
            "HOLD_OR_REDUCE",
        )

    def test_allocation_curve_uses_the_normalized_score(self):
        policy = load_policy()
        scored = _reliability_degraded(
            "SOL", 70.0, 0.8, asset_type="satellite",
            relative_strength_vs_btc="OUTPERFORM",
        ).as_dict()
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": scored},
            current_weights={"USDT": 1.0},
        )
        allowance = result.deployment_allowances["SOL"]
        self.assertAlmostEqual(allowance["score"], scored["normalized_score"], places=9)
        self.assertEqual(allowance["eligibility_state"], "ELIGIBLE_INCREASE")
        self.assertGreater(allowance["strategic_target_weight"], 0.0)
        self.assertIsNotNone(allowance["normalized_score"])
        self.assertIsNotNone(allowance["effective_score"])
        # Coverage still caps deployment as evidence confidence (MEDIUM band).
        self.assertEqual(allowance["evidence_class"], "LIMITED")
        self.assertAlmostEqual(allowance["confidence_deployment_factor"], 0.7)


class EthGateSpaceTests(unittest.TestCase):
    def test_eth_reduce_floor_uses_the_normalized_score(self):
        policy = load_policy()
        scored = _reliability_degraded(
            "ETH", 44.0, 0.8, asset_type="core",
            relative_strength_vs_btc="OUTPERFORM",
        )
        # Effective 45.2 would sit at the hold floor (45); the normalized 30
        # reads clearly below it and routes ETH to REDUCE on the same
        # observed evidence.
        self.assertGreaterEqual(scored.weighted_score, 45.0)
        self.assertLess(scored.normalized_score, 45.0)
        self.assertEqual(
            eth_core_eligibility(scored, policy, current_weight=0.10),
            "REDUCE",
        )

    def test_eth_gate_falls_back_to_effective_when_normalization_unavailable(self):
        policy = load_policy()
        sparse = score_assessment(AssetAssessment(
            symbol="ETH", factor_scores={"trend": 60.0}, asset_type="core",
        ))[0]
        self.assertIsNone(sparse.normalized_score)
        # Below the normalization floor the comparison falls back to the
        # effective score; missing relative evidence then routes ETH to the
        # fail-defensive HOLD_ONLY preserve state, never a manufactured exit.
        self.assertEqual(
            eth_core_eligibility(sparse, policy, current_weight=0.10),
            "HOLD_ONLY",
        )

    def test_satellite_and_eth_gates_share_one_space(self):
        # The same reading must not enter a satellite on the normalized
        # space while ETH keeps comparing its effective score: both gate
        # families read the assessment's normalized_score field.
        scored = _reliability_degraded(
            "ETH", 70.0, 0.8, asset_type="core",
            relative_strength_vs_btc="OUTPERFORM",
        )
        self.assertGreater(scored.normalized_score, scored.weighted_score)
        self.assertEqual(
            eth_core_eligibility(scored, load_policy(), current_weight=0.10),
            "ELIGIBLE_INCREASE",
        )


if __name__ == "__main__":
    unittest.main()
