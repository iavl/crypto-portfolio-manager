"""Core residual routing under the BTC baseline (Strategy V2.2 Phase A).

Where every core dollar goes when the btc-baseline core cannot absorb it:
the capital hierarchy routes unabsorbed budget to the BTC baseline before
cash, a hard BTC block leaves it as unused risk budget, and the full
allocation layer reconciles the core split with satellites and the stable
sleeve.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy(**core):
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    data["core_allocation"]["mode"] = "btc_baseline_with_active_tilts"
    # V2.3 stress-loss budget disabled: this file pins its own mechanics
    # in isolation; the combined caps are pinned in test_stress_cap_integration.
    data["risk"]["stress_loss_budget"]["enabled"] = False
    for key, value in core.items():
        if key == "eth":
            data["core_allocation"]["eth"].update(value)
        else:
            data["core_allocation"][key] = value
    return policy_from_mapping(data)


def _inputs():
    return PortfolioRiskInputs(
        asset_volatility={"BTC": 0.10, "ETH": 0.12},
        correlations={"BTC": {"ETH": 0.3}, "ETH": {"BTC": 0.3}},
    )


_BTC_STRONG = {"weighted_score": 75, "normalized_score": 75, "confidence": "HIGH"}
_ETH_ELIGIBLE = {
    "weighted_score": 70, "normalized_score": 70, "confidence": "HIGH",
    "relative_strength_vs_btc": 60,
}


class CoreResidualRoutingTests(unittest.TestCase):
    def test_cap_bound_residual_becomes_cash_not_a_cap_violation(self):
        policy = _policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE},
            current_weights={"USDT": 1.0}, risk_inputs=_inputs(),
        )
        # NORMAL: 15% stable floor leaves 85% risky; BTC caps at 50% and no
        # tilt is enabled, so 35% is unused risk budget held as cash.
        self.assertAlmostEqual(result.target_weights["BTC"], 0.50)
        self.assertAlmostEqual(result.target_weights.get("ETH", 0.0), 0.0)
        hierarchy = result.risk_engine["capital_hierarchy"]
        self.assertAlmostEqual(hierarchy["btc_residual_allocation"], 0.0)
        self.assertAlmostEqual(hierarchy["unused_risky_budget"], 0.35, places=6)
        self.assertAlmostEqual(
            result.risk_engine["cash_attribution"]["NO_ALPHA_CASH"], 0.35, places=6
        )

    def test_tilt_residual_still_flows_to_btc_headroom(self):
        policy = _policy(eth={"tilt_enabled": True})
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE},
            current_weights={"USDT": 1.0}, risk_inputs=_inputs(),
            eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        # Core budget 85%: ETH tilt 0.85*0.2 = 0.17, BTC baseline capped 0.50.
        self.assertAlmostEqual(result.target_weights["ETH"], 0.17)
        self.assertAlmostEqual(result.target_weights["BTC"], 0.50)
        self.assertAlmostEqual(
            result.risk_engine["capital_hierarchy"]["eth_alpha_tilt"], 0.17
        )
        self.assertAlmostEqual(
            result.risk_engine["cash_attribution"]["NO_ALPHA_CASH"], 0.18, places=6
        )

    def test_hard_btc_block_leaves_the_whole_core_budget_unused(self):
        severe = dict(_BTC_STRONG, event_risk={"state": "SEVERE"})
        policy = _policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": severe, "ETH": _ETH_ELIGIBLE},
            current_weights={"USDT": 1.0}, risk_inputs=_inputs(),
        )
        self.assertEqual(result.target_weights.get("BTC", 0.0), 0.0)
        self.assertAlmostEqual(
            result.risk_engine["capital_hierarchy"]["unused_risky_budget"], 0.85,
            places=6,
        )

    def test_satellite_envelope_comes_out_of_unused_btc_budget_first(self):
        policy = _policy()
        data = json.loads(json.dumps(policy.as_dict()))
        data["satellite_alpha"]["BNB"]["tilt_enabled"] = True
        policy = policy_from_mapping(data)
        satellite = {
            "factor_scores": {
                "trend": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
                "relative_strength_btc": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
                "capital_flows": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
                "valuation": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
                "fundamentals": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
                "onchain": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
            },
            "weighted_score": 85, "normalized_score": 85, "confidence": "HIGH",
            "critical_data_complete": True, "score_coverage": 1.0,
            "relative_strength_vs_btc": "OUTPERFORM",
        }
        # Strategy V2.3: the satellite deploys through its admitted
        # BTC-relative alpha tilt, not the generic composite score.
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE, "BNB": satellite},
            current_weights={"USDT": 1.0},
            risk_inputs=PortfolioRiskInputs(
                asset_volatility={"BTC": 0.10, "ETH": 0.12, "BNB": 0.15},
                correlations={
                    "BTC": {"ETH": 0.3, "BNB": 0.3},
                    "ETH": {"BTC": 0.3, "BNB": 0.3},
                    "BNB": {"BTC": 0.3, "ETH": 0.3},
                },
            ),
            satellite_alpha_states={"BNB": "BNB_ALPHA_POSITIVE"},
        )
        # The satellite deploys risk the capped BTC baseline could not use:
        # BTC stays at its cap and the no-alpha cash category shrinks.
        self.assertAlmostEqual(result.target_weights["BTC"], 0.50)
        self.assertGreater(result.target_weights.get("BNB", 0.0), 0.0)
        self.assertLess(
            result.risk_engine["cash_attribution"]["NO_ALPHA_CASH"], 0.35 - 1e-9,
        )

    def test_core_split_reconciles_with_the_whole_portfolio(self):
        policy = _policy(eth={"tilt_enabled": True})
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC_STRONG, "ETH": _ETH_ELIGIBLE},
            current_weights={"USDT": 1.0}, risk_inputs=_inputs(),
            eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        total = sum(result.target_weights.values())
        self.assertAlmostEqual(total, 1.0)
        stable = sum(
            result.target_weights.get(symbol, 0.0)
            for symbol in policy.stable_symbols
        )
        self.assertGreaterEqual(stable, policy.min_stablecoin_weight - 1e-9)


if __name__ == "__main__":
    unittest.main()
