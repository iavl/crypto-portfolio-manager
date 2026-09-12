import unittest

from crypto_portfolio.engine.regime import RegimeInputs, determine_regime
from crypto_portfolio.engine.risk import run_risk_gate
from crypto_portfolio.engine.scoring import score_factors
from crypto_portfolio.engine.scoring import score_assessment
from crypto_portfolio.models.evidence import AssetAssessment, FactorScore
from crypto_portfolio.models.policy import resolve_policy


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
        self.assertAlmostEqual(result.score, 60.0)
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
            RegimeInputs("BEARISH", "ELEVATED", -0.15, "NEUTRAL", "HEALTHY", False)
        )
        self.assertEqual(result.regime, "CAPITAL_PRESERVATION")

    def test_drawdown_floor_matches_policy_bands(self):
        for drawdown, expected in (
            (-0.05, "NORMAL"),
            (-0.09, "DEFENSIVE"),
            (-0.12, "CAPITAL_PRESERVATION"),
            (-0.16, "CAPITAL_PRESERVATION"),
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
            for drawdown in (-0.01, -0.06, -0.09, -0.12, -0.15)
        ]
        self.assertEqual(regimes, sorted(regimes, key=order.get))

    def test_vote_based_regime_transitions_move_one_notch_per_review(self):
        votes = RegimeInputs("BEARISH", "ELEVATED", "NORMAL", "OUTFLOW", "WEAK", False)
        # Without prior context the computed regime applies unchanged.
        self.assertEqual(determine_regime(votes).regime, "CAPITAL_PRESERVATION")
        # A vote-based jump straight to CAPITAL_PRESERVATION must pass through
        # one defensive review first, in both directions.
        capped = determine_regime(votes, previous="NORMAL")
        self.assertEqual(capped.regime, "DEFENSIVE")
        self.assertTrue(any("awaits confirmation" in reason for reason in capped.reasons))
        self.assertEqual(determine_regime(votes, previous="DEFENSIVE").regime, "CAPITAL_PRESERVATION")
        all_clear = RegimeInputs("HEALTHY", "LOW", "NORMAL", "NEUTRAL", "HEALTHY", False)
        self.assertEqual(determine_regime(all_clear, previous="CAPITAL_PRESERVATION").regime, "DEFENSIVE")
        self.assertEqual(determine_regime(all_clear, previous={"market_regime": "DEFENSIVE"}).regime, "NORMAL")

    def test_mandatory_floors_and_severe_events_ignore_the_transition_cap(self):
        floor_case = RegimeInputs("HEALTHY", "LOW", -0.13, "NEUTRAL", "HEALTHY", False)
        self.assertEqual(determine_regime(floor_case, previous="NORMAL").regime, "CAPITAL_PRESERVATION")
        severe_case = RegimeInputs("HEALTHY", "LOW", "NORMAL", "NEUTRAL", "HEALTHY", "SEVERE")
        self.assertEqual(determine_regime(severe_case, previous="NORMAL").regime, "CAPITAL_PRESERVATION")
        plain = RegimeInputs("HEALTHY", "LOW", "NORMAL", "NEUTRAL", "HEALTHY", False)
        with self.assertRaises(ValueError):
            determine_regime(plain, previous="BOGUS")

    def test_regime_transition_cap_can_be_disabled_by_policy(self):
        from crypto_portfolio.models.policy import Policy

        policy = resolve_policy()
        disabled = Policy(
            **{**policy.__dict__, "regime_transitions": {"enabled": False, "max_notches_per_review": 1}},
        )
        votes = RegimeInputs("BEARISH", "ELEVATED", "NORMAL", "OUTFLOW", "WEAK", False)
        self.assertEqual(determine_regime(votes, policy=disabled, previous="NORMAL").regime, "CAPITAL_PRESERVATION")


if __name__ == "__main__":
    unittest.main()
