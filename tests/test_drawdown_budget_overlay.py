"""Regression tests for the drawdown budget overlay and its recovery path."""

import copy
import json
import unittest
from pathlib import Path

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.feasibility import (
    DRAWDOWN_BUDGET_INFEASIBLE,
    check_policy_feasibility,
    drawdown_budget_feasibility,
)
from crypto_portfolio.engine.regime import RegimeInputs, market_only_regime
from crypto_portfolio.engine.rebalance import recommend_rebalance
from crypto_portfolio.engine.risk import (
    drawdown_budget_ladder_path,
    drawdown_budget_overlay_floor,
    run_risk_gate,
)
from crypto_portfolio.models.policy import PolicyError, load_policy, policy_from_mapping
from crypto_portfolio.research.stress import drawdown_budget_stress

CONFIG = Path(__file__).resolve().parents[1] / "config" / "policy.json"


def _overlay_disabled_policy():
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    raw["risk"]["drawdown_budget_overlay"]["enabled"] = False
    return policy_from_mapping(raw)


class OverlayPolicyParsingTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_canonical_block_parses(self):
        policy = policy_from_mapping(self.raw)
        self.assertEqual(
            dict(policy.drawdown_budget_overlay),
            {"enabled": True, "recovery_reviews": 5, "recovery_risky_floor": 0.25},
        )
        self.assertIn("drawdown_budget_overlay", policy.as_dict()["risk"])

    def _mutate(self, mutate):
        raw = copy.deepcopy(self.raw)
        mutate(raw["risk"]["drawdown_budget_overlay"])
        return raw

    def test_missing_block_is_rejected(self):
        raw = copy.deepcopy(self.raw)
        del raw["risk"]["drawdown_budget_overlay"]
        with self.assertRaises(PolicyError):
            policy_from_mapping(raw)

    def test_incomplete_and_unknown_fields_are_rejected(self):
        with self.assertRaises(PolicyError):
            policy_from_mapping(self._mutate(lambda o: o.pop("enabled")))
        with self.assertRaises(PolicyError):
            policy_from_mapping(self._mutate(lambda o: o.update({"extra": 1})))

    def test_field_types_are_validated(self):
        with self.assertRaises(PolicyError):
            policy_from_mapping(self._mutate(lambda o: o.update({"enabled": "yes"})))
        with self.assertRaises(PolicyError):
            policy_from_mapping(self._mutate(lambda o: o.update({"recovery_reviews": 0})))
        with self.assertRaises(PolicyError):
            policy_from_mapping(self._mutate(lambda o: o.update({"recovery_reviews": 2.5})))
        with self.assertRaises(PolicyError):
            policy_from_mapping(self._mutate(lambda o: o.update({"recovery_risky_floor": 1.0})))
        with self.assertRaises(PolicyError):
            policy_from_mapping(self._mutate(lambda o: o.update({"recovery_risky_floor": -0.1})))


class OverlayFloorTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()

    def test_floor_tracks_consumed_budget(self):
        for drawdown, expected in ((0.0, 0.0), (-0.03, 0.2), (-0.09, 0.6), (-0.12, 0.8), (-0.15, 1.0)):
            floor, reason = drawdown_budget_overlay_floor(self.policy, drawdown)
            self.assertAlmostEqual(floor, expected, places=12)
            self.assertEqual(reason is not None, drawdown < 0.0)

    def test_floor_is_monotone_in_worsening_drawdown(self):
        previous = -1.0
        for fraction in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0, 1.3):
            floor, _ = drawdown_budget_overlay_floor(self.policy, -fraction * 0.15)
            self.assertGreaterEqual(floor, previous - 1e-12)
            previous = floor

    def test_none_drawdown_and_disabled_overlay_are_inert(self):
        self.assertEqual(drawdown_budget_overlay_floor(self.policy, None), (0.0, None))
        disabled = _overlay_disabled_policy()
        self.assertEqual(drawdown_budget_overlay_floor(disabled, -0.30), (0.0, None))

    def test_recovery_streak_lifts_only_when_confirmed(self):
        _, short = drawdown_budget_overlay_floor(self.policy, -0.15, 4)
        floor_short, _ = drawdown_budget_overlay_floor(self.policy, -0.15, 4)
        self.assertAlmostEqual(floor_short, 1.0)
        floor_confirmed, reason = drawdown_budget_overlay_floor(self.policy, -0.15, 5)
        self.assertAlmostEqual(floor_confirmed, 0.75)
        self.assertIn("market recovery", reason)
        # A recovery floor below the ladder cap never loosens the floor.
        floor_mild, _ = drawdown_budget_overlay_floor(self.policy, -0.03, 5)
        self.assertAlmostEqual(floor_mild, 0.2)

    def test_worsening_never_lowers_the_floor_even_with_streak(self):
        milder, _ = drawdown_budget_overlay_floor(self.policy, -0.12, 5)
        deeper, _ = drawdown_budget_overlay_floor(self.policy, -0.15, 5)
        self.assertGreaterEqual(deeper, milder)

    def test_invalid_inputs_raise(self):
        for bad in (0.05, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                drawdown_budget_overlay_floor(self.policy, bad)
        with self.assertRaises(ValueError):
            drawdown_budget_overlay_floor(self.policy, -0.1, -1)
        with self.assertRaises(ValueError):
            drawdown_budget_overlay_floor(self.policy, -0.1, True)


class LadderPathTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()

    def test_severe_decline_stays_within_budget(self):
        path = drawdown_budget_ladder_path(
            self.policy, risky_sleeve_return=-0.45, steps=40, regime_risky_weight=0.85
        )
        tolerance = path["single_step_bound"] + 1e-9
        self.assertLessEqual(abs(path["worst_drawdown"]), path["budget"] + tolerance)

    def test_disabled_overlay_breaches_on_the_same_path(self):
        disabled = _overlay_disabled_policy()
        path = drawdown_budget_ladder_path(
            disabled, risky_sleeve_return=-0.45, steps=40, regime_risky_weight=0.85
        )
        self.assertGreater(abs(path["worst_drawdown"]), 2 * path["budget"])

    def test_recovery_heals_a_budget_limit_drawdown(self):
        path = drawdown_budget_ladder_path(
            self.policy, risky_sleeve_return=0.30, steps=40,
            regime_risky_weight=0.85, start_drawdown=-0.15, market_recovery_streak=5,
        )
        self.assertGreater(path["terminal_drawdown"], -0.15 + 1e-9)

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            drawdown_budget_ladder_path(self.policy, risky_sleeve_return=-1.5, steps=5, regime_risky_weight=0.5)
        with self.assertRaises(ValueError):
            drawdown_budget_ladder_path(self.policy, risky_sleeve_return=-0.4, steps=0, regime_risky_weight=0.5)
        with self.assertRaises(ValueError):
            drawdown_budget_ladder_path(self.policy, risky_sleeve_return=-0.4, steps=5, regime_risky_weight=1.5)


class MarketOnlyRegimeTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()

    def test_deep_drawdown_does_not_leak_into_the_market_reading(self):
        inputs = RegimeInputs(
            btc_trend="BULLISH", volatility_state="LOW", flow_state="POSITIVE",
            breadth_state="HEALTHY", portfolio_drawdown_band=-0.30,
        )
        self.assertEqual(market_only_regime(inputs, policy=self.policy), "NORMAL")

    def test_bearish_market_domains_map_defensively(self):
        inputs = RegimeInputs(
            btc_trend="BEARISH", volatility_state="HIGH", flow_state="NEGATIVE",
            breadth_state="WEAK",
        )
        self.assertEqual(market_only_regime(inputs, policy=self.policy), "CAPITAL_PRESERVATION")

    def test_unknown_flow_domain_adds_no_severity(self):
        inputs = RegimeInputs(
            btc_trend="BULLISH", volatility_state="NORMAL", flow_state="UNKNOWN",
            breadth_state="HEALTHY",
        )
        self.assertEqual(market_only_regime(inputs, policy=self.policy), "NORMAL")


class OverlayAllocationTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()
        self.assessments = {
            "BTC": {"weighted_score": 60.0, "confidence": "MEDIUM"},
            "ETH": {"weighted_score": 55.0, "confidence": "MEDIUM"},
        }
        self.current = {"BTC": 0.39, "ETH": 0.16, "USDT": 0.45}

    def _stable_weight(self, result):
        return sum(
            weight for symbol, weight in result.target_weights.items()
            if symbol in self.policy.stable_symbols
        )

    def test_breach_forces_fully_stable_target(self):
        result = build_target_allocation(
            policy=self.policy, regime="CAPITAL_PRESERVATION",
            assessments=self.assessments, current_weights=self.current,
            portfolio_drawdown=-0.18,
        )
        self.assertAlmostEqual(self._stable_weight(result), 1.0, places=9)
        self.assertTrue(any("overlay" in reason for reason in result.allocation_reasons))
        self.assertTrue(any("overlay" in c for c in result.constraints_applied))
        # Strategic target stays the regime's own 50%; the rest is residual cash.
        self.assertAlmostEqual(result.strategic_stable_target, 0.5, places=9)

    def test_partial_consumption_shrinks_the_risky_budget(self):
        result = build_target_allocation(
            policy=self.policy, regime="NORMAL", assessments=self.assessments,
            current_weights=self.current, portfolio_drawdown=-0.09,
        )
        self.assertGreaterEqual(self._stable_weight(result), 0.6 - 1e-9)
        self.assertLessEqual(1.0 - self._stable_weight(result), 0.4 + 1e-9)

    def test_without_drawdown_the_overlay_stays_silent(self):
        result = build_target_allocation(
            policy=self.policy, regime="NORMAL", assessments=self.assessments,
            current_weights=self.current,
        )
        quiet = build_target_allocation(
            policy=_overlay_disabled_policy(), regime="NORMAL",
            assessments=self.assessments, current_weights=self.current,
            portfolio_drawdown=-0.18,
        )
        self.assertEqual(
            {s: round(w, 9) for s, w in result.target_weights.items()},
            {s: round(w, 9) for s, w in quiet.target_weights.items()},
        )
        self.assertFalse(any("overlay" in reason for reason in result.allocation_reasons))

    def test_confirmed_recovery_re_risks_beyond_the_ladder(self):
        result = build_target_allocation(
            policy=self.policy, regime="CAPITAL_PRESERVATION",
            assessments=self.assessments, current_weights={"USDT": 1.0},
            portfolio_drawdown=-0.15, market_recovery_streak=5,
        )
        risky = 1.0 - self._stable_weight(result)
        self.assertAlmostEqual(risky, 0.25, places=9)
        self.assertTrue(any("market recovery" in reason for reason in result.allocation_reasons))


class OverlayRiskGateTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()

    def test_gate_requires_the_overlay_floor(self):
        below = {"USDT": 0.55, "BTC": 0.45}
        result = run_risk_gate(
            below, policy=self.policy, regime="NORMAL",
            current_drawdown=-0.09, market_recovery_streak=0,
        )
        codes = {violation.code for violation in result.violations}
        self.assertIn("STABLECOIN_FLOOR", codes)
        self.assertFalse(result.ok)
        satisfied = {"USDT": 0.6, "BTC": 0.4}
        ok_result = run_risk_gate(
            satisfied, policy=self.policy, regime="NORMAL",
            current_drawdown=-0.09, market_recovery_streak=0,
        )
        self.assertNotIn(
            "STABLECOIN_FLOOR",
            {violation.code for violation in ok_result.violations},
        )


class OverlayRebalanceTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()
        self.assessments = {
            "BTC": {"weighted_score": 60.0, "confidence": "MEDIUM"},
            "ETH": {"weighted_score": 55.0, "confidence": "MEDIUM"},
        }
        self.current = {"BTC": 0.39, "ETH": 0.16, "USDT": 0.45}

    def _allocation(self, **kwargs):
        return build_target_allocation(
            policy=self.policy, regime="CAPITAL_PRESERVATION",
            assessments=self.assessments, current_weights=self.current, **kwargs
        )

    def test_overlay_de_risk_bypasses_staging_and_bands(self):
        allocation = self._allocation(portfolio_drawdown=-0.18)
        result = recommend_rebalance(
            self.current, dict(allocation.target_weights), 100_000.0,
            policy=self.policy, regime="CAPITAL_PRESERVATION",
            portfolio_drawdown=-0.18, market_recovery_streak=0,
        )
        by_symbol = {action.symbol: action for action in result.actions}
        self.assertEqual(by_symbol["BTC"].action, "EXIT")
        self.assertEqual(by_symbol["BTC"].action_reason, "RISK_BUDGET_BREACH")
        self.assertFalse(by_symbol["BTC"].staging_applied)
        self.assertAlmostEqual(by_symbol["BTC"].amount_usd, 39_000.0, places=6)
        self.assertEqual(by_symbol["ETH"].action, "EXIT")

    def test_positions_at_target_are_not_forced_out_by_the_hard_reason(self):
        # A mild overlay floor with the book already at target: no hard exits.
        allocation = self._allocation(portfolio_drawdown=-0.02)
        result = recommend_rebalance(
            dict(allocation.target_weights), dict(allocation.target_weights), 100_000.0,
            policy=self.policy, regime="CAPITAL_PRESERVATION",
            portfolio_drawdown=-0.02, market_recovery_streak=0,
        )
        for action in result.actions:
            self.assertIn(action.action, {"HOLD", "WAIT"})

    def test_target_below_overlay_floor_is_rejected(self):
        with self.assertRaises(ValueError):
            recommend_rebalance(
                self.current, {"BTC": 0.5, "ETH": 0.2, "USDT": 0.3}, 100_000.0,
                policy=self.policy, regime="CAPITAL_PRESERVATION",
                portfolio_drawdown=-0.18, market_recovery_streak=0,
            )


class OverlayStressAndFeasibilityTests(unittest.TestCase):
    def test_canonical_budget_stress_checks_pass(self):
        stress = drawdown_budget_stress()
        self.assertTrue(stress["enabled"])
        for name, value in stress["checks"].items():
            self.assertTrue(value, name)

    def test_disabled_overlay_reports_disabled_semantics(self):
        stress = drawdown_budget_stress(_overlay_disabled_policy())
        self.assertFalse(stress["enabled"])
        self.assertTrue(stress["checks"]["disabled_overlay_breaches_on_same_path"])
        # With the overlay off, the within-budget check is not asserted.
        self.assertFalse(stress["checks"]["severe_decline_stays_within_budget"])

    def test_canonical_policy_ladder_holds_the_budget(self):
        report = check_policy_feasibility()
        self.assertFalse(
            [item for item in report.findings if item.code == DRAWDOWN_BUDGET_INFEASIBLE]
        )
        for row in report.drawdown_rows:
            if row.get("status") == "AVAILABLE":
                self.assertIn("ladder", row)
                self.assertIn("static_projected_drawdown", row)

    def test_disabled_overlay_restores_the_one_shot_verdict(self):
        disabled = _overlay_disabled_policy()
        rows, findings = drawdown_budget_feasibility(disabled)
        self.assertTrue(
            [item for item in findings if item.code == DRAWDOWN_BUDGET_INFEASIBLE]
        )
        available = [row for row in rows if row.get("status") == "AVAILABLE"]
        self.assertTrue(available)
        for row in available:
            self.assertAlmostEqual(
                row["projected_drawdown"], row["static_projected_drawdown"], places=12
            )


if __name__ == "__main__":
    unittest.main()
