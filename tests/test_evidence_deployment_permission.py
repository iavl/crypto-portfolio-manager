"""Evidence deployment permission (Strategy V2.1 Phase C).

Data confidence and evidence permission are distinct concepts: the deployment
factor is keyed on the low-evidence contract classes (ACTIONABLE / LIMITED /
NOT_ACTIONABLE), so LIMITED coverage halves deployment instead of silently
inheriting the LOW confidence band's zero.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _factors():
    return {
        "trend": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
        "relative_strength_btc": {"score": 80, "availability": "AVAILABLE", "reliability": 1.0},
        "capital_flows": {"score": 75, "availability": "AVAILABLE", "reliability": 1.0},
        "valuation": {"score": 70, "availability": "AVAILABLE", "reliability": 1.0},
        "fundamentals": {"score": 72, "availability": "AVAILABLE", "reliability": 1.0},
        "onchain": {"score": 68, "availability": "AVAILABLE", "reliability": 1.0},
    }


def _sol(coverage: float, confidence: str = "HIGH", **fields):
    return {
        "factor_scores": _factors(),
        "confidence": confidence,
        "critical_data_complete": True,
        "score_coverage": coverage,
        "relative_strength_vs_btc": "OUTPERFORM",
        **fields,
    }


class EvidenceDeploymentPermissionTests(unittest.TestCase):
    def test_case_d_limited_evidence_halves_deployment(self):
        policy = load_policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": _sol(0.75)},
        )
        allowance = result.deployment_allowances["SOL"]
        self.assertEqual(allowance["evidence_class"], "LIMITED")
        self.assertEqual(allowance["eligibility_state"], "ELIGIBLE_INCREASE")
        self.assertAlmostEqual(allowance["evidence_deployment_factor"], 0.5)
        self.assertAlmostEqual(
            allowance["max_immediate_increase_weight"],
            allowance["strategic_target_weight"] * 0.5,
        )
        self.assertGreater(allowance["max_immediate_increase_weight"], 0)

    def test_not_actionable_evidence_blocks_new_risk_entirely(self):
        policy = load_policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": _sol(0.5)},
        )
        allowance = result.deployment_allowances["SOL"]
        self.assertEqual(allowance["evidence_class"], "NOT_ACTIONABLE")
        self.assertEqual(allowance["conviction_state"], "NO_NEW_RISK")
        self.assertEqual(allowance["deployment_factor"], 0)
        self.assertEqual(result.target_weights.get("SOL", 0), 0)

    def test_low_confidence_band_no_longer_zeroes_deployment(self):
        policy = load_policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"SOL": _sol(0.95, confidence="LOW")},
        )
        allowance = result.deployment_allowances["SOL"]
        self.assertEqual(allowance["asset_confidence"], "LOW")
        self.assertEqual(allowance["evidence_class"], "ACTIONABLE")
        self.assertGreater(allowance["deployment_factor"], 0)

    def test_actionable_full_coverage_deploys_fully(self):
        policy = load_policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": _sol(0.95)},
        )
        allowance = result.deployment_allowances["SOL"]
        self.assertAlmostEqual(allowance["evidence_deployment_factor"], 1.0)

    def test_missing_evidence_never_forces_a_reduce(self):
        # Case E: a held satellite with not-actionable evidence keeps its
        # position; the permission factor blocks new risk only.
        policy = load_policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"SOL": _sol(0.5)},
            current_weights={"BTC": 0.5, "ETH": 0.3, "SOL": 0.06, "USDT": 0.14},
        )
        self.assertAlmostEqual(result.target_weights["SOL"], 0.06)
        self.assertTrue(result.deployment_allowances["SOL"].get("preserve_existing"))


class PermissionConfigTests(unittest.TestCase):
    def test_factors_are_validated_by_the_policy_model(self):
        data = json.loads(json.dumps(load_policy().as_dict()))
        del data["execution"]["evidence_deployment_factor"]["LIMITED"]
        with self.assertRaisesRegex(Exception, "evidence_deployment_factor"):
            policy_from_mapping(data)

        data = json.loads(json.dumps(load_policy().as_dict()))
        data["execution"]["evidence_deployment_factor"]["NOT_ACTIONABLE"] = 0.9
        with self.assertRaisesRegex(Exception, "monotonic"):
            policy_from_mapping(data)

    def test_preregistered_factors_match_the_plan(self):
        factors = load_policy().execution["evidence_deployment_factor"]
        self.assertEqual(factors["ACTIONABLE"], 1.0)
        self.assertEqual(factors["LIMITED"], 0.5)
        self.assertEqual(factors["NOT_ACTIONABLE"], 0.0)


if __name__ == "__main__":
    unittest.main()
