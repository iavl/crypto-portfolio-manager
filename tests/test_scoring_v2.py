import unittest
import math
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from crypto_portfolio.engine.factors.flows import calculate_flow_factor
from crypto_portfolio.engine.factors.relative_strength import calculate_relative_strength
from crypto_portfolio.engine.risk import event_risk_deployment_factor, run_risk_gate
from crypto_portfolio.engine.scoring import calculate_factor_reliability, score_assessment, score_factors
from crypto_portfolio.metrics_registry import METRIC_REGISTRY, validate_metric_ownership
from crypto_portfolio.models.evidence import AssetAssessment, FactorScore
from crypto_portfolio.models.metrics_history import MetricObservation, stable_observation_id
from crypto_portfolio.models.market import Candle, OHLCVSeries
from crypto_portfolio.models.policy import load_policy


class ScoringV2Tests(unittest.TestCase):
    @staticmethod
    def v2_policy():
        return load_policy()

    def test_drawdown_has_no_v2_trend_score_authority(self):
        from crypto_portfolio.engine.factors.trend import calculate_trend_factor
        common = dict(symbol="ETH", current_spot_price=100, ma20=100, ma50=100, ma100=100, ma200=100,
                      return_30d=0, return_90d=0, return_180d=0, support_zones=(), volume_state="UNKNOWN",
                      atr14=None, atr_percent=None, realized_vol_30d=None, realized_vol_90d=None,
                      relative_volume=None, trend_state="NEUTRAL", ohlcv_hash=None, volume_profile_hash=None,
                      volume_profile_poc=None, volume_profile_val=None, volume_profile_vah=None,
                      market_data_fresh=True, data_quality_flags=(), data_confidence="HIGH")
        results = []
        for drawdown in (-0.05, -0.7):
            with patch("crypto_portfolio.engine.factors.trend._snapshot", return_value=SimpleNamespace(**common, current_drawdown=drawdown)):
                results.append(calculate_trend_factor({}).score)
        self.assertEqual(results[0], results[1])

    def test_factor_schema_rejects_inconsistent_availability(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((Path(__file__).parents[1] / "schemas/decision.schema.json").read_text())
        validator = Draft202012Validator(schema["$defs"]["factorScore"])
        for state, score, reliability in (("MISSING", 80, 0), ("NOT_APPLICABLE", None, 1), ("AVAILABLE", None, 1)):
            self.assertFalse(validator.is_valid(dict(factor="trend", score=score, availability=state, reliability=reliability)))
        self.assertTrue(validator.is_valid(dict(factor="trend", score=80)))

    def test_relative_risk_normalization_penalizes_volatility(self):
        btc = [100] * 181
        low = [100 * math.exp(i * 0.0001 + 0.001 * math.sin(math.pi * i / 30)) for i in range(181)]
        high = [100 * math.exp(i * 0.0001 + 0.2 * math.sin(math.pi * i / 30)) for i in range(181)]
        a = calculate_relative_strength(low, btc)
        b = calculate_relative_strength(high, btc)
        self.assertAlmostEqual(a.relative_180d, b.relative_180d)
        self.assertGreater(a.score, b.score)
        self.assertLess(b.score, 100)

    def test_intraday_relative_volatility_uses_daily_returns(self):
        def series(symbol, hours):
            candles = []
            for hour in range(0, 180 * 24 + 1, hours):
                price = 100 if symbol == "BTC" else 100 * math.exp(hour / 24 * 0.0002 + 0.01 * math.sin(hour / 24))
                timestamp = (datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hour)).isoformat()
                candles.append(Candle(timestamp, price, price, price, price, 1))
            return OHLCVSeries(symbol, "1D" if hours == 24 else "4H", tuple(candles))
        policy = self.v2_policy()
        daily = calculate_relative_strength(series("ETH", 24), series("BTC", 24), symbol="ETH", policy=policy)
        intraday = calculate_relative_strength(series("ETH", 4), series("BTC", 4), symbol="ETH", policy=policy)
        self.assertEqual(daily.risk_adjusted_excess_returns, intraday.risk_adjusted_excess_returns)
        self.assertEqual(daily.score, intraday.score)
        stale = calculate_relative_strength(
            replace(series("ETH", 24), fetched_at="2025-08-01T00:00:00Z"),
            replace(series("BTC", 24), fetched_at="2025-08-01T00:00:00Z"), symbol="ETH", policy=policy)
        self.assertEqual(stale.facts.freshness, "STALE")
        self.assertAlmostEqual(score_factors({"relative_strength_btc": stale}, {"relative_strength_btc": 1.0}, policy=policy).coverage, 0.35)

    def test_normalized_flow_representation_and_denominator_order(self):
        result = calculate_flow_factor({"aum_30d": 1000, "flow_30d": 5})
        self.assertAlmostEqual(result.normalized_flow, 0.005)
        self.assertIsNone(calculate_flow_factor({"aum_30d": 1000}).score)
        observation = MetricObservation(
            stable_observation_id("BTC", "flows.etf_net_30d", "2026-09-01T00:00:00Z", "test", 5),
            "BTC", "flows.etf_net_30d", "capital_flows", 5, "USD", "30d",
            "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z", "test", "CURRENT", "HIGH", metadata={"aum": 1000},
        )
        for value in (observation, observation.as_dict(), [observation], [observation.as_dict()]):
            self.assertAlmostEqual(calculate_flow_factor(value, symbol="BTC").normalized_flow, 0.005)
        low_source = calculate_flow_factor(replace(observation, confidence="LOW"))
        self.assertAlmostEqual(score_factors({"capital_flows": low_source}, {"capital_flows": 1.0}).coverage, 0.3)
        with self.assertRaises(ValueError):
            calculate_flow_factor([observation, observation], symbol="BTC")

    def test_factor_quality_survives_assessment_serialization(self):
        flow = calculate_flow_factor({"normalized_flow_ratio": 0.01})
        for value in (flow, flow.as_dict()):
            assessment = AssetAssessment("ETH", {"capital_flows": value})
            parsed = AssetAssessment.from_mapping("ETH", assessment.as_dict())
            self.assertAlmostEqual(parsed.factor_scores["capital_flows"].reliability, 0.6)

    def test_unscored_typed_allocation_uses_canonical_score(self):
        from crypto_portfolio.engine.allocation import build_target_allocation
        assessment = AssetAssessment("SOL", {"trend": 100}, confidence="HIGH", relative_strength_vs_btc="OUTPERFORM")
        scored, _ = score_assessment(assessment)
        self.assertEqual(build_target_allocation(assessments={"SOL": assessment}),
                         build_target_allocation(assessments={"SOL": scored}))

    def test_score_and_flow_monotonicity(self):
        for raw in (0, 20, 50, 80, 100):
            scores = [score_factors({"trend": FactorScore("trend", raw, reliability=r / 10)}, {"trend": 1.0}).score for r in range(11)]
            self.assertEqual(scores, sorted(scores, reverse=raw < 50))
        scores = [calculate_flow_factor({"normalized_flow_ratio": value / 100000}).score for value in range(-1100, 1101)]
        self.assertEqual(scores, sorted(scores))
    def test_v2_weights_are_validated_not_rescaled(self):
        for weights in ({"trend": 2.0}, {"trend": 0.2}):
            with self.assertRaisesRegex(ValueError, "sum to 1"):
                score_factors({"trend": 80}, weights)

    def test_factor_result_coverage_reaches_score(self):
        result = calculate_relative_strength([100 * 1.001**i for i in range(91)], [100] * 91, symbol="ETH")
        for factor in (result, result.as_dict()):
            scored = score_factors({"relative_strength_btc": factor}, {"relative_strength_btc": 1.0})
            self.assertAlmostEqual(scored.coverage, result.coverage)
            self.assertAlmostEqual(scored.score, 50 + result.coverage * (result.score - 50))
        flow = calculate_flow_factor({"normalized_flow_ratio": 0.01})
        scored = score_factors({"capital_flows": flow}, {"capital_flows": 1.0})
        self.assertAlmostEqual(scored.coverage, 0.6)
        self.assertAlmostEqual(scored.score, 80)
        stale = flow.as_dict()
        stale["facts"]["freshness"] = "STALE"
        self.assertAlmostEqual(score_factors({"capital_flows": stale}, {"capital_flows": 1.0}).coverage, 0.3)

    def test_btc_missing_state_is_persisted_as_not_applicable(self):
        assessment, _ = score_assessment(AssetAssessment("BTC", {"relative_strength_btc": None}))
        self.assertEqual(assessment.factor_scores["relative_strength_btc"].availability, "NOT_APPLICABLE")

    def test_severe_event_risk_blocks_new_exposure(self):
        from crypto_portfolio.engine.allocation import satellite_eligibility

        value = {"weighted_score": 100, "relative_strength_vs_btc": "OUTPERFORM", "event_risk": {"state": "SEVERE"}}
        self.assertEqual(satellite_eligibility(value), "INELIGIBLE")
        typed = AssetAssessment("SOL", {}, event_risk={"state": "SEVERE"})
        self.assertEqual(typed.event_risk.state, "SEVERE")
        gate = run_risk_gate({"BTC": 0.5, "ETH": 0.2, "SOL": 0.2, "USDT": 0.1}, assessments={"SOL": {**value, "confidence": "HIGH"}})
        self.assertEqual(gate.deployment_caps["SOL"], 0.0)

    def test_hysteresis_does_not_override_negative_relative_strength(self):
        from crypto_portfolio.engine.allocation import satellite_eligibility

        for score in range(60, 67):
            self.assertEqual(satellite_eligibility({"weighted_score": score, "relative_strength_vs_btc": "UNDERPERFORM"}, current_weight=0.05), "INELIGIBLE")

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
            stable_observation_id("BTC", "flows.etf_net_30d", "2026-09-01T00:00:00Z", "test", 10),
            "BTC", "flows.etf_net_30d", "capital_flows", 10, "USD", "30d",
            "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z", "test", "CURRENT", "HIGH",
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
