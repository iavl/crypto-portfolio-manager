"""Drawdown budget policy semantics (Strategy V2.3 Phase 3).

``risk.drawdown_budget_mode`` makes the 15% number's meaning explicit:
HARD_TARGET binds ex-ante sizing through the stress-loss budget;
WARNING_BAND demotes it to a warning and REQUIRES a separately decided
``hard_stress_loss_limit`` (never derived from historical returns).
"""

import json
import unittest

from crypto_portfolio.models.policy import PolicyError, load_policy, policy_from_mapping


def _raw():
    return json.loads(json.dumps(load_policy().as_dict()))


class DrawdownBudgetModeTests(unittest.TestCase):
    def test_canonical_policy_declares_hard_target(self):
        policy = load_policy()
        self.assertEqual(policy.drawdown_budget_mode, "HARD_TARGET")
        self.assertIsNone(policy.hard_stress_loss_limit)
        self.assertTrue(policy.stress_loss_budget["enabled"])

    def test_unknown_mode_is_rejected(self):
        raw = _raw()
        raw["risk"]["drawdown_budget_mode"] = "SOFT"
        with self.assertRaises(PolicyError):
            policy_from_mapping(raw)

    def test_warning_band_requires_a_separate_hard_limit(self):
        raw = _raw()
        raw["risk"]["drawdown_budget_mode"] = "WARNING_BAND"
        with self.assertRaises(PolicyError):
            policy_from_mapping(raw)
        raw["risk"]["hard_stress_loss_limit"] = 0.30
        policy = policy_from_mapping(raw)
        self.assertEqual(policy.drawdown_budget_mode, "WARNING_BAND")
        self.assertEqual(policy.hard_stress_loss_limit, 0.30)

    def test_warning_band_limit_must_exceed_the_warning_level(self):
        raw = _raw()
        raw["risk"]["drawdown_budget_mode"] = "WARNING_BAND"
        raw["risk"]["hard_stress_loss_limit"] = 0.15
        with self.assertRaises(PolicyError):
            policy_from_mapping(raw)

    def test_hard_target_rejects_a_stray_hard_limit(self):
        raw = _raw()
        raw["risk"]["hard_stress_loss_limit"] = 0.30
        with self.assertRaises(PolicyError):
            policy_from_mapping(raw)

    def test_stress_switch_is_validated_and_round_trips(self):
        raw = _raw()
        raw["risk"]["stress_loss_budget"] = {"enabled": False}
        policy = policy_from_mapping(raw)
        self.assertFalse(policy.stress_loss_budget["enabled"])
        self.assertEqual(
            policy_from_mapping(policy.as_dict()).stress_loss_budget,
            {"enabled": False},
        )
        raw["risk"]["stress_loss_budget"] = {"enabled": "yes"}
        with self.assertRaises(PolicyError):
            policy_from_mapping(raw)

    def test_warning_band_round_trips_through_as_dict(self):
        raw = _raw()
        raw["risk"]["drawdown_budget_mode"] = "WARNING_BAND"
        raw["risk"]["hard_stress_loss_limit"] = 0.25
        policy = policy_from_mapping(raw)
        rebuilt = policy_from_mapping(policy.as_dict())
        self.assertEqual(rebuilt.drawdown_budget_mode, "WARNING_BAND")
        self.assertEqual(rebuilt.hard_stress_loss_limit, 0.25)


if __name__ == "__main__":
    unittest.main()
