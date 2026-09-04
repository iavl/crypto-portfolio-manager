import io
import json
import math
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.data_collection import CollectionReporter, collection_summary, format_collection_event
from crypto_portfolio.models.metrics_history import (
    CollectionEvent,
    MetricObservation,
    stable_observation_id,
)
from crypto_portfolio.state.context import build_history_context
from crypto_portfolio.state.metrics import (
    append_metric_observation,
    compare_latest_metric,
    latest_metric,
    metric_series,
    previous_metric,
    read_collection_events,
    read_metric_observations,
)


def observation(value, observed_at, *, freshness="CURRENT", source="test", supersedes=None, revision_reason=None):
    return MetricObservation(
        stable_observation_id("ETH", "fundamentals.tvl", observed_at, source, value),
        "ETH",
        "fundamentals.tvl",
        "fundamentals",
        value,
        "USD",
        "30d",
        observed_at,
        observed_at,
        source,
        freshness,
        "HIGH",
        supersedes_observation_id=supersedes,
        revision_reason=revision_reason,
    )


class MetricHistoryTests(unittest.TestCase):
    def test_model_validation_and_stable_identity(self):
        first_id = stable_observation_id("eth", "fundamentals.tvl", "2026-09-01", "test", 100)
        self.assertEqual(first_id, stable_observation_id("ETH", "fundamentals.tvl", "2026-09-01T00:00:00Z", "test", 100.0))
        with self.assertRaises(ValueError):
            MetricObservation(first_id, "ETH", "unknown.metric", "fundamentals", 100, "USD", None, "2026-09-01", "2026-09-01", "test", "CURRENT", "HIGH")
        for value in (math.nan, math.inf, -math.inf, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MetricObservation(first_id, "ETH", "fundamentals.tvl", "fundamentals", value, "USD", None, "2026-09-01", "2026-09-01", "test", "CURRENT", "HIGH")
        # Non-negative scoring-factor values must be rejected at the model boundary.
        spot_id = stable_observation_id("BTC", "market.spot_price", "2026-09-01", "test", -5.0)
        with self.assertRaises(ValueError):
            MetricObservation(spot_id, "BTC", "market.spot_price", "trend", -5.0, "USD", None, "2026-09-01", "2026-09-01", "test", "CURRENT", "HIGH")
        with self.assertRaises(ValueError):
            MetricObservation(first_id, "ETH", "fundamentals.tvl", "fundamentals", -1.0, "USD", None, "2026-09-01", "2026-09-01", "test", "CURRENT", "HIGH")
        with self.assertRaises(ValueError):
            MetricObservation(first_id, "ETH", "fundamentals.tvl", "onchain", 100, "USD", None, "2026-09-01", "2026-09-01", "test", "CURRENT", "HIGH")
        evidence = MetricObservation(
            first_id, "ETH", "fundamentals.tvl", "fundamentals", 100, "USD", "30d",
            "2026-09-01", "2026-09-01", "test", "CURRENT", "HIGH",
        ).to_evidence()
        self.assertEqual(evidence.metadata["observation_id"], first_id)

    def test_append_query_dedup_revision_and_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.jsonl"
            first = observation(100, "2026-09-01")
            later = observation(110, "2026-09-02")
            append_metric_observation(first, path)
            append_metric_observation(first, path)
            append_metric_observation(later, path)
            self.assertEqual(len(read_metric_observations(path)), 2)
            self.assertEqual(len(metric_series("ETH", "fundamentals.tvl", path=path, start="2026-09-02")), 1)
            self.assertEqual(latest_metric("ETH", "fundamentals.tvl", path=path).value, 110)
            self.assertEqual(previous_metric("ETH", "fundamentals.tvl", path=path).value, 100)
            comparison = compare_latest_metric("ETH", "fundamentals.tvl", path=path)
            self.assertEqual(comparison["absolute_change"], 10)
            self.assertAlmostEqual(comparison["percentage_change"], 0.1)
            self.assertEqual(comparison["trend"], "IMPROVING")
            revised = observation(105, "2026-09-02", supersedes=later.observation_id, revision_reason="corrected source value")
            append_metric_observation(revised, path)
            self.assertEqual(latest_metric("ETH", "fundamentals.tvl", path=path).value, 105)
            self.assertEqual(previous_metric("ETH", "fundamentals.tvl", path=path).value, 100)
            # A same-value revision still persists (value unchanged but the
            # supersedes chain and revision reason carry meaning).
            same_value_revision = MetricObservation(
                "rev-same-1",
                "ETH", "fundamentals.tvl", "fundamentals", 105.0, "USD", "30d",
                "2026-09-02", "2026-09-02", "test", "CURRENT", "HIGH",
                supersedes_observation_id=revised.observation_id,
                revision_reason="same value, updated metadata",
            )
            append_metric_observation(same_value_revision, path)
            self.assertEqual(len(read_metric_observations(path)), 4)
            self.assertEqual(
                latest_metric("ETH", "fundamentals.tvl", path=path).revision_reason,
                "same value, updated metadata",
            )
            with self.assertRaises(ValueError):
                append_metric_observation(observation(106, "2026-09-02"), path)

    def test_trend_edge_cases_and_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.jsonl"
            append_metric_observation(observation(0, "2026-09-01"), path)
            append_metric_observation(observation(10, "2026-09-02"), path)
            comparison = compare_latest_metric("ETH", "fundamentals.tvl", path=path)
            self.assertIsNone(comparison["percentage_change"])
            self.assertEqual(comparison["trend"], "IMPROVING")
            contextual = MetricObservation(
                stable_observation_id("MARKET", "flows.exchange_netflow", "2026-09-03", "test", -5),
                "MARKET", "flows.exchange_netflow", "capital_flows", -5, "USD", None,
                "2026-09-03", "2026-09-03", "test", "CURRENT", "HIGH",
            )
            append_metric_observation(contextual, path)
            append_metric_observation(MetricObservation(
                stable_observation_id("MARKET", "flows.exchange_netflow", "2026-09-04", "test", 5),
                "MARKET", "flows.exchange_netflow", "capital_flows", 5, "USD", None,
                "2026-09-04", "2026-09-04", "test", "CURRENT", "HIGH",
            ), path)
            self.assertEqual(compare_latest_metric("MARKET", "flows.exchange_netflow", path=path)["trend"], "CONFLICTING")
            append_metric_observation(observation(120, "2026-09-04", freshness="STALE"), path)
            self.assertEqual(compare_latest_metric("ETH", "fundamentals.tvl", path=path)["trend"], "CONFLICTING")
            context = build_history_context(
                snapshot_path=Path(directory) / "missing-snapshots.jsonl",
                decision_path=Path(directory) / "missing-decisions.jsonl",
                metrics_path=path,
                metric_keys=("fundamentals.tvl",),
            )
            self.assertIn("ETH", context["metric_history_summary"])
            self.assertIn("fundamentals.tvl", context["metric_history_summary"]["ETH"])

    def test_collection_events_and_reporter(self):
        with tempfile.TemporaryDirectory() as directory:
            observation_path = Path(directory) / "observations.jsonl"
            event_path = Path(directory) / "events.jsonl"
            stream = io.StringIO()
            reporter = CollectionReporter(stream, str(observation_path), str(event_path))
            current = observation(100, "2026-09-01")
            reporter.record(CollectionEvent("e1", "2026-09-01", "ETH", "fundamentals.tvl", "SUCCESS", source="test", observed_at=current.observed_at, fetched_at=current.fetched_at), current)
            reporter.record(CollectionEvent("e2", "2026-09-01", "ETH", "fundamentals.revenue_30d", "FAILED", reason="source unavailable", source="test"))
            reporter.record(CollectionEvent("e3", "2026-09-01", "ETH", "fundamentals.fees_30d", "STALE", reason="last value is old", source="test"))
            reporter.record(CollectionEvent("e4", "2026-09-01", "ETH", "fundamentals.active_users", "CONFLICT", reason="sources disagree", source="test"))
            reporter.record(CollectionEvent("e5", "2026-09-01", "BTC", "fundamentals.tvl", "NOT_APPLICABLE", reason="not an application metric"))
            summary = reporter.print_summary()
            self.assertEqual(len(read_metric_observations(observation_path)), 1)
            self.assertEqual(len(read_collection_events(event_path)), 5)
            self.assertEqual(summary["counts"]["SUCCESS"], 1)
            self.assertEqual(summary["counts"]["NOT_APPLICABLE"], 1)
            self.assertIn("[DATA] ETH fundamentals.tvl SUCCESS", stream.getvalue())
            self.assertIn("Data Collection Summary", stream.getvalue())
            self.assertEqual(collection_summary(read_collection_events(event_path))["critical_failures"], 0)

    def test_collection_criticality_is_review_specific(self):
        regulatory = CollectionEvent(
            "regulatory", "2026-09-01", "ETH", "risk.regulatory_event_status", "FAILED",
            reason="primary source unavailable", source="event-scan",
        )
        security = CollectionEvent(
            "security", "2026-09-01", "ETH", "risk.security_event_status", "FAILED",
            reason="primary source unavailable", source="event-scan",
        )
        snapshot = collection_summary((regulatory, security), review_type="SNAPSHOT_REVIEW")
        self.assertEqual(snapshot["critical_failures"], 1)
        regulatory_text = format_collection_event(regulatory, review_type="SNAPSHOT_REVIEW")
        self.assertNotIn("CRITICAL DATA FAILURE", regulatory_text)
        self.assertIn("not hard-critical for this review", regulatory_text)
        self.assertIn("CRITICAL DATA FAILURE", format_collection_event(security, review_type="SNAPSHOT_REVIEW"))

        full = collection_summary((regulatory,), review_type="FULL_REVIEW")
        event = collection_summary((regulatory,), review_type="EVENT_REVIEW")
        self.assertEqual(full["critical_failures"], 0)
        self.assertEqual(event["critical_failures"], 1)

        success = CollectionEvent(
            "price", "2026-09-01", "ETH", "market.spot_price", "SUCCESS",
            source="test", observed_at="2026-09-01", fetched_at="2026-09-01",
        )
        self.assertEqual(
            collection_summary((success, regulatory), review_type="SNAPSHOT_REVIEW")["critical_failures"],
            0,
        )

    def test_history_records_validate_against_schemas(self):
        root = Path(__file__).parents[1] / "schemas"
        current = observation(100, "2026-09-01")
        event = CollectionEvent("e1", "2026-09-01", "ETH", "fundamentals.tvl", "SUCCESS")
        for filename, record in (
            ("metric-observation.schema.json", current.as_dict()),
            ("collection-event.schema.json", event.as_dict()),
        ):
            schema = json.loads((root / filename).read_text())
            errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(record))
            self.assertEqual(errors, [], "\n".join(error.message for error in errors))


if __name__ == "__main__":
    unittest.main()
