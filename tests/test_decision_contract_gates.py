import unittest
from datetime import date, timedelta
from tempfile import TemporaryDirectory
from pathlib import Path

from crypto_portfolio.engine.calculation_evidence import (
    validate_calculation_context,
    validate_packet_calculations,
)
from crypto_portfolio.engine.decision_packet import build_decision_review_packet
from crypto_portfolio.engine.entry import build_entry_plan
from crypto_portfolio.engine.report_packet import build_report_packet
from crypto_portfolio.engine.scoring import score_factors
from crypto_portfolio.engine.rebalance import RebalanceAction
from crypto_portfolio.models.market import Candle, OHLCVSeries, SpotPrice
from crypto_portfolio.models.decision import Decision
from crypto_portfolio.models.policy import policy_hash, resolve_policy
from crypto_portfolio.state.decisions import append_decision


def _plan():
    start = date(2025, 1, 1)
    candles = tuple(
        Candle(
            (start + timedelta(days=index)).isoformat() + "T00:00:00Z",
            100.0,
            102.0,
            98.0,
            100.0,
            100.0,
        )
        for index in range(365)
    )
    series = OHLCVSeries("ETH", "1D", candles, source="synthetic", fetched_at="2026-01-01T00:00:00Z")
    spot = SpotPrice("ETH", 100.0, "2026-01-01T08:00:00Z", "synthetic", "2026-01-01T08:00:00Z")
    from crypto_portfolio.engine.technical import build_technical_snapshot

    return build_entry_plan("ETH", 1000.0, build_technical_snapshot(series, spot), "NORMAL", "HIGH")


class DecisionContractGateTests(unittest.TestCase):
    def test_executable_persistence_path_requires_calculation_context(self):
        with self.assertRaisesRegex(ValueError, "CALCULATION_CONTEXT_REQUIRED"):
            validate_packet_calculations(
                None,
                [{"action": "INCREASE", "amount_usd": 1.0}],
                {},
                require_context=True,
            )

    def test_rich_persisted_decision_requires_calculation_context(self):
        policy = resolve_policy()
        decision = Decision(
            "2026-01-01T00:00:00Z",
            "NORMAL",
            {"BTC": 0.5, "USDT": 0.5},
            {"BTC": 0.6, "USDT": 0.4},
            actions=(RebalanceAction("BTC", "INCREASE", 0.5, 0.6, 100.0, "NORMAL"),),
            policy_hash=policy_hash(policy),
            resolved_policy=policy.as_dict(),
        )
        with TemporaryDirectory() as directory, self.assertRaisesRegex(
            ValueError, "CALCULATION_CONTEXT_REQUIRED"
        ):
            append_decision(decision, Path(directory) / "decisions.jsonl")

    def test_context_binding_rejects_wrong_policy(self):
        policy = resolve_policy()
        context = {
            "as_of": "2026-01-01T00:00:00Z",
            "resolved_policy": policy.as_dict(),
            "assessments": {},
            "evidence": [],
        }
        with self.assertRaisesRegex(ValueError, "CALCULATION_POLICY_MISMATCH"):
            validate_calculation_context(
                context,
                expected_policy_hash="0" * 64,
                expected_as_of="2026-01-02T00:00:00Z",
            )
        self.assertEqual(
            validate_calculation_context(
                context,
                expected_policy_hash=policy_hash(policy),
                expected_as_of="2026-01-02T00:00:00Z",
            ),
            {},
        )

    def test_missing_quality_provenance_is_visible_in_confidence_reasons(self):
        result = score_factors(
            {
                "trend": 70,
                "valuation": 70,
                "fundamentals": 70,
                "onchain": 70,
                "capital_flows": 70,
                "relative_strength_btc": 70,
            },
            symbol="SOL",
        )
        self.assertIn("SOURCE_QUALITY_UNSPECIFIED", result.factor_data_confidence.reasons)
        self.assertIn("REDUNDANCY_UNSPECIFIED", result.factor_data_confidence.reasons)

    def test_execution_plans_round_trip_through_review_and_report_packets(self):
        plan = _plan()
        packet = build_decision_review_packet(
            current_weights={"ETH": 0.9, "USDT": 0.1},
            target_weights={"ETH": 0.9, "USDT": 0.1},
            assessments={"ETH": {"weighted_score": 70, "confidence": "HIGH"}},
            execution_plans={"ETH": plan.as_dict()},
        )
        self.assertEqual(packet.execution_plans["ETH"]["symbol"], "ETH")
        report = build_report_packet(packet)
        self.assertEqual(report.execution_plans["ETH"]["approved_amount_usd"], 1000.0)


if __name__ == "__main__":
    unittest.main()
