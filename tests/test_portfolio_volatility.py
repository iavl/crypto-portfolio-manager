"""Portfolio volatility and covariance math (Strategy V2 Phase 1)."""

import math
import unittest

from crypto_portfolio.engine.portfolio_risk import (
    PortfolioRiskInputs,
    daily_returns,
    marginal_risk_contributions,
    portfolio_volatility,
    realized_volatility,
)


def _alternating_returns(count: int, up: float, down: float | None = None) -> list[float]:
    down = down if down is not None else -up
    return [up if index % 2 == 0 else down for index in range(count)]


def _inputs(vols: dict[str, float], correlations: dict[str, dict[str, float | None]] | None = None):
    return PortfolioRiskInputs(asset_volatility=vols, correlations=correlations or {})


class DailyReturnsTests(unittest.TestCase):
    def test_simple_returns(self):
        self.assertAlmostEqual(daily_returns([100.0, 110.0, 99.0])[0], 0.10)
        self.assertAlmostEqual(daily_returns([100.0, 110.0, 99.0])[1], -0.10)

    def test_invalid_prices_fail_closed(self):
        with self.assertRaises(ValueError):
            daily_returns([100.0, 0.0])
        with self.assertRaises(ValueError):
            daily_returns([100.0])
        with self.assertRaises(ValueError):
            daily_returns([100.0, float("nan")])


class PortfolioVolatilityTests(unittest.TestCase):
    def test_case_a_all_cash_has_zero_volatility(self):
        # The stable sleeve is outside the covariance model by construction:
        # the caller passes the risky sleeve only, and an empty one is vol 0.
        self.assertEqual(portfolio_volatility({}, {"BTC": {"BTC": 0.04}}), 0.0)
        # A weight on a symbol absent from the covariance model is an error
        # even for stable-looking names: cash-ness is the caller's classification.
        with self.assertRaisesRegex(ValueError, "missing"):
            portfolio_volatility({"USDT": 1.0}, {"BTC": {"BTC": 0.04}})

    def test_case_b_single_asset_volatility_is_the_asset_volatility(self):
        variance = 0.6 ** 2
        covariance = {"BTC": {"BTC": variance}}
        self.assertAlmostEqual(portfolio_volatility({"BTC": 1.0}, covariance), 0.6)
        # Partial exposure scales with weight, not weight squared.
        self.assertAlmostEqual(portfolio_volatility({"BTC": 0.5}, covariance), 0.3)

    def test_case_c_two_asset_closed_form(self):
        sigma_btc, sigma_eth, rho = 0.5, 0.6, 0.7
        covariance = {
            "BTC": {"BTC": sigma_btc ** 2, "ETH": rho * sigma_btc * sigma_eth},
            "ETH": {"BTC": rho * sigma_btc * sigma_eth, "ETH": sigma_eth ** 2},
        }
        expected = math.sqrt(
            0.25 * sigma_btc ** 2 + 0.25 * sigma_eth ** 2
            + 2 * 0.25 * rho * sigma_btc * sigma_eth
        )
        self.assertAlmostEqual(
            portfolio_volatility({"BTC": 0.5, "ETH": 0.5}, covariance), expected
        )

    def test_case_d_high_volatility_asset_raises_portfolio_risk(self):
        base = _inputs(
            {"BTC": 0.4, "ETH": 0.5},
            {"BTC": {"ETH": 0.8}, "ETH": {"BTC": 0.8}},
        )
        with_sol = _inputs(
            {"BTC": 0.4, "ETH": 0.5, "SOL": 0.9},
            {"BTC": {"ETH": 0.8, "SOL": 0.75}, "ETH": {"BTC": 0.8, "SOL": 0.75}, "SOL": {"BTC": 0.75, "ETH": 0.75}},
        )
        base_cov = base.covariance()
        weights = {"BTC": 0.35, "ETH": 0.25}
        before = portfolio_volatility(weights, base_cov)
        after = portfolio_volatility({**weights, "SOL": 0.10}, with_sol.covariance())
        self.assertGreater(after, before)

    def test_case_e_correlated_assets_are_not_diversification(self):
        # Two perfectly correlated 60%-vol assets average to 60% vol, exactly
        # like a single asset: correlation one removes every diversification
        # benefit, and the math must show it.
        perfect = _inputs({"A": 0.6, "B": 0.6}, {"A": {"B": 1.0}, "B": {"A": 1.0}})
        self.assertAlmostEqual(
            portfolio_volatility({"A": 0.5, "B": 0.5}, perfect.covariance()), 0.6
        )
        # The same assets uncorrelated diversify below either asset's vol.
        independent = _inputs({"A": 0.6, "B": 0.6}, {"A": {"B": 0.0}, "B": {"A": 0.0}})
        diversified = portfolio_volatility({"A": 0.5, "B": 0.5}, independent.covariance())
        self.assertAlmostEqual(diversified, 0.6 / math.sqrt(2))

    def test_exposed_symbol_without_covariance_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            portfolio_volatility({"BTC": 0.5, "SOL": 0.5}, {"BTC": {"BTC": 0.04}})

    def test_zero_weight_never_requires_covariance(self):
        covariance = {"BTC": {"BTC": 0.04}}
        self.assertAlmostEqual(portfolio_volatility({"BTC": 1.0, "SOL": 0.0}, covariance), 0.2)


class RealizedVolatilityTests(unittest.TestCase):
    def test_alternating_returns_annualize(self):
        returns = _alternating_returns(30, 0.02, -0.02)
        daily_variance = 0.02 ** 2  # population of alternating +-r has mean 0
        sample = sum((r - sum(returns) / len(returns)) ** 2 for r in returns) / (len(returns) - 1)
        expected = math.sqrt(sample if sample > 0 else daily_variance) * math.sqrt(365)
        self.assertAlmostEqual(realized_volatility(returns, annualization_days=365), expected)

    def test_insufficient_history_is_an_error(self):
        with self.assertRaises(ValueError):
            realized_volatility([0.01], annualization_days=365)


class RiskContributionTests(unittest.TestCase):
    def test_10pct_weight_can_carry_far_more_than_10pct_of_risk(self):
        sigma_btc, sigma_sol, rho = 0.4, 0.9, 0.8
        covariance = {
            "BTC": {"BTC": sigma_btc ** 2, "SOL": rho * sigma_btc * sigma_sol},
            "SOL": {"BTC": rho * sigma_btc * sigma_sol, "SOL": sigma_sol ** 2},
        }
        weights = {"BTC": 0.9, "SOL": 0.1}
        contributions = marginal_risk_contributions(weights, covariance)
        self.assertAlmostEqual(
            sum(item["risk_contribution_share"] for item in contributions.values()), 1.0
        )
        self.assertGreater(
            contributions["SOL"]["risk_contribution_share"],
            contributions["SOL"]["weight"],
            "a high-vol high-beta satellite must contribute more risk than weight",
        )

    def test_uncorrelated_equal_weights_split_risk_equally(self):
        covariance = {
            "A": {"A": 0.09, "B": 0.0},
            "B": {"A": 0.0, "B": 0.09},
        }
        contributions = marginal_risk_contributions({"A": 0.5, "B": 0.5}, covariance)
        self.assertAlmostEqual(
            contributions["A"]["risk_contribution_share"],
            contributions["B"]["risk_contribution_share"],
        )

    def test_empty_book_has_no_contributions(self):
        self.assertEqual(marginal_risk_contributions({}, {"A": {"A": 0.04}}), {})


if __name__ == "__main__":
    unittest.main()
