"""Marginal risk contribution semantics (Strategy V2 Phase 1)."""

import unittest

from crypto_portfolio.engine.portfolio_risk import (
    marginal_risk_contributions,
    portfolio_volatility,
)


def _two_asset_covariance(sigma_a: float, sigma_b: float, rho: float):
    return {
        "A": {"A": sigma_a ** 2, "B": rho * sigma_a * sigma_b},
        "B": {"A": rho * sigma_a * sigma_b, "B": sigma_b ** 2},
    }


class MarginalRiskContributionTests(unittest.TestCase):
    def test_contributions_sum_to_portfolio_volatility(self):
        covariance = _two_asset_covariance(0.4, 0.8, 0.6)
        weights = {"A": 0.7, "B": 0.3}
        sigma = portfolio_volatility(weights, covariance)
        contributions = marginal_risk_contributions(weights, covariance)
        total = sum(item["marginal_risk_contribution"] for item in contributions.values())
        self.assertAlmostEqual(total, sigma)

    def test_contribution_shares_sum_to_one(self):
        covariance = _two_asset_covariance(0.5, 0.5, 0.9)
        contributions = marginal_risk_contributions({"A": 0.5, "B": 0.5}, covariance)
        self.assertAlmostEqual(
            sum(item["risk_contribution_share"] for item in contributions.values()), 1.0
        )

    def test_weight_equals_share_only_without_covariance(self):
        independent = _two_asset_covariance(0.5, 0.5, 0.0)
        contributions = marginal_risk_contributions({"A": 0.5, "B": 0.5}, independent)
        self.assertAlmostEqual(
            contributions["A"]["risk_contribution_share"], contributions["A"]["weight"]
        )
        # Any correlation pushes the share above the weight for both assets.
        correlated = _two_asset_covariance(0.5, 0.5, 0.9)
        correlated_contributions = marginal_risk_contributions({"A": 0.5, "B": 0.5}, correlated)
        self.assertGreater(
            correlated_contributions["A"]["risk_contribution_share"],
            correlated_contributions["A"]["weight"],
        )

    def test_small_high_risk_allocation_carries_more_risk_than_weight(self):
        covariance = _two_asset_covariance(0.3, 1.2, 0.7)
        # 90% calm asset, 10% wild asset: the wild one contributes a share of
        # portfolio risk well above its 10% weight (risk concentration shows
        # up here even when it is invisible in the weight table).
        contributions = marginal_risk_contributions({"A": 0.9, "B": 0.1}, covariance)
        self.assertGreater(
            contributions["B"]["risk_contribution_share"],
            contributions["B"]["weight"] * 2,
        )
        self.assertLess(
            contributions["A"]["risk_contribution_share"],
            contributions["A"]["weight"],
        )

    def test_invalid_weights_fail(self):
        covariance = _two_asset_covariance(0.3, 0.3, 0.0)
        with self.assertRaises(ValueError):
            marginal_risk_contributions({"A": -0.5}, covariance)
        with self.assertRaises(ValueError):
            marginal_risk_contributions({"A": float("inf")}, covariance)


if __name__ == "__main__":
    unittest.main()
