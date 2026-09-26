"""Generic tactical conviction has no position authority (V2.3 Phase 1).

``conviction == TACTICAL_ONLY`` (and any market/structural composite score
state) can no longer produce a satellite position in volatility-budget mode:
without the asset's own admitted POSITIVE BTC-relative alpha there is no new
risk. The legacy risk-engine mode keeps the frozen V2.2 tactical slice for
A/B replay comparability until the Phase 7 canonical migration.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy(mode):
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = mode
    if mode == "volatility_budget":
        data["core_allocation"]["mode"] = "btc_baseline_with_active_tilts"
    return policy_from_mapping(data)


def _risk_inputs():
    return PortfolioRiskInputs(
        asset_volatility={"BTC": 0.35, "SOL": 0.80},
        correlations={"BTC": {"SOL": 0.7}, "SOL": {"BTC": 0.7}},
    )


def _tactical_only_sol():
    # Strong market family, structural evidence absent -> TACTICAL_ONLY.
    return {
        "factor_scores": {
            "trend": {"score": 90, "availability": "AVAILABLE", "reliability": 1.0},
            "relative_strength_btc": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
            "capital_flows": {"score": 80, "availability": "AVAILABLE", "reliability": 1.0},
            "valuation": {"score": None, "availability": "MISSING"},
            "fundamentals": {"score": None, "availability": "MISSING"},
            "onchain": {"score": None, "availability": "MISSING"},
        },
        "weighted_score": 60,
        "normalized_score": None,
        "confidence": "HIGH",
        "critical_data_complete": True,
        "score_coverage": 0.55,
        "relative_strength_vs_btc": "OUTPERFORM",
    }


def _full_conviction_sol():
    assessment = _tactical_only_sol()
    assessment["factor_scores"].update({
        "valuation": {"score": 80, "availability": "AVAILABLE", "reliability": 1.0},
        "fundamentals": {"score": 82, "availability": "AVAILABLE", "reliability": 1.0},
        "onchain": {"score": 78, "availability": "AVAILABLE", "reliability": 1.0},
    })
    assessment["weighted_score"] = 82
    assessment["normalized_score"] = 82
    assessment["score_coverage"] = 1.0
    return assessment


_BTC = {"weighted_score": 75, "normalized_score": 75, "confidence": "HIGH"}


class GenericTacticalNoAuthorityTests(unittest.TestCase):
    def test_vol_mode_tactical_only_gets_no_position(self):
        policy = _policy("volatility_budget")
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC, "SOL": _tactical_only_sol()},
            risk_inputs=_risk_inputs(),
        )
        self.assertNotIn("SOL", result.target_weights)
        allowance = result.deployment_allowances["SOL"]
        self.assertEqual(allowance["conviction_state"], "TACTICAL_ONLY")
        self.assertEqual(allowance["alpha_authority"], "RESEARCH_ONLY")
        self.assertNotEqual(allowance["final_score_authority"], "TACTICAL_FRACTION")

    def test_vol_mode_full_conviction_alone_still_gets_no_position(self):
        # Strategy V2.3 Phase 5's demotion, pinned here: neither the composite
        # score nor its conviction state deploys; only admitted alpha does.
        policy = _policy("volatility_budget")
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC, "SOL": _full_conviction_sol()},
            risk_inputs=_risk_inputs(),
        )
        self.assertNotIn("SOL", result.target_weights)
        allowance = result.deployment_allowances["SOL"]
        self.assertEqual(allowance["conviction_state"], "FULL_CONVICTION")
        self.assertEqual(allowance["alpha_authority"], "RESEARCH_ONLY")

    def test_vol_mode_high_score_curve_alone_creates_no_target(self):
        policy = _policy("volatility_budget")
        held = {"BTC": 0.6, "SOL": 0.03, "USDT": 0.37}
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"BTC": _BTC, "SOL": _full_conviction_sol()},
            current_weights=held,
            risk_inputs=_risk_inputs(),
        )
        # No new risk: the strategic target never exceeds the held weight no
        # matter how strong the generic composite score is.
        self.assertLessEqual(result.target_weights.get("SOL", 0.0), 0.03 + 1e-9)
        self.assertTrue(result.deployment_allowances["SOL"].get("preserve_existing"))

    def test_legacy_mode_keeps_the_frozen_tactical_slice(self):
        policy = _policy("legacy_drawdown")
        tactical_fraction = policy.allocation["tactical_fraction"]
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={"SOL": _tactical_only_sol()},
        )
        allowance = result.deployment_allowances["SOL"]
        self.assertEqual(allowance["final_score_authority"], "TACTICAL_FRACTION")
        self.assertAlmostEqual(
            result.target_weights["SOL"], 0.25 * tactical_fraction,
        )
        self.assertEqual(allowance["alpha_authority"], "LEGACY_PATH")

    def test_states_for_unknown_symbols_are_rejected(self):
        policy = _policy("volatility_budget")
        with self.assertRaises(ValueError):
            build_target_allocation(
                policy=policy, regime="NORMAL",
                assessments={"BTC": _BTC, "SOL": _tactical_only_sol()},
                risk_inputs=_risk_inputs(),
                satellite_alpha_states={"SOL": "NOT_A_STATE"},
            )


if __name__ == "__main__":
    unittest.main()
