"""Low-evidence contract (Strategy V2 Phase 2).

Coverage limits what new risk is allowed; it never independently forces a
REDUCE of an existing position.
"""

import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.scoring import low_evidence_contract, score_assessment
from crypto_portfolio.models.evidence import AssetAssessment
from crypto_portfolio.models.policy import load_policy


class LowEvidenceContractTests(unittest.TestCase):
    def test_high_coverage_is_actionable(self):
        result = low_evidence_contract(
            coverage=0.95, critical_data_complete=True, policy=load_policy()
        )
        self.assertEqual(result["evidence_class"], "ACTIONABLE")

    def test_medium_coverage_is_limited(self):
        result = low_evidence_contract(
            coverage=0.75, critical_data_complete=True, policy=load_policy()
        )
        self.assertEqual(result["evidence_class"], "LIMITED")

    def test_below_investable_or_incomplete_is_not_actionable(self):
        self.assertEqual(
            low_evidence_contract(
                coverage=0.5, critical_data_complete=True, policy=load_policy()
            )["evidence_class"],
            "NOT_ACTIONABLE",
        )
        self.assertEqual(
            low_evidence_contract(
                coverage=0.95, critical_data_complete=False, policy=load_policy()
            )["evidence_class"],
            "NOT_ACTIONABLE",
        )

    def test_no_class_ever_forces_a_reduce(self):
        for coverage in (0.0, 0.3, 0.65, 0.8, 1.0):
            result = low_evidence_contract(
                coverage=coverage, critical_data_complete=coverage >= 0.6,
                policy=load_policy(),
            )
            self.assertFalse(result["may_force_reduce"])


class CoverageDeclineNeverForcesReduceTests(unittest.TestCase):
    def _satellite_assessment(self, factors):
        return score_assessment(AssetAssessment(
            symbol="SOL",
            factor_scores=factors,
            asset_type="satellite",
            relative_strength_vs_btc="OUTPERFORM",
        ))[0].as_dict()

    def test_held_satellite_with_collapsed_coverage_is_preserved_not_exited(self):
        before = self._satellite_assessment(
            {"trend": 80.0, "valuation": 75.0, "fundamentals": 70.0,
             "onchain": 65.0, "capital_flows": 60.0, "relative_strength_btc": 72.0}
        )
        after = self._satellite_assessment({"trend": 80.0})
        policy = load_policy()
        held = {"SOL": 0.10, "USDT": 0.90}
        strong = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": before},
            current_weights=held,
        )
        degraded = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": after},
            current_weights=held,
        )
        # The strong-evidence review produces a real strategic target; the
        # collapsed-coverage review never manufactures an exit — the position
        # rides the preserve bucket with the same held weight.
        self.assertGreater(strong.target_weights.get("SOL", 0.0), 0.0)
        self.assertAlmostEqual(degraded.target_weights.get("SOL", 0.0), 0.10, places=9)
        allowance = degraded.deployment_allowances["SOL"]
        self.assertEqual(allowance["eligibility_state"], "HOLD_OR_REDUCE")
        self.assertEqual(allowance["evidence_class"], "NOT_ACTIONABLE")
        self.assertEqual(allowance["deployment_factor"], 0.0)

    def test_unheld_low_coverage_satellite_gets_no_target(self):
        assessment = self._satellite_assessment({"trend": 80.0})
        result = build_target_allocation(
            policy=load_policy(), regime="NORMAL", assessments={"SOL": assessment},
            current_weights={"USDT": 1.0},
        )
        self.assertEqual(result.target_weights.get("SOL", 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
