import unittest

from crypto_portfolio.engine.ledger import (
    ExternalCashFlow,
    PortfolioSnapshot,
    build_nav_history,
    cash_flow_adjusted_return,
    current_drawdown,
    max_drawdown,
)


class LedgerTests(unittest.TestCase):
    def test_deposit_is_not_return(self):
        states = build_nav_history(
            [PortfolioSnapshot("2026-01-01", 20000), PortfolioSnapshot("2026-01-02", 25000, 5000)]
        )
        self.assertAlmostEqual(cash_flow_adjusted_return([PortfolioSnapshot("2026-01-01", 20000), PortfolioSnapshot("2026-01-02", 25000, 5000)]), 0)
        self.assertAlmostEqual(states[-1].nav_per_unit, 1.0)

    def test_explicit_cash_flow_object_is_supported(self):
        flow = ExternalCashFlow("2026-01-02T00:00:00Z", 5000)
        states = build_nav_history(
            [PortfolioSnapshot("2026-01-01T00:00:00Z", 20000), PortfolioSnapshot("2026-01-02T00:00:00Z", 25000, flow)]
        )
        self.assertAlmostEqual(states[-1].nav_per_unit, 1.0)

    def test_withdrawal_is_not_loss(self):
        states = build_nav_history(
            [PortfolioSnapshot("2026-01-01", 20000), PortfolioSnapshot("2026-01-02", 15000, -5000)]
        )
        self.assertAlmostEqual(states[-1].nav_per_unit, 1.0)
        self.assertAlmostEqual(cash_flow_adjusted_return([PortfolioSnapshot("2026-01-01", 20000), PortfolioSnapshot("2026-01-02", 15000, -5000)]), 0)

    def test_positive_and_negative_market_returns(self):
        positive = build_nav_history([PortfolioSnapshot("2026-01-01", 100), PortfolioSnapshot("2026-01-02", 110)])[1]
        negative = build_nav_history([PortfolioSnapshot("2026-01-01", 100), PortfolioSnapshot("2026-01-02", 90)])[1]
        self.assertAlmostEqual(positive.nav_per_unit, 1.1)
        self.assertAlmostEqual(negative.nav_per_unit, 0.9)

    def test_repeated_flows_are_unit_neutral(self):
        snapshots = [
            PortfolioSnapshot("2026-01-01", 100),
            PortfolioSnapshot("2026-01-02", 150, 50),
            PortfolioSnapshot("2026-01-03", 125, -25),
            PortfolioSnapshot("2026-01-04", 175, 50),
            PortfolioSnapshot("2026-01-05", 150, -25),
        ]
        self.assertAlmostEqual(cash_flow_adjusted_return(snapshots), 0)

    def test_flows_and_market_movement_are_separated(self):
        deposit_loss = build_nav_history(
            [PortfolioSnapshot("2026-01-01", 100), PortfolioSnapshot("2026-01-02", 140, 50), PortfolioSnapshot("2026-01-03", 135)]
        )
        withdrawal_gain = build_nav_history(
            [PortfolioSnapshot("2026-01-01", 100), PortfolioSnapshot("2026-01-02", 88, -20)]
        )
        self.assertAlmostEqual(deposit_loss[-1].nav_per_unit, 0.9)
        self.assertAlmostEqual(withdrawal_gain[-1].nav_per_unit, 1.1)

    def test_current_and_max_drawdown_use_nav(self):
        states = build_nav_history(
            [
                PortfolioSnapshot("2026-01-01", 100),
                PortfolioSnapshot("2026-01-02", 90),
                PortfolioSnapshot("2026-01-03", 95),
                PortfolioSnapshot("2026-01-04", 80),
            ]
        )
        self.assertAlmostEqual(current_drawdown(states), -0.2)
        self.assertAlmostEqual(max_drawdown(states), -0.2)

    def test_invalid_zero_and_ordered_history_fail(self):
        with self.assertRaises(ValueError):
            build_nav_history([PortfolioSnapshot("2026-01-01", 0)])
        with self.assertRaises(ValueError):
            build_nav_history([PortfolioSnapshot("2026-01-02", 100), PortfolioSnapshot("2026-01-01", 100)])


if __name__ == "__main__":
    unittest.main()
