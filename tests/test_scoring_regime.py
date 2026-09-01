import unittest

from crypto_portfolio.engine.regime import RegimeInputs, determine_regime
from crypto_portfolio.engine.scoring import score_factors


class ScoringAndRegimeTests(unittest.TestCase):
    def test_full_score_uses_canonical_weights(self):
        result = score_factors(
            {
                "trend": 80,
                "valuation": 70,
                "fundamentals": 60,
                "onchain": 50,
                "capital_flows": 40,
                "relative_strength_btc": 30,
                "event_risk": 20,
            }
        )
        self.assertAlmostEqual(result.score, 59.0)
        self.assertEqual(result.missing_factors, ())
        self.assertAlmostEqual(sum(result.effective_weights.values()), 1.0)

    def test_missing_factor_renormalizes_and_lowers_confidence(self):
        result = score_factors(
            {"trend": 80, "valuation": 60},
            {"trend": 0.25, "valuation": 0.2, "onchain": 0.1},
            confidence="HIGH",
        )
        self.assertAlmostEqual(result.score, (80 * 0.25 + 60 * 0.2) / 0.45)
        self.assertEqual(result.missing_factors, ("onchain",))
        self.assertEqual(result.confidence, "MEDIUM")
        self.assertAlmostEqual(sum(result.effective_weights.values()), 1.0)

    def test_all_factors_missing_and_invalid_score_fail(self):
        with self.assertRaises(ValueError):
            score_factors({}, {"trend": 1.0})
        with self.assertRaises(ValueError):
            score_factors({"trend": 101}, {"trend": 1.0})

    def test_regime_requires_multiple_risk_dimensions(self):
        normal = determine_regime(
            RegimeInputs("HEALTHY", "LOW", "NORMAL", "NEUTRAL", "HEALTHY", False)
        )
        defensive = determine_regime(
            RegimeInputs("BEARISH", "ELEVATED", "NORMAL", "NEUTRAL", "HEALTHY", False)
        )
        capital = determine_regime(
            RegimeInputs("HEALTHY", "LOW", "NORMAL", "NEUTRAL", "HEALTHY", True)
        )
        self.assertEqual(normal.regime, "NORMAL")
        self.assertEqual(defensive.regime, "DEFENSIVE")
        self.assertEqual(capital.regime, "CAPITAL_PRESERVATION")

    def test_drawdown_can_force_capital_preservation(self):
        result = determine_regime(
            RegimeInputs("BEARISH", "ELEVATED", -0.2, "NEUTRAL", "HEALTHY", False)
        )
        self.assertEqual(result.regime, "CAPITAL_PRESERVATION")


if __name__ == "__main__":
    unittest.main()
