import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.decision_packet import build_decision_review_packet
from crypto_portfolio.engine.factor_packet import build_asset_factor_packet
from crypto_portfolio.engine.factors.flows import calculate_flow_factor
from crypto_portfolio.engine.scoring import score_assessment
from crypto_portfolio.engine.metric_plan import build_metric_collection_plan
from crypto_portfolio.engine.rebalance import recommend_rebalance
from crypto_portfolio.engine.scoring import score_factors
from crypto_portfolio.events import EventScanner, EventSourceScanResponse
from crypto_portfolio.metric_availability import evaluate_factor_sufficiency
from crypto_portfolio.metrics_registry import metric_definition
from crypto_portfolio.models import ManualAssetContext
from crypto_portfolio.models.evidence import AssetAssessment, FactorScore
from crypto_portfolio.models.policy import load_policy
from crypto_portfolio.state.metrics import read_collection_events


AS_OF = "2026-09-09T00:00:00Z"


class AaveRefactorTests(unittest.TestCase):
    def test_aave_uses_the_defi_protocol_profile(self):
        policy = load_policy()
        self.assertEqual(policy.scoring_profile_name("AAVE"), "defi_protocol")
        self.assertEqual(policy.scoring_profile("AAVE"), {
            "trend": 0.30,
            "valuation": 0.20,
            "fundamentals": 0.35,
            "onchain": 0.0,
            "capital_flows": 0.0,
            "relative_strength_btc": 0.15,
            "btc_valuation": 0.0,
            "macro_liquidity": 0.0,
        })

    def test_aave_zero_weight_factors_are_not_applicable(self):
        result = score_factors(
            {"trend": 80, "valuation": 70, "fundamentals": 75, "relative_strength_btc": 65},
            symbol="AAVE",
        )
        self.assertEqual(result.missing_factors, ())
        self.assertEqual(result.coverage, 1.0)
        for factor in ("onchain", "capital_flows", "btc_valuation", "macro_liquidity"):
            self.assertEqual(result.factor_availability[factor], "NOT_APPLICABLE")
            self.assertIn(factor, result.not_applicable_factors)
        self.assertFalse(metric_definition("onchain.active_addresses").applies_to("AAVE"))
        self.assertEqual(
            evaluate_factor_sufficiency("onchain", (), policy=load_policy(), asset="AAVE").status,
            "NOT_APPLICABLE",
        )

    def test_aave_plan_excludes_chain_native_and_flow_scoring_requests(self):
        plan = build_metric_collection_plan(["AAVE"], policy=load_policy())
        aave_keys = {item.metric_key for item in plan.for_asset("AAVE")}
        self.assertFalse(any(key.startswith("onchain.") for key in aave_keys))
        self.assertFalse(any(key.startswith("flows.") for key in aave_keys))
        self.assertNotIn("risk.governance_event_status", aave_keys)

    def test_governance_is_not_an_automatic_event_category_but_aave_security_remains(self):
        scanner = EventScanner()
        with self.assertRaises(ValueError):
            scanner.build_requests("AAVE", "governance", AS_OF)
        requests = scanner.build_requests("AAVE", "security", AS_OF)
        self.assertTrue(requests)
        result = scanner.scan(
            "AAVE",
            "security",
            AS_OF,
            responses=tuple(EventSourceScanResponse(item.source_id, True, AS_OF, ()) for item in requests),
        )
        self.assertEqual(result.status, "NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES")

    def test_manual_context_is_optional_and_has_no_provider_penalty(self):
        context = ManualAssetContext(
            "AAVE", "GOVERNANCE", "risk parameter discussion", "MIXED", "MEDIUM", "CONTEXT_ONLY", AS_OF,
        )
        self.assertEqual(context.as_dict()["source"], "MANUAL_USER_INPUT")
        with self.assertRaises(ValueError):
            ManualAssetContext("AAVE", "GOVERNANCE", "x", "MIXED", "MEDIUM", "CONTEXT_ONLY", AS_OF, "web")

        factor_packet = build_asset_factor_packet("AAVE", manual_asset_contexts=(context,))
        self.assertEqual(factor_packet.as_dict()["manual_asset_contexts"][0]["source"], "MANUAL_USER_INPUT")
        schema = json.loads((Path(__file__).parents[1] / "schemas/manual-asset-context.schema.json").read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(context.as_dict()))
        self.assertEqual(errors, [])

    def test_confidence_dimensions_use_independent_metadata(self):
        current = score_factors(
            {"trend": {"score": 80, "freshness": "CURRENT", "source_quality": 1.0}},
            {"trend": 1.0},
        )
        stale = score_factors(
            {"trend": {"score": 80, "freshness": "STALE", "source_quality": 0.5}},
            {"trend": 1.0},
        )
        self.assertGreater(current.data_confidence_score, stale.data_confidence_score)
        self.assertNotIn("redundancy", score_factors({"trend": 80}, {"trend": 1.0}).factor_data_confidence.dimensions)

    def test_factor_quality_survives_asset_assessment_normalization(self):
        factor = calculate_flow_factor({"normalized_flow_ratio": 0.01})
        assessment, result = score_assessment(AssetAssessment("ETH", {"capital_flows": factor}))
        self.assertEqual(assessment.factor_scores["capital_flows"].freshness, "CURRENT")
        self.assertIn("freshness", result.factor_data_confidence.dimensions)
        self.assertIn("source_quality", result.factor_data_confidence.dimensions)

    def test_factor_score_metadata_survives_round_trip(self):
        factor = FactorScore("trend", 80, freshness="STALE", source_quality=0.5)
        restored = AssetAssessment.from_mapping("AAVE", AssetAssessment("AAVE", {"trend": factor}).as_dict())
        self.assertEqual(restored.factor_scores["trend"].freshness, "STALE")
        self.assertEqual(restored.factor_scores["trend"].source_quality, 0.5)

    def test_medium_confidence_and_elevated_event_keep_the_same_target(self):
        common = {"weighted_score": 85, "relative_strength_vs_btc": "OUTPERFORM"}
        high = build_target_allocation(assessments={"AAVE": {**common, "confidence": "HIGH"}})
        medium = build_target_allocation(assessments={"AAVE": {**common, "confidence": "MEDIUM"}})
        elevated = build_target_allocation(assessments={
            "AAVE": {**common, "confidence": "HIGH", "event_risk": {"state": "ELEVATED"}}
        })
        self.assertEqual(high.target_weights["AAVE"], medium.target_weights["AAVE"])
        self.assertEqual(high.target_weights["AAVE"], elevated.target_weights["AAVE"])
        self.assertEqual(high.deployment_allowances["AAVE"]["risk_tier_source"], "MANUAL_ASSESSMENT")
        self.assertLess(medium.deployment_factors["AAVE"], high.deployment_factors["AAVE"])
        self.assertLess(elevated.deployment_factors["AAVE"], high.deployment_factors["AAVE"])

    def test_deployment_restriction_cannot_create_reduce(self):
        result = recommend_rebalance(
            {"AAVE": 0.05, "USDT": 0.95},
            {"AAVE": 0.12, "USDT": 0.88},
            1000,
            deployment_caps={"AAVE": 0.0},
        )
        action = next(item for item in result if item.symbol == "AAVE")
        self.assertEqual(action.target_weight, 0.12)
        self.assertEqual(action.action, "WAIT")

    def test_low_confidence_position_has_zero_immediate_allowance(self):
        result = build_target_allocation(assessments={
            "AAVE": {"weighted_score": 85, "confidence": "LOW", "relative_strength_vs_btc": "OUTPERFORM"}
        })
        self.assertGreater(result.target_weights["AAVE"], 0)
        self.assertEqual(result.deployment_allowances["AAVE"]["max_immediate_increase_weight"], 0)

    def test_decision_confidence_keeps_portfolio_data_independent_from_asset_evidence(self):
        packet = build_decision_review_packet(
            current_weights={"BTC": 0.8, "USDT": 0.2},
            target_weights={"BTC": 0.8, "USDT": 0.2},
            assessments={"BTC": {"weighted_score": 70, "confidence": "LOW", "confidence_score": 0.2}},
        )
        components = packet.decision_confidence.components
        self.assertEqual(components["portfolio_data"]["score"], 1.0)
        self.assertLess(components["asset_evidence"]["score"], components["portfolio_data"]["score"])

    def test_historical_governance_collection_events_are_ignored(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "collection-events.jsonl"
            path.write_text(
                '{"event_id":"old","timestamp":"2026-09-09T00:00:00Z","asset":"AAVE",'
                '"metric_key":"risk.governance_event_status","status":"FAILED","reason":"old"}\n',
                encoding="utf-8",
            )
            self.assertEqual(read_collection_events(path), [])


if __name__ == "__main__":
    unittest.main()
