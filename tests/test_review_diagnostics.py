import unittest
from crypto_portfolio.engine.review_diagnostics import portfolio_stress
from crypto_portfolio.models.policy import resolve_policy


class ReviewDiagnosticsTests(unittest.TestCase):
    def test_stress_is_diagnostic_and_requires_complete_scenario(self):
        policy = resolve_policy()
        result = portfolio_stress(
            {"BTC": 0.5, "ETH": 0.2, "AAVE": 0.1, "USDT": 0.2},
            policy=policy,
            drawdown=-0.01,
        )
        self.assertEqual(result["status"], "DIAGNOSTIC_ONLY")
        self.assertEqual(result["availability"], "AVAILABLE")
        self.assertTrue(result["budget_breach"])

        missing = portfolio_stress({"BTC": 0.8, "SOL": 0.1, "USDT": 0.1}, policy=policy)
        self.assertEqual(missing["availability"], "UNAVAILABLE")
        self.assertEqual(missing["missing_assets"], ["SOL"])
