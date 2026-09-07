import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.core_eligibility import eth_core_eligibility


class EthCoreAllocationTests(unittest.TestCase):
    def assessment(self, score=80, confidence="HIGH", relative=70, **extra):
        return {
            "weighted_score": score,
            "confidence": confidence,
            "relative_strength_vs_btc": relative,
            **extra,
        }

    def test_core_gate_states(self):
        self.assertEqual(eth_core_eligibility(self.assessment()), "ELIGIBLE_INCREASE")
        self.assertEqual(eth_core_eligibility(self.assessment(confidence="LOW")), "HOLD_ONLY")
        self.assertEqual(eth_core_eligibility(self.assessment(score=40)), "REDUCE")
        self.assertEqual(eth_core_eligibility(self.assessment(relative=None)), "HOLD_ONLY")
        self.assertEqual(eth_core_eligibility(self.assessment(relative=20)), "REDUCE")
        self.assertEqual(eth_core_eligibility(self.assessment(event_risk={"state": "SEVERE"})), "INELIGIBLE")
        self.assertEqual(eth_core_eligibility(self.assessment(), chain_liveness="HALTED"), "INELIGIBLE")

    def test_score_confidence_relative_and_event_are_monotonic(self):
        def target(score=80, confidence="HIGH", relative=70, event_risk=None):
            assessment = self.assessment(score, confidence, relative)
            if event_risk:
                assessment["event_risk"] = {"state": event_risk}
            result = build_target_allocation(
                assessments={"BTC": {"weighted_score": 80, "confidence": "HIGH"}, "ETH": assessment}
            )
            return result.target_weights.get("ETH", 0)

        self.assertGreaterEqual(target(score=80), target(score=60))
        self.assertGreaterEqual(target(confidence="HIGH"), target(confidence="MEDIUM"))
        self.assertGreaterEqual(target(confidence="MEDIUM"), target(confidence="LOW"))
        self.assertGreaterEqual(target(relative=70), target(relative=40))
        self.assertGreaterEqual(target(event_risk="NORMAL"), target(event_risk="HIGH"))
        self.assertGreaterEqual(target(event_risk="HIGH"), target(event_risk="SEVERE"))

    def test_hold_only_does_not_increase_existing_eth(self):
        result = build_target_allocation(
            current_weights={"BTC": 0.6, "ETH": 0.1, "USDT": 0.3},
            assessments={"BTC": {"weighted_score": 80, "confidence": "HIGH"}, "ETH": self.assessment(relative=None)},
        )
        self.assertLessEqual(result.target_weights.get("ETH", 0), 0.1 + 1e-9)

    def test_allocation_accepts_chain_liveness_gate(self):
        result = build_target_allocation(
            assessments={"BTC": {"weighted_score": 80, "confidence": "HIGH"}, "ETH": self.assessment()},
            chain_liveness={"ETH": "HALTED"},
        )
        self.assertEqual(result.target_weights.get("ETH", 0), 0)

    def test_equal_quality_uses_configured_anchor_when_caps_allow(self):
        result = build_target_allocation(
            assessments={
                "BTC": {"weighted_score": 50, "confidence": "HIGH"},
                "ETH": self.assessment(score=55, confidence="HIGH", relative=50),
            },
        )
        self.assertGreater(result.target_weights.get("BTC", 0), result.target_weights.get("ETH", 0))
        self.assertLessEqual(result.target_weights.get("ETH", 0), 0.4)

    def test_both_core_assets_blocked_leave_residual_stable(self):
        result = build_target_allocation(
            assessments={
                "BTC": {"weighted_score": 80, "confidence": "LOW"},
                "ETH": self.assessment(confidence="LOW", relative=None),
            },
        )
        self.assertEqual(result.target_weights.get("BTC", 0), 0)
        self.assertEqual(result.target_weights.get("ETH", 0), 0)
        self.assertGreaterEqual(result.target_weights.get("USDT", 0), 0.9)


if __name__ == "__main__":
    unittest.main()
