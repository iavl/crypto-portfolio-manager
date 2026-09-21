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

        # Every managed risky asset now has an explicit scenario return, so
        # the diagnostic can run instead of silently turning itself off.
        for symbol in policy.satellite_symbols:
            held = {"BTC": 0.6, symbol: 0.2, "USDT": 0.2}
            with self.subTest(symbol=symbol):
                self.assertNotEqual(
                    portfolio_stress(held, policy=policy, drawdown=-0.01)["availability"],
                    "UNAVAILABLE",
                )

        # An unmanaged holding has no scenario return; the diagnostic fails
        # closed and names the asset rather than assuming a zero shock.
        missing = portfolio_stress({"BTC": 0.8, "LUNC": 0.1, "USDT": 0.1}, policy=policy)
        self.assertEqual(missing["availability"], "UNAVAILABLE")
        self.assertEqual(missing["missing_assets"], ["LUNC"])

    def test_diagnostics_label_planned_and_strategic_scenarios_separately(self):
        from crypto_portfolio.engine.review_diagnostics import build_review_diagnostics

        result = build_review_diagnostics(
            current_weights={"BTC": 0.5, "USDT": 0.5},
            target_weights={"BTC": 0.6, "USDT": 0.4},
            portfolio_value=1000,
            actions=(),
            policy=resolve_policy(),
        )
        self.assertEqual(result["scenarios"]["PLANNED_PROPOSALS"]["name"], "PLANNED_PROPOSALS")
        strategic = result["scenarios"]["STRATEGIC_TARGET"]
        self.assertEqual(strategic["weights"], {"BTC": 0.6, "USDT": 0.4})
        self.assertIn("not a proposed", strategic["basis"])
