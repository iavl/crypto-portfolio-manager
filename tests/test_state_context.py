import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.models.decision import Decision
from crypto_portfolio.state.context import (
    build_history_context,
    last_full_review,
    latest_decision,
    latest_snapshot,
    portfolio_nav_history,
)
from crypto_portfolio.state.decisions import append_decision
from crypto_portfolio.state.cash_flows import append_cash_flow_resolution
from crypto_portfolio.state.snapshots import append_snapshot
from crypto_portfolio.models.cash_flow import CashFlowResolution


class StateContextTests(unittest.TestCase):
    def test_history_is_loaded_before_new_review_and_full_review_is_due(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "snapshots.jsonl"
            decision_path = Path(directory) / "decisions.jsonl"
            append_snapshot(
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "external_cash_flow": 0,
                    "external_cash_flow_type": "NONE",
                    "cash_flow_resolution_status": "CONFIRMED_NONE",
                    "positions": [
                        {"symbol": "BTC", "value_usd": 100},
                        {"symbol": "USDT", "value_usd": 100},
                    ],
                },
                snapshot_path,
            )
            append_decision(
                Decision(
                    "2026-01-01T00:00:00Z",
                    "NORMAL",
                    {"BTC": 0.5, "USDT": 0.5},
                    {"BTC": 0.6, "USDT": 0.4},
                    review_type="FULL_REVIEW",
                ),
                decision_path,
            )
            append_snapshot(
                {
                    "timestamp": "2026-01-16T00:00:00Z",
                    "external_cash_flow": 0,
                    "external_cash_flow_type": "NONE",
                    "cash_flow_resolution_status": "CONFIRMED_NONE",
                    "positions": [
                        {"symbol": "BTC", "value_usd": 110},
                        {"symbol": "USDT", "value_usd": 90},
                    ],
                },
                snapshot_path,
            )
            self.assertEqual(latest_snapshot(snapshot_path)["timestamp"], "2026-01-16T00:00:00Z")
            self.assertEqual(latest_decision(decision_path)["review_type"], "FULL_REVIEW")
            self.assertEqual(len(portfolio_nav_history(snapshot_path)), 2)
            self.assertIsNotNone(last_full_review(decision_path))
            context = build_history_context(snapshot_path, decision_path, metrics_path=Path(directory) / "metrics.jsonl", as_of="2026-01-16T00:00:00Z")
            self.assertAlmostEqual(context["current_drawdown"], 0.0)
            self.assertEqual(context["previous_target_weights"]["BTC"], 0.6)
            self.assertTrue(context["full_review_due"])

    def test_resolution_file_turns_unresolved_history_final_without_rewriting_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "snapshots.jsonl"
            resolution_path = Path(directory) / "cash-flow-resolutions.jsonl"
            append_snapshot({
                "snapshot_id": "s1",
                "timestamp": "2026-01-01T00:00:00Z",
                "cash_flow_resolution_status": "CONFIRMED_NONE",
                "external_cash_flow": 0,
                "external_cash_flow_type": "NONE",
                "positions": [{"symbol": "BTC", "value_usd": 100}],
            }, snapshot_path)
            append_snapshot({
                "snapshot_id": "s2",
                "timestamp": "2026-01-02T00:00:00Z",
                "positions": [{"symbol": "BTC", "value_usd": 150}],
            }, snapshot_path)
            append_cash_flow_resolution(CashFlowResolution(
                "r2", "s2", "2026-01-03T00:00:00Z", "CONFIRMED_AMOUNT", 50, "DEPOSIT", "user confirmed",
            ), resolution_path)
            result = build_history_context(snapshot_path, cash_flow_resolution_path=resolution_path)
            self.assertEqual(result["performance_finality"], "FINAL")
            self.assertEqual(result["cash_flow_resolution_status"], "CONFIRMED_AMOUNT")
            self.assertEqual(result["nav_history_result"]["performance_finality"], "FINAL")


if __name__ == "__main__":
    unittest.main()
