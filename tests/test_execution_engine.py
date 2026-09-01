import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.engine.entry import build_entry_plan
from crypto_portfolio.engine.execution import validate_execution_plan
from crypto_portfolio.engine.rebalance import validate_execution_plan as validate_rebalance_plan
from crypto_portfolio.engine.technical import build_technical_snapshot
from crypto_portfolio.models.decision import Decision
from crypto_portfolio.models.execution import ExecutionPlan, ExecutionTranche, PriceZone
from crypto_portfolio.models.market import Candle, OHLCVSeries
from crypto_portfolio.state.decisions import append_decision, read_decisions


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
    return OHLCVSeries("ETH", "1D", tuple(candles), source="synthetic")


class ExecutionEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = build_technical_snapshot(series(), 282)

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
        self.assertEqual(accepted.entry_mode, "BREAKOUT")
        self.assertLessEqual(accepted.tranches[0].amount_usd, 2000 * 0.20 + 1e-7)

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
        tranche = ExecutionTranche(1, 1, 10, 90, 100, 95, 10 / 95, structural_sources=zone.sources)
        with self.assertRaises(ValueError):
            ExecutionPlan(1, "ETH", "INCREASE", 10, 11, 0, 110, "PULLBACK", "HIGH", (tranche,))
        plan = build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH")
        schema = json.loads((Path(__file__).parents[1] / "schemas" / "execution-plan.schema.json").read_text())
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(plan.as_dict()))
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))

    def test_decision_persists_optional_execution_plans(self):
        plan = build_entry_plan("ETH", 2000, self.snapshot, "NORMAL", "HIGH")
        decision = Decision(
            "2026-01-01T00:00:00Z",
            "NORMAL",
            1,
            {"BTC": 1.0},
            {"BTC": 0.9, "USDT": 0.1},
            execution_plans={"ETH": plan},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.jsonl"
            append_decision(decision, path)
            persisted = read_decisions(path)[0]
        self.assertEqual(persisted["execution_plans"]["ETH"]["symbol"], "ETH")
        schema = json.loads((Path(__file__).parents[1] / "schemas" / "decision.schema.json").read_text())
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(persisted))
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))


if __name__ == "__main__":
    unittest.main()
