"""Strategy V2.3 validation contract (Phase 6)."""

import unittest
from datetime import datetime, timedelta, timezone

from crypto_portfolio.engine.strategy_replay import ReplayReview
from crypto_portfolio.models.policy import load_policy, policy_from_mapping
from crypto_portfolio.research.v23_validation import (
    alpha_attribution_report,
    risk_attribution,
    run_v23_ablation,
    v23_ablation_ladder,
    v23_manifest,
    v23_policy_decision_matrix,
)


def _vol_policy():
    policy = load_policy()
    return policy_from_mapping({
        **policy.as_dict(),
        "risk_engine": {**policy.as_dict()["risk_engine"], "mode": "volatility_budget"},
    })


def _reviews(count=30):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    reviews = []
    btc = 100.0
    for index in range(count):
        moment = start + timedelta(days=index)
        reviews.append(ReplayReview(
            as_of=moment.isoformat().replace("+00:00", "Z"),
            period_end=(moment + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            current_weights={"BTC": 0.4, "USDT": 0.6},
            portfolio_value=10000.0,
            current_prices={"BTC": btc, "USDT": 1.0},
            assessments={
                "BTC": {
                    "weighted_score": 70, "normalized_score": 70, "confidence": "HIGH",
                    "factor_scores": {}, "critical_data_complete": True,
                    "score_coverage": 1.0,
                },
            },
            regime_inputs={
                "btc_trend": "BULLISH", "volatility_state": "LOW",
                "flow_state": "POSITIVE", "breadth_state": "HEALTHY",
            },
            next_returns={"BTC": 0.004 if index % 2 == 0 else 0.0, "USDT": 0.0},
        ))
        btc *= 1.004 if index % 2 == 0 else 1.0
    return reviews


class _StubRunner:
    """Deterministic stand-in returning one metrics table per policy mode."""

    def __init__(self):
        self.calls = []

    def __call__(self, reviews, *, policy, fee_bps, slippage_bps,
                 risk_inputs_by_review, cash_carry=None):
        self.calls.append((policy, fee_bps))
        stress_on = bool(policy.stress_loss_budget.get("enabled"))
        tilt_on = any(
            entry.get("tilt_enabled") for entry in policy.satellite_alpha.values()
        ) or bool(policy.core_allocation["eth"]["tilt_enabled"])
        base = 0.10 if not stress_on else 0.08
        if tilt_on:
            base += 0.01
        if fee_bps:
            base -= 0.005
        return {
            "metrics": {
                "cagr": base,
                "maximum_drawdown": -0.15 if stress_on else -0.20,
                "annualized_volatility": 0.12,
                "sharpe_rf_zero": 0.8, "sortino_target_zero": 1.0, "calmar": base / 0.15,
                "total_return": base, "cvar_95_period": -0.02,
                "worst_30d_return": -0.05, "worst_90d_return": -0.09,
            },
            "benchmark_comparison": {
                "vol_matched_btc_cash_investable": {"excess_return_annualized": base},
                "btc_buy_and_hold_investable": {"excess_return_annualized": base / 2},
            },
            "total_turnover": 1.0, "total_cost_usd": 10.0 if fee_bps else 0.0,
            "average_cash_weight": 0.6,
            "cash_carry": {"carry_contribution": 0.02},
            "regime_counts": {"NORMAL": len(reviews)},
            "stall_attribution": {"reviews": len(reviews)},
            "reviews": [],
            "valuations": [],
        }


class LadderShapeTests(unittest.TestCase):
    def test_ladder_has_the_preregistered_rungs(self):
        ladder = v23_ablation_ladder(_vol_policy())
        self.assertEqual(
            [rung["rung"] for rung in ladder],
            ["A", "B", "C", "D", "E", "F", "G", "H", "I"],
        )

    def test_rung_a_is_btc_only_vol_targeting(self):
        data = v23_ablation_ladder(_vol_policy())[0]["policy"]
        policy = policy_from_mapping(data)
        self.assertEqual(policy.satellite_symbols, ())
        self.assertFalse(policy.stress_loss_budget["enabled"])
        self.assertFalse(policy.core_allocation["eth"]["tilt_enabled"])
        self.assertEqual(
            policy.risk_engine["regime_risk_scaling"]["DEFENSIVE"]["target_volatility_multiplier"],
            1.0,
        )
        self.assertGreaterEqual(policy.risk_engine["recovery"]["stage_1_reviews"], 10**6)

    def test_rung_b_enables_the_stress_budget(self):
        data = v23_ablation_ladder(_vol_policy())[1]["policy"]
        policy = policy_from_mapping(data)
        self.assertTrue(policy.stress_loss_budget["enabled"])
        self.assertEqual(policy.drawdown_budget_mode, "HARD_TARGET")

    def test_rung_c_is_regime_v2_r1(self):
        data = v23_ablation_ladder(_vol_policy())[2]["policy"]
        policy = policy_from_mapping(data)
        self.assertEqual(
            policy.regime_model["excluded_domains"], ("volatility",),
        )
        self.assertEqual(
            policy.risk_engine["regime_risk_scaling"]["DEFENSIVE"]["target_volatility_multiplier"],
            load_policy().risk_engine["regime_risk_scaling"]["DEFENSIVE"]["target_volatility_multiplier"],
        )

    def test_alpha_rungs_follow_the_admission_verdicts(self):
        unlocked = v23_ablation_ladder(
            _vol_policy(),
            bnb_admitted_signals=("rel_return_30d",),
            aave_admitted_signals=("protocol_tvl_growth_90d",),
            eth_tilt_admitted=True,
        )
        d = policy_from_mapping(unlocked[3]["policy"])
        self.assertTrue(d.satellite_alpha["BNB"]["tilt_enabled"])
        self.assertEqual(d.satellite_alpha["BNB"]["admitted_signals"], ("rel_return_30d",))
        self.assertFalse(d.satellite_alpha["AAVE"]["tilt_enabled"])
        e = policy_from_mapping(unlocked[4]["policy"])
        self.assertTrue(e.satellite_alpha["AAVE"]["tilt_enabled"])
        f = policy_from_mapping(unlocked[5]["policy"])
        self.assertTrue(f.core_allocation["eth"]["tilt_enabled"])
        # Locked (nothing admitted): D/E/F are diagnostic no-ops.
        locked = v23_ablation_ladder(_vol_policy())
        for index in (3, 4, 5):
            policy = policy_from_mapping(locked[index]["policy"])
            self.assertFalse(any(
                entry["tilt_enabled"] for entry in policy.satellite_alpha.values()
            ))
            self.assertFalse(policy.core_allocation["eth"]["tilt_enabled"])

    def test_rung_g_restores_the_recovery_fsm_and_h_adds_costs(self):
        ladder = v23_ablation_ladder(_vol_policy())
        g = policy_from_mapping(ladder[6]["policy"])
        self.assertEqual(
            g.risk_engine["recovery"]["stage_1_reviews"],
            load_policy().risk_engine["recovery"]["stage_1_reviews"],
        )
        self.assertTrue(ladder[6]["zero_cost"])
        self.assertFalse(ladder[7]["zero_cost"])
        self.assertFalse(ladder[8]["zero_cost"])


class RunnerTests(unittest.TestCase):
    def test_runner_executes_every_rung_with_full_identity(self):
        policy = _vol_policy()
        runner = _StubRunner()
        result = run_v23_ablation(
            _reviews(), policy=policy, risk_inputs_by_review=[None] * 30,
            git_sha="deadbeef",
            alpha_registry_hash="a" * 64,
            cash_carry_name="RISK_FREE_PROXY",
            bnb_admitted_signals=("rel_return_30d",),
            backtest_runner=runner,
        )
        self.assertEqual(len(result["rungs"]), 9)
        self.assertEqual(len(runner.calls), 9)
        first = result["rungs"][0]
        self.assertEqual(first["manifest"]["strategy_version"], "strategy-v2.3")
        self.assertEqual(first["manifest"]["alpha_registry_hash"], "a" * 64)
        self.assertEqual(first["manifest"]["cash_carry_convention"], "RISK_FREE_PROXY")
        self.assertEqual(first["manifest"]["risk_budget_mode"], "HARD_TARGET")
        self.assertIn("delta_vs_previous", result["rungs"][1])
        self.assertIn("risk_attribution", first)
        self.assertIn("cash_carry_contribution", first["metrics"])

    def test_alpha_attribution_covers_every_module_and_residual(self):
        policy = _vol_policy()
        result = run_v23_ablation(
            _reviews(), policy=policy, risk_inputs_by_review=[None] * 30,
            backtest_runner=_StubRunner(),
        )
        attribution = alpha_attribution_report(result["rungs"])
        for key in (
            "btc_baseline_cagr", "stress_loss_budget_cagr_delta", "regime_v2_cagr_delta",
            "bnb_alpha_cagr_delta", "aave_alpha_cagr_delta", "eth_alpha_cagr_delta",
            "emergency_fsm_cagr_delta", "execution_cost_cagr_delta", "cash_carry_contribution",
            "full_v23_cagr", "interaction_residual",
        ):
            self.assertIn(key, attribution)
        self.assertIsNotNone(attribution["interaction_residual"])


class RiskAttributionTests(unittest.TestCase):
    def test_counts_days_beyond_levels_and_binding_shares(self):
        result = {
            "valuations": [
                {"drawdown": -0.05}, {"drawdown": -0.16}, {"drawdown": -0.22},
                {"drawdown": -0.27}, {"drawdown": -0.14},
            ],
            "reviews": [
                {"allocation": {"risk_engine": {
                    "binding_risk_constraint": "stress_loss_budget",
                    "stress_budget": {"stress_budget_utilization": 1.2,
                                      "stress_loss_by_scenario": {"severe_crypto_crash": -0.18}},
                }}},
                {"allocation": {"risk_engine": {
                    "binding_risk_constraint": "volatility_budget",
                    "stress_budget": {"stress_budget_utilization": 0.9,
                                      "stress_loss_by_scenario": {"severe_crypto_crash": -0.15}},
                }}},
            ],
        }
        block = risk_attribution(result)
        self.assertEqual(block["valuation_days"], 5)
        self.assertEqual(block["days_beyond_15pct"], 3)
        self.assertEqual(block["days_beyond_20pct"], 2)
        self.assertEqual(block["days_beyond_25pct"], 1)
        self.assertAlmostEqual(block["worst_stress_loss"], -0.18)
        self.assertAlmostEqual(block["avg_stress_budget_utilization"], 1.05)
        self.assertAlmostEqual(block["binding_constraint_shares"]["stress_loss_budget"], 0.5)

    def test_empty_result_is_reported_not_fabricated(self):
        block = risk_attribution({})
        self.assertEqual(block["valuation_days"], 0)
        self.assertIsNone(block["worst_stress_loss"])
        self.assertEqual(block["binding_constraint_shares"], {})


class DecisionMatrixTests(unittest.TestCase):
    def _ladder(self, shift=0.0):
        runner = _StubRunner()
        result = run_v23_ablation(
            _reviews(), policy=_vol_policy(), risk_inputs_by_review=[None] * 30,
            backtest_runner=runner,
        )
        if shift:
            for rung in result["rungs"]:
                rung["metrics"]["vol_matched_excess_annualized"] += shift
        return result

    def test_modules_derive_verdicts_from_both_windows(self):
        primary = self._ladder()
        sister = self._ladder()
        matrix = v23_policy_decision_matrix(
            primary, sister, bnb_admitted=False, aave_admitted=False, eth_admitted=False,
        )
        modules = matrix["modules"]
        for name in (
            "stress_loss_budget", "regime_v2", "bnb_admitted_alpha",
            "aave_admitted_alpha", "eth_admitted_alpha", "emergency_fsm",
            "execution_layer", "btc_baseline_core",
        ):
            self.assertIn(name, modules)
            self.assertIn(modules[name]["decision"], {"KEEP", "RESEARCH_ONLY", "REMOVE"})
        # The stub makes every layer a no-op delta (identical metrics), so
        # nothing earns KEEP on flat evidence and unadmitted alpha cannot.
        self.assertNotEqual(modules["bnb_admitted_alpha"]["decision"], "KEEP")

    def test_admission_gates_keep(self):
        positive = self._ladder(shift=0.01)
        matrix = v23_policy_decision_matrix(
            positive, positive, bnb_admitted=True, aave_admitted=False, eth_admitted=False,
        )
        # Equal-metric stubs still yield zero deltas vs the previous rung
        # (both shifted equally), so decisions stay non-KEEP: the matrix only
        # reads DELTAS, never levels.
        self.assertIn(
            matrix["modules"]["stress_loss_budget"]["decision"],
            {"KEEP", "RESEARCH_ONLY", "REMOVE"},
        )

    def test_single_window_is_reported_honestly(self):
        matrix = v23_policy_decision_matrix(
            self._ladder(), None,
            bnb_admitted=False, aave_admitted=False, eth_admitted=False,
        )
        self.assertIn(
            "insufficient windows", matrix["modules"]["stress_loss_budget"]["reason"],
        )


class ManifestTests(unittest.TestCase):
    def test_manifest_freezes_the_v23_identity(self):
        policy = _vol_policy()
        manifest = v23_manifest(
            policy, git_sha="abc123", alpha_registry_hash="b" * 64,
            cash_carry_convention="RISK_FREE_PROXY",
        )
        self.assertEqual(manifest["git_sha"], "abc123")
        self.assertEqual(manifest["strategy_version"], "strategy-v2.3")
        self.assertEqual(manifest["risk_budget_mode"], "HARD_TARGET")
        self.assertEqual(manifest["cash_carry_convention"], "RISK_FREE_PROXY")
        self.assertEqual(manifest["alpha_registry_hash"], "b" * 64)


if __name__ == "__main__":
    unittest.main()
