"""ETH tilt policy wiring (Strategy V2.2 Phase B).

The discrete alpha state travels with the frozen review contract into the
allocation engine, and the canonical policy keeps the tilt research-only
until the admission rule passes.
"""

import json
import unittest

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.engine.strategy_replay import ReplayReview
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _policy(**eth):
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["risk_engine"]["mode"] = "volatility_budget"
    data["core_allocation"]["eth"].update(eth)
    return policy_from_mapping(data)


def _inputs():
    return PortfolioRiskInputs(
        asset_volatility={"BTC": 0.10, "ETH": 0.12},
        correlations={"BTC": {"ETH": 0.3}, "ETH": {"BTC": 0.3}},
    )


_CORE = {
    "BTC": {"weighted_score": 75, "normalized_score": 75, "confidence": "HIGH"},
    "ETH": {"weighted_score": 70, "normalized_score": 70, "confidence": "HIGH",
            "relative_strength_vs_btc": 60},
}


class ReplayContractTests(unittest.TestCase):
    def test_review_carries_and_validates_the_alpha_state(self):
        review = ReplayReview(
            as_of="2026-01-01T00:00:00Z", period_end="2026-01-02T00:00:00Z",
            current_weights={"USD": 1.0}, portfolio_value=100.0,
            eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        self.assertEqual(review.eth_alpha_state, "ETH_ALPHA_POSITIVE")
        self.assertEqual(review.decision_view().eth_alpha_state, "ETH_ALPHA_POSITIVE")
        with self.assertRaisesRegex(ValueError, "eth_alpha_state"):
            ReplayReview(
                as_of="2026-01-01T00:00:00Z", period_end="2026-01-02T00:00:00Z",
                current_weights={"USD": 1.0}, portfolio_value=100.0,
                eth_alpha_state="ETH_ALPHA_HUGE",
            )

    def test_alpha_state_round_trips_through_mapping(self):
        review = ReplayReview.from_mapping({
            "as_of": "2026-01-01T00:00:00Z", "period_end": "2026-01-02T00:00:00Z",
            "current_weights": {"USD": 1.0}, "portfolio_value": 100.0,
            "eth_alpha_state": "eth_alpha_negative",
        })
        self.assertEqual(review.eth_alpha_state, "ETH_ALPHA_NEGATIVE")

    def test_state_is_optional_and_defaults_to_none(self):
        review = ReplayReview.from_mapping({
            "as_of": "2026-01-01T00:00:00Z", "period_end": "2026-01-02T00:00:00Z",
            "current_weights": {"USD": 1.0}, "portfolio_value": 100.0,
        })
        self.assertIsNone(review.eth_alpha_state)


class TiltPolicyTests(unittest.TestCase):
    def test_canonical_policy_keeps_the_tilt_research_only(self):
        data = load_policy().as_dict()
        self.assertFalse(data["core_allocation"]["eth"]["tilt_enabled"])
        self.assertAlmostEqual(data["core_allocation"]["eth"]["tilt_fraction_positive"], 0.2)

    def test_positive_state_moves_eth_only_when_tilt_enabled(self):
        research = build_target_allocation(
            policy=_policy(), regime="NORMAL", assessments=_CORE,
            risk_inputs=_inputs(), eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        self.assertAlmostEqual(research.target_weights.get("ETH", 0.0), 0.0)
        active = build_target_allocation(
            policy=_policy(tilt_enabled=True), regime="NORMAL", assessments=_CORE,
            risk_inputs=_inputs(), eth_alpha_state="ETH_ALPHA_POSITIVE",
        )
        self.assertAlmostEqual(active.target_weights["ETH"], 0.85 * 0.2)
        self.assertAlmostEqual(active.target_weights["BTC"], 0.50)

    def test_neutral_state_matches_disabled_tilt_outcome(self):
        neutral = build_target_allocation(
            policy=_policy(tilt_enabled=True), regime="NORMAL", assessments=_CORE,
            risk_inputs=_inputs(), eth_alpha_state="ETH_ALPHA_NEUTRAL",
        )
        self.assertAlmostEqual(neutral.target_weights.get("ETH", 0.0), 0.0)
        self.assertAlmostEqual(neutral.target_weights["BTC"], 0.50)


if __name__ == "__main__":
    unittest.main()
