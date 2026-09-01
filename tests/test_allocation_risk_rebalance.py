import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.rebalance import recommend_rebalance, validate_execution_plan
from crypto_portfolio.engine.risk import run_risk_gate
from crypto_portfolio.models.policy import resolve_policy


class AllocationRiskRebalanceTests(unittest.TestCase):
    def test_allocation_is_bounded_and_deterministic(self):
        assessments = {
            "SOL": {"score": 90, "confidence": "HIGH"},
            "AAVE": {"score": 75, "confidence": "MEDIUM"},
        }
        first = build_target_allocation(regime="NORMAL", assessments=assessments)
        second = build_target_allocation(regime="NORMAL", assessments=assessments)
        self.assertEqual(dict(first.target_weights), dict(second.target_weights))
        self.assertAlmostEqual(sum(first.target_weights.values()), 1.0)
        self.assertGreaterEqual(first.target_weights["USDT"], 0.10)
        satellite_weight = sum(first.target_weights.get(symbol, 0) for symbol in ("SOL", "AAVE", "BNB", "LINK"))
        self.assertLessEqual(satellite_weight, 0.25)

    def test_low_confidence_satellite_receives_zero(self):
        result = build_target_allocation(
            assessments={"SOL": {"score": 95, "confidence": "LOW"}}
        )
        self.assertEqual(result.target_weights.get("SOL", 0), 0)

    def test_capital_preservation_reduces_risky_exposure(self):
        normal = build_target_allocation(
            regime="NORMAL", assessments={"SOL": {"score": 90, "confidence": "HIGH"}}
        )
        capital = build_target_allocation(
            regime="CAPITAL_PRESERVATION", assessments={"SOL": {"score": 90, "confidence": "HIGH"}}
        )
        normal_stable = sum(normal.target_weights.get(symbol, 0) for symbol in ("USDT", "USDC", "DAI", "FDUSD", "TUSD", "USD", "CASH"))
        capital_stable = sum(capital.target_weights.get(symbol, 0) for symbol in ("USDT", "USDC", "DAI", "FDUSD", "TUSD", "USD", "CASH"))
        self.assertGreater(capital_stable, normal_stable)
        self.assertLessEqual(capital.target_weights.get("SOL", 0), 0.05)

    def test_custom_stablecoin_floor_is_hard(self):
        policy = resolve_policy({"min_stablecoin_weight": 0.4})
        result = build_target_allocation(policy=policy)
        stable = sum(result.target_weights.get(symbol, 0) for symbol in policy.stable_symbols)
        self.assertGreaterEqual(stable, 0.4)
        self.assertTrue(run_risk_gate(result, policy=policy).ok)

    def test_risk_gate_reports_constraints(self):
        result = run_risk_gate(
            {"BTC": 0.6, "SOL": 0.3, "USDT": 0.1},
            assessments={"SOL": {"confidence": "LOW", "severe_event": True}},
        )
        codes = {item.code for item in result.violations}
        self.assertIn("SATELLITE_CAP", codes)
        self.assertIn("SEVERE_EVENT_EXPOSURE", codes)
        self.assertFalse(result.ok)
        drawdown = run_risk_gate({"BTC": 0.9, "USDT": 0.1}, current_drawdown=-0.21)
        self.assertIn("DRAWDOWN_BREACH", {item.code for item in drawdown.violations})

    def test_rebalance_thresholds_and_no_trade(self):
        hold = recommend_rebalance({"BTC": 0.48, "USDT": 0.52}, {"BTC": 0.50, "USDT": 0.50}, 1000)
        watch = recommend_rebalance({"BTC": 0.45, "USDT": 0.55}, {"BTC": 0.49, "USDT": 0.51}, 1000)
        active = recommend_rebalance({"BTC": 0.39, "USDT": 0.61}, {"BTC": 0.50, "USDT": 0.50}, 1000)
        self.assertTrue(hold.no_trade)
        self.assertEqual(hold[0].action, "HOLD")
        self.assertEqual(watch[0].action, "WAIT")
        self.assertEqual(watch[0].priority, "WATCH")
        self.assertEqual(active[0].action, "INCREASE")
        self.assertEqual(active[0].priority, "HIGH")

    def test_new_cash_and_thesis_break_exit(self):
        result = recommend_rebalance(
            {"BTC": 0.4, "SOL": 0.2, "USDT": 0.4},
            {"BTC": 0.5, "SOL": 0.1, "USDT": 0.4},
            1000,
            new_cash_available=100,
            thesis_broken={"SOL"},
        )
        actions = {item.symbol: item for item in result}
        self.assertEqual(actions["SOL"].action, "EXIT")
        self.assertEqual(actions["BTC"].action, "INCREASE")
        self.assertAlmostEqual(actions["BTC"].amount_usd, 100)

    def test_execution_plan_validation(self):
        self.assertTrue(
            validate_execution_plan(
                [
                    {"allocation_fraction": 0.3, "price_low": 90, "price_high": 100, "description": "support"},
                    {"allocation_fraction": 0.7, "description": "confirmation"},
                ]
            )
        )
        with self.assertRaises(ValueError):
            validate_execution_plan(
                [{"allocation_fraction": 0.3, "price_low": 100, "price_high": 90, "description": "bad"}]
            )
        with self.assertRaises(ValueError):
            validate_execution_plan(
                [{"allocation_fraction": 0.6, "description": "incomplete"}]
            )


if __name__ == "__main__":
    unittest.main()
