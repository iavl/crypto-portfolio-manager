"""Asset-specific satellite alpha routing (Strategy V2.3 Phase 1).

In volatility-budget mode, satellite NEW risk routes exclusively through the
asset's own admitted BTC-relative alpha ensemble: BNB/AAVE tilt only when
their ensemble is POSITIVE and the policy unlocks the tilt; SOL is
research-only and can never earn production authority. Risk envelope, event
risk, liveness, and tier caps stay shared.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _vol_policy(**satellite_alpha):
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    data["core_allocation"]["mode"] = "btc_baseline_with_active_tilts"
    for symbol, entry in satellite_alpha.items():
        data["satellite_alpha"][symbol].update(entry)
    return policy_from_mapping(data)


def _risk_inputs():
    return PortfolioRiskInputs(
        asset_volatility={"BTC": 0.35, "BNB": 0.45, "AAVE": 0.60, "SOL": 0.80},
        correlations={
            "BTC": {"BNB": 0.8, "AAVE": 0.75, "SOL": 0.7},
            "BNB": {"BTC": 0.8, "AAVE": 0.8, "SOL": 0.8},
            "AAVE": {"BTC": 0.75, "BNB": 0.8, "SOL": 0.8},
            "SOL": {"BTC": 0.7, "BNB": 0.8, "AAVE": 0.8},
        },
    )


def _satellite(structural: bool, **fields):
    factors = {
        "trend": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
        "relative_strength_btc": {"score": 80, "availability": "AVAILABLE", "reliability": 1.0},
        "capital_flows": {"score": 75, "availability": "AVAILABLE", "reliability": 1.0},
    }
    if structural:
        factors.update({
            "valuation": {"score": 72, "availability": "AVAILABLE", "reliability": 1.0},
            "fundamentals": {"score": 74, "availability": "AVAILABLE", "reliability": 1.0},
            "onchain": {"score": 70, "availability": "AVAILABLE", "reliability": 1.0},
        })
    return {
        "factor_scores": factors,
        "weighted_score": 78 if structural else 60,
        "normalized_score": 78 if structural else None,
        "confidence": "HIGH",
        "critical_data_complete": True,
        "score_coverage": 1.0 if structural else 0.55,
        "relative_strength_vs_btc": "OUTPERFORM",
        **fields,
    }


_BTC = {"weighted_score": 75, "normalized_score": 75, "confidence": "HIGH"}


class AssetSpecificRoutingTests(unittest.TestCase):
    def test_admitted_positive_alpha_earns_the_preregistered_tilt(self):
        policy = _vol_policy(BNB={"tilt_enabled": True})
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC, "BNB": _satellite(structural=True)},
            risk_inputs=_risk_inputs(),
            satellite_alpha_states={"BNB": "BNB_ALPHA_POSITIVE"},
        )
        allowance = result.deployment_allowances["BNB"]
        self.assertEqual(allowance["eligibility_state"], "ELIGIBLE_INCREASE")
        self.assertEqual(allowance["alpha_authority"], "ADMITTED_TILT")
        self.assertEqual(allowance["final_score_authority"], "ADMITTED_ALPHA_TILT")
        # Preregistered tilt REQUEST: 10% of the approved risky budget (85%
        # in NORMAL). The V2.3 stress-loss budget then scales the whole
        # sleeve (crash loss participates in ex-ante sizing), so the final
        # target is the request times the engine's uniform scale factor.
        self.assertAlmostEqual(allowance["requested_strategic_weight"], 0.10 * 0.85, places=6)
        scale = result.risk_engine["risk_scaling_factor"]
        self.assertLess(scale, 1.0)
        self.assertEqual(
            result.risk_engine["binding_risk_constraint"], "stress_loss_budget",
        )
        self.assertAlmostEqual(
            result.target_weights["BNB"], 0.10 * 0.85 * scale, places=6,
        )

    def test_neutral_alpha_never_tilts_even_when_unlocked(self):
        policy = _vol_policy(BNB={"tilt_enabled": True})
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC, "BNB": _satellite(structural=True)},
            risk_inputs=_risk_inputs(),
            satellite_alpha_states={"BNB": "BNB_ALPHA_NEUTRAL"},
        )
        self.assertNotIn("BNB", result.target_weights)
        allowance = result.deployment_allowances["BNB"]
        self.assertEqual(allowance["alpha_authority"], "LOCKED")

    def test_locked_policy_ignores_a_positive_alpha_state(self):
        policy = _vol_policy()  # canonical tilt_enabled stays false
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC, "BNB": _satellite(structural=True)},
            risk_inputs=_risk_inputs(),
            satellite_alpha_states={"BNB": "BNB_ALPHA_POSITIVE"},
        )
        self.assertNotIn("BNB", result.target_weights)
        self.assertEqual(
            result.deployment_allowances["BNB"]["alpha_authority"], "LOCKED",
        )

    def test_research_only_sol_never_receives_new_risk(self):
        policy = _vol_policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC, "SOL": _satellite(structural=True)},
            risk_inputs=_risk_inputs(),
            satellite_alpha_states={"SOL": "SOL_ALPHA_POSITIVE"},
        )
        self.assertNotIn("SOL", result.target_weights)
        self.assertEqual(
            result.deployment_allowances["SOL"]["alpha_authority"], "RESEARCH_ONLY",
        )

    def test_alpha_tilt_is_bounded_by_the_risk_envelope(self):
        policy = _vol_policy(BNB={"tilt_enabled": True, "tilt_fraction_positive": 0.5})
        held = {"BTC": 0.4, "BNB": 0.02, "USDT": 0.58}
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC, "BNB": _satellite(structural=True)},
            current_weights=held,
            risk_inputs=_risk_inputs(),
            satellite_alpha_states={"BNB": "BNB_ALPHA_POSITIVE"},
        )
        envelope = result.deployment_allowances["BNB"]["risk_envelope_weight"]
        self.assertLessEqual(result.target_weights["BNB"], envelope + 1e-9)

    def test_invalid_alpha_state_names_are_rejected(self):
        policy = _vol_policy(BNB={"tilt_enabled": True})
        with self.assertRaisesRegex(ValueError, "BNB_ALPHA_POSITIVE"):
            build_target_allocation(
                policy=policy, regime="NORMAL",
                assessments={"BTC": _BTC, "BNB": _satellite(structural=True)},
                risk_inputs=_risk_inputs(),
                satellite_alpha_states={"BNB": "ETH_ALPHA_POSITIVE"},
            )

    def test_held_position_without_alpha_is_preserved_not_exited(self):
        policy = _vol_policy()
        held = {"BTC": 0.5, "AAVE": 0.05, "USDT": 0.45}
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC, "AAVE": _satellite(structural=True)},
            current_weights=held,
            risk_inputs=_risk_inputs(),
            satellite_alpha_states={"AAVE": "AAVE_ALPHA_NEUTRAL"},
        )
        allowance = result.deployment_allowances["AAVE"]
        self.assertEqual(allowance["eligibility_state"], "ELIGIBLE_INCREASE")
        self.assertTrue(allowance.get("preserve_existing"))
        # No new risk: the target never exceeds the held weight.
        self.assertLessEqual(result.target_weights.get("AAVE", 0.0), 0.05 + 1e-9)
        self.assertTrue(any("alpha gate" in reason for reason in result.allocation_reasons))

    def test_alpha_attribution_fields_are_reported(self):
        policy = _vol_policy(BNB={"tilt_enabled": True})
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC, "BNB": _satellite(structural=True)},
            risk_inputs=_risk_inputs(),
            satellite_alpha_states={"BNB": "BNB_ALPHA_POSITIVE"},
        )
        allowance = result.deployment_allowances["BNB"]
        self.assertEqual(allowance["alpha_state"], "BNB_ALPHA_POSITIVE")
        self.assertEqual(allowance["alpha_tilt_fraction"], 0.10)


if __name__ == "__main__":
    unittest.main()
