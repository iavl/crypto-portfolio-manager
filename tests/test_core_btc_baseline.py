"""BTC-default core baseline (Strategy V2.2 Phase A).

The approved core budget belongs to the BTC baseline first; ETH earns core
budget only through an explicitly approved active tilt. These tests pin the
plan's Cases A and C plus the hard-block semantics for the default asset.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import (
    _allocate_core,
    build_target_allocation,
)
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy(**core):
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    data["core_allocation"]["mode"] = "btc_baseline_with_active_tilts"
    for key, value in core.items():
        if key == "eth":
            data["core_allocation"]["eth"].update(value)
        else:
            data["core_allocation"][key] = value
    return policy_from_mapping(data)


def _inputs():
    return PortfolioRiskInputs(
        asset_volatility={"BTC": 0.25, "ETH": 0.30},
        correlations={"BTC": {"ETH": 0.85}, "ETH": {"BTC": 0.85}},
    )


_BTC_STRONG = {"weighted_score": 75, "normalized_score": 75, "confidence": "HIGH"}
_ETH_ELIGIBLE = {
    "weighted_score": 70, "normalized_score": 70, "confidence": "HIGH",
    "relative_strength_vs_btc": 60,
}


class BtcBaselineCoreTests(unittest.TestCase):
    def test_case_a_zero_tilt_routes_the_whole_core_budget_to_btc(self):
        policy = _policy()
        weights, residual, reasons = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
        )
        self.assertAlmostEqual(weights["BTC"], 0.50)
        self.assertAlmostEqual(weights.get("ETH", 0.0), 0.0)
        self.assertAlmostEqual(residual, 0.0)
        self.assertTrue(any("tilt_enabled=false" in reason for reason in reasons))

    def test_case_c_btc_never_crosses_the_single_asset_cap(self):
        policy = _policy()
        weights, residual, reasons = _allocate_core(
            policy, 0.85, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
        )
        self.assertAlmostEqual(weights["BTC"], 0.50)
        self.assertAlmostEqual(weights.get("ETH", 0.0), 0.0)
        self.assertAlmostEqual(residual, 0.35)
        self.assertTrue(any("single-asset cap" in reason for reason in reasons))

    def test_weak_btc_evidence_does_not_block_the_baseline(self):
        policy = _policy()
        weak = {
            "weighted_score": 40, "normalized_score": 40, "confidence": "LOW",
            "critical_data_complete": True,
        }
        weights, residual, _ = _allocate_core(
            policy, 0.50, {"BTC": weak, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
        )
        self.assertAlmostEqual(weights["BTC"], 0.50)
        self.assertAlmostEqual(residual, 0.0)

    def test_missing_btc_assessment_still_deploys_the_baseline(self):
        policy = _policy()
        weights, _, _ = _allocate_core(
            policy, 0.50, {"ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
        )
        self.assertAlmostEqual(weights["BTC"], 0.50)

    def test_severe_btc_event_blocks_the_baseline(self):
        policy = _policy()
        severe = dict(_BTC_STRONG, event_risk={"state": "SEVERE"})
        weights, residual, reasons = _allocate_core(
            policy, 0.50, {"BTC": severe, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
        )
        self.assertNotIn("BTC", weights)
        self.assertAlmostEqual(residual, 0.50)
        self.assertTrue(any("hard-blocked" in reason for reason in reasons))

    def test_broken_thesis_blocks_the_baseline(self):
        policy = _policy()
        broken = dict(_BTC_STRONG, thesis_broken=True)
        weights, _, _ = _allocate_core(
            policy, 0.50, {"BTC": broken, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
        )
        self.assertNotIn("BTC", weights)

    def test_critical_liveness_blocks_the_baseline(self):
        policy = _policy()
        weights, _, _ = _allocate_core(
            policy, 0.50, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
            chain_liveness={"BTC": {"status": "HALTED"}},
        )
        self.assertNotIn("BTC", weights)

    def test_case_e_legacy_mode_replays_the_fixed_anchor(self):
        policy = _policy(mode="legacy_anchor")
        weights, _, _ = _allocate_core(
            policy, 0.85, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
        )
        # Legacy anchor split: ETH still earns its 30% anchor share under the
        # eligibility gates instead of the BTC baseline absorbing everything.
        self.assertGreater(weights.get("ETH", 0.0), 0.25)
        self.assertAlmostEqual(weights["BTC"], 0.50)

    def test_legacy_risk_mode_keeps_the_legacy_anchor_core(self):
        # The live pipeline (legacy drawdown engine) must not see its core
        # semantics rewritten by the V2.2 strategy config.
        data = json.loads(json.dumps(load_policy().as_dict()))
        data["core_allocation"]["mode"] = "btc_baseline_with_active_tilts"
        policy = policy_from_mapping(data)
        self.assertEqual(policy.risk_engine["mode"], "legacy_drawdown")
        legacy = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE},
        )
        # The legacy drawdown engine keeps the anchor-based split (nonzero
        # ETH) even though the configured core mode is the BTC baseline.
        self.assertGreater(legacy.target_weights.get("ETH", 0.0), 0.0)
        self.assertTrue(any(
            "legacy BTC/ETH anchor" in item for item in legacy.constraints_applied
        ))

    def test_zero_budget_returns_no_core_weights(self):
        policy = _policy()
        weights, residual, _ = _allocate_core(
            policy, 0.0, {"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE}, {}, 0.50,
            risk_mode="volatility_budget",
        )
        self.assertEqual(weights, {})
        self.assertAlmostEqual(residual, 0.0)

    def test_canonical_policy_uses_the_btc_baseline_mode(self):
        data = json.loads(json.dumps(load_policy().as_dict()))
        self.assertEqual(data["core_allocation"]["mode"], "btc_baseline_with_active_tilts")
        self.assertEqual(data["core_allocation"]["default_core_asset"], "BTC")
        self.assertEqual(
            data["core_allocation"]["legacy_anchor"], {"BTC": 0.7, "ETH": 0.3}
        )
        self.assertFalse(data["core_allocation"]["eth"]["tilt_enabled"])


if __name__ == "__main__":
    unittest.main()
