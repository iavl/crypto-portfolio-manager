"""Regression tests for history integrity."""

import json
import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.models.metrics_history import MetricObservation
from crypto_portfolio.state._jsonl import append_record, read_records
from crypto_portfolio.state.context import build_history_context




class HistoricalRecordTests(unittest.TestCase):




    def test_build_history_context_surfaces_unparseable_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observations = root / "observations.jsonl"
            unknown_metric = MetricObservation(
                "ob-1", "ETH", "market.spot_price", "trend", 100.0, "USD", None,
                "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z", "test", "CURRENT", "HIGH",
            ).as_dict()
            invalid_metric = {
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
                handle.write(json.dumps(invalid_metric) + "\n")
            context = build_history_context(
                snapshot_path=root / "missing-snapshots.jsonl",
                decision_path=root / "missing-decisions.jsonl",
                metrics_path=observations,
                cash_flow_resolution_path=root / "missing-resolutions.jsonl",
            )
            self.assertIn("ETH", context["metric_history_summary"])
            self.assertNotIn("fundamentals.semantic", context["metric_history_summary"].get("BTC", {}))



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
