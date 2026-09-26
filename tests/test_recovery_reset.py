"""Recovery reset rules (Strategy V2.1 Phase B).

A new portfolio low, market-regime deterioration, a severe systemic event, or
critical chain liveness must knock the book out of the recovery ladder and
back onto the drawdown ladder; a continuous decline must never re-risk early.
"""

import json
import unittest

from crypto_portfolio.engine.risk_recovery import (
    RecoveryState,
    advance_emergency_recovery,
)
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy():
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    return policy_from_mapping(data)


def _advance(state, *, drawdown, regime="NORMAL", vol=0.10, **kwargs):
    return advance_emergency_recovery(
        state, portfolio_drawdown=drawdown, market_only_regime=regime,
        portfolio_volatility=vol, **kwargs,
    )


class ContinuousDeclineTests(unittest.TestCase):
    def test_case_a_persistent_decline_never_re_risks(self):
        policy = _policy()
        state = RecoveryState()
        seen = set()
        for step in range(1, 26):
            state, block = _advance(
                state, drawdown=-0.01 * step, regime="DEFENSIVE", vol=0.40,
                policy=policy,
            )
            seen.add(block["state"])
        self.assertNotIn("RECOVERY_1", seen)
        self.assertNotIn("RECOVERY_2", seen)
        self.assertEqual(block["state"], "BREACH")
        self.assertAlmostEqual(block["risky_cap"], 0.25)


class ResetTriggerTests(unittest.TestCase):
    def setUp(self):
        self.policy = _policy()

    def _ladder_base(self, days=7):
        state = RecoveryState()
        for _ in range(days):
            state, block = _advance(state, drawdown=-0.18, policy=self.policy)
        return state, block

    def test_case_c_new_low_during_recovery_resets(self):
        policy = self.policy
        state, block = self._ladder_base()
        self.assertEqual(block["state"], "RECOVERY_1")
        state, block = _advance(state, drawdown=-0.19, policy=policy)
        self.assertEqual(block["state"], "BREACH")
        self.assertEqual(block["reviews_since_new_low"], 0)
        # The restarted clock needs the full stage_1_reviews again.
        for _ in range(4):
            state, block = _advance(state, drawdown=-0.19, policy=policy)
        self.assertEqual(block["state"], "BREACH")
        state, block = _advance(state, drawdown=-0.19, policy=policy)
        self.assertEqual(block["state"], "RECOVERY_1")

    def test_market_deterioration_resets_a_recovery_stage(self):
        state, block = self._ladder_base()
        self.assertEqual(block["state"], "RECOVERY_1")
        state, block = _advance(state, drawdown=-0.18, regime="DEFENSIVE", policy=self.policy)
        self.assertEqual(block["state"], "BREACH")
        self.assertEqual(block["market_normal_streak"], 0)

    def test_severe_systemic_event_resets_a_recovery_stage(self):
        state, block = self._ladder_base()
        state, block = _advance(
            state, drawdown=-0.18, systemic_event_severe=True, policy=self.policy
        )
        self.assertEqual(block["state"], "BREACH")

    def test_critical_chain_liveness_resets_a_recovery_stage(self):
        state, block = self._ladder_base()
        state, block = _advance(
            state, drawdown=-0.18,
            chain_liveness={"SOL": {"status": "HALTED"}},
            policy=self.policy,
        )
        self.assertEqual(block["state"], "BREACH")

    def test_degraded_liveness_is_not_critical(self):
        state, block = self._ladder_base()
        state, block = _advance(
            state, drawdown=-0.18,
            chain_liveness={"SOL": {"status": "DEGRADED"}},
            policy=self.policy,
        )
        self.assertEqual(block["state"], "RECOVERY_1")

    def test_repeated_identical_low_is_not_a_new_low(self):
        state, block = self._ladder_base()
        clock = block["reviews_since_new_low"]
        state, block = _advance(state, drawdown=-0.18, policy=self.policy)
        self.assertEqual(block["reviews_since_new_low"], clock + 1)
        self.assertEqual(block["state"], "RECOVERY_1")

    def test_no_reisk_loop_after_release(self):
        state = RecoveryState()
        states = []
        for _ in range(40):
            state, block = _advance(state, drawdown=-0.18, policy=self.policy)
            states.append(block["state"])
        self.assertIn("NORMAL", states)
        # After the first release there is no oscillation back into BREACH
        # absent a new low, deterioration, or severe event.
        first_release = states.index("NORMAL")
        self.assertEqual(set(states[first_release:]), {"NORMAL"})


if __name__ == "__main__":
    unittest.main()
