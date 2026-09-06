import unittest

from crypto_portfolio.engine.regime import RegimeInputs, determine_regime
from crypto_portfolio.engine.risk import run_risk_gate
from crypto_portfolio.engine.scoring import score_factors
from crypto_portfolio.engine.scoring import score_assessment
from crypto_portfolio.models.evidence import AssetAssessment, FactorScore
from crypto_portfolio.models.policy import legacy_policy, resolve_policy


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
            }
        )
        self.assertAlmostEqual(result.score, 59.5)
        self.assertEqual(result.missing_factors, ())
        self.assertAlmostEqual(sum(result.effective_weights.values()), 1.0)
        self.assertEqual(set(result.effective_weights), {
            "trend", "valuation", "fundamentals", "onchain", "capital_flows", "relative_strength_btc"
        })

    def test_missing_factor_keeps_weight_and_shrinks_to_neutral(self):
        result = score_factors(
            {"trend": 80, "valuation": 60},
            {"trend": 0.5, "valuation": 0.4, "onchain": 0.1},
            confidence="HIGH",
        )
        self.assertAlmostEqual(result.score, 80 * 0.5 + 60 * 0.4 + 50 * 0.1)
        self.assertEqual(result.missing_factors, ("onchain",))
        self.assertEqual(result.confidence, "HIGH")
        self.assertAlmostEqual(sum(result.effective_weights.values()), 1.0)
        self.assertAlmostEqual(result.coverage, 0.9)
        self.assertEqual(result.effective_factor_scores["onchain"], 50.0)

    def test_reliability_shrinks_toward_neutral(self):
        for raw, reliability, expected in ((80, 1.0, 80), (80, 0.5, 65), (80, 0.0, 50), (20, 0.5, 35)):
            with self.subTest(raw=raw, reliability=reliability):
                result = score_factors(
                    {"trend": FactorScore("trend", raw, reliability=reliability)},
                    {"trend": 1.0},
                )
                self.assertAlmostEqual(result.score, expected)
                self.assertAlmostEqual(result.coverage, reliability)

    def test_missing_all_factors_is_neutral_but_not_investable(self):
        result = score_factors({}, {"trend": 1.0}, confidence="HIGH")
        self.assertEqual(result.score, 50.0)
        self.assertEqual(result.coverage, 0.0)
        self.assertEqual(result.confidence, "LOW")

    def test_not_applicable_requires_zero_weight(self):
        valid = score_factors(
            {"relative_strength_btc": FactorScore("relative_strength_btc", None, availability="NOT_APPLICABLE")},
            {"trend": 1.0, "relative_strength_btc": 0.0},
            symbol="BTC",
        )
        self.assertEqual(valid.not_applicable_factors, ("relative_strength_btc",))
        with self.assertRaises(ValueError):
            score_factors(
                {"trend": FactorScore("trend", None, availability="NOT_APPLICABLE")},
                {"trend": 1.0},
            )

    def test_v1_replay_keeps_legacy_event_factor_and_renormalization(self):
        result = score_factors(
            {
                "trend": 80,
                "valuation": 70,
                "fundamentals": 60,
                "onchain": 50,
                "capital_flows": 40,
                "relative_strength_btc": 30,
                "event_risk": 20,
            },
            policy=legacy_policy(),
        )
        self.assertAlmostEqual(result.score, 59.0)
        self.assertEqual(result.scoring_model_version, 1)
        missing = score_factors({"trend": 80}, policy=legacy_policy())
        self.assertAlmostEqual(missing.score, 80.0)
        self.assertEqual(missing.effective_weights, {"trend": 1.0})

    def test_confidence_tracks_coverage_and_critical_completeness(self):
        low_coverage = score_factors(
            {"trend": 80}, {"trend": 0.2, "valuation": 0.8}, confidence="HIGH"
        )
        medium_coverage = score_factors(
            {"trend": 80}, {"trend": 0.7, "valuation": 0.3}, confidence="HIGH"
        )
        self.assertEqual(low_coverage.confidence, "LOW")
        self.assertEqual(medium_coverage.confidence, "MEDIUM")
        self.assertEqual(
            score_factors({"trend": 80}, {"trend": 1.0}, confidence="HIGH", critical_data_complete=False).confidence,
            "LOW",
        )
        assessment, _ = score_assessment(
            AssetAssessment("BTC", {"trend": 80}, confidence="HIGH", critical_data_complete=False)
        )
        self.assertEqual(assessment.confidence, "LOW")

    def test_unknown_factor_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown scoring factor"):
            score_factors({"fundamental": 80}, {"fundamentals": 1.0})

    def test_all_factors_missing_is_neutral_and_invalid_score_fails(self):
        self.assertEqual(score_factors({}, {"trend": 1.0}).score, 50.0)
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

    def test_drawdown_floor_matches_policy_bands(self):
        for drawdown, expected in (
            (-0.07, "NORMAL"),
            (-0.13, "DEFENSIVE"),
            (-0.17, "CAPITAL_PRESERVATION"),
            (-0.21, "CAPITAL_PRESERVATION"),
        ):
            with self.subTest(drawdown=drawdown):
                result = determine_regime(
                    RegimeInputs("HEALTHY", "LOW", drawdown, "NEUTRAL", "HEALTHY", False)
                )
                self.assertEqual(result.regime, expected)

    def test_breach_boundary_is_strictly_below_minus_d(self):
        # AGENTS.md: breach is `< -D`; exactly -D is the capital-preservation
        # floor, not a breach. Regime and risk gate must agree.
        policy = resolve_policy()
        budget = policy.max_portfolio_drawdown
        for drawdown, breach in ((-budget, False), (-budget - 1e-9, True)):
            with self.subTest(drawdown=drawdown):
                floor = determine_regime(
                    RegimeInputs("HEALTHY", "LOW", drawdown, "NEUTRAL", "HEALTHY", False),
                    policy=policy,
                )
                gate = run_risk_gate(
                    {"BTC": 0.6, "USDT": 0.4},
                    policy=policy,
                    current_drawdown=drawdown,
                )
                self.assertEqual(
                    any(item.code == "DRAWDOWN_BREACH" for item in gate.violations),
                    breach,
                )
                self.assertEqual(floor.regime, "CAPITAL_PRESERVATION")

    def test_worse_drawdown_is_never_less_defensive(self):
        order = {"NORMAL": 0, "DEFENSIVE": 1, "CAPITAL_PRESERVATION": 2}
        regimes = [
            determine_regime(
                RegimeInputs("HEALTHY", "LOW", drawdown, "NEUTRAL", "HEALTHY", False)
            ).regime
            for drawdown in (-0.01, -0.08, -0.12, -0.16, -0.20)
        ]
        self.assertEqual(regimes, sorted(regimes, key=order.get))


if __name__ == "__main__":
    unittest.main()
