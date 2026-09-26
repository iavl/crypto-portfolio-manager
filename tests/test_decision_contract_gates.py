import unittest
from datetime import date, timedelta
from dataclasses import replace
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

    def test_context_binding_rejects_top_level_assessment_mismatch(self):
        policy = resolve_policy()
        context = {
            "as_of": "2026-01-01T00:00:00Z",
            "resolved_policy": policy.as_dict(),
            "assessments": {
                "BTC": {
                    "factor_scores": {},
                    "weighted_score": None,
                    "confidence": "LOW",
                    "asset_type": "core",
                }
            },
            "evidence": [],
        }
        with self.assertRaisesRegex(ValueError, "CALCULATION_ASSESSMENT_MISMATCH"):
            validate_calculation_context(
                context,
                expected_assessments={
                    "BTC": {
                        **context["assessments"]["BTC"],
                        "confidence": "HIGH",
                    }
                },
            )

    def test_append_gate_requires_execution_plan_for_approved_increase(self):
        decision = Decision(
            "2026-01-01T00:00:00Z",
            "NORMAL",
            {"BTC": 0.5, "USDT": 0.5},
            {"BTC": 0.6, "USDT": 0.4},
            actions=(RebalanceAction("BTC", "INCREASE", 0.5, 0.6, 100.0, "NORMAL"),),
        )
        with TemporaryDirectory() as directory, self.assertRaisesRegex(
            ValueError, "EXECUTION_PLAN_REQUIRED"
        ):
            append_decision(decision, Path(directory) / "decisions.jsonl")

    def test_decision_rejects_future_non_execution_evidence(self):
        from crypto_portfolio.models.evidence import Evidence

        with self.assertRaisesRegex(ValueError, "observed after decision timestamp"):
            Decision(
                "2026-01-01T00:00:00Z",
                "NORMAL",
                {"BTC": 1.0},
                {"BTC": 1.0},
                evidence=(
                    Evidence(
                        "future-event",
                        "BTC",
                        "event_risk",
                        "fixture",
                        "2026-01-02T00:00:00Z",
                        "2026-01-02T00:00:00Z",
                        "CURRENT",
                        "HIGH",
                    ),
                ),
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
            assessments={"ETH": {"weighted_score": 70, "normalized_score": 70, "confidence": "HIGH"}},
            execution_plans={"ETH": plan.as_dict()},
        )
        self.assertEqual(packet.execution_plans["ETH"]["symbol"], "ETH")
        report = build_report_packet(packet)
        self.assertEqual(report.execution_plans["ETH"]["approved_amount_usd"], 1000.0)

    def test_missing_diagnostic_inputs_are_explicitly_unavailable(self):
        packet = build_decision_review_packet(
            current_weights={"BTC": 0.5, "USDT": 0.5},
            target_weights={"BTC": 0.6, "USDT": 0.4},
            assessments={"BTC": {"weighted_score": 70, "normalized_score": 70, "confidence": "HIGH"}},
        )
        self.assertEqual(packet.review_diagnostics["availability"], "UNAVAILABLE")
        self.assertEqual(packet.target_attribution["availability"], "UNAVAILABLE")

    def test_trend_replay_from_persisted_snapshot_round_trips_swing_points(self):
        """The replay contract re-derives trend from the receipt's snapshot dict.

        Real snapshots carry swing points, so the dict path must rebuild every
        embedded model or persisted decisions with executable actions cannot be
        validated.
        """
        import math as math_module
        from types import SimpleNamespace

        from crypto_portfolio.engine.calculation_evidence import (
            trend_calculation_evidence,
            validate_trend_calculation,
        )
        from crypto_portfolio.engine.factors.trend import calculate_trend_factor
        from crypto_portfolio.engine.technical import build_technical_snapshot

        policy = resolve_policy()
        start = date(2025, 4, 1)
        candles = []
        for index in range(426):
            phase = index % 10
            close = 100.0 + phase * 2.0 if phase <= 5 else 110.0 - (phase - 5) * 2.0
            timestamp = (start + timedelta(days=index)).isoformat() + "T00:00:00Z"
            candles.append(Candle(timestamp, close - 1.0, close + 2.0, close - 2.0, close, 1000.0))
        series = OHLCVSeries("ETH", "1D", candles, source="synthetic", fetched_at="2026-06-01T08:00:00Z")
        spot = SpotPrice("ETH", 100.0, "2026-06-01T08:00:00Z", "synthetic", "2026-06-01T08:00:00Z")
        snapshot = build_technical_snapshot(series, spot, as_of="2026-06-01T08:00:00Z", policy=policy)
        self.assertTrue(snapshot.swing_highs)
        self.assertTrue(snapshot.swing_lows)

        direct = calculate_trend_factor(snapshot, policy=policy)
        replayed = calculate_trend_factor(snapshot.as_dict(), policy=policy)
        self.assertTrue(math_module.isclose(direct.score, replayed.score, abs_tol=1e-9))
        receipt = trend_calculation_evidence(snapshot, policy)
        detail = validate_trend_calculation(
            SimpleNamespace(evidence_ids=(receipt.id,), score=direct.score, availability="AVAILABLE"),
            {receipt.id: receipt},
            symbol="ETH",
            as_of=snapshot.as_of,
            policy=policy,
        )
        self.assertTrue(math_module.isclose(detail["score"], direct.score, abs_tol=1e-9))

    def test_trend_receipt_binds_prior_volume_history(self):
        from types import SimpleNamespace
        from crypto_portfolio.engine.calculation_evidence import validate_trend_calculation
        from crypto_portfolio.engine.factors.trend import calculate_trend_factor

        policy = resolve_policy()
        start = date(2025, 4, 1)
        candles = tuple(
            Candle(
                (start + timedelta(days=index)).isoformat() + "T00:00:00Z",
                99.0,
                102.0,
                98.0,
                100.0,
                100.0,
            )
            for index in range(426)
        )
        series = OHLCVSeries("ETH", "1D", candles, source="synthetic", fetched_at="2026-06-01T08:00:00Z")
        spot = SpotPrice("ETH", 100.0, "2026-06-01T08:00:00Z", "synthetic", "2026-06-01T08:00:00Z")
        from crypto_portfolio.engine.technical import build_technical_snapshot

        snapshot = replace(
            build_technical_snapshot(series, spot, as_of="2026-06-01T08:00:00Z", policy=policy),
            volume_state="WEAK",
            relative_volume=0.5,
        )
        history = [{"observed_at": "2026-05-30T00:00:00Z", "relative_volume": 0.5}]
        result = calculate_trend_factor(snapshot, policy=policy, previous_relative_volumes=history)
        receipt = {item.id: item for item in result.evidence_records}
        detail = validate_trend_calculation(
            SimpleNamespace(score=result.score, evidence_ids=result.evidence_ids),
            receipt,
            symbol="ETH",
            as_of=snapshot.as_of,
            policy=policy,
        )
        self.assertEqual(detail["score"], result.score)
        self.assertEqual(
            receipt[result.evidence_ids[0]].metadata["previous_relative_volume_history"],
            history,
        )

    def test_trend_receipt_rejects_undated_or_current_close_history(self):
        from crypto_portfolio.engine.factors.trend import calculate_trend_factor
        from crypto_portfolio.engine.technical import build_technical_snapshot

        policy = resolve_policy()
        start = date(2025, 4, 1)
        candles = tuple(
            Candle(
                (start + timedelta(days=index)).isoformat() + "T00:00:00Z",
                99.0, 102.0, 98.0, 100.0, 100.0,
            )
            for index in range(426)
        )
        series = OHLCVSeries("ETH", "1D", candles, source="synthetic", fetched_at="2026-06-01T08:00:00Z")
        spot = SpotPrice("ETH", 100.0, "2026-06-01T08:00:00Z", "synthetic", "2026-06-01T08:00:00Z")
        snapshot = build_technical_snapshot(series, spot, as_of="2026-06-01T08:00:00Z", policy=policy)
        with self.assertRaisesRegex(ValueError, "dated previous volume history"):
            calculate_trend_factor(snapshot, policy=policy, previous_relative_volumes=[0.5])
        with self.assertRaisesRegex(ValueError, "precede the current completed close"):
            calculate_trend_factor(
                snapshot,
                policy=policy,
                previous_relative_volumes=[
                    {"observed_at": "2026-05-31T00:00:00Z", "relative_volume": 0.5}
                ],
            )

    def test_flow_receipt_rejects_score_changes_without_input_changes(self):
        import copy
        from crypto_portfolio.engine.calculation_evidence import flow_calculation_evidence
        from crypto_portfolio.engine.factors.flows import calculate_flow_factor
        from crypto_portfolio.engine.scoring import score_factors

        policy = resolve_policy()
        as_of = "2026-01-01T00:00:00Z"
        flow_input = {"normalized_flow_ratio": 0.005}
        flow = calculate_flow_factor(flow_input, symbol="BTC", policy=policy)
        receipt = flow_calculation_evidence(
            flow_input, symbol="BTC", as_of=as_of, policy=policy
        )
        factors = {
            "trend": 70,
            "capital_flows": {
                "factor": "capital_flows",
                "score": flow.score,
                "evidence_ids": [receipt.id],
                "reliability": flow.coverage,
            },
            "btc_valuation": 70,
            "macro_liquidity": 70,
        }
        expected = score_factors(factors, symbol="BTC", policy=policy).score
        context = {
            "as_of": as_of,
            "resolved_policy": policy.as_dict(),
            "assessments": {
                "BTC": {
                    "factor_scores": factors,
                    "weighted_score": expected,
                    "confidence": "HIGH",
                    "asset_type": "core",
                    "relative_strength_vs_btc": None,
                    "risk_tier": "normal",
                }
            },
            "evidence": [receipt.as_dict()],
        }
        validate_calculation_context(context)
        tampered = copy.deepcopy(context)
        tampered["assessments"]["BTC"]["factor_scores"]["capital_flows"]["score"] = 0.0
        tampered["assessments"]["BTC"]["weighted_score"] = score_factors(
            tampered["assessments"]["BTC"]["factor_scores"], symbol="BTC", policy=policy
        ).score
        with self.assertRaisesRegex(ValueError, "CALCULATION_SCORE_MISMATCH: capital_flows"):
            validate_calculation_context(tampered)

    def test_relative_strength_receipt_replays_both_price_series(self):
        from types import SimpleNamespace
        from crypto_portfolio.engine.calculation_evidence import (
            relative_strength_calculation_evidence,
            validate_relative_strength_calculation,
        )
        from crypto_portfolio.engine.factors.relative_strength import calculate_relative_strength

        policy = resolve_policy()
        as_of = "2026-01-01T00:00:00Z"
        asset = tuple(100.0 + index * 0.1 for index in range(220))
        btc = tuple(100.0 + index * 0.1 for index in range(220))
        result = calculate_relative_strength(asset, btc, symbol="SOL", policy=policy, as_of=as_of)
        receipt = relative_strength_calculation_evidence(
            asset, btc, symbol="SOL", as_of=as_of, policy=policy
        )
        replayed = validate_relative_strength_calculation(
            SimpleNamespace(score=result.score, evidence_ids=(receipt.id,)),
            {receipt.id: receipt},
            symbol="SOL",
            as_of=as_of,
            policy=policy,
        )
        self.assertEqual(replayed["score"], result.score)
        self.assertEqual(replayed["coverage"], 1.0)

if __name__ == "__main__":
    unittest.main()
