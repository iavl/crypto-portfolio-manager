"""Cash attribution by cause (Strategy V2.1 Phase D).

Every cash dollar in the final target carries a cause: minimum reserve,
volatility budget, emergency brake, no-alpha budget, or unallocated
residual. A high-cash book must be explainable, not just observed.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy():
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    return policy_from_mapping(data)


def _inputs(vol_btc=0.25, vol_eth=0.30):
    return PortfolioRiskInputs(
        asset_volatility={"BTC": vol_btc, "ETH": vol_eth},
        correlations={"BTC": {"ETH": 0.85}, "ETH": {"BTC": 0.85}},
    )


_CORE = {
    "BTC": {"weighted_score": 75, "normalized_score": 75, "confidence": "HIGH"},
    "ETH": {"weighted_score": 70, "normalized_score": 70, "confidence": "HIGH",
            "relative_strength_vs_btc": 60},
}

_CATEGORIES = (
    "MINIMUM_RESERVE_CASH", "VOLATILITY_BUDGET_CASH", "EMERGENCY_CASH",
    "NO_ALPHA_CASH", "EXECUTION_PENDING_CASH", "UNALLOCATED_RESIDUAL",
)


class CashAttributionTests(unittest.TestCase):
    def test_categories_sum_to_the_final_stable_weight(self):
        policy = _policy()
        for regime in ("NORMAL", "CAPITAL_PRESERVATION"):
            result = build_target_allocation(
                policy=policy, regime=regime, assessments=_CORE,
                risk_inputs=_inputs(vol_btc=0.9, vol_eth=1.1),
            )
            attribution = result.risk_engine["cash_attribution"]
            for category in _CATEGORIES:
                self.assertIn(category, attribution)
            stable = sum(
                weight for symbol, weight in result.target_weights.items()
                if symbol in policy.stable_symbols
            )
            self.assertAlmostEqual(sum(attribution.values()), stable, places=6)
            self.assertAlmostEqual(attribution["MINIMUM_RESERVE_CASH"], 0.15)
            self.assertGreater(attribution["VOLATILITY_BUDGET_CASH"], 0)

    def test_emergency_drawdown_creates_emergency_cash(self):
        policy = _policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_CORE,
            portfolio_drawdown=-0.14, risk_inputs=_inputs(),
        )
        attribution = result.risk_engine["cash_attribution"]
        self.assertEqual(
            result.risk_engine["emergency_overlay_state"]["state"], "EMERGENCY"
        )
        self.assertGreater(attribution["EMERGENCY_CASH"], 0)

    def test_no_alpha_cash_shrinks_when_btc_can_absorb(self):
        policy = _policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL", assessments=_CORE, risk_inputs=_inputs(),
        )
        # BTC and ETH both eligible: only the 1pp crumb beyond both sleeve
        # caps (0.50 + 0.34 of the 0.85 budget) remains unexplained by alpha,
        # and it is bounded by that cap arithmetic, not by missing evidence.
        self.assertLessEqual(result.risk_engine["cash_attribution"]["NO_ALPHA_CASH"], 0.0101)

    def test_all_cash_weak_evidence_cash_is_explained(self):
        policy = _policy()
        result = build_target_allocation(
            policy=policy, regime="NORMAL",
            assessments={
                "BTC": {"weighted_score": 45, "confidence": "LOW",
                        "critical_data_complete": True},
                "ETH": {"weighted_score": 40, "confidence": "LOW",
                        "relative_strength_vs_btc": None},
            },
            current_weights={"USDT": 1.0},
            risk_inputs=_inputs(),
        )
        attribution = result.risk_engine["cash_attribution"]
        stable = sum(
            weight for symbol, weight in result.target_weights.items()
            if symbol in policy.stable_symbols
        )
        self.assertAlmostEqual(sum(attribution.values()), stable, places=6)
        # 15% minimum reserve plus the budget beyond BTC's own cap.
        self.assertAlmostEqual(attribution["MINIMUM_RESERVE_CASH"], 0.15)
        self.assertAlmostEqual(attribution["NO_ALPHA_CASH"], 0.35)


if __name__ == "__main__":
    unittest.main()
