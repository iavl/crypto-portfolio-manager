"""Regression tests for backward-compatible history replay."""

import json
import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.model_routing import (
    RoutingError,
    validate_historical_stage_model,
)
from crypto_portfolio.models.decision import Decision
from crypto_portfolio.models.metrics_history import MetricObservation
from crypto_portfolio.state._jsonl import append_record, read_records
from crypto_portfolio.state.context import build_history_context


def _routing_v1_metadata() -> dict:
    return {
        "routing_policy_version": 1,
        "stages_used": {
            "history": "PYTHON",
            "factor_semantic_analysis": "TERRA",
            "report_generation": "TERRA",
        },
    }


class HistoricalRecordTests(unittest.TestCase):
    def test_v1_routing_record_loads_after_config_change(self):
        # A record persisted before the LUNA_MAX-only routing change must replay.
        record = {
            "timestamp": "2026-09-02T12:01:30Z",
            "policy_version": 1,
            "market_regime": "NORMAL",
            "current_weights": {"BTC": 0.6, "USDT": 0.4},
            "target_weights": {"BTC": 0.6, "USDT": 0.4},
            "status": "PENDING",
            "review_type": "FULL_REVIEW",
            "routing_metadata": _routing_v1_metadata(),
        }
        decision = Decision.from_mapping(record)
        self.assertEqual(decision.review_type, "FULL_REVIEW")
        self.assertEqual(decision.routing_metadata["stages_used"]["factor_semantic_analysis"], "TERRA")

    def test_historical_stage_model_validation(self):
        self.assertTrue(validate_historical_stage_model("history", "PYTHON"))
        self.assertTrue(validate_historical_stage_model("factor_semantic_analysis", "TERRA"))
        with self.assertRaises(RoutingError):
            validate_historical_stage_model("factor_semantic_analysis", "LUNA_MEDIUM")
        with self.assertRaises(RoutingError):
            validate_historical_stage_model("not_a_stage", "TERRA")

    def test_observation_shaped_evidence_projects(self):
        observation = MetricObservation(
            "observation-1", "BTC", "market.spot_price", "trend", 100.0, "USD", None,
            "2026-09-01", "2026-09-01", "test", "CURRENT", "HIGH",
        ).as_dict()
        record = {
            "timestamp": "2026-09-02T12:01:30Z",
            "policy_version": 1,
            "market_regime": "NORMAL",
            "current_weights": {"BTC": 1.0},
            "target_weights": {"BTC": 0.9, "USDT": 0.1},
            "evidence": [observation],
        }
        decision = Decision.from_mapping(record)
        self.assertEqual(len(decision.evidence), 1)
        self.assertIsInstance(decision.evidence[0].id, str)
        self.assertEqual(decision.evidence[0].factor, "trend")

    def test_hybrid_evidence_keeps_evidence_fields(self):
        hybrid = {
            "id": "semantic-BTC-fundamentals",
            "observation_id": "semantic-BTC-fundamentals",
            "asset": "BTC",
            "metric_key": "fundamentals.semantic",
            "factor": "fundamentals",
            "source": "test",
            "observed_at": "2026-09-03T14:45:20Z",
            "fetched_at": "2026-09-03T14:45:20Z",
            "freshness": "CURRENT",
            "confidence": "MEDIUM",
            "value": {"max_supply": 21000000},
            "summary": "semantic fact",
            "metadata": {"kind": "bounded_semantic_fact"},
        }
        record = {
            "timestamp": "2026-09-03T14:45:20Z",
            "policy_version": 1,
            "market_regime": "NORMAL",
            "current_weights": {"BTC": 1.0},
            "target_weights": {"BTC": 0.9, "USDT": 0.1},
            "evidence": [hybrid],
        }
        decision = Decision.from_mapping(record)
        self.assertEqual(decision.evidence[0].id, "semantic-BTC-fundamentals")
        self.assertEqual(decision.evidence[0].value, {"max_supply": 21000000})

    def test_build_history_context_surfaces_unparseable_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observations = root / "observations.jsonl"
            unknown_metric = MetricObservation(
                "ob-1", "ETH", "market.spot_price", "trend", 100.0, "USD", None,
                "2026-09-01", "2026-09-01", "test", "CURRENT", "HIGH",
            ).as_dict()
            legacy_metric = {
                "observation_id": "ob-2",
                "asset": "BTC",
                "metric_key": "fundamentals.semantic",
                "factor": "fundamentals",
                "value": {"market_cap_rank": 1},
                "observed_at": "2026-09-03T14:45:20Z",
                "fetched_at": "2026-09-03T14:45:20Z",
                "source": "test",
                "freshness": "CURRENT",
                "confidence": "MEDIUM",
            }
            with observations.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(unknown_metric) + "\n")
                handle.write(json.dumps(legacy_metric) + "\n")
            context = build_history_context(
                snapshot_path=root / "missing-snapshots.jsonl",
                decision_path=root / "missing-decisions.jsonl",
                metrics_path=observations,
            )
            self.assertIn("ETH", context["metric_history_summary"])
            self.assertEqual(
                context["history_warnings"],
                ("unknown metric key: fundamentals.semantic",),
            )


    def test_unicode_line_separators_do_not_break_jsonl_framing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            append_record(path, {"summary": "a\u2028b\u2029c\u0085d", "value": 1})
            append_record(path, {"summary": "plain", "value": 2})
            records = read_records(path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["summary"], "a\u2028b\u2029c\u0085d")


if __name__ == "__main__":
    unittest.main()
