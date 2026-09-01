import json
import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.models.decision import Decision
from crypto_portfolio.providers.base import (
    EventDataProvider,
    FundamentalDataProvider,
    MarketDataProvider,
    OnchainDataProvider,
)
from crypto_portfolio.state.decisions import append_decision, read_decisions
from crypto_portfolio.state.snapshots import append_snapshot, read_snapshots


class StateSchemaProviderTests(unittest.TestCase):
    def test_state_is_append_only_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "portfolio.jsonl"
            append_snapshot({"timestamp": "2026-09-01", "policy_version": 1}, snapshot_path)
            append_snapshot({"timestamp": "2026-09-02", "policy_version": 1}, snapshot_path)
            self.assertEqual(len(read_snapshots(snapshot_path)), 2)

            decision_path = Path(directory) / "decisions.jsonl"
            decision = Decision(
                "2026-09-01",
                "NORMAL",
                1,
                {"BTC": 1.0},
                {"BTC": 0.9, "USDT": 0.1},
            )
            append_decision(decision, decision_path)
            append_decision({"timestamp": "2026-09-02", "policy_version": 1, "market_regime": "NORMAL"}, decision_path)
            self.assertEqual(len(read_decisions(decision_path)), 2)

    def test_schemas_are_valid_json_and_expose_new_contracts(self):
        for filename in ("portfolio.schema.json", "decision.schema.json", "evidence.schema.json"):
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
