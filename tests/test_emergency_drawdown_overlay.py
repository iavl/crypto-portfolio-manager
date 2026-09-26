"""Emergency drawdown overlay bands (Strategy V2 Phase 1)."""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs, emergency_drawdown_state
from crypto_portfolio.engine.risk import (
    emergency_drawdown_overlay_floor,
    risk_overlay_floor,
    run_risk_gate,
)
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _vol_policy():
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    return policy_from_mapping(data)


def _bands(**overrides):
    values = {
        "caution_fraction": 0.6,
        "emergency_fraction": 0.8,
        "breach_fraction": 1.0,
        "caution_risky_cap": 0.9,
        "emergency_risky_cap": 0.6,
        "breach_risky_cap": 0.25,
    }
    values.update(overrides)
    return values


def _inputs():
    return PortfolioRiskInputs(
        asset_volatility={"BTC": 0.1, "ETH": 0.12},
        correlations={"BTC": {"ETH": 0.5}, "ETH": {"BTC": 0.5}},
    )


_ASSESSMENTS = {
    "BTC": {"weighted_score": 70, "normalized_score": 70, "confidence": "HIGH"},
    "ETH": {
        "weighted_score": 60, "normalized_score": 60, "confidence": "HIGH",
        "relative_strength_vs_btc": "OUTPERFORM",
    },
}


class EmergencyDrawdownStateTests(unittest.TestCase):
    def test_band_boundaries(self):
        budget = 0.15
        cases = [
            (-0.01, "NORMAL", 1.0),
            (-0.089, "NORMAL", 1.0),          # 59.3% consumed: below caution
            (-0.09, "CAUTION", 0.9),          # exactly 60%: caution begins
            (-0.10, "CAUTION", 0.9),
            (-0.119, "CAUTION", 0.9),
            (-0.12, "EMERGENCY", 0.6),        # exactly 80%: emergency begins
            (-0.149, "EMERGENCY", 0.6),
            (-0.15, "EMERGENCY", 0.6),        # at budget: emergency, not breach
            (-0.1501, "BREACH", 0.25),        # strictly beyond budget
        ]
        for drawdown, state, cap in cases:
            result = emergency_drawdown_state(drawdown, budget=budget, **_bands())
            self.assertEqual(result["state"], state, drawdown)
            self.assertAlmostEqual(result["risky_cap"], cap, places=9, msg=str(drawdown))

    def test_worsening_drawdown_never_loosens_the_cap(self):
        caps = [
            emergency_drawdown_state(-fraction * 0.15, budget=0.15, **_bands())["risky_cap"]
            for fraction in (i / 100.0 for i in range(0, 101, 5))
        ]
        self.assertEqual(caps, sorted(caps, reverse=True))

    def test_bands_align_with_the_mandatory_regime_floors(self):
        # references/risk-model.md: <= -0.60D is at least DEFENSIVE and
        # <= -0.80D is CAPITAL_PRESERVATION; the emergency caution and
        # emergency stages must sit exactly on those mandatory floors.
        resolved = load_policy()
        engine = resolved.risk_engine["emergency_overlay"]
        self.assertAlmostEqual(engine["caution_fraction"], 0.6)
        self.assertAlmostEqual(engine["emergency_fraction"], 0.8)
        self.assertAlmostEqual(engine["breach_fraction"], 1.0)

    def test_invalid_band_configurations_are_rejected(self):
        with self.assertRaises(ValueError):
            emergency_drawdown_state(-0.1, budget=0.15, **_bands(emergency_fraction=0.5))
        with self.assertRaises(ValueError):
            emergency_drawdown_state(
                -0.1, budget=0.15, **_bands(emergency_risky_cap=0.95)
            )
        with self.assertRaises(ValueError):
            emergency_drawdown_state(0.05, budget=0.15, **_bands())


class EmergencyOverlayFloorTests(unittest.TestCase):
    def test_normal_drawdown_leaves_normal_sizing_alone(self):
        floor, reason, state = emergency_drawdown_overlay_floor(_vol_policy(), -0.05)
        self.assertAlmostEqual(floor, 0.0)
        self.assertIsNone(reason)
        self.assertEqual(state["state"], "NORMAL")

    def test_emergency_stage_caps_risky_weight(self):
        floor, reason, state = emergency_drawdown_overlay_floor(_vol_policy(), -0.13)
        self.assertAlmostEqual(floor, 0.4)
        self.assertEqual(state["state"], "EMERGENCY")
        self.assertIn("EMERGENCY", reason)

    def test_recovery_streak_re_risks_past_the_brake(self):
        # Confirmed market recovery lifts the risky allowance to the shared
        # recovery floor even while the emergency state says cut deeper. The
        # default breach cap equals the recovery floor, so this uses a tighter
        # breach cap to show the shared re-risk path still applies.
        data = json.loads(json.dumps(load_policy().as_dict()))
        data["risk_engine"]["mode"] = "volatility_budget"
        data["risk_engine"]["emergency_overlay"]["breach_risky_cap"] = 0.1
        policy = policy_from_mapping(data)
        floor, reason, state = emergency_drawdown_overlay_floor(
            policy, -0.155, market_recovery_streak=5
        )
        self.assertEqual(state["state"], "BREACH")
        recovery_floor = policy.drawdown_budget_overlay["recovery_risky_floor"]
        self.assertAlmostEqual(floor, 1.0 - recovery_floor)
        self.assertTrue(state.get("recovery_floor_applied"))

    def test_mode_dispatch_reports_the_active_mechanism(self):
        legacy_policy = load_policy()
        floor_legacy, _, state_legacy = risk_overlay_floor(legacy_policy, -0.09)
        floor_vol, _, state_vol = risk_overlay_floor(_vol_policy(), -0.09)
        self.assertEqual(state_legacy["state"], "LEGACY_LADDER")
        self.assertEqual(state_vol["state"], "CAUTION")
        # At 60% of budget consumed the legacy ladder already holds the book
        # at 40% risky; the staged brake is at 90%. Normal sizing no longer
        # rides the drawdown ladder in volatility_budget mode.
        self.assertGreater(floor_legacy, floor_vol)


class VolatilityModeGateTests(unittest.TestCase):
    def test_rebalance_validates_against_the_emergency_floor_not_the_ladder(self):
        # Regression: at ~50% of budget consumed the legacy ladder demands a
        # 50% stable sleeve, but the staged brake is still NORMAL there. The
        # rebalance engine must validate against the mode's own floor, or
        # every volatility-budget replay would abort with
        # STABLECOIN_FLOOR_UNRESOLVED as soon as any drawdown exists.
        from crypto_portfolio.engine.rebalance import recommend_rebalance

        policy = _vol_policy()
        allocation = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_ASSESSMENTS,
            portfolio_drawdown=-0.0758, risk_inputs=_inputs(),
        )
        result = recommend_rebalance(
            {}, dict(allocation.target_weights), 100000.0,
            policy=policy, regime="NORMAL", portfolio_drawdown=-0.0758,
        )
        self.assertIsNotNone(result.post_action_projection)

    def test_gate_accepts_emergency_floors_in_budget_mode(self):
        policy = _vol_policy()
        allocation = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_ASSESSMENTS,
            portfolio_drawdown=-0.13, risk_inputs=_inputs(),
        )
        gate = run_risk_gate(
            allocation, policy=policy, regime="NORMAL", current_drawdown=-0.13
        )
        self.assertTrue(gate.ok, [item.as_dict() for item in gate.errors])
        stable = sum(
            allocation.target_weights.get(symbol, 0.0)
            for symbol in policy.stable_symbols
        )
        self.assertGreaterEqual(stable, 1.0 - 0.6 - 1e-9)

    def test_budget_mode_risky_sleeve_respects_emergency_cap(self):
        policy = _vol_policy()
        calm = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_ASSESSMENTS,
            portfolio_drawdown=-0.13, risk_inputs=_inputs(),
        )
        risky = 1.0 - calm.stable_sleeve_target
        self.assertLessEqual(risky, 0.6 + 1e-9)
        self.assertEqual(
            calm.risk_engine["emergency_overlay_state"]["state"], "EMERGENCY"
        )
        self.assertEqual(calm.risk_engine["binding_risk_constraint"], "emergency_overlay")

    def test_breach_beyond_budget_is_still_a_hard_error(self):
        policy = _vol_policy()
        allocation = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_ASSESSMENTS,
            portfolio_drawdown=-0.16, risk_inputs=_inputs(),
        )
        gate = run_risk_gate(
            allocation, policy=policy, regime="NORMAL", current_drawdown=-0.16
        )
        codes = {item.code for item in gate.violations}
        self.assertIn("DRAWDOWN_BREACH", codes)
        self.assertFalse(gate.ok)


if __name__ == "__main__":
    unittest.main()
