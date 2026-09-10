import unittest

from crypto_portfolio.engine.confidence import (
    ConfidenceCap,
    DecisionScope,
    aggregate_asset_evidence_confidence,
    calculate_data_confidence,
    calculate_decision_confidence,
)
from crypto_portfolio.engine.decision_packet import build_decision_review_packet
from crypto_portfolio.engine.risk import run_risk_gate
from crypto_portfolio.events import EventScanner, EventSourceScanResponse
from crypto_portfolio.metric_availability import evaluate_factor_sufficiency, metric_availability
from crypto_portfolio.models.metrics_history import CollectionEvent
from crypto_portfolio.models.policy import load_policy


AS_OF = "2026-09-09T00:00:00Z"


class ConfidenceRefactorTests(unittest.TestCase):
    def test_factor_sufficiency_ignores_supporting_failures_after_primary_minimum(self):
        events = tuple(
            CollectionEvent(
                f"event-{index}", AS_OF, "ETH", metric, status,
                source="test", reason=None if status == "SUCCESS" else "provider unavailable",
                observed_at=AS_OF if status == "SUCCESS" else None,
                fetched_at=AS_OF if status == "SUCCESS" else None,
            )
            for index, (metric, status) in enumerate(
                (
                    ("market.return_30d", "SUCCESS"),
                    ("market.return_90d", "SUCCESS"),
                    ("market.ma20", "SUCCESS"),
                    ("market.ma50", "FAILED"),
                    ("market.ma100", "FAILED"),
                )
            )
        )
        result = evaluate_factor_sufficiency("trend", events, policy=load_policy(), asset="ETH")
        self.assertEqual(result.status, "SUFFICIENT")
        self.assertEqual(result.primary_available, 2)

    def test_metric_importance_has_four_evidence_classes(self):
        self.assertEqual(metric_availability("BTC", "market.spot_price").requirement, "CRITICAL")
        self.assertEqual(metric_availability("ETH", "market.return_30d").requirement, "PRIMARY")
        self.assertEqual(metric_availability("ETH", "market.ma20").requirement, "SUPPORTING")
        self.assertEqual(metric_availability("ETH", "fundamentals.developer_activity").requirement, "OPTIONAL")

    def test_data_confidence_does_not_score_cross_factor_signal_disagreement(self):
        result = calculate_data_confidence(
            (
                {"asset": "ETH", "metric_key": "market.return_30d", "value": 0.1, "direction": "POSITIVE", "observed_at": AS_OF, "source": "a", "source_group": "a"},
                {"asset": "ETH", "metric_key": "market.return_30d", "value": -0.1, "direction": "NEGATIVE", "observed_at": AS_OF, "source": "b", "source_group": "b"},
            ),
            metric_weights={"market.return_30d": 1.0},
            as_of=AS_OF,
            policy=load_policy(),
        )
        self.assertNotIn("signal_consistency", result.dimensions)

    def test_redundancy_is_per_metric_not_provider_count(self):
        same_metric = calculate_data_confidence(
            (
                {"asset": "ETH", "metric_key": "market.return_30d", "value": 0.1, "observed_at": AS_OF, "source": "a", "source_group": "a"},
                {"asset": "ETH", "metric_key": "market.return_30d", "value": 0.1, "observed_at": AS_OF, "source": "b", "source_group": "b"},
            ),
            metric_weights={"market.return_30d": 1.0}, as_of=AS_OF, policy=load_policy(),
        )
        different_metrics = calculate_data_confidence(
            (
                {"asset": "ETH", "metric_key": "market.return_30d", "value": 0.1, "observed_at": AS_OF, "source": "a", "source_group": "a"},
                {"asset": "ETH", "metric_key": "market.return_90d", "value": 0.1, "observed_at": AS_OF, "source": "b", "source_group": "b"},
            ),
            metric_weights={"market.return_30d": 0.5, "market.return_90d": 0.5}, as_of=AS_OF, policy=load_policy(),
        )
        self.assertGreater(same_metric.dimensions["redundancy"].score, different_metrics.dimensions["redundancy"].score)

    def test_watchlist_asset_does_not_contaminate_hold_scope(self):
        policy = load_policy()
        score = aggregate_asset_evidence_confidence(
            {"BTC": 0.9, "ETH": 0.85, "AAVE": 0.25},
            {"BTC": 0.60, "ETH": 0.25, "AAVE": 0.0},
            policy=policy,
        )
        self.assertGreater(score, 0.8)
        self.assertEqual(
            aggregate_asset_evidence_confidence({"AAVE": 0.25}, {"AAVE": 0.10}, policy=policy),
            0.25,
        )

    def test_decision_confidence_persists_action_scope(self):
        scope = DecisionScope("HOLD", ("BTC", "ETH"), {"BTC": 0.60, "ETH": 0.25})
        result = calculate_decision_confidence(
            {
                "portfolio_data": 1.0,
                "regime_confidence": 1.0,
                "asset_evidence": {"assets": {"BTC": 0.9, "ETH": 0.85}, "weights": scope.exposure_weights},
                "portfolio_accounting": 1.0,
                "signal_agreement": 1.0,
            },
            scope=scope,
            policy=load_policy(),
        )
        self.assertEqual(result.scope["action"], "HOLD")
        self.assertEqual(result.scope["relevant_assets"], ["BTC", "ETH"])

    def test_healthy_core_hold_is_at_least_medium_with_optional_gap(self):
        packet = build_decision_review_packet(
            review_type="SNAPSHOT_REVIEW",
            market_regime="NORMAL",
            current_weights={"BTC": 0.60, "ETH": 0.25, "USDT": 0.15},
            target_weights={"BTC": 0.60, "ETH": 0.25, "USDT": 0.15},
            assessments={
                "BTC": {"weighted_score": 70, "confidence": "HIGH", "confidence_score": 0.90},
                "ETH": {"weighted_score": 68, "confidence": "HIGH", "confidence_score": 0.85},
                "AAVE": {"weighted_score": 70, "confidence": "LOW", "confidence_score": 0.25},
            },
        )
        self.assertGreaterEqual(packet.decision_confidence.score, 0.60)
        self.assertEqual(packet.decision_confidence.scope["relevant_assets"], ["BTC", "ETH"])

    def test_security_unknown_is_scoped_to_risk_increasing_action(self):
        policy = load_policy()
        result = calculate_decision_confidence(
            {name: 1.0 for name in ("portfolio_data", "regime_confidence", "asset_evidence", "portfolio_accounting", "signal_agreement")},
            scope=DecisionScope("INCREASE", ("AAVE",), {"AAVE": 0.10}),
            caps=(ConfidenceCap("SECURITY_UNKNOWN", policy.confidence["caps"]["security_unknown"], "ACTION:INCREASE", "target security is unknown"),),
            policy=policy,
        )
        self.assertEqual(result.band, "LOW")
        self.assertIn("ACTION:INCREASE", result.blocked_actions)

    def test_confirmed_exploit_is_high_evidence_but_critical_risk(self):
        gate = run_risk_gate(
            {"BTC": 0.60, "ETH": 0.25, "AAVE": 0.05, "USDT": 0.10},
            policy=load_policy(),
            assessments={"AAVE": {"confidence": "HIGH", "event_risk": {"state": "CRITICAL"}}},
            actions=({"symbol": "AAVE", "action": "INCREASE", "amount_usd": 1.0},),
        )
        self.assertTrue(any(item.code == "SEVERE_EVENT_EXPOSURE" for item in gate.errors))

    def test_governance_normalizes_materiality_and_direction(self):
        response = EventSourceScanResponse(
            "source", True, AS_OF,
            ({
                "title": "fee switch proposal",
                "published_at": AS_OF,
                "materiality": "WATCH",
                "impact_direction": "POSITIVE",
                "magnitude": "MEDIUM",
                "implementation_status": "PROPOSED",
                "confidence": 0.9,
            },),
        )
        item = response.items[0]
        self.assertTrue(item["is_material"])
        self.assertEqual(item["severity"], "WATCH")
        self.assertEqual(item["impact_direction"], "POSITIVE")
        self.assertEqual(item["implementation_status"], "PROPOSED")
        with self.assertRaisesRegex(ValueError, "unknown event scan materiality"):
            EventSourceScanResponse("source", True, AS_OF, ({"title": "bad", "materiality": "???"},))

    def test_positive_material_governance_event_is_retained_without_critical_state(self):
        scanner = EventScanner()
        requests = scanner.build_requests("AAVE", "governance", AS_OF)
        response = EventSourceScanResponse(
            requests[0].source_id, True, AS_OF,
            ({
                "title": "fee switch proposal",
                "published_at": AS_OF,
                "materiality": True,
                "is_material": True,
                "severity": "WATCH",
                "impact_direction": "POSITIVE",
                "magnitude": "MEDIUM",
                "implementation_status": "PROPOSED",
                "affected_assets": ["AAVE"],
            },),
        )
        all_responses = tuple(
            response if request.source_id == requests[0].source_id
            else EventSourceScanResponse(request.source_id, True, AS_OF, ())
            for request in requests
        )
        result = scanner.scan("AAVE", "governance", AS_OF, responses=all_responses)
        self.assertEqual(result.status, "MATERIAL_EVENT_FOUND")
        self.assertEqual(result.state, "WATCH")


if __name__ == "__main__":
    unittest.main()
