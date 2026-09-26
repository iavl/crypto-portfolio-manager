"""Volatility-budget risk-engine mode (Strategy V2 Phase 1).

Covers the mode switch, the scaling math, fail-closed inputs, and the A/B
guarantee: legacy mode must reproduce Strategy V1 allocation exactly, and
volatility_budget mode must bound the risky sleeve by the combined cap
minimum rather than stacked multipliers.
"""

import json
import random
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import (
    PortfolioRiskInputs,
    combined_risk_cap,
    volatility_budget_scale,
)
from crypto_portfolio.engine.risk import run_risk_gate
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _vol_policy(mode: str = "volatility_budget", **overrides):
    data = json.loads(json.dumps(load_policy().as_dict()))
    engine = data["risk_engine"]
    engine["mode"] = mode
    # These tests document the frozen V2.1 anchor-core mechanics; the
    # canonical V2.2 btc-baseline core has its own test files.
    data["core_allocation"]["mode"] = "legacy_anchor"
    for key, value in overrides.items():
        if key in engine["portfolio_risk"]:
            engine["portfolio_risk"][key] = value
    return policy_from_mapping(data)


def _synthetic_inputs(vol_btc=0.6, vol_eth=0.7, rho=0.85):
    return PortfolioRiskInputs(
        asset_volatility={"BTC": vol_btc, "ETH": vol_eth},
        correlations={"BTC": {"ETH": rho}, "ETH": {"BTC": rho}},
    )


_ASSESSMENTS = {
    "BTC": {"weighted_score": 70, "normalized_score": 70, "confidence": "HIGH"},
    "ETH": {
        "weighted_score": 60, "normalized_score": 60, "confidence": "HIGH",
        "relative_strength_vs_btc": "OUTPERFORM",
    },
}


class VolatilityBudgetScaleTests(unittest.TestCase):
    def test_scale_is_target_over_volatility(self):
        result = volatility_budget_scale(0.4, target_volatility=0.25, max_volatility=0.35)
        self.assertAlmostEqual(result["risk_scaling_factor"], 0.625)
        self.assertEqual(result["binding"], "volatility_budget")

    def test_within_budget_never_scales_up(self):
        result = volatility_budget_scale(0.2, target_volatility=0.25, max_volatility=0.35)
        self.assertEqual(result["risk_scaling_factor"], 1.0)
        self.assertEqual(result["binding"], "none")

    def test_max_band_flags_without_extra_shrink(self):
        result = volatility_budget_scale(0.5, target_volatility=0.25, max_volatility=0.35)
        self.assertTrue(result["max_volatility_exceeded"])
        self.assertAlmostEqual(result["risk_scaling_factor"], 0.5)

    def test_zero_volatility_consumes_no_budget(self):
        result = volatility_budget_scale(0.0, target_volatility=0.25, max_volatility=0.35)
        self.assertEqual(result["risk_scaling_factor"], 1.0)


class CombinedRiskCapTests(unittest.TestCase):
    def test_tightest_cap_wins_without_multiplying(self):
        result = combined_risk_cap({"volatility": 0.6, "emergency": 0.75, "liveness": 0.7})
        self.assertAlmostEqual(result["cap"], 0.6)
        self.assertEqual(result["binding_constraint"], "volatility")

    def test_none_candidates_are_ignored(self):
        result = combined_risk_cap({"a": None, "b": 0.5})
        self.assertAlmostEqual(result["cap"], 0.5)

    def test_empty_candidates_leave_the_sleeve_unbounded(self):
        result = combined_risk_cap({})
        self.assertEqual(result["cap"], 1.0)
        self.assertEqual(result["binding_constraint"], "none")


class RiskEngineModeTests(unittest.TestCase):
    def test_legacy_mode_reproduces_v1_allocation_exactly(self):
        legacy = _vol_policy("legacy_drawdown")
        baseline = build_target_allocation(
            policy=legacy, regime="NORMAL", assessments=_ASSESSMENTS,
            portfolio_drawdown=-0.06, risk_inputs=_synthetic_inputs(),
        )
        current = build_target_allocation(
            policy=load_policy(), regime="NORMAL", assessments=_ASSESSMENTS,
            portfolio_drawdown=-0.06,
        )
        self.assertEqual(dict(baseline.target_weights), dict(current.target_weights))
        self.assertEqual(baseline.risk_engine["mode"], "legacy_drawdown")
        self.assertEqual(
            baseline.risk_engine["emergency_overlay_state"]["state"], "LEGACY_LADDER"
        )

    def test_volatility_budget_requires_risk_inputs(self):
        policy = _vol_policy()
        with self.assertRaisesRegex(ValueError, "portfolio risk inputs"):
            build_target_allocation(policy=policy, regime="NORMAL", assessments=_ASSESSMENTS)

    def test_high_volatility_inputs_scale_the_risky_sleeve_down(self):
        policy = _vol_policy()
        calm = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_ASSESSMENTS,
            risk_inputs=_synthetic_inputs(vol_btc=0.10, vol_eth=0.12, rho=0.1),
        )
        wild = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_ASSESSMENTS,
            risk_inputs=_synthetic_inputs(vol_btc=0.90, vol_eth=1.1, rho=0.95),
        )
        calm_risky = 1.0 - calm.stable_sleeve_target
        wild_risky = 1.0 - wild.stable_sleeve_target
        self.assertGreater(calm_risky, 0.5)
        self.assertLess(wild_risky, calm_risky)
        self.assertEqual(wild.risk_engine["mode"], "volatility_budget")
        self.assertLess(wild.risk_engine["risk_scaling_factor"], 1.0)
        self.assertEqual(
            wild.risk_engine["volatility_budget_scale"]["binding"], "volatility_budget"
        )
        self.assertGreater(
            wild.risk_engine["portfolio_volatility"],
            policy.risk_engine["portfolio_risk"]["target_volatility"],
        )
        # The freed weight lands in the stable sleeve, not in thin air.
        self.assertAlmostEqual(sum(wild.target_weights.values()), 1.0)

    def test_within_budget_leaves_strategic_target_untouched(self):
        policy = _vol_policy()
        calm = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_ASSESSMENTS,
            risk_inputs=_synthetic_inputs(vol_btc=0.08, vol_eth=0.10, rho=0.1),
        )
        self.assertAlmostEqual(calm.risk_engine["risk_scaling_factor"], 1.0)
        self.assertEqual(calm.risk_engine["binding_risk_constraint"], "strategic_target")
        self.assertAlmostEqual(
            calm.risk_engine["asset_risk_contributions"]["BTC"]["weight"]
            + calm.risk_engine["asset_risk_contributions"]["ETH"]["weight"],
            1.0 - calm.stable_sleeve_target,
        )

    def test_risk_contribution_shares_sum_to_one(self):
        policy = _vol_policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_ASSESSMENTS,
            risk_inputs=_synthetic_inputs(vol_btc=0.7, vol_eth=0.9, rho=0.8),
        )
        contributions = result.risk_engine["asset_risk_contributions"]
        self.assertTrue(contributions)
        self.assertAlmostEqual(
            sum(item["risk_contribution_share"] for item in contributions.values()), 1.0
        )

    def test_ab_replay_modes_differ_on_the_same_inputs(self):
        legacy = _vol_policy("legacy_drawdown")
        budget = _vol_policy()
        inputs = _synthetic_inputs(vol_btc=0.8, vol_eth=0.95, rho=0.9)
        legacy_result = build_target_allocation(
            policy=legacy, regime="NORMAL", assessments=_ASSESSMENTS,
            risk_inputs=inputs,
        )
        budget_result = build_target_allocation(
            policy=budget, regime="NORMAL", assessments=_ASSESSMENTS,
            risk_inputs=inputs,
        )
        legacy_risky = 1.0 - legacy_result.stable_sleeve_target
        budget_risky = 1.0 - budget_result.stable_sleeve_target
        # A/B is the point: with a hot covariance the budget mode must hold
        # less risky weight than the legacy drawdown-only path at no drawdown.
        self.assertLess(budget_risky, legacy_risky)

    def test_missing_covariance_for_allocated_asset_fails_closed(self):
        policy = _vol_policy()
        # SOL is satellite-eligible on a strong score but has no covariance row.
        assessments = {
            "BTC": {"weighted_score": 70, "normalized_score": 70, "confidence": "HIGH"},
            "ETH": {
                "weighted_score": 60, "normalized_score": 60, "confidence": "HIGH",
                "relative_strength_vs_btc": "OUTPERFORM",
            },
            "SOL": {
                "weighted_score": 90, "normalized_score": 90, "confidence": "HIGH",
                "relative_strength_vs_btc": "OUTPERFORM",
            },
        }
        with self.assertRaisesRegex(ValueError, "missing"):
            build_target_allocation(
                policy=policy, regime="NORMAL", assessments=assessments,
                risk_inputs=_synthetic_inputs(),
            )

    def test_volatility_mode_passes_the_risk_gate(self):
        policy = _vol_policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_ASSESSMENTS,
            risk_inputs=_synthetic_inputs(vol_btc=0.5, vol_eth=0.6, rho=0.8),
        )
        gate = run_risk_gate(result, policy=policy, regime="NORMAL")
        self.assertTrue(gate.ok, [item.as_dict() for item in gate.errors])


class PortfolioRiskInputsTests(unittest.TestCase):
    def test_round_trip_serialization(self):
        inputs = _synthetic_inputs()
        restored = PortfolioRiskInputs.from_mapping(inputs.as_dict())
        self.assertEqual(restored.asset_volatility, inputs.asset_volatility)
        self.assertEqual(
            restored.covariance()["ETH"]["BTC"], inputs.covariance()["ETH"]["BTC"]
        )

    def test_asymmetric_correlations_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "symmetric"):
            PortfolioRiskInputs(
                asset_volatility={"A": 0.3, "B": 0.3},
                correlations={"A": {"B": 0.5}, "B": {"A": 0.4}},
            )

    def test_build_from_closes_requires_btc_history(self):
        from crypto_portfolio.engine.portfolio_risk import (
            build_portfolio_risk_inputs_from_closes,
        )

        rng = random.Random(3)
        closes = {"BTC": [100.0 * (1.0 + rng.gauss(0, 0.02)) for _ in range(120)]}
        inputs = build_portfolio_risk_inputs_from_closes(
            closes,
            window_weights={"30d": 0.4, "90d": 0.6},
            correlation_window_days=90,
            annualization_days=365,
            minimum_history_days=100,
        )
        self.assertIn("BTC", inputs.asset_volatility)
        short = {"BTC": [100.0] * 10}
        with self.assertRaises(ValueError):
            build_portfolio_risk_inputs_from_closes(
                short,
                window_weights={"30d": 0.4, "90d": 0.6},
                correlation_window_days=90,
                annualization_days=365,
                minimum_history_days=100,
            )


if __name__ == "__main__":
    unittest.main()
