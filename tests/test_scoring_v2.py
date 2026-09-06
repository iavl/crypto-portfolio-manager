import unittest

from crypto_portfolio.engine.factors.flows import calculate_flow_factor
from crypto_portfolio.engine.factors.relative_strength import calculate_relative_strength
from crypto_portfolio.engine.risk import event_risk_deployment_factor, run_risk_gate
from crypto_portfolio.engine.scoring import calculate_factor_reliability, score_assessment, score_factors
from crypto_portfolio.metrics_registry import METRIC_REGISTRY, validate_metric_ownership
from crypto_portfolio.models.evidence import AssetAssessment, FactorScore
from crypto_portfolio.models.metrics_history import MetricObservation, stable_observation_id


class ScoringV2Tests(unittest.TestCase):
    def test_reliability_metadata_mapping(self):
        self.assertEqual(calculate_factor_reliability(1, "CURRENT", "HIGH"), 1.0)
        self.assertEqual(calculate_factor_reliability(1, "STALE", "MEDIUM"), 0.375)
        self.assertEqual(calculate_factor_reliability(1, "UNKNOWN", "HIGH"), 0.0)

    def test_event_risk_is_not_a_scoring_factor(self):
        self.assertTrue(validate_metric_ownership())
        self.assertFalse(any(item.factor == "event_risk" and item.is_scoring_factor for item in METRIC_REGISTRY.values()))
        scores = {factor: 70 for factor in ("trend", "valuation", "fundamentals", "onchain", "capital_flows", "relative_strength_btc")}
        self.assertEqual(score_factors(scores).score, 70.0)

    def test_flow_dead_zone_continuity_and_missing_denominator(self):
        neutral = calculate_flow_factor({"normalized_flow_ratio": 0.0005})
        self.assertEqual(neutral.state, "NEUTRAL")
        self.assertEqual(neutral.score, 50.0)
        strong = calculate_flow_factor({"flow": 10, "denominator": 1000})
        self.assertEqual(strong.state, "POSITIVE")
        self.assertEqual(strong.score, 100.0)
        missing = calculate_flow_factor({"flow": 10})
        self.assertEqual(missing.state, "UNKNOWN")
        self.assertIsNone(missing.score)

    def test_flow_observation_metadata_supplies_normalization_denominator(self):
        observation = MetricObservation(
            stable_observation_id("BTC", "flows.etf_net_30d", "2026-09-01", "test", 10),
            "BTC", "flows.etf_net_30d", "capital_flows", 10, "USD", "30d",
            "2026-09-01", "2026-09-01", "test", "CURRENT", "HIGH",
            metadata={"aum": 1000},
        )
        result = calculate_flow_factor(observations=(observation,))
        self.assertEqual(result.state, "POSITIVE")
        self.assertEqual(result.score, 100.0)

    def test_relative_strength_v2_retains_raw_and_normalized_values(self):
        btc = [100.0] * 181
        asset = [100.0 * 1.001**index for index in range(181)]
        result = calculate_relative_strength(asset, btc, symbol="ETH")
        self.assertIsNotNone(result.relative_180d)
        self.assertIsNotNone(result.risk_adjusted_excess_returns["180d"])
        self.assertIn("relative_90d", result.facts.current)
        self.assertIn("risk_adjusted_relative_90d", result.facts.current)

    def test_btc_relative_strength_is_not_applicable(self):
        result = calculate_relative_strength(symbol="BTC")
        self.assertEqual(result.state, "NOT_APPLICABLE")
        self.assertEqual(result.coverage, 0.0)
        self.assertEqual(score_factors({"trend": FactorScore("trend", 80)}, symbol="BTC").not_applicable_factors, ("relative_strength_btc",))

    def test_scored_assessment_persists_missing_factor_states(self):
        assessment, result = score_assessment(
            AssetAssessment("ETH", {"trend": 80})
        )
        self.assertEqual(assessment.scoring_profile_name, "default")
        self.assertEqual(assessment.score_coverage, result.coverage)
        self.assertEqual(assessment.factor_scores["valuation"].availability, "MISSING")

    def test_event_risk_multipliers_are_monotonic(self):
        values = [event_risk_deployment_factor(state) for state in ("NORMAL", "ELEVATED", "HIGH", "SEVERE", "CRITICAL")]
        self.assertEqual(values, sorted(values, reverse=True))
        gated = run_risk_gate(
            {"SOL": 0.2, "BTC": 0.7, "USDT": 0.1},
            assessments={"SOL": {"confidence": "HIGH", "event_risk": {"state": "HIGH"}}},
        )
        self.assertEqual(gated.deployment_caps["SOL"], 0.5)


if __name__ == "__main__":
    unittest.main()
