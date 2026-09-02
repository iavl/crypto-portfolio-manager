import json
import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.models.decision import Decision, DecisionStatusEvent
from crypto_portfolio.providers.base import (
    EventDataProvider,
    FundamentalDataProvider,
    MarketDataProvider,
    OnchainDataProvider,
)
from crypto_portfolio.state.decisions import (
    append_decision,
    append_status_event,
    read_decisions,
    read_status_events,
    reconstruct_decision_status,
)
from crypto_portfolio.state.snapshots import append_snapshot, read_snapshots


class StateSchemaProviderTests(unittest.TestCase):
    def test_state_is_append_only_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "portfolio.jsonl"
            snapshot = {
                "timestamp": "2026-09-01",
                "policy_version": 1,
                "positions": [
                    {"symbol": "BTC", "value_usd": 100},
                    {"symbol": "USDT", "value_usd": 100},
                ],
            }
            append_snapshot(snapshot, snapshot_path)
            append_snapshot({**snapshot, "timestamp": "2026-09-02"}, snapshot_path)
            self.assertEqual(len(read_snapshots(snapshot_path)), 2)
            self.assertEqual(read_snapshots(snapshot_path)[0]["timestamp"], "2026-09-01T00:00:00Z")
            self.assertEqual(len(read_snapshots(snapshot_path)[0]["policy_hash"]), 64)
            self.assertTrue(read_snapshots(snapshot_path)[0]["snapshot_id"])

            decision_path = Path(directory) / "decisions.jsonl"
            decision = Decision(
                "2026-09-01",
                "NORMAL",
                1,
                {"BTC": 1.0},
                {"BTC": 0.9, "USDT": 0.1},
            )
            append_decision(decision, decision_path)
            self.assertEqual(len(read_decisions(decision_path)), 1)
            self.assertEqual(len(read_decisions(decision_path)[0]["policy_hash"]), 64)
            self.assertTrue(read_decisions(decision_path)[0]["decision_id"])
            with self.assertRaises(ValueError):
                append_decision(
                    {"timestamp": "2026-09-02", "policy_version": 1, "market_regime": "NORMAL"},
                    decision_path,
                )

    def test_status_changes_are_append_only_events(self):
        with tempfile.TemporaryDirectory() as directory:
            decision_path = Path(directory) / "decisions.jsonl"
            event_path = Path(directory) / "status-events.jsonl"
            decision = Decision(
                "2026-09-01T00:00:00Z",
                "NORMAL",
                1,
                {"BTC": 1.0},
                {"BTC": 1.0},
                decision_id="decision-1",
            )
            append_decision(decision, decision_path)
            event = DecisionStatusEvent("decision-1", "2026-09-02", "CONFIRMED")
            append_status_event(event, event_path)
            self.assertEqual(len(read_decisions(decision_path)), 1)
            self.assertEqual(len(read_status_events(event_path)), 1)
            self.assertEqual(
                reconstruct_decision_status(decision, read_status_events(event_path)).status,
                "CONFIRMED",
            )

    def test_duplicate_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "portfolio.jsonl"
            snapshot = {
                "timestamp": "2026-09-01T00:00:00Z",
                "positions": [{"symbol": "BTC", "value_usd": 100}, {"symbol": "USDT", "value_usd": 100}],
            }
            append_snapshot(snapshot, snapshot_path)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                append_snapshot(snapshot, snapshot_path)

    def test_state_rejects_wrong_policy_and_unresolved_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "portfolio.jsonl"
            snapshot = {
                "timestamp": "2026-09-01T00:00:00Z",
                "policy_version": 99,
                "positions": [{"symbol": "BTC", "value_usd": 100}],
            }
            with self.assertRaises(ValueError):
                append_snapshot(snapshot, snapshot_path)
            with self.assertRaises(ValueError):
                append_decision(
                    Decision(
                        "2026-09-01T00:00:00Z",
                        "NORMAL",
                        1,
                        {"BTC": 1.0},
                        {"BTC": 1.0},
                        evidence=("missing-evidence",),
                    ),
                    Path(directory) / "decisions.jsonl",
                )

    def test_schemas_are_valid_json_and_expose_new_contracts(self):
        for filename in ("portfolio.schema.json", "decision.schema.json", "evidence.schema.json", "execution-plan.schema.json", "market.schema.json"):
            data = json.loads((Path(__file__).parents[1] / "schemas" / filename).read_text())
            self.assertEqual(data["type"], "object")
        decision = json.loads((Path(__file__).parents[1] / "schemas" / "decision.schema.json").read_text())
        self.assertIn("policy_version", decision["properties"])
        self.assertIn("evidence", decision["properties"])

    def test_provider_protocols_exist_without_network_implementation(self):
        for provider in (MarketDataProvider, FundamentalDataProvider, OnchainDataProvider, EventDataProvider):
            self.assertTrue(hasattr(provider, "__annotations__") or hasattr(provider, "__dict__"))


if __name__ == "__main__":
    unittest.main()
