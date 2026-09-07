import unittest

from crypto_portfolio.engine.allocation import build_target_allocation, satellite_eligibility
from crypto_portfolio.engine.rebalance import RebalanceAction, recommend_rebalance
from crypto_portfolio.engine.execution import validate_execution_plan
from crypto_portfolio.engine.risk import run_risk_gate
from crypto_portfolio.models.evidence import AssetAssessment
from crypto_portfolio.models.policy import resolve_policy
from crypto_portfolio.engine.scoring import score_factors


class AllocationRiskRebalanceTests(unittest.TestCase):
    def test_allocation_is_bounded_and_deterministic(self):
        assessments = {
            "SOL": {"weighted_score": 90, "confidence": "HIGH"},
            "AAVE": {"weighted_score": 75, "confidence": "MEDIUM"},
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
            assessments={"SOL": {"weighted_score": 95, "confidence": "LOW"}}
        )
        self.assertEqual(result.target_weights.get("SOL", 0), 0)

    def test_capital_preservation_reduces_risky_exposure(self):
        normal = build_target_allocation(
            regime="NORMAL", assessments={"SOL": {"weighted_score": 90, "confidence": "HIGH"}}
        )
        capital = build_target_allocation(
            regime="CAPITAL_PRESERVATION", assessments={"SOL": {"weighted_score": 90, "confidence": "HIGH"}}
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

    def test_u_and_usd1_count_toward_stablecoin_floor(self):
        policy = resolve_policy()
        weights = {"BTC": 0.9, "U": 0.05, "USD1": 0.05}
        risk = run_risk_gate(weights, policy=policy)
        self.assertNotIn("STABLECOIN_FLOOR", {item.code for item in risk.violations})
        allocation = build_target_allocation(policy=policy, current_weights=weights)
        self.assertGreaterEqual(
            sum(allocation.target_weights.get(symbol, 0) for symbol in policy.stable_symbols),
            policy.min_stablecoin_weight,
        )
        self.assertGreater(allocation.target_weights.get("U", 0), 0)
        self.assertGreater(allocation.target_weights.get("USD1", 0), 0)

    def test_excluded_asset_has_no_score_target_or_rebalance_action(self):
        policy = resolve_policy()
        allocation = build_target_allocation(
            policy=policy,
            assessments={"LUNC": {"weighted_score": 100, "confidence": "HIGH"}},
            current_weights={"BTC": 0.7, "USDT": 0.1, "LUNC": 0.2},
        )
        self.assertNotIn("LUNC", allocation.target_weights)
        rebalance = recommend_rebalance(
            {"BTC": 0.7, "USDT": 0.1, "LUNC": 0.2},
            allocation.target_weights,
            1000,
            policy=policy,
        )
        self.assertNotIn("LUNC", {item.symbol for item in rebalance})
        with self.assertRaises(ValueError):
            score_factors({"trend": 80}, policy=policy, symbol="LUNC")
        risk = run_risk_gate({"BTC": 0.7, "USDT": 0.1, "LUNC": 0.2}, policy=policy)
        self.assertIn("EXCLUDED_ASSET_TARGET", {item.code for item in risk.violations})

    def test_risk_gate_reports_constraints(self):
        result = run_risk_gate(
            {"BTC": 0.6, "SOL": 0.3, "USDT": 0.1},
            assessments={"SOL": {"confidence": "LOW", "event_risk": {"state": "SEVERE"}}},
        )
        codes = {item.code for item in result.violations}
        self.assertIn("SATELLITE_CAP", codes)
        self.assertIn("SEVERE_EVENT_EXPOSURE", codes)
        self.assertFalse(result.ok)
        drawdown = run_risk_gate({"BTC": 0.9, "USDT": 0.1}, current_drawdown=-0.21)
        self.assertIn("DRAWDOWN_BREACH", {item.code for item in drawdown.violations})

    def test_risk_gate_enforces_regime_stable_target_and_core_minimum(self):
        stable = run_risk_gate(
            {"BTC": 0.9, "USDT": 0.1}, regime="CAPITAL_PRESERVATION"
        )
        self.assertIn("STABLECOIN_FLOOR", {item.code for item in stable.violations})
        core = run_risk_gate(
            {"SOL": 0.9, "USDT": 0.1}, regime="DEFENSIVE"
        )
        self.assertIn("CORE_MINIMUM", {item.code for item in core.violations})

    def test_rebalance_thresholds_and_no_trade(self):
        hold = recommend_rebalance({"BTC": 0.48, "USDT": 0.52}, {"BTC": 0.50, "USDT": 0.50}, 1000)
        watch = recommend_rebalance({"BTC": 0.45, "USDT": 0.55}, {"BTC": 0.49, "USDT": 0.51}, 1000)
        active = recommend_rebalance({"BTC": 0.39, "USDT": 0.61}, {"BTC": 0.50, "USDT": 0.50}, 1000)
        self.assertTrue(hold.no_trade)
        self.assertEqual(hold[0].action, "HOLD")
        self.assertEqual(watch[0].action, "WAIT")
        self.assertEqual(watch[0].priority, "WATCH")
        self.assertEqual(watch[0].amount_usd, 0)
        self.assertEqual(active[0].action, "INCREASE")
        self.assertEqual(active[0].priority, "HIGH")
        self.assertGreater(active[0].amount_usd, 0)

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
        self.assertAlmostEqual(actions["BTC"].amount_usd, 150)
        self.assertTrue(result.reconciliation["balanced"])
        self.assertAlmostEqual(
            result.reconciliation["external_new_cash"]
            + result.reconciliation["planned_sells"]
            - result.reconciliation["planned_buys"],
            result.reconciliation["residual_stablecoin_change"],
        )

    def test_rebalance_enforces_stable_sleeve_floor(self):
        with self.assertRaises(ValueError):
            recommend_rebalance(
                {"BTC": 0.95, "USDT": 0.05},
                {"BTC": 0.95, "USDT": 0.05},
                1000,
            )
        with self.assertRaises(ValueError):
            recommend_rebalance(
                {"BTC": 0.8, "USDT": 0.2},
                {"BTC": 0.8, "USDT": 0.2},
                1000,
                regime="DEFENSIVE",
            )
        # At the global floor and above the regime target the call succeeds.
        result = recommend_rebalance(
            {"BTC": 0.9, "USDT": 0.1},
            {"BTC": 0.9, "USDT": 0.1},
            1000,
        )
        self.assertTrue(result.no_trade)
        defensive = recommend_rebalance(
            {"BTC": 0.75, "USDT": 0.25},
            {"BTC": 0.75, "USDT": 0.25},
            1000,
            regime="DEFENSIVE",
        )
        self.assertTrue(defensive.no_trade)

    def test_satellite_sizing_scales_with_score_confidence_and_risk(self):
        def weight(score=85, confidence="HIGH", risk_tier="normal"):
            return build_target_allocation(
                assessments={
                    "SOL": {
                        "weighted_score": score,
                        "confidence": confidence,
                        "risk_tier": risk_tier,
                        "relative_strength_vs_btc": "OUTPERFORM",
                    }
                }
            ).target_weights.get("SOL", 0)

        self.assertEqual(weight(score=66), 0)
        self.assertEqual(satellite_eligibility({"weighted_score": 67, "confidence": "HIGH", "relative_strength_vs_btc": "OUTPERFORM"}), "ELIGIBLE")
        self.assertLess(weight(score=70), weight(score=80))
        self.assertLess(weight(confidence="MEDIUM"), weight(confidence="HIGH"))
        self.assertLess(weight(risk_tier="high_beta"), weight(risk_tier="normal"))
        self.assertLessEqual(weight(), 0.25)

    def test_satellite_score_hysteresis(self):
        assessment = {"weighted_score": 66, "confidence": "HIGH", "relative_strength_vs_btc": "OUTPERFORM"}
        self.assertEqual(satellite_eligibility(assessment), "INELIGIBLE")
        self.assertEqual(satellite_eligibility(assessment, current_weight=0.05), "HOLD_ONLY")
        below_exit = {**assessment, "weighted_score": 59}
        self.assertEqual(satellite_eligibility(below_exit, current_weight=0.05), "INELIGIBLE")
        result = build_target_allocation(assessments={"SOL": assessment}, current_weights={"SOL": 0.05, "USDT": 0.1, "BTC": 0.85})
        self.assertAlmostEqual(result.target_weights.get("SOL", 0), 0.05)

    def test_missing_relative_strength_is_hold_only(self):
        new_risk = build_target_allocation(
            assessments={"SOL": {"weighted_score": 90, "confidence": "HIGH"}}
        )
        existing = build_target_allocation(
            current_weights={"SOL": 0.05, "USDT": 0.1, "BTC": 0.85},
            assessments={"SOL": {"weighted_score": 90, "confidence": "HIGH"}},
        )
        self.assertEqual(new_risk.target_weights.get("SOL", 0), 0)
        self.assertAlmostEqual(existing.target_weights.get("SOL", 0), 0.05)

    def test_event_risk_is_independent_from_base_score(self):
        normal = build_target_allocation(
            assessments={"SOL": {"weighted_score": 100, "confidence": "HIGH", "relative_strength_vs_btc": "OUTPERFORM"}}
        )
        critical = build_target_allocation(
            assessments={"SOL": {"weighted_score": 100, "confidence": "HIGH", "relative_strength_vs_btc": "OUTPERFORM", "event_risk": {"state": "CRITICAL"}}}
        )
        self.assertGreater(normal.target_weights.get("SOL", 0), 0)
        self.assertEqual(critical.target_weights.get("SOL", 0), 0)

    def test_stablecoins_are_one_sleeve(self):
        current = {"BTC": 0.9, "USDT": 0.05, "USDC": 0.05}
        target = {"BTC": 0.9, "USDT": 0.10}
        result = recommend_rebalance(current, target, 1000)
        self.assertEqual(
            {action.symbol: action.action for action in result if action.symbol in {"USDT", "USDC"}},
            {"USDT": "HOLD", "USDC": "HOLD"},
        )

    def test_typed_thesis_broken_matches_mapping(self):
        typed = build_target_allocation(
            assessments={
                "SOL": AssetAssessment(
                    "SOL",
                    {},
                    weighted_score=90,
                    confidence="HIGH",
                    asset_type="satellite",
                    relative_strength_vs_btc="OUTPERFORM",
                    thesis_broken=True,
                )
            }
        )
        mapped = build_target_allocation(
            assessments={
                "SOL": {
                    "weighted_score": 90,
                    "confidence": "HIGH",
                    "relative_strength_vs_btc": "OUTPERFORM",
                    "thesis_broken": True,
                }
            }
        )
        self.assertEqual(typed.target_weights.get("SOL", 0), mapped.target_weights.get("SOL", 0))

    def test_worse_regimes_reduce_satellite_capacity_and_risk(self):
        assessment = {
            "SOL": {
                "weighted_score": 85,
                "confidence": "HIGH",
                "relative_strength_vs_btc": "OUTPERFORM",
            }
        }
        normal = build_target_allocation(regime="NORMAL", assessments=assessment)
        defensive = build_target_allocation(regime="DEFENSIVE", assessments=assessment)
        capital = build_target_allocation(regime="CAPITAL_PRESERVATION", assessments=assessment)
        def satellite(result):
            return result.target_weights.get("SOL", 0)

        def stable(result):
            return sum(
                result.target_weights.get(symbol, 0)
                for symbol in ("USDT", "USDC", "DAI", "FDUSD", "TUSD", "USD", "CASH")
            )
        self.assertGreaterEqual(satellite(normal), satellite(defensive))
        self.assertGreaterEqual(satellite(defensive), satellite(capital))
        self.assertLessEqual(stable(normal), stable(defensive))
        self.assertLessEqual(stable(defensive), stable(capital))

    def test_old_execution_zones_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_execution_plan(
                [
                    {"allocation_fraction": 0.3, "price_low": 90, "price_high": 100, "description": "support"},
                    {"allocation_fraction": 0.7, "description": "confirmation"},
                ]
            )
        with self.assertRaises(ValueError):
            validate_execution_plan(
                [{"allocation_fraction": 0.3, "price_low": 100, "price_high": 90, "description": "bad"}]
            )
        with self.assertRaises(ValueError):
            validate_execution_plan(
                [{"allocation_fraction": 0.6, "description": "incomplete"}]
            )

    def test_rebalance_action_amount_matches_executability(self):
        with self.assertRaises(ValueError):
            RebalanceAction("BTC", "INCREASE", 0, 0.5, 0, "NORMAL")
        with self.assertRaises(ValueError):
            RebalanceAction("BTC", "WAIT", 0.5, 0.5, 1, "WATCH")


if __name__ == "__main__":
    unittest.main()
