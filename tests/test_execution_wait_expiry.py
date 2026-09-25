"""Bounded execution WAIT expiry (Strategy V2 Phase 3)."""

import unittest
from datetime import date, timedelta

from crypto_portfolio.engine.entry import build_entry_plan
from crypto_portfolio.engine.execution import validate_execution_plan
from crypto_portfolio.engine.technical import build_technical_snapshot
from crypto_portfolio.models.market import Candle, OHLCVSeries, SpotPrice
from crypto_portfolio.models.policy import load_policy, policy_from_mapping


def _rising_series(count=365, *, final_price=None):
    """A steadily rising series: no pullback structure, extended at the end."""
    candles = []
    start = date(2025, 1, 1)
    for index in range(count):
        close = 100 + index * 0.8
        if index >= count - 40:
            close *= 1.01 ** (index - (count - 41))  # accelerating, extended
        candles.append(Candle(
            (start + timedelta(days=index)).isoformat() + "T00:00:00Z",
            close - 0.5, close + 1.5, close - 1.5, close, 150,
        ))
    return OHLCVSeries(
        "SOL", "1D", tuple(candles), source="synthetic",
        fetched_at="2026-01-01T00:00:00Z",
    )


def _snapshot(series, price):
    return build_technical_snapshot(
        series, SpotPrice(series.symbol, price, "2026-01-01T08:00:00Z", "synthetic", "2026-01-01T08:00:00Z"),
        policy=load_policy(),
    )


class WaitExpiryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.series = _rising_series()
        cls.snapshot = _snapshot(cls.series, float(cls.series.candles[-1].close))
        base = build_entry_plan("SOL", 4000, cls.snapshot, "NORMAL", "HIGH")
        # The synthetic extension gate must actually hold this at WAIT;
        # otherwise the fixture is not exercising the expiry path.
        assert base.action == "WAIT", base.rationale

    def test_below_expiry_stays_wait(self):
        plan = build_entry_plan(
            "SOL", 4000, self.snapshot, "NORMAL", "HIGH", wait_streak=4,
        )
        self.assertEqual(plan.action, "WAIT")
        self.assertEqual(plan.planned_amount_usd, 0.0)

    def test_at_expiry_deploys_partially_at_market(self):
        plan = build_entry_plan(
            "SOL", 4000, self.snapshot, "NORMAL", "HIGH", wait_streak=5,
        )
        self.assertEqual(plan.action, "INCREASE")
        self.assertEqual(plan.entry_mode, "MARKET_TIMEOUT")
        self.assertEqual(plan.reserve_policy, "TIMEOUT_RESERVE")
        self.assertEqual(len(plan.tranches), 1)
        tranche = plan.tranches[0]
        self.assertAlmostEqual(tranche.reference_price, self.snapshot.current_spot_price)
        self.assertGreater(tranche.amount_usd, 0)
        self.assertAlmostEqual(tranche.allocation_fraction, 1.0)
        max_fraction = float(
            load_policy().execution["max_initial_tranche"]["NORMAL"]
        )
        self.assertLessEqual(
            plan.planned_amount_usd / plan.approved_amount_usd, max_fraction + 1e-9
        )
        self.assertAlmostEqual(
            plan.planned_amount_usd + plan.reserve_amount_usd, plan.approved_amount_usd
        )
        self.assertEqual(
            plan.gate_details["code"], "EXECUTION_TIMEOUT_PARTIAL_DEPLOYMENT"
        )
        self.assertTrue(validate_execution_plan(plan))
        self.assertEqual(plan.planning_context["wait_streak"], 5)

    def test_broken_thesis_never_expires_into_a_buy(self):
        plan = build_entry_plan(
            "SOL", 4000, self.snapshot, "NORMAL", "HIGH",
            wait_streak=50, thesis_broken=True,
        )
        self.assertEqual(plan.action, "WAIT")

    def test_capital_preservation_never_expires_into_a_buy(self):
        plan = build_entry_plan(
            "SOL", 4000, self.snapshot, "CAPITAL_PRESERVATION", "HIGH", wait_streak=50,
        )
        self.assertEqual(plan.action, "WAIT")

    def test_disabled_wait_expiry_keeps_the_veto(self):
        import json
        data = json.loads(json.dumps(load_policy().as_dict()))
        data["execution_overlay"]["wait"]["enabled"] = False
        policy = policy_from_mapping(data)
        plan = build_entry_plan(
            "SOL", 4000, self.snapshot, "NORMAL", "HIGH",
            wait_streak=50, policy=policy,
        )
        self.assertEqual(plan.action, "WAIT")

    def test_negative_streak_is_rejected(self):
        with self.assertRaises(ValueError):
            build_entry_plan(
                "SOL", 4000, self.snapshot, "NORMAL", "HIGH", wait_streak=-1,
            )

    def test_expiry_plan_round_trips_the_schema(self):
        from jsonschema import Draft202012Validator, FormatChecker
        import json
        plan = build_entry_plan(
            "SOL", 4000, self.snapshot, "NORMAL", "HIGH", wait_streak=5,
        )
        schema = json.load(open("schemas/execution-plan.schema.json"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self.assertEqual([], list(validator.iter_errors(plan.as_dict())))


if __name__ == "__main__":
    unittest.main()
