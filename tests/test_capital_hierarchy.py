"""Capital hierarchy (Strategy V2.1 Phase D).

BTC is the default risky asset in volatility-budget mode: budget that no ETH
or satellite alpha case can justify returns to the BTC baseline before it
becomes cash. Cash then means unused risk budget, never failed allocation.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy(**hierarchy):
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    # These tests document the frozen V2.1 anchor-core mechanics; the
    # canonical V2.2 btc-baseline core has its own test files.
    data["core_allocation"]["mode"] = "legacy_anchor"
    if hierarchy:
        data["capital_hierarchy"].update(hierarchy)
    return policy_from_mapping(data)


def _inputs():
    return PortfolioRiskInputs(
        asset_volatility={"BTC": 0.25, "ETH": 0.30, "SOL": 0.55},
        correlations={
            "BTC": {"ETH": 0.85, "SOL": 0.8},
            "ETH": {"BTC": 0.85, "SOL": 0.8},
            "SOL": {"BTC": 0.8, "ETH": 0.8},
        },
    )


_CORE = {
    "BTC": {"weighted_score": 75, "normalized_score": 75, "confidence": "HIGH"},
    "ETH": {"weighted_score": 70, "normalized_score": 70, "confidence": "HIGH",
            "relative_strength_vs_btc": 60},
}


def _satellite(score=85):
    return {
        "factor_scores": {
            "trend": {"score": score, "availability": "AVAILABLE", "reliability": 1.0},
            "relative_strength_btc": {"score": score, "availability": "AVAILABLE", "reliability": 1.0},
            "capital_flows": {"score": score, "availability": "AVAILABLE", "reliability": 1.0},
            "valuation": {"score": score, "availability": "AVAILABLE", "reliability": 1.0},
            "fundamentals": {"score": score, "availability": "AVAILABLE", "reliability": 1.0},
            "onchain": {"score": score, "availability": "AVAILABLE", "reliability": 1.0},
        },
        "weighted_score": score,
        "normalized_score": score,
        "confidence": "HIGH",
        "critical_data_complete": True,
        "score_coverage": 1.0,
        "relative_strength_vs_btc": "OUTPERFORM",
    }


class CapitalHierarchyConfigTests(unittest.TestCase):
    def test_hierarchy_fields_are_validated(self):
        with self.assertRaisesRegex(Exception, "default_risky_asset"):
            _policy(default_risky_asset="ETH")
        data = json.loads(json.dumps(load_policy().as_dict()))
        data["risk_engine"]["mode"] = "volatility_budget"
        del data["capital_hierarchy"]["btc_baseline_enabled"]
        with self.assertRaisesRegex(Exception, "capital_hierarchy"):
            policy_from_mapping(data)

    def test_hierarchy_is_reported_and_round_trips(self):
        policy = _policy()
        block = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_CORE, risk_inputs=_inputs(),
        ).risk_engine["capital_hierarchy"]
        self.assertEqual(block["default_risky_asset"], "BTC")
        self.assertTrue(block["btc_baseline_enabled"])
        self.assertAlmostEqual(block["approved_risky_budget"], 0.85)

    def test_legacy_mode_has_no_hierarchy_authority(self):
        data = json.loads(json.dumps(load_policy().as_dict()))
        policy = policy_from_mapping(data)
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_CORE,
        )
        self.assertNotIn("capital_hierarchy", result.risk_engine)


class HierarchyBehaviorTests(unittest.TestCase):
    def test_case_b_systemic_risk_scales_the_budget_not_the_hierarchy(self):
        policy = _policy()
        normal = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_CORE, risk_inputs=_inputs(),
        )
        defensive = build_target_allocation(
            policy=policy, regime="CAPITAL_PRESERVATION", assessments=_CORE, risk_inputs=_inputs(),
        )
        # Unused budget is cash: a defensive market shrinks the effective
        # volatility band, so more of the sleeve stays cash by design.
        self.assertGreaterEqual(
            defensive.risk_engine["capital_hierarchy"]["unused_risky_budget"],
            normal.risk_engine["capital_hierarchy"]["unused_risky_budget"] - 1e-9,
        )

    def test_case_d_satellite_conviction_competes_with_btc_not_cash(self):
        policy = _policy()
        without = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_CORE, risk_inputs=_inputs(),
        )
        with_satellite = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={**_CORE, "SOL": _satellite()},
            risk_inputs=_inputs(),
        )
        satellite_weight = with_satellite.target_weights.get("SOL", 0.0)
        self.assertGreater(satellite_weight, 0)
        # The satellite's dollars come out of BTC's baseline: the core budget
        # shrinks by exactly the satellite's envelope share, and cash is
        # unchanged (the sleeve total is set by the risk budget).
        btc_delta = without.target_weights["BTC"] - with_satellite.target_weights["BTC"]
        self.assertGreater(btc_delta, 0)
        # The satellite's entry converts previously cap-bound budget into
        # deployed risk: the raw sleeve uses the full approved budget and the
        # no-alpha cash category drops to zero.
        used_without = without.risk_engine["capital_hierarchy"]["used_risky_budget"]
        used_with = with_satellite.risk_engine["capital_hierarchy"]["used_risky_budget"]
        self.assertGreaterEqual(used_with, used_without - 1e-9)
        self.assertAlmostEqual(
            with_satellite.risk_engine["cash_attribution"]["NO_ALPHA_CASH"], 0.0, places=6
        )

    def test_case_e_missing_satellite_evidence_does_not_enlarge_cash(self):
        policy = _policy()
        no_satellite = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_CORE, risk_inputs=_inputs(),
        )
        broken_satellite = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={
                **_CORE,
                "SOL": {"weighted_score": 30, "confidence": "HIGH",
                        "critical_data_complete": True, "score_coverage": 1.0,
                        "relative_strength_vs_btc": "OUTPERFORM"},
            },
            risk_inputs=_inputs(),
        )
        # A satellite with no alpha case leaves the budget on the BTC
        # baseline: cash weight is identical, BTC absorbs what SOL declined.
        self.assertAlmostEqual(
            broken_satellite.risk_engine["capital_hierarchy"]["used_risky_budget"],
            no_satellite.risk_engine["capital_hierarchy"]["used_risky_budget"],
            delta=1e-9,
        )
        self.assertAlmostEqual(
            broken_satellite.target_weights["BTC"],
            no_satellite.target_weights["BTC"],
            delta=1e-9,
        )


if __name__ == "__main__":
    unittest.main()
