"""ETH tilt attribution and opportunity cost (Strategy V2.2 Phase B, 5.8).

The attribution reconstructs, from the replay record alone, what the ETH
tilt requested, what the risk engine allowed, and what holding ETH instead
of BTC actually cost or earned over the following period.
"""

import unittest

from crypto_portfolio.engine.eth_relative_alpha import eth_tilt_attribution
from crypto_portfolio.models.policy import load_policy


def _row(state: str, requested_budget: float, final: float):
    return {
        "as_of": "2026-01-01T00:00:00Z",
        "eth_alpha_state": state,
        "allocation": {"risk_engine": {"capital_hierarchy": {
            "approved_risky_budget": requested_budget,
            "satellite_alpha_tilt": 0.05,
            "eth_alpha_tilt": final,
        }}},
    }


class _Review:
    def __init__(self, eth: float | None, btc: float | None):
        self.next_returns = (
            {"ETH": eth, "BTC": btc} if eth is not None else {}
        )


class EthTiltAttributionTests(unittest.TestCase):
    def test_requested_and_final_tilt_are_reconstructed(self):
        policy = load_policy()
        rows = [
            _row("ETH_ALPHA_POSITIVE", 0.85, 0.10),
            _row("ETH_ALPHA_NEUTRAL", 0.85, 0.0),
            _row("ETH_ALPHA_POSITIVE", 0.85, 0.17),
        ]
        reviews = [_Review(0.01, 0.02), _Review(-0.01, 0.01), _Review(0.03, 0.01)]
        report = eth_tilt_attribution(rows, reviews, policy=policy)
        self.assertEqual(report["reviews"], 3)
        self.assertEqual(
            report["state_counts"],
            {"ETH_ALPHA_NEUTRAL": 1, "ETH_ALPHA_POSITIVE": 2},
        )
        # Tilt requested = 20% of the core budget (0.85 - 0.05 satellite) on
        # each POSITIVE review.
        self.assertAlmostEqual(report["total_requested_tilt_weight"], 0.32)
        self.assertAlmostEqual(report["total_final_tilt_weight"], 0.27)
        first = report["entries"][0]
        self.assertAlmostEqual(first["tilt_requested_weight"], 0.16)
        self.assertAlmostEqual(first["tilt_final_weight"], 0.10)
        self.assertAlmostEqual(first["ethbtc_opportunity_cost_return"], -0.01)

    def test_risk_engine_constraint_is_the_unhonored_share(self):
        policy = load_policy()
        rows = [_row("ETH_ALPHA_POSITIVE", 0.80, 0.04)]
        report = eth_tilt_attribution(rows, [_Review(None, None)], policy=policy)
        entry = report["entries"][0]
        # 0.2 * (0.80 - 0.05) = 0.15 requested, 0.04 deployed.
        self.assertAlmostEqual(entry["tilt_requested_weight"], 0.15)
        self.assertAlmostEqual(entry["tilt_constrained_by_risk_engine"], 0.11)
        self.assertIsNone(entry["ethbtc_opportunity_cost_return"])

    def test_missing_state_defaults_to_neutral(self):
        policy = load_policy()
        rows = [_row(None, 0.85, 0.0)]
        report = eth_tilt_attribution(rows, [_Review(0.0, 0.0)], policy=policy)
        self.assertEqual(report["state_counts"], {"ETH_ALPHA_NEUTRAL": 1})
        self.assertAlmostEqual(report["total_requested_tilt_weight"], 0.0)


if __name__ == "__main__":
    unittest.main()
