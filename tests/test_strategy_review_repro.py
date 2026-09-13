"""Synthetic regressions from the 2026-09-13 strategy review (plan.md).

Every case uses fabricated inputs only.  Assertions marked
``PENDING_POLICY_DECISION`` document current behavior that may only change
after the matching phase-8 decision; the other assertions capture the
pre-fix defect and are flipped when the corresponding phase lands:

- F2 missing-data exits            -> phase 4
- F5 relative-strength unit guess  -> phase 4
- A2 confidence label vs numeric   -> phase 2
- A3 REDUCE with NO_TRADE scope    -> phase 3
- F4 double deployment cap         -> phase 5
- F6 post-action stable shortfall  -> phase 6
- F1 satellite entry cliff         -> PENDING_POLICY_DECISION (8A)
- F3 core temporary-cap targets    -> PENDING_POLICY_DECISION (8B)
- F7 regime notch per review       -> PENDING_POLICY_DECISION (8D)
"""

import unittest

from crypto_portfolio.engine.allocation import build_target_allocation, satellite_eligibility
from crypto_portfolio.engine.confidence import DecisionScope, calculate_decision_confidence
from crypto_portfolio.engine.core_eligibility import relative_strength_score
from crypto_portfolio.engine.decision_packet import build_decision_review_packet
from crypto_portfolio.engine.rebalance import recommend_rebalance
from crypto_portfolio.engine.regime import RegimeInputs, determine_regime
from crypto_portfolio.engine.scoring import score_factors

CORE = {
    "BTC": {"weighted_score": 80, "confidence": "HIGH"},
    "ETH": {"weighted_score": 80, "confidence": "HIGH", "relative_strength_vs_btc": 70},
}
CURRENT = {"BTC": 0.5, "ETH": 0.2, "USDT": 0.2, "SOL": 0.1}
ALL_FACTORS = {
    "trend": 80, "valuation": 80, "fundamentals": 80,
    "onchain": 80, "capital_flows": 80, "relative_strength_btc": 80,
}


def _sol_target(score: float) -> float:
    result = build_target_allocation(
        regime="NORMAL",
        assessments={
            **CORE,
            "SOL": {"weighted_score": score, "confidence": "HIGH", "relative_strength_vs_btc": "OUTPERFORM"},
        },
        current_weights=CURRENT,
    )
    return result.target_weights.get("SOL", 0.0)


class F1SatelliteEntryCliffTests(unittest.TestCase):
    """Score improving across the entry boundary must not cut a held target."""

    def test_documented_cliff_pending_8a(self):
        # Current contract: 62-66 hold the current 10% (HOLD_ONLY band),
        # 67 becomes ELIGIBLE with score_strength 0 and the target drops to
        # zero.  Monotone repair is gated on the 8A hysteresis decision.
        self.assertAlmostEqual(_sol_target(61), 0.05)
        self.assertAlmostEqual(_sol_target(62), 0.10)
        self.assertAlmostEqual(_sol_target(66), 0.10)
        self.assertAlmostEqual(_sol_target(67), 0.0)
        self.assertAlmostEqual(_sol_target(85), 0.25)


class F2MissingDataExitTests(unittest.TestCase):
    def test_missing_factors_currently_ineligible(self):
        assessment = {
            "weighted_score": 50,
            "confidence": "LOW",
            "critical_data_complete": False,
            "relative_strength_vs_btc": None,
        }
        self.assertEqual(satellite_eligibility(assessment, current_weight=0.1), "INELIGIBLE")

    def test_confirmed_negative_evidence_still_ineligible(self):
        assessment = {
            "weighted_score": 50,
            "confidence": "HIGH",
            "critical_data_complete": True,
            "relative_strength_vs_btc": "UNDERPERFORM",
        }
        self.assertEqual(satellite_eligibility(assessment, current_weight=0.1), "INELIGIBLE")


class F3CoreTemporaryCapTests(unittest.TestCase):
    def test_documented_core_target_reduction_pending_8b(self):
        def eth_target(eth: dict) -> float:
            result = build_target_allocation(
                assessments={"BTC": CORE["BTC"], "ETH": eth},
                current_weights={"BTC": 0.5, "ETH": 0.255, "USDT": 0.245},
            )
            return result.target_weights.get("ETH", 0.0)

        baseline = eth_target(CORE["ETH"])
        low_confidence = eth_target({**CORE["ETH"], "confidence": "LOW"})
        high_event = eth_target({**CORE["ETH"], "event_risk": {"state": "HIGH"}})
        self.assertAlmostEqual(baseline, 0.255)
        self.assertAlmostEqual(low_confidence, 0.082258, places=5)
        self.assertAlmostEqual(high_event, 0.15)


class F4DoubleDeploymentCapTests(unittest.TestCase):
    def _result(self, **kwargs):
        allocation = build_target_allocation(
            regime="NORMAL",
            assessments={
                **CORE,
                "SOL": {"weighted_score": 85, "confidence": "HIGH", "relative_strength_vs_btc": "OUTPERFORM"},
            },
            current_weights=CURRENT,
            decision_confidence={"score": 0.7},
        )
        return recommend_rebalance(
            CURRENT, dict(allocation.target_weights), 10000.0,
            decision_confidence={"score": 0.7}, **kwargs,
        )

    def test_same_cap_applied_twice_currently(self):
        once = self._result()
        twice = self._result(deployment_caps={"SOL": 0.7})
        amount_once = next(a.amount_usd for a in once.actions if a.symbol == "SOL")
        amount_twice = next(a.amount_usd for a in twice.actions if a.symbol == "SOL")
        self.assertAlmostEqual(amount_once, 910.0)
        self.assertAlmostEqual(amount_twice, 637.0)


class F5RelativeStrengthUnitTests(unittest.TestCase):
    def test_guess_scaling_currently_applied(self):
        self.assertEqual(relative_strength_score({"relative_strength_vs_btc": 0.5}), 50.0)
        self.assertEqual(relative_strength_score({"relative_strength_vs_btc": 1}), 100.0)
        self.assertEqual(relative_strength_score({"relative_strength_vs_btc": 1.01}), 1.01)

    def test_satellite_numeric_sign_interpretation(self):
        # Numeric values are currently read as excess-return signs.
        self.assertEqual(
            satellite_eligibility(
                {"weighted_score": 85, "confidence": "HIGH", "critical_data_complete": True, "relative_strength_vs_btc": -0.2}
            ),
            "INELIGIBLE",
        )


class F6PostActionConstraintTests(unittest.TestCase):
    def test_stable_shortfall_swallowed_by_hold_band(self):
        result = recommend_rebalance(
            {"BTC": 0.50, "ETH": 0.36, "USDT": 0.14},
            {"BTC": 0.50, "ETH": 0.35, "USDT": 0.15},
            10000.0,
        )
        self.assertEqual(result.decision, "NO_TRADE")
        self.assertTrue(all(action.action == "HOLD" for action in result.actions))


class F7RegimeNotchTests(unittest.TestCase):
    def test_same_evidence_advances_one_notch_per_review_pending_8d(self):
        evidence = RegimeInputs(btc_trend="BEARISH", volatility_state="HIGH", flow_state="OUTFLOW")
        first = determine_regime(evidence, previous="NORMAL")
        second = determine_regime(evidence, previous=first)
        self.assertEqual(first.regime, "DEFENSIVE")
        self.assertEqual(second.regime, "CAPITAL_PRESERVATION")

    def test_drawdown_floor_is_never_delayed(self):
        evidence = RegimeInputs(btc_trend="BULLISH", portfolio_drawdown_band=-0.10)
        capped = determine_regime(evidence, previous="NORMAL")
        self.assertEqual(capped.regime, "DEFENSIVE")
        breach = determine_regime(
            RegimeInputs(btc_trend="BULLISH", portfolio_drawdown_band=-0.16), previous="NORMAL"
        )
        self.assertEqual(breach.regime, "CAPITAL_PRESERVATION")


class A2ConfidenceLabelTests(unittest.TestCase):
    def test_supplied_low_label_overrides_numeric_high(self):
        result = score_factors(ALL_FACTORS, confidence="LOW", symbol="SOL")
        self.assertEqual(result.confidence, "LOW")
        self.assertEqual(result.data_confidence_band, "HIGH")
        self.assertEqual(result.data_confidence_score, 1.0)

    def test_no_label_matches_numeric_band(self):
        result = score_factors(ALL_FACTORS, symbol="SOL")
        self.assertEqual(result.confidence, "HIGH")


class A3ScopeMismatchTests(unittest.TestCase):
    def _packet(self, scope_action="NO_TRADE"):
        decision_confidence = calculate_decision_confidence(
            {
                "portfolio_data": 0.9,
                "regime_confidence": 0.9,
                "asset_evidence": {"assets": {"BTC": 0.9}, "weights": {"BTC": 1.0}},
                "portfolio_accounting": 1.0,
                "signal_agreement": 1.0,
            },
            scope=DecisionScope(scope_action, ("BTC",), {"BTC": 0.5}),
        )
        return build_decision_review_packet(
            review_type="SNAPSHOT_REVIEW",
            market_regime="NORMAL",
            current_weights={"BTC": 0.5, "ETH": 0.2, "AAVE": 0.1, "USDT": 0.2},
            target_weights={"BTC": 0.5, "ETH": 0.2, "AAVE": 0.0, "USDT": 0.3},
            assessments={"AAVE": {"weighted_score": 60, "confidence": "MEDIUM"}},
            actions=[{
                "symbol": "AAVE", "action": "REDUCE", "amount_usd": 1000.0,
                "current_weight": 0.1, "target_weight": 0.0,
            }],
            decision_confidence=decision_confidence,
        )

    def test_reduce_action_with_no_trade_scope_currently_accepted(self):
        packet = self._packet()
        aave = next(item for item in packet.assets if item.symbol == "AAVE")
        self.assertEqual(aave.action, "REDUCE")
        self.assertEqual(packet.decision_confidence.scope["action"], "NO_TRADE")


if __name__ == "__main__":
    unittest.main()
