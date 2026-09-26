"""Stress cap integration into the volatility-budget engine (V2.3 Phase 3).

The final risky sleeve is the tightest of strategic / volatility / stress /
emergency caps — caps never multiply. Stress-bound de-risking lands in a
named cash category, and every layer keeps a distinct role: realized
volatility (vol budget), crash loss (stress budget), portfolio drawdown
(emergency overlay).
"""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy(**risk):
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    data["core_allocation"]["mode"] = "btc_baseline_with_active_tilts"
    data["risk"].update(risk)
    return policy_from_mapping(data)


def _inputs(vol_btc=0.10, vol_eth=0.12):
    return PortfolioRiskInputs(
        asset_volatility={"BTC": vol_btc, "ETH": vol_eth},
        correlations={"BTC": {"ETH": 0.5}, "ETH": {"BTC": 0.5}},
    )


_BTC = {"weighted_score": 75, "normalized_score": 75, "confidence": "HIGH"}
_ETH = {
    "weighted_score": 70, "normalized_score": 70, "confidence": "HIGH",
    "relative_strength_vs_btc": 60,
}


class StressCapIntegrationTests(unittest.TestCase):
    def test_btc_baseline_is_capped_by_the_stress_budget(self):
        policy = _policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC}, risk_inputs=_inputs(),
        )
        engine = result.risk_engine
        self.assertEqual(engine["binding_risk_constraint"], "stress_loss_budget")
        # BTC-only book: severe scenario -40%, 15% budget -> 0.375 sleeve.
        self.assertAlmostEqual(result.target_weights["BTC"], 0.375, places=6)
        self.assertEqual(
            engine["stress_budget"]["binding_stress_scenario"], "severe_crypto_crash",
        )
        self.assertGreater(engine["stress_budget"]["stress_budget_utilization"], 1.0)
        self.assertGreater(
            result.risk_engine["cash_attribution"]["STRESS_BUDGET_CASH"], 0,
        )

    def test_caps_never_multiply(self):
        policy = _policy()
        calm = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC}, risk_inputs=_inputs(),
        )
        wild = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC},
            risk_inputs=_inputs(vol_btc=0.9, vol_eth=1.0),
        )
        calm_risky = 1.0 - calm.stable_sleeve_target
        wild_risky = 1.0 - wild.stable_sleeve_target
        # The volatility budget would shrink the wild book further, but the
        # stress cap already binds on both; the min() means the calm and wild
        # books can only get closer, never compound below the tightest cap.
        self.assertAlmostEqual(calm_risky, 0.375, places=6)
        self.assertLessEqual(wild_risky, calm_risky + 1e-9)
        self.assertEqual(
            wild.risk_engine["binding_risk_constraint"], "volatility_budget",
        )

    def test_emergency_brake_still_wins_when_tighter(self):
        policy = _policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC},
            portfolio_drawdown=-0.14,  # EMERGENCY: cap 0.6 < stress 0.375? no -
            risk_inputs=_inputs(),
        )
        # 0.375 stress cap is tighter than the 0.6 emergency cap here, so the
        # stress budget binds; a breach cap (0.25) is the tighter brake:
        breached = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC},
            portfolio_drawdown=-0.15 - 1e-9,  # BREACH: cap 0.25
            risk_inputs=_inputs(),
        )
        self.assertEqual(
            breached.risk_engine["emergency_overlay_state"]["state"], "BREACH",
        )
        self.assertAlmostEqual(
            1.0 - breached.stable_sleeve_target, 0.25, places=6,
        )
        self.assertEqual(result.risk_engine["binding_risk_constraint"], "stress_loss_budget")

    def test_disabled_stress_budget_leaves_the_sleeve_to_other_caps(self):
        policy = _policy(stress_loss_budget={"enabled": False})
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC}, risk_inputs=_inputs(),
        )
        self.assertIsNone(result.risk_engine["stress_budget"])
        self.assertEqual(result.risk_engine["binding_risk_constraint"], "strategic_target")
        # Without the stress layer the BTC-only book is bounded by the
        # single-asset cap (0.50), not the crash budget.
        self.assertAlmostEqual(1.0 - result.stable_sleeve_target, 0.50, places=6)

    def test_warning_band_uses_the_hard_limit_not_the_warning_level(self):
        # Same BTC+ETH book under both modes: the WARNING_BAND sleeve is
        # sized against its 0.25 hard limit, the HARD_TARGET sleeve against
        # the 0.15 budget — the warning level (0.15) itself no longer sizes.
        book = {
            "BTC": _BTC,
            "ETH": {**_ETH, "relative_strength_vs_btc": "OUTPERFORM"},
        }
        inputs = _inputs()
        hard_data = json.loads(json.dumps(_policy().as_dict()))
        hard_data["core_allocation"]["eth"]["tilt_enabled"] = True
        hard = build_target_allocation(
            policy=policy_from_mapping(hard_data), regime="NORMAL", assessments=book,
            risk_inputs=inputs, eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        warning_data = json.loads(json.dumps(_policy().as_dict()))
        warning_data["core_allocation"]["eth"]["tilt_enabled"] = True
        warning_data["risk"]["drawdown_budget_mode"] = "WARNING_BAND"
        warning_data["risk"]["hard_stress_loss_limit"] = 0.25
        warning = build_target_allocation(
            policy=policy_from_mapping(warning_data), regime="NORMAL",
            assessments=book, risk_inputs=inputs,
            eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        self.assertEqual(warning.risk_engine["drawdown_budget_mode"], "WARNING_BAND")
        self.assertAlmostEqual(warning.risk_engine["stress_budget"]["budget"], 0.25)
        hard_risky = 1.0 - hard.stable_sleeve_target
        warning_risky = 1.0 - warning.stable_sleeve_target
        # raw sleeve = BTC 0.50 (cap) + ETH tilt 0.17; worst loss 0.2935.
        self.assertAlmostEqual(hard_risky, 0.67 * 0.15 / 0.2935, places=6)
        self.assertAlmostEqual(warning_risky, 0.67 * 0.25 / 0.2935, places=6)
        self.assertGreater(warning_risky, hard_risky)

    def test_alpha_tilt_and_baseline_share_the_stress_scale(self):
        data = json.loads(json.dumps(_policy().as_dict()))
        data["satellite_alpha"]["BNB"]["tilt_enabled"] = True
        policy = policy_from_mapping(data)
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC, "BNB": {
                "weighted_score": 80, "normalized_score": 80, "confidence": "HIGH",
                "relative_strength_vs_btc": "OUTPERFORM",
                "critical_data_complete": True, "score_coverage": 1.0,
            }},
            risk_inputs=PortfolioRiskInputs(
                asset_volatility={"BTC": 0.10, "ETH": 0.12, "BNB": 0.20},
                correlations={
                    "BTC": {"ETH": 0.5, "BNB": 0.5},
                    "ETH": {"BTC": 0.5, "BNB": 0.5},
                    "BNB": {"BTC": 0.5, "ETH": 0.5},
                },
            ),
            satellite_alpha_states={"BNB": "BNB_ALPHA_POSITIVE"},
        )
        scale = result.risk_engine["risk_scaling_factor"]
        self.assertEqual(result.risk_engine["binding_risk_constraint"], "stress_loss_budget")
        # The tilt request is 10% of the approved budget; the final book is
        # the whole raw sleeve scaled uniformly by the stress cap.
        self.assertAlmostEqual(
            result.deployment_allowances["BNB"]["requested_strategic_weight"], 0.085,
        )
        self.assertAlmostEqual(
            result.target_weights["BNB"], 0.085 * scale, places=6,
        )

    def test_legacy_mode_never_applies_the_stress_cap(self):
        data = json.loads(json.dumps(load_policy().as_dict()))
        policy = policy_from_mapping(data)
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments={"BTC": _BTC},
        )
        self.assertNotIn("stress_budget", result.risk_engine)
        self.assertGreater(1.0 - result.stable_sleeve_target, 0.375)


if __name__ == "__main__":
    unittest.main()
