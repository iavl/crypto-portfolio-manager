import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.engine.entry import build_entry_plan, build_execution_evidence
from crypto_portfolio.engine.execution import validate_execution_plan
from crypto_portfolio.engine.rebalance import RebalanceAction, validate_execution_plan as validate_rebalance_plan
from crypto_portfolio.engine.technical import build_technical_snapshot
from crypto_portfolio.models.decision import Decision
from crypto_portfolio.models.execution import ExecutionPlan, ExecutionTranche, Invalidation, PriceZone
from crypto_portfolio.models.market import Candle, OHLCVSeries, SpotPrice
from crypto_portfolio.state.decisions import append_decision, read_decisions
from crypto_portfolio.state.market_data import cache_ohlcv, load_ohlcv


def series(count=365, *, last_volume=200):
    candles = []
    start = date(2025, 1, 1)
    for index in range(count):
        close = 100 + index * 0.5
        if index == 330:
            close -= 20
        candles.append(
            Candle(
                (start + timedelta(days=index)).isoformat(),
                close - 0.5,
                close + 2,
                close - 2,
                close,
                last_volume if index == count - 1 else 100,
            )
        )
    return OHLCVSeries("ETH", "1D", tuple(candles), source="synthetic", fetched_at="2026-01-01T00:00:00Z")


class ExecutionEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = build_technical_snapshot(
            series(),
            SpotPrice("ETH", 282, "2026-01-01T08:00:00Z", "synthetic", "2026-01-01T08:00:00Z"),
        )

    def test_healthy_pullback_is_staged_and_reconciled(self):
        plan = build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH")
        self.assertEqual(plan.action, "INCREASE")
        self.assertEqual(plan.entry_mode, "PULLBACK")
        self.assertGreater(plan.planned_amount_usd, 0)
        self.assertLessEqual(plan.planned_amount_usd, plan.approved_amount_usd)
        self.assertAlmostEqual(
            sum(tranche.amount_usd for tranche in plan.tranches), plan.planned_amount_usd
        )
        self.assertAlmostEqual(plan.planned_amount_usd + plan.unallocated_amount_usd, 2000)
        self.assertTrue(plan.invalidation["review_only"])
        self.assertTrue(validate_execution_plan(plan))
        self.assertTrue(validate_rebalance_plan(plan))
        self.assertTrue(validate_execution_plan(plan.as_dict()))

    def test_wait_cases_do_not_manufacture_orders(self):
        extended = build_entry_plan("ETH", 2000, self.snapshot, "DEFENSIVE", "HIGH", entry_mode="PULLBACK")
        self.assertEqual(extended.action, "INCREASE")
        far = build_technical_snapshot(series(), 500, policy=None)
        wait = build_entry_plan("ETH", 2000, far, "NORMAL", "HIGH")
        self.assertEqual(wait.action, "WAIT")
        self.assertEqual(wait.entry_mode, "WAIT")
        self.assertEqual(wait.planned_amount_usd, 0)
        self.assertEqual(wait.unallocated_amount_usd, 2000)
        capital = build_entry_plan("ETH", 2000, self.snapshot, "CAPITAL_PRESERVATION", "HIGH")
        self.assertEqual(capital.action, "WAIT")
        self.assertEqual(capital.planned_amount_usd, 0)
        short_snapshot = build_technical_snapshot(series(119), 160)
        short = build_entry_plan("ETH", 2000, short_snapshot, "NORMAL", "HIGH")
        self.assertEqual(short.action, "WAIT")

        stale_snapshot = build_technical_snapshot(
            series(),
            SpotPrice("ETH", 282, "2026-09-02T08:00:00Z", "synthetic", "2026-09-02T08:00:00Z"),
        )
        stale_plan = build_entry_plan("ETH", 2000, stale_snapshot, "NORMAL", "HIGH")
        self.assertEqual(stale_plan.action, "WAIT")

    def test_regime_and_confidence_are_monotonic(self):
        normal = build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH")
        defensive = build_entry_plan("ETH", 2000, self.snapshot, "DEFENSIVE", "HIGH")
        medium = build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "MEDIUM")
        self.assertLessEqual(defensive.tranches[0].amount_usd, normal.tranches[0].amount_usd)
        self.assertLessEqual(medium.planned_amount_usd, normal.planned_amount_usd)

    def test_missing_volume_reduces_deployment(self):
        missing = build_technical_snapshot(series(), 282, volume_reliable=False)
        plan = build_entry_plan("ETH", 2000, missing, "NORMAL", "HIGH")
        regular = build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH")
        self.assertLessEqual(plan.planned_amount_usd, regular.planned_amount_usd)

    def test_breakout_requires_gates_and_has_small_first_tranche(self):
        rejected = build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH", entry_mode="BREAKOUT")
        self.assertEqual(rejected.action, "WAIT")
        accepted = build_entry_plan(
            "ETH",
            2000,
            self.snapshot,
            "NORMAL",
            "HIGH",
            entry_mode="BREAKOUT",
            breakout_confirmed=True,
            relative_strength_confirmed=True,
        )
        self.assertEqual(accepted.entry_mode, "WAIT")
        self.assertEqual(accepted.planned_amount_usd, 0)
        with self.assertRaises(ValueError):
            build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH", entry_mode="MIXED")

    def test_approved_amount_only_changes_dollars_not_zones(self):
        small = build_entry_plan("ETH", 1000, self.snapshot, "NORMAL", "HIGH")
        large = build_entry_plan("ETH", 5000, self.snapshot, "NORMAL", "HIGH")
        self.assertEqual(
            [(item.price_low, item.price_high, item.reference_price) for item in small.tranches],
            [(item.price_low, item.price_high, item.reference_price) for item in large.tranches],
        )

    def test_model_and_schema_reject_bad_quantity_or_reconciliation(self):
        zone = PriceZone(90, 100, kind="SUPPORT", sources=("MA50",))
        with self.assertRaises(ValueError):
            ExecutionTranche(1, 1, 10, 90, 100, 95, 1)
        with self.assertRaises(ValueError):
            ExecutionTranche(1, 1, 10, 90, 100, 95, 10 / 95, structural_sources=())
        with self.assertRaises(ValueError):
            ExecutionTranche(1, 1, 10, 90, 100, 95, 10 / 95, structural_sources=("MA50", "MA50"))
        tranche = ExecutionTranche(1, 1, 10, 90, 100, 95, 10 / 95, structural_sources=zone.sources)
        with self.assertRaises(ValueError):
            ExecutionPlan(1, "ETH", "INCREASE", 10, 11, 0, 110, "PULLBACK", "HIGH", (tranche,))
        plan = build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH")
        schema = json.loads((Path(__file__).parents[1] / "schemas" / "execution-plan.schema.json").read_text())
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(plan.as_dict()))
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))
        self.assertEqual(ExecutionPlan.from_mapping(plan.as_dict()), plan)

        malformed = plan.as_dict()
        malformed["tranches"][0]["structural_sources"] = []
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(malformed)))
        with self.assertRaises(ValueError):
            ExecutionPlan.from_mapping(malformed)

        malformed = plan.as_dict()
        malformed["tranches"][0]["structural_sources"] = ["MA50", "MA50"]
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(malformed)))
        with self.assertRaises(ValueError):
            ExecutionPlan.from_mapping(malformed)

        malformed = plan.as_dict()
        malformed["ohlcv_metadata"]["candle_count"] = 0
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(malformed)))
        with self.assertRaises(ValueError):
            ExecutionPlan.from_mapping(malformed)

    def test_decision_persists_optional_execution_plans(self):
        plan = build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH")
        action = RebalanceAction("ETH", "INCREASE", 0.0, 0.5, 2000, "NORMAL")
        evidence = build_execution_evidence(self.snapshot, plan)
        decision = Decision(
            "2026-01-01T00:00:00Z",
            "NORMAL",
            1,
            {"BTC": 1.0},
            {"BTC": 0.9, "USDT": 0.1},
            actions=(action,),
            evidence=(evidence,),
            execution_plans={"ETH": plan},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.jsonl"
            append_decision(decision, path)
            persisted = read_decisions(path)[0]
        self.assertEqual(Decision.from_mapping(persisted).execution_plans["ETH"], plan)
        self.assertEqual(persisted["execution_plans"]["ETH"]["symbol"], "ETH")
        schema = json.loads((Path(__file__).parents[1] / "schemas" / "decision.schema.json").read_text())
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(persisted))
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))

    def test_execution_plan_must_match_approved_increase_action(self):
        def wait_plan(symbol, amount):
            return ExecutionPlan(1, symbol, "INCREASE", amount, 0, amount, 100, "WAIT", "HIGH")

        with self.assertRaises(ValueError):
            Decision(
                "2026-01-01T00:00:00Z", "NORMAL", 1, {"BTC": 1.0}, {"BTC": 1.0},
                actions=(RebalanceAction("ETH", "INCREASE", 0, 0.5, 1000, "NORMAL"),),
                execution_plans={"ETH": wait_plan("ETH", 500)},
            )
        with self.assertRaises(ValueError):
            Decision(
                "2026-01-01T00:00:00Z", "NORMAL", 1, {"BTC": 1.0}, {"BTC": 1.0},
                execution_plans={"ETH": wait_plan("ETH", 1000)},
            )
        with self.assertRaises(ValueError):
            Decision(
                "2026-01-01T00:00:00Z", "NORMAL", 1, {"BTC": 1.0}, {"BTC": 1.0},
                actions=(RebalanceAction("ETH", "INCREASE", 0, 0.5, 1000, "NORMAL"),),
                execution_plans={"SOL": wait_plan("SOL", 1000)},
            )
        accepted = Decision(
            "2026-01-01T00:00:00Z", "NORMAL", 1, {"BTC": 1.0}, {"BTC": 1.0},
            actions=(RebalanceAction("ETH", "INCREASE", 0, 0.5, 1000, "NORMAL"),),
            execution_plans={"ETH": wait_plan("ETH", 1000)},
        )
        self.assertEqual(accepted.actions[0].amount_usd, 1000)

    def test_setup_quality_blocks_weak_zones_and_scales_staging(self):
        weak_zone = PriceZone(100, 101, kind="SUPPORT", strength=0, sources=("CUSTOM",))
        weak_snapshot = replace(self.snapshot, support_zones=(weak_zone,), setup_quality=20)
        weak_plan = build_entry_plan("ETH", 2000, weak_snapshot, "NORMAL", "HIGH")
        self.assertEqual(weak_plan.action, "WAIT")

        moderate = PriceZone(276, 278, kind="SUPPORT", strength=60, sources=("MA50",))
        one_zone = replace(self.snapshot, support_zones=(moderate,))
        one_plan = build_entry_plan("ETH", 2000, one_zone, "NORMAL", "HIGH")
        strong = tuple(
            PriceZone(low, low + 2, kind="SUPPORT", strength=95, sources=("SWING_LOW", source))
            for low, source in ((276, "MA50"), (264, "MA100"), (252, "MA200"))
        )
        strong_snapshot = replace(self.snapshot, support_zones=strong)
        strong_plan = build_entry_plan("ETH", 2000, strong_snapshot, "NORMAL", "HIGH")
        self.assertGreater(strong_plan.planned_amount_usd, one_plan.planned_amount_usd)
        self.assertEqual(strong_plan.tranches[0].reference_price, 277)

    def test_nearest_qualified_zone_is_selected_before_deeper_quality(self):
        zones = (
            PriceZone(276, 278, kind="SUPPORT", strength=60, sources=("MA50",)),
            PriceZone(220, 222, kind="SUPPORT", strength=98, sources=("SWING_LOW", "MA200")),
        )
        snapshot = replace(self.snapshot, support_zones=zones)
        plan = build_entry_plan("ETH", 2000, snapshot, "NORMAL", "HIGH")
        self.assertEqual(plan.tranches[0].reference_price, 277)

    def test_plan_model_rejects_mixed_and_invalid_invalidation(self):
        with self.assertRaises(ValueError):
            ExecutionPlan(1, "ETH", "WAIT", 0, 0, 0, 100, "MIXED", "LOW")
        with self.assertRaises(ValueError):
            ExecutionPlan(2, "ETH", "WAIT", 0, 0, 0, 100, "WAIT", "LOW")
        with self.assertRaises(ValueError):
            Invalidation("BAD", "review", 100, review_only=False)
        plan = build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH")
        raw = plan.as_dict()
        raw["invalidation"] = {"kind": "BAD", "trigger": "review", "reference_price": 100, "review_only": False, "automatic_order": False}
        with self.assertRaises(ValueError):
            ExecutionPlan.from_mapping(raw)

    def test_decision_requires_matching_execution_technical_evidence(self):
        plan = build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH")
        action = RebalanceAction("ETH", "INCREASE", 0, 0.5, 2000, "NORMAL")
        with self.assertRaises(ValueError):
            Decision(
                "2026-01-01T00:00:00Z", "NORMAL", 1, {"BTC": 1.0}, {"BTC": 1.0},
                actions=(action,), execution_plans={"ETH": plan},
            )
        evidence = build_execution_evidence(self.snapshot, plan)
        broken = replace(evidence, value={"ohlcv_hash": plan.ohlcv_hash, "technical_summary": {}})
        with self.assertRaises(ValueError):
            Decision(
                "2026-01-01T00:00:00Z", "NORMAL", 1, {"BTC": 1.0}, {"BTC": 1.0},
                actions=(action,), evidence=(broken,), execution_plans={"ETH": plan},
            )

    def test_entry_planner_does_not_build_exit_plans(self):
        for action in ("REDUCE", "EXIT"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH", action=action)

    def test_cached_ohlcv_replays_identical_snapshot_and_plan(self):
        spot = SpotPrice("ETH", 282, "2026-01-01T08:00:00Z", "synthetic", "2026-01-01T08:00:00Z")
        original = build_technical_snapshot(series(), spot)
        original_plan = build_entry_plan("ETH", 2000, original, "NORMAL", "HIGH")
        with tempfile.TemporaryDirectory() as directory:
            cache_ohlcv(series(), directory)
            replay_series = load_ohlcv(series().ohlcv_hash, directory)
            replay = build_technical_snapshot(replay_series, spot)
            replay_plan = build_entry_plan("ETH", 2000, replay, "NORMAL", "HIGH")
        self.assertEqual(original, replay)
        self.assertEqual(original_plan, replay_plan)

    def test_cached_ohlcv_is_immutable(self):
        original = series()
        different_provenance = OHLCVSeries(
            original.symbol,
            original.timeframe,
            original.candles,
            source="other",
            fetched_at=original.fetched_at,
        )
        self.assertEqual(original.ohlcv_hash, different_provenance.ohlcv_hash)
        with tempfile.TemporaryDirectory() as directory:
            cache_ohlcv(original, directory)
            with self.assertRaises(ValueError):
                cache_ohlcv(different_provenance, directory)


if __name__ == "__main__":
    unittest.main()
