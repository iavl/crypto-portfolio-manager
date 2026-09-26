"""BTC residual allocation (Strategy V2.1 Phase D).

The BTC baseline absorbs core budget that the anchor split and the ETH gates
cannot justify, bounded by the single-asset cap and blocked only by a hard
BTC risk event — which is what unlocks the all-cash deployment path.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy():
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    # These tests document the frozen V2.1 anchor-core mechanics; the
    # canonical V2.2 btc-baseline core has its own test files.
    data["core_allocation"]["mode"] = "legacy_anchor"
    # V2.3 stress-loss budget disabled: this file pins its own mechanics
    # in isolation; the combined caps are pinned in test_stress_cap_integration.
    data["risk"]["stress_loss_budget"]["enabled"] = False
    return policy_from_mapping(data)


def _inputs():
    return PortfolioRiskInputs(
        asset_volatility={"BTC": 0.25, "ETH": 0.30},
        correlations={"BTC": {"ETH": 0.85}, "ETH": {"BTC": 0.85}},
    )


class BtcResidualTests(unittest.TestCase):
    def test_all_cash_with_weak_evidence_deploys_the_btc_baseline(self):
        policy = _policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={
                "BTC": {"weighted_score": 45, "confidence": "LOW",
                        "critical_data_complete": True},
                "ETH": {"weighted_score": 40, "confidence": "LOW",
                        "critical_data_complete": True,
                        "relative_strength_vs_btc": None},
            },
            current_weights={"USDT": 1.0},
            risk_inputs=_inputs(),
        )
        # The BTC baseline fills to its single-asset cap: the volatility
        # budget, not missing alpha evidence, governs the default asset.
        self.assertAlmostEqual(result.target_weights["BTC"], 0.50)
        self.assertAlmostEqual(result.target_weights.get("ETH", 0.0), 0.0)
        self.assertAlmostEqual(
            result.risk_engine["capital_hierarchy"]["btc_residual_allocation"], 0.50
        )

    def test_hard_btc_risk_blocks_the_baseline(self):
        policy = _policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={
                "BTC": {"weighted_score": 75, "confidence": "HIGH",
                        "event_risk": {"state": "SEVERE"}},
                "ETH": {"weighted_score": 40, "confidence": "LOW",
                        "relative_strength_vs_btc": None},
            },
            current_weights={"USDT": 1.0},
            risk_inputs=_inputs(),
        )
        self.assertEqual(result.target_weights.get("BTC", 0), 0)
        self.assertEqual(
            result.risk_engine["capital_hierarchy"]["btc_residual_allocation"], 0.0
        )

    def test_case_c_eth_strength_grows_the_eth_tilt(self):
        from crypto_portfolio.engine.allocation import _allocate_core

        policy = _policy()
        # Compared at the core split with a budget small enough that no
        # sleeve cap binds, so the anchor tilt is visible without the
        # water-fill masking it.
        btc = {"weighted_score": 75, "normalized_score": 75, "confidence": "HIGH"}
        weak, _, _ = _allocate_core(
            policy, 0.60,
            {"BTC": btc, "ETH": {"weighted_score": 70, "normalized_score": 70,
                                 "confidence": "MEDIUM", "relative_strength_vs_btc": 60}},
            {}, 0.50,
        )
        strong, residual, _ = _allocate_core(
            policy, 0.60,
            {"BTC": btc, "ETH": {"weighted_score": 90, "normalized_score": 90,
                                 "confidence": "HIGH", "relative_strength_vs_btc": 60}},
            {}, 0.50,
        )
        self.assertGreater(strong["ETH"], weak["ETH"])
        self.assertGreater(weak["BTC"], strong["BTC"])
        # ETH competes with BTC inside the same budget: the split sums to it.
        self.assertAlmostEqual(weak["BTC"] + weak["ETH"] + residual, 0.60)
        self.assertAlmostEqual(strong["BTC"] + strong["ETH"] + residual, 0.60)

    def test_residual_routing_is_bounded_by_the_single_asset_cap(self):
        policy = _policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={
                "BTC": {"weighted_score": 75, "normalized_score": 75, "confidence": "HIGH"},
                "ETH": {"weighted_score": 70, "normalized_score": 70, "confidence": "HIGH",
                        "relative_strength_vs_btc": 60},
            },
            risk_inputs=_inputs(),
        )
        self.assertLessEqual(result.target_weights["BTC"], 0.50 + 1e-9)


if __name__ == "__main__":
    unittest.main()
