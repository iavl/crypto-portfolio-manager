"""Emergency recovery FSM core behavior (Strategy V2.1 Phase B).

The staged ladder BREACH -> RECOVERY_1 -> RECOVERY_2 -> release advances on
market state and the since-trough clock, never on the portfolio's own NAV
recovery, and the persisted state round-trips so replay and production share
one contract.
"""

import json
import unittest

from crypto_portfolio.engine.risk_recovery import (
    RecoveryState,
    advance_emergency_recovery,
)
from crypto_portfolio.engine.risk import emergency_drawdown_overlay_floor
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


class PersistenceContractTests(unittest.TestCase):
    def test_state_round_trips_through_its_mapping(self):
        state = RecoveryState(
            state="RECOVERY_1", reviews_in_state=3, market_normal_streak=7,
            reviews_since_new_low=9, last_portfolio_low=-0.18,
        )
        again = RecoveryState.from_mapping(state.as_dict())
        self.assertEqual(again, state)
        self.assertEqual(RecoveryState.from_mapping(None), RecoveryState())

    def test_invalid_state_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown emergency recovery state"):
            RecoveryState(state="RECOVERY_3")
        with self.assertRaisesRegex(ValueError, "last_portfolio_low"):
            RecoveryState(last_portfolio_low=0.05)
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            RecoveryState.from_mapping({"state": "NORMAL", "surprise": 1})

    def test_recovery_config_is_validated_by_the_policy_model(self):
        base = json.loads(json.dumps(load_policy().as_dict()))
        base["risk_engine"]["mode"] = "volatility_budget"
        caps = base["risk_engine"]["recovery"]
        caps["stage_1_risky_cap"] = 0.1  # below breach cap 0.25
        with self.assertRaisesRegex(Exception, "recovery"):
            policy_from_mapping(base)

        base = json.loads(json.dumps(load_policy().as_dict()))
        base["risk_engine"]["mode"] = "volatility_budget"
        base["risk_engine"]["recovery"]["release_reviews"] = 2  # below stage_2
        with self.assertRaisesRegex(Exception, "recovery"):
            policy_from_mapping(base)

        base = json.loads(json.dumps(load_policy().as_dict()))
        base["risk_engine"]["mode"] = "volatility_budget"
        base["risk_engine"]["recovery"]["max_portfolio_volatility"] = 0.9  # above max band
        with self.assertRaisesRegex(Exception, "recovery"):
            policy_from_mapping(base)


class StagedLadderTests(unittest.TestCase):
    def setUp(self):
        self.policy = _policy()

    def test_case_b_breach_recovers_in_stages_then_releases(self):
        state = RecoveryState()
        states = []
        for day in range(23):
            state, block = _advance(state, drawdown=-0.18, policy=self.policy)
            states.append(block["state"])
        self.assertEqual(states[0], "BREACH")
        self.assertEqual(states[5], "RECOVERY_1")   # 5 reviews after trough
        self.assertEqual(states[10], "RECOVERY_2")  # 10 reviews after trough
        self.assertEqual(states[20], "NORMAL")      # released at 20 reviews
        self.assertEqual(states[21], "NORMAL")      # and stays released

    def test_recovery_caps_are_the_preregistered_stages(self):
        state = RecoveryState()
        for _ in range(6):
            state, block = _advance(state, drawdown=-0.18, policy=self.policy)
        self.assertEqual(block["state"], "RECOVERY_1")
        self.assertAlmostEqual(block["risky_cap"], 0.40)
        for _ in range(5):
            state, block = _advance(state, drawdown=-0.18, policy=self.policy)
        self.assertEqual(block["state"], "RECOVERY_2")
        self.assertAlmostEqual(block["risky_cap"], 0.60)

    def test_healed_drawdown_relaxes_to_the_ladder(self):
        state = RecoveryState(
            state="RECOVERY_2", reviews_since_new_low=11,
            last_portfolio_low=-0.18,
        )
        # Drawdown healed into the CAUTION band: the emergency is over.
        state, block = _advance(state, drawdown=-0.11, policy=self.policy)
        self.assertEqual(block["state"], "CAUTION")
        self.assertAlmostEqual(block["risky_cap"], 0.90)

    def test_high_volatility_blocks_recovery_entry(self):
        state = RecoveryState()
        for _ in range(8):
            state, block = _advance(
                state, drawdown=-0.18, vol=0.40, policy=self.policy
            )
        self.assertEqual(block["state"], "BREACH")
        self.assertFalse(block["recovery_conditions"]["volatility_within_threshold"])

    def test_unknown_volatility_fails_closed(self):
        state = RecoveryState()
        for _ in range(8):
            state, block = _advance(state, drawdown=-0.18, vol=None, policy=self.policy)
        self.assertEqual(block["state"], "BREACH")
        self.assertFalse(block["recovery_conditions"]["volatility_within_threshold"])


class OverlayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.policy = _policy()

    def test_advanced_block_sets_the_floor_and_reports_the_state(self):
        state = RecoveryState()
        for _ in range(6):
            state, block = _advance(state, drawdown=-0.18, policy=self.policy)
        floor, reason, reported = emergency_drawdown_overlay_floor(
            self.policy, -0.18, recovery_state=block
        )
        self.assertAlmostEqual(floor, 0.60)
        self.assertEqual(reported["state"], "RECOVERY_1")
        self.assertIn("RECOVERY_1", reason)

    def test_bare_persisted_state_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "advance_emergency_recovery"):
            emergency_drawdown_overlay_floor(
                self.policy, -0.18, recovery_state=RecoveryState()
            )

    def test_advance_validates_its_inputs(self):
        with self.assertRaisesRegex(ValueError, "portfolio_drawdown"):
            _advance(RecoveryState(), drawdown=0.05, policy=self.policy)
        with self.assertRaisesRegex(ValueError, "market_only_regime"):
            _advance(RecoveryState(), drawdown=-0.1, regime="TANTRUM", policy=self.policy)


if __name__ == "__main__":
    unittest.main()
