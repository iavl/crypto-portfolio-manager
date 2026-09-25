"""Deterministic risk-tier derivation and hysteresis (Strategy V2 Phase 4)."""

import unittest

from crypto_portfolio.engine.risk_tier import (
    deterministic_risk_tier,
    estimate_risk_tiers,
)
from crypto_portfolio.models.policy import load_policy


def _tier(vol=0.8, btc_vol=0.5, beta=1.0, previous=None):
    return deterministic_risk_tier(
        asset_volatility=vol, btc_volatility=btc_vol, beta_to_btc=beta,
        previous_tier=previous, policy=load_policy(),
    )


class DeterministicRiskTierTests(unittest.TestCase):
    def test_low_vol_beta_near_one_is_normal(self):
        result = _tier(vol=0.5, btc_vol=0.5, beta=1.0)
        self.assertEqual(result["tier"], "normal")
        self.assertEqual(result["source"], "DETERMINISTIC_ESTIMATE")

    def test_high_vol_and_high_beta_is_high_beta(self):
        self.assertEqual(_tier(vol=0.95, btc_vol=0.5, beta=1.8)["tier"], "high_beta")
        self.assertEqual(_tier(vol=0.5, btc_vol=0.5, beta=1.8)["tier"], "high_beta")
        self.assertEqual(_tier(vol=0.95, btc_vol=0.5, beta=1.0)["tier"], "high_beta")

    def test_measurement_never_produces_the_manual_high_tier(self):
        # ``high`` stays reserved for explicitly supplied severity.
        for vol, beta in ((3.0, 4.0), (5.0, 0.5), (0.5, 5.0)):
            self.assertEqual(_tier(vol=vol, btc_vol=0.5, beta=beta)["tier"], "high_beta")

    def test_provenance_is_carried(self):
        result = _tier(vol=0.9, btc_vol=0.5, beta=1.7)
        self.assertEqual(result["source"], "DETERMINISTIC_ESTIMATE")
        self.assertAlmostEqual(result["basis"]["volatility_ratio"], 1.8)
        self.assertAlmostEqual(result["basis"]["beta_to_btc"], 1.7)
        self.assertEqual(result["basis"]["entered_from"], "both")

    def test_invalid_inputs_fail_closed(self):
        with self.assertRaises(ValueError):
            _tier(vol=0.0, btc_vol=0.5)
        with self.assertRaises(ValueError):
            _tier(vol=0.8, btc_vol=0.0)
        with self.assertRaises(ValueError):
            deterministic_risk_tier(
                asset_volatility=0.8, btc_volatility=0.5, beta_to_btc=None,
                previous_tier="extreme", policy=load_policy(),
            )


class EstimateRiskTiersTests(unittest.TestCase):
    def test_measured_assets_get_tiers_btc_is_the_anchor(self):
        result = estimate_risk_tiers(
            annualized_volatility={"BTC": 0.5, "ETH": 0.55, "SOL": 1.1},
            beta={"ETH": 1.1, "SOL": 1.7},
            btc_symbol="BTC",
            policy=load_policy(),
        )
        self.assertNotIn("BTC", result)
        self.assertEqual(result["ETH"]["tier"], "normal")
        self.assertEqual(result["SOL"]["tier"], "high_beta")

    def test_missing_btc_volatility_is_an_error(self):
        with self.assertRaises(ValueError):
            estimate_risk_tiers(
                annualized_volatility={"ETH": 0.5}, beta={}, policy=load_policy(),
            )


if __name__ == "__main__":
    unittest.main()
