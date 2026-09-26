"""Regime-driven target-volatility scaling (Strategy V2.1 Phase A).

The regime acts on the volatility-budget engine by scaling the target
volatility band, not by raising the stable sleeve. Case C of the phase tests:
NORMAL / DEFENSIVE / CAPITAL_PRESERVATION must produce 25% / 20% / 15%
effective target volatility from the 25% base band.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import (
    PortfolioRiskInputs,
    effective_target_volatility,
    regime_target_volatility_multiplier,
    volatility_budget_scale,
)
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy(mode="volatility_budget"):
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = mode
    return policy_from_mapping(data)


def _inputs(vol_btc=0.6, vol_eth=0.7, rho=0.85):
    return PortfolioRiskInputs(
        asset_volatility={"BTC": vol_btc, "ETH": vol_eth},
        correlations={"BTC": {"ETH": rho}, "ETH": {"BTC": rho}},
    )


_ASSESSMENTS = {
    "BTC": {"weighted_score": 70, "confidence": "HIGH"},
    "ETH": {
        "weighted_score": 60, "confidence": "HIGH",
        "relative_strength_vs_btc": "OUTPERFORM",
    },
}


class EffectiveTargetVolatilityTests(unittest.TestCase):
    def test_case_c_multipliers_produce_the_preregistered_ladder(self):
        policy = _policy()
        base = policy.risk_engine["portfolio_risk"]["target_volatility"]
        self.assertAlmostEqual(base, 0.25)
        scaling = policy.risk_engine["regime_risk_scaling"]
        ladder = {
            regime: effective_target_volatility(
                base, regime_target_volatility_multiplier(regime, scaling)
            )
            for regime in ("NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION")
        }
        self.assertAlmostEqual(ladder["NORMAL"], 0.25)
        self.assertAlmostEqual(ladder["DEFENSIVE"], 0.20)
        self.assertAlmostEqual(ladder["CAPITAL_PRESERVATION"], 0.15)

    def test_effective_target_never_exceeds_the_base_band(self):
        self.assertAlmostEqual(effective_target_volatility(0.25, 1.0), 0.25)
        self.assertAlmostEqual(effective_target_volatility(0.25, 0.5), 0.125)
        with self.assertRaisesRegex(ValueError, "multiplier"):
            effective_target_volatility(0.25, 1.5)
        with self.assertRaisesRegex(ValueError, "multiplier"):
            effective_target_volatility(0.25, 0.0)

    def test_unknown_regime_has_no_multiplier(self):
        with self.assertRaisesRegex(ValueError, "no entry for regime"):
            regime_target_volatility_multiplier("TANTRUM", {})

    def test_scale_uses_the_effective_band(self):
        defensive = volatility_budget_scale(0.28, target_volatility=0.20, max_volatility=0.35)
        self.assertAlmostEqual(defensive["risk_scaling_factor"], 0.20 / 0.28)
        self.assertEqual(defensive["binding"], "volatility_budget")


class AllocationRegimeScalingTests(unittest.TestCase):
    def test_case_c_allocation_reports_the_effective_band_per_regime(self):
        policy = _policy()
        results = {
            regime: build_target_allocation(
                policy=policy, regime=regime, assessments=_ASSESSMENTS,
                risk_inputs=_inputs(vol_btc=0.10, vol_eth=0.12, rho=0.1),
            )
            for regime in ("NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION")
        }
        ladder = {
            regime: result.risk_engine["effective_target_volatility"]
            for regime, result in results.items()
        }
        self.assertAlmostEqual(ladder["NORMAL"], 0.25)
        self.assertAlmostEqual(ladder["DEFENSIVE"], 0.20)
        self.assertAlmostEqual(ladder["CAPITAL_PRESERVATION"], 0.15)
        for regime, result in results.items():
            block = result.risk_engine
            self.assertAlmostEqual(block["base_target_volatility"], 0.25)
            self.assertAlmostEqual(
                block["effective_target_volatility"],
                effective_target_volatility(
                    block["base_target_volatility"], block["regime_volatility_multiplier"]
                ),
            )
            self.assertEqual(block["market_regime"], regime)
            self.assertFalse(block["drawdown_influenced_regime"])

    def test_defensive_band_scales_the_risky_sleeve_further(self):
        policy = _policy()
        # Same wild inputs: the tighter defensive band must scale the sleeve
        # at least as hard as NORMAL, and never scale it up.
        normal = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_ASSESSMENTS,
            risk_inputs=_inputs(vol_btc=0.5, vol_eth=0.6, rho=0.8),
        )
        defensive = build_target_allocation(
            policy=policy, regime="DEFENSIVE", assessments=_ASSESSMENTS,
            risk_inputs=_inputs(vol_btc=0.5, vol_eth=0.6, rho=0.8),
        )
        self.assertLessEqual(
            1.0 - defensive.stable_sleeve_target,
            1.0 - normal.stable_sleeve_target + 1e-12,
        )

    def test_legacy_mode_has_no_volatility_multiplier_authority(self):
        policy = _policy("legacy_drawdown")
        result = build_target_allocation(
            policy=policy, regime="DEFENSIVE", assessments=_ASSESSMENTS,
            risk_inputs=_inputs(vol_btc=0.1, vol_eth=0.12, rho=0.1),
        )
        self.assertTrue(result.risk_engine["drawdown_influenced_regime"])
        self.assertNotIn("effective_target_volatility", result.risk_engine)


class PolicyValidationTests(unittest.TestCase):
    def test_regime_risk_scaling_is_mandatory_and_monotonic(self):
        data = json.loads(json.dumps(load_policy().as_dict()))
        data["risk_engine"]["mode"] = "volatility_budget"
        del data["risk_engine"]["regime_risk_scaling"]["DEFENSIVE"]
        with self.assertRaisesRegex(Exception, "regime_risk_scaling"):
            policy_from_mapping(data)

        data = json.loads(json.dumps(load_policy().as_dict()))
        data["risk_engine"]["mode"] = "volatility_budget"
        data["risk_engine"]["regime_risk_scaling"]["DEFENSIVE"][
            "target_volatility_multiplier"
        ] = 1.2
        with self.assertRaisesRegex(Exception, "monotonic|fraction|multiplier"):
            policy_from_mapping(data)

        data = json.loads(json.dumps(load_policy().as_dict()))
        data["risk_engine"]["mode"] = "volatility_budget"
        data["risk_engine"]["regime_risk_scaling"]["NORMAL"] = {"wrong_field": 1.0}
        with self.assertRaisesRegex(Exception, "regime_risk_scaling"):
            policy_from_mapping(data)

    def test_scaling_survives_the_policy_round_trip(self):
        policy = _policy()
        again = policy_from_mapping(policy.as_dict())
        self.assertEqual(
            again.risk_engine["regime_risk_scaling"],
            policy.risk_engine["regime_risk_scaling"],
        )


if __name__ == "__main__":
    unittest.main()
