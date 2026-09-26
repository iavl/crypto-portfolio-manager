"""Stress-loss budget math (Strategy V2.3 Phase 3)."""

import unittest

from crypto_portfolio.engine.stress_budget import (
    stress_budget_diagnostics,
    stress_loss_by_scenario,
    stress_loss_cap,
)
from crypto_portfolio.models.policy import load_policy


SCENARIOS = {
    "severe_crypto_crash": {"BTC": -0.4, "ETH": -0.55, "BNB": -0.55},
    "btc_gap_down": {"BTC": -0.3, "ETH": -0.35, "BNB": -0.35},
    "eth_alt_crash": {"BTC": -0.1, "ETH": -0.45, "BNB": -0.45},
}


class StressLossTests(unittest.TestCase):
    def test_loss_is_the_weighted_sum_per_scenario(self):
        losses = stress_loss_by_scenario(
            {"BTC": 0.5, "ETH": 0.3, "BNB": 0.2}, SCENARIOS,
        )
        self.assertAlmostEqual(losses["severe_crypto_crash"], -(0.2 + 0.165 + 0.11))
        self.assertAlmostEqual(losses["btc_gap_down"], -(0.15 + 0.105 + 0.07))

    def test_unpriced_exposure_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "does not price"):
            stress_loss_by_scenario({"SOL": 0.2}, SCENARIOS)

    def test_invalid_returns_are_rejected(self):
        with self.assertRaises(ValueError):
            stress_loss_by_scenario({"BTC": 0.5}, {"bad": {"BTC": 0.2}})
        with self.assertRaises(ValueError):
            stress_loss_by_scenario({"BTC": 0.5}, {"bad": {"BTC": float("nan")}})

    def test_zero_weights_do_not_require_pricing(self):
        losses = stress_loss_by_scenario({"BTC": 0.5, "SOL": 0.0}, SCENARIOS)
        self.assertAlmostEqual(losses["btc_gap_down"], -0.15)

    def test_cap_solves_the_worst_scenario_against_the_budget(self):
        solver = stress_loss_cap({"BTC": 0.5}, SCENARIOS, 0.15)
        self.assertEqual(solver["binding_stress_scenario"], "severe_crypto_crash")
        # 0.15 budget / 0.20 worst loss = 0.75 sleeve scale.
        self.assertAlmostEqual(solver["cap"], 0.75)
        self.assertAlmostEqual(solver["stress_budget_utilization"], 0.20 / 0.15)

    def test_within_budget_cap_is_one(self):
        solver = stress_loss_cap({"BTC": 0.3}, SCENARIOS, 0.15)
        self.assertAlmostEqual(solver["cap"], 1.0)
        self.assertAlmostEqual(solver["stress_budget_utilization"], 0.12 / 0.15)

    def test_no_loss_means_no_constraint(self):
        solver = stress_loss_cap({"BTC": 0.0}, SCENARIOS, 0.15)
        self.assertIsNone(solver["cap"])

    def test_invalid_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            stress_loss_cap({"BTC": 0.5}, SCENARIOS, 0.0)

    def test_diagnostics_report_every_scenario(self):
        block = stress_budget_diagnostics({"BTC": 0.5, "ETH": 0.35}, SCENARIOS, 0.15)
        self.assertEqual(block["contract"], "STRESS_LOSS_BUDGET_V23")
        self.assertEqual(
            sorted(block["stress_loss_by_scenario"]), sorted(SCENARIOS),
        )
        self.assertEqual(block["binding_stress_scenario"], "severe_crypto_crash")
        self.assertAlmostEqual(
            block["stress_loss_by_scenario"]["severe_crypto_crash"],
            -(0.2 + 0.1925),
        )
        self.assertLess(block["stress_cap"], 1.0)

    def test_canonical_scenarios_cover_the_managed_universe(self):
        policy = load_policy()
        scenarios = policy.stress_scenarios
        for symbol in (*policy.core_symbols, *policy.satellite_symbols):
            for name, returns in scenarios.items():
                self.assertIn(symbol, returns, f"{name}/{symbol}")


if __name__ == "__main__":
    unittest.main()
