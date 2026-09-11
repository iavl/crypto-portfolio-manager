"""Regression coverage: confidence attribution is deterministic and auditable."""

import unittest

from crypto_portfolio.engine.confidence import (
    ConfidenceCap,
    calculate_data_confidence,
    calculate_decision_confidence,
    calculate_regime_confidence,
    confidence_attribution,
    top_confidence_drags,
)
from crypto_portfolio.models.policy import load_policy


AS_OF = "2026-09-11T02:27:00Z"


class RegimeConfidenceAttributionTests(unittest.TestCase):
    def test_regime_confidence_attribution_sums_to_raw(self):
        result = calculate_regime_confidence(
            {
                "trend": 0.8,
                "volatility": 0.7,
                "breadth": 0.6,
                "flows": 0.5,
                "portfolio_drawdown": 0.9,
                "systemic_risk": 0.4,
            },
            policy=load_policy(),
        )
        attribution = confidence_attribution(result)
        total = sum(item["contribution"] for item in attribution["components"].values())
        self.assertAlmostEqual(total, result.raw_score, places=12)
        for name, item in attribution["components"].items():
            self.assertAlmostEqual(item["contribution"], item["score"] * item["weight"], places=12)
            self.assertAlmostEqual(item["weight"], result.dimensions[name].weight, places=12)

    def test_confidence_cap_reports_raw_and_final(self):
        result = calculate_regime_confidence(
            {
                "trend": 0.9,
                "volatility": 0.9,
                "breadth": 0.9,
                "flows": 0.9,
                "portfolio_drawdown": 1.0,
                "systemic_risk": 1.0,
            },
            caps=(ConfidenceCap("CRITICAL_DOMAIN_UNKNOWN", 0.79, reason="drawdown unknown"),),
            policy=load_policy(),
        )
        attribution = confidence_attribution(result)
        self.assertGreater(attribution["raw_score"], attribution["final_score"])
        self.assertEqual(attribution["final_score"], result.score)
        self.assertEqual(attribution["caps"][0]["code"], "CRITICAL_DOMAIN_UNKNOWN")
        self.assertEqual(attribution["caps"][0]["ceiling"], 0.79)

    def test_attribution_accepts_serialized_results(self):
        result = calculate_regime_confidence({"trend": 0.5}, policy=load_policy())
        serialized = result.as_dict()
        self.assertEqual(
            confidence_attribution(result),
            confidence_attribution(serialized),
        )

    def test_cap_cannot_exceed_raw_reported_as_equal(self):
        result = calculate_regime_confidence(
            {"trend": 0.2},
            caps=(ConfidenceCap("TEST_CAP", 0.99, reason="test"),),
            policy=load_policy(),
        )
        attribution = confidence_attribution(result)
        self.assertAlmostEqual(attribution["raw_score"], attribution["final_score"], places=12)


class DecisionConfidenceAttributionTests(unittest.TestCase):
    def test_decision_confidence_attribution_sums_to_raw(self):
        result = calculate_decision_confidence(
            {
                "portfolio_data": 0.9,
                "regime_confidence": 0.5,
                "asset_evidence": 0.8,
                "portfolio_accounting": 0.3,
                "signal_agreement": 0.7,
            },
            policy=load_policy(),
        )
        attribution = confidence_attribution(result)
        total = sum(item["contribution"] for item in attribution["components"].values())
        self.assertAlmostEqual(total, result.raw_score, places=12)
        self.assertEqual(attribution["final_score"], result.score)
        self.assertEqual(
            set(attribution["components"]),
            {"portfolio_data", "regime_confidence", "asset_evidence", "portfolio_accounting", "signal_agreement"},
        )
        self.assertAlmostEqual(attribution["components"]["regime_confidence"]["contribution"], 0.10, places=12)

    def test_decision_attribution_exposes_penalties_and_caps(self):
        result = calculate_decision_confidence(
            {"portfolio_data": 1.0},
            caps=(ConfidenceCap("CRITICAL_MISSING", 0.5, reason="critical data missing"),),
            soft_penalties=({"penalty": 0.05, "code": "STALE_TREND"},),
            policy=load_policy(),
        )
        attribution = confidence_attribution(result)
        self.assertEqual(attribution["caps"][0]["ceiling"], 0.5)
        self.assertEqual(attribution["soft_penalties"][0]["penalty"], 0.05)
        self.assertGreater(attribution["raw_score"], attribution["final_score"])


class DataConfidenceDragTests(unittest.TestCase):
    def test_top_confidence_drags_rank_weighted_headroom(self):
        result = calculate_data_confidence(
            (
                {"asset": "ETH", "metric_key": "market.return_30d", "value": 0.1, "observed_at": AS_OF, "source": "a", "source_group": "a"},
                {"asset": "ETH", "metric_key": "market.return_90d", "value": 0.1, "observed_at": "2026-08-01T00:00:00Z", "source": "a", "source_group": "a"},
            ),
            metric_weights={"market.return_30d": 1.0, "market.return_90d": 1.0},
            as_of=AS_OF,
            policy=load_policy(),
        )
        drags = top_confidence_drags(result)
        self.assertTrue(drags)
        self.assertTrue(all(item["impact"] >= 0 for item in drags))
        self.assertEqual(drags[0]["dimension"], max(drags, key=lambda item: item["impact"])["dimension"])

    def test_attribution_rejects_invalid_scores(self):
        with self.assertRaises(ValueError):
            confidence_attribution({"dimensions": {"trend": {"score": 1.5, "weight": 0.5}}, "raw_score": 0.5, "score": 0.5, "band": "LOW"})


if __name__ == "__main__":
    unittest.main()
