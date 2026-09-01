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
from crypto_portfolio.state.snapshots import append_snapshot


class StateContextTests(unittest.TestCase):
    def test_history_is_loaded_before_new_review_and_full_review_is_due(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "snapshots.jsonl"
            decision_path = Path(directory) / "decisions.jsonl"
            append_snapshot(
                {
                    "timestamp": "2026-01-01T00:00:00Z",
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
                    1,
                    {"BTC": 0.5, "USDT": 0.5},
                    {"BTC": 0.6, "USDT": 0.4},
                    review_type="FULL_REVIEW",
                ),
                decision_path,
            )
            append_snapshot(
                {
                    "timestamp": "2026-01-16T00:00:00Z",
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
            context = build_history_context(snapshot_path, decision_path, as_of="2026-01-16T00:00:00Z")
            self.assertAlmostEqual(context["current_drawdown"], 0.0)
            self.assertEqual(context["previous_target_weights"]["BTC"], 0.6)
            self.assertTrue(context["full_review_due"])


if __name__ == "__main__":
    unittest.main()
