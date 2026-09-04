import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.engine.decision_packet import (
    build_decision_review_packet,
    should_run_sol_final_review,
)
from crypto_portfolio.engine.factor_packet import build_asset_factor_packet
from crypto_portfolio.engine.factors.flows import classify_flow_state
from crypto_portfolio.engine.factors.relative_strength import calculate_relative_strength
from crypto_portfolio.engine.factors.trend import calculate_trend_factor
from crypto_portfolio.engine.metric_history import build_factor_facts
from crypto_portfolio.engine.metric_normalization import normalize_metric_result, persist_metric_result
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest, build_metric_collection_plan
from crypto_portfolio.engine.report_packet import build_report_packet
from crypto_portfolio.engine.regime_inputs import build_regime_inputs
from crypto_portfolio.metrics_registry import METRIC_REGISTRY, MetricDefinition
from crypto_portfolio.model_routing import RoutingError, load_model_routing, validate_stage_model
from crypto_portfolio.models.market import TechnicalSnapshot
from crypto_portfolio.models.metrics_history import MetricObservation, stable_observation_id
from crypto_portfolio.models.execution import PriceZone


def _observation(value, day="2026-09-01"):
    return MetricObservation(
        stable_observation_id("ETH", "fundamentals.tvl", day, "test", value),
        "ETH",
        "fundamentals.tvl",
        "fundamentals",
        value,
        "USD",
        None,
        day,
        day,
        "test",
        "CURRENT",
        "HIGH",
    )


class PythonFirstArchitectureTests(unittest.TestCase):
    def test_registry_aliases_and_collection_plan(self):
        definition = MetricDefinition(
            key="fundamentals.tvl",
            factor="fundamentals",
            asset_scope=("AAVE", "ETH"),
            value_type="number",
            unit="USD",
            freshness="7d",
            trend_enabled=True,
        )
        self.assertEqual(definition.expected_type, "number")
        self.assertEqual(definition.freshness, "7d")
        plan = build_metric_collection_plan(
            {"positions": [{"symbol": "USDT", "value_usd": 100}]},
            watchlist=("SOL",),
        )
        self.assertFalse(plan.for_asset("USDT"))
        self.assertTrue(plan.for_asset("SOL"))
        self.assertIn("market.spot_price", plan.metric_keys)

    def test_review_specific_criticality_is_resolved_by_plan(self):
        governance = METRIC_REGISTRY["risk.governance_event_status"]
        security = METRIC_REGISTRY["risk.security_event_status"]
        self.assertFalse(governance.is_critical_for("SNAPSHOT_REVIEW"))
        self.assertFalse(governance.is_critical_for("FULL_REVIEW"))
        self.assertTrue(governance.is_critical_for("EVENT_REVIEW"))
        self.assertTrue(all(security.is_critical_for(review) for review in ("SNAPSHOT_REVIEW", "FULL_REVIEW", "EVENT_REVIEW")))

        for review_type, expected in (("SNAPSHOT_REVIEW", ("risk.security_event_status",)), ("FULL_REVIEW", ("risk.security_event_status",)), ("EVENT_REVIEW", ("risk.governance_event_status", "risk.security_event_status"))):
            plan = MetricCollectionPlan(
                review_type,
                (
                    MetricRequest("ETH", "risk.governance_event_status"),
                    MetricRequest("ETH", "risk.security_event_status"),
                ),
            )
            self.assertEqual(plan.critical_metric_keys, expected)

        with self.assertRaises(ValueError):
            MetricDefinition(
                key="fundamentals.tvl", factor="fundamentals", expected_type="number",
                critical_review_types=("UNKNOWN_REVIEW",),
            )
        with self.assertRaises(ValueError):
            MetricDefinition(
                key="fundamentals.tvl", factor="fundamentals", expected_type="number",
                critical_review_types=("FULL_REVIEW", "FULL_REVIEW"),
            )

    def test_normalization_history_and_facts_are_python_owned(self):
        result = normalize_metric_result(
            {
                "asset": "ETH",
                "metric_key": "relative.return_vs_btc_30d",
                "value": 5,
                "unit": "%",
                "observed_at": "2026-09-02",
                "fetched_at": "2026-09-02",
                "source": "test",
                "confidence": "HIGH",
            }
        )
        self.assertEqual(result.observation.value, 0.05)
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "observations.jsonl"
            event_path = Path(directory) / "events.jsonl"
            persist_metric_result(result, observation_path=result_path, event_path=event_path)
            self.assertTrue(result_path.exists())
            self.assertTrue(event_path.exists())
        facts = build_factor_facts(
            [_observation(100, "2026-09-01"), _observation(110, "2026-09-02")],
            symbol="ETH",
            factor="fundamentals",
        )
        self.assertEqual(facts.changes["fundamentals.tvl"]["absolute_change"], 10)
        self.assertAlmostEqual(facts.changes["fundamentals.tvl"]["percentage_change"], 0.1)
        self.assertEqual(facts.trends["fundamentals.tvl"], "IMPROVING")

    def test_deterministic_factors_and_regime_inputs(self):
        common = dict(
            symbol="ETH",
            as_of="2026-09-02",
            current_spot_price=120,
            last_completed_close=118,
            history_days=365,
            ma20=112,
            ma50=105,
            ma100=100,
            ma200=90,
            return_30d=0.10,
            return_90d=0.20,
            return_180d=0.30,
            realized_vol_30d=0.2,
            realized_vol_90d=0.25,
            atr14=4,
            atr_percent=4 / 120,
            relative_volume=1.4,
            current_drawdown=-0.05,
            support_zones=(PriceZone(95, 100, kind="SUPPORT", strength=70, sources=("MA50",)),),
            trend_state="STRONG_UPTREND",
            volatility_state="NORMAL",
            volume_state="SUPPORTIVE",
            technical_confidence="HIGH",
            data_confidence="HIGH",
            data_quality="FULL",
            history_sufficient=True,
            market_data_fresh=True,
            cadence_valid=True,
            source_known=True,
            spot_time_valid=True,
            volume_reliable=True,
            provenance_consistent=True,
            ohlcv_hash="0" * 64,
        )
        up = TechnicalSnapshot(**common)
        down = TechnicalSnapshot(
            **{
                **common,
                "current_spot_price": 80,
                "last_completed_close": 82,
                "ma20": 90,
                "ma50": 100,
                "ma100": 105,
                "ma200": 110,
                "return_30d": -0.1,
                "return_90d": -0.2,
                "return_180d": -0.3,
                "current_drawdown": -0.6,
                "support_zones": (PriceZone(70, 75, kind="SUPPORT", strength=70, sources=("MA50",)),),
                "trend_state": "STRONG_DOWNTREND",
                "volume_state": "WEAK",
            }
        )
        first = calculate_trend_factor(up)
        second = calculate_trend_factor(up)
        self.assertEqual(first.score, second.score)
        self.assertGreater(first.score, calculate_trend_factor(down).score)
        relative = calculate_relative_strength(
            [100 * 1.01**index for index in range(181)],
            [100 * 1.005**index for index in range(181)],
            symbol="ETH",
        )
        self.assertGreater(relative.relative_90d, 0)
        self.assertEqual(relative.state, "OUTPERFORM")
        self.assertEqual(classify_flow_state(-1), "NEGATIVE")
        inputs = build_regime_inputs(up, -0.05, "NORMAL", "HEALTHY")
        self.assertEqual(inputs.btc_trend, "BULLISH")
        self.assertEqual(inputs.breadth_state, "HEALTHY")

    def test_packets_are_compact_immutable_and_sol_is_conditional(self):
        factor_packet = build_asset_factor_packet(
            "ETH",
            facts={"fundamentals": build_factor_facts([_observation(10)], symbol="ETH", factor="fundamentals")},
            evidence_ids=("e1",),
        )
        self.assertNotIn("raw_ohlcv", factor_packet.as_dict())
        packet = build_decision_review_packet(
            review_type="SNAPSHOT_REVIEW",
            market_regime="NORMAL",
            current_weights={"ETH": 0.9, "USD": 0.1},
            target_weights={"ETH": 0.9, "USD": 0.1},
            assessments={"ETH": {"weighted_score": 75, "confidence": "HIGH", "factor_scores": {"trend": 75}}},
            factor_packets={"ETH": factor_packet},
        )
        report = build_report_packet(packet)
        self.assertTrue(report.finalized)
        with self.assertRaises(TypeError):
            report.target_weights["ETH"] = 0.1
        with self.assertRaises(ValueError):
            build_report_packet({
                "review_type": "SNAPSHOT_REVIEW",
                "market_regime": "NORMAL",
                "current_weights": {"ETH": 1},
                "target_weights": {"ETH": 1},
                "assets": [],
                "execution_summary": {"raw_webpage": "do not pass"},
            })
        self.assertFalse(should_run_sol_final_review(packet))
        self.assertTrue(
            should_run_sol_final_review(
                review_type="SNAPSHOT_REVIEW",
                market_regime="NORMAL",
                actions=[{"symbol": "ETH", "action": "EXIT", "amount_usd": 100}],
            )
        )

    def test_routing_rejects_non_max_luna_and_schemas_validate(self):
        routing = load_model_routing()
        self.assertEqual(routing.model_for_stage("metric_collection"), "LUNA_MAX")
        with self.assertRaises(RoutingError):
            validate_stage_model("metric_collection", "LUNA_LOW")
        factor_packet = build_asset_factor_packet(
            "ETH",
            facts={"fundamentals": build_factor_facts([_observation(10)], symbol="ETH", factor="fundamentals")},
        )
        packet = build_decision_review_packet(
            review_type="SNAPSHOT_REVIEW",
            market_regime="NORMAL",
            current_weights={"ETH": 1},
            target_weights={"ETH": 1},
            assessments={"ETH": {"weighted_score": 70, "confidence": "HIGH", "factor_scores": {"trend": 70}}},
            factor_packets={"ETH": factor_packet},
        )
        report = build_report_packet(packet)
        root = Path(__file__).parents[1]
        payloads = {
            "model-routing.schema.json": routing.as_dict(),
            "factor-packet.schema.json": factor_packet.as_dict(),
            "decision-review-packet.schema.json": packet.as_dict(),
            "report-packet.schema.json": report.as_dict(),
        }
        for filename, payload in payloads.items():
            schema = json.loads((root / "schemas" / filename).read_text())
            errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload))
            self.assertEqual(errors, [], "\n".join(error.message for error in errors))


if __name__ == "__main__":
    unittest.main()
