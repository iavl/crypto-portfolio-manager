import unittest

from crypto_portfolio.engine.execution_replay import simulate_execution_plan


PLAN = {
    "action": "INCREASE",
    "approved_amount_usd": 300,
    "planned_amount_usd": 300,
    "tranches": [
        {
            "sequence": 1,
            "amount_usd": 200,
            "price_low": 95,
            "price_high": 100,
            "reference_price": 98,
        },
        {
            "sequence": 2,
            "amount_usd": 100,
            "price_low": 85,
            "price_high": 90,
            "reference_price": 88,
        },
    ],
}


def bar(day, *, open=105, high=110, low=101, close=104, **extra):
    return {
        "timestamp": f"2026-01-{day:02d}T00:00:00Z",
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        **extra,
    }


class ExecutionReplayTests(unittest.TestCase):
    def test_wick_touch_does_not_fill(self):
        result = simulate_execution_plan(
            PLAN,
            [bar(2, open=105, high=108, low=99, close=104)],
            decision_as_of="2026-01-01T00:00:00Z",
        )
        self.assertEqual(result["status"], "NOT_FILLED")
        self.assertEqual(result["events"][0]["status"], "WICK_TOUCH_NOT_FILLED")

    def test_one_tranche_per_bar_and_partial_liquidity(self):
        result = simulate_execution_plan(
            PLAN,
            [
                bar(2, open=99, high=101, low=87, close=96, fill_fraction=0.5),
                bar(3, open=97, high=99, low=94, close=96),
                bar(4, open=89, high=91, low=86, close=88),
            ],
            decision_as_of="2026-01-01T00:00:00Z",
        )
        self.assertEqual(result["status"], "FILLED")
        self.assertEqual([item["amount_usd"] for item in result["events"]], [100, 100, 100])

    def test_invalidation_stops_remaining_fills(self):
        result = simulate_execution_plan(
            PLAN,
            [
                bar(2, open=99, high=101, low=96, close=98),
                bar(3, plan_valid=False),
                bar(4, open=88, high=90, low=86, close=87),
            ],
            decision_as_of="2026-01-01T00:00:00Z",
        )
        self.assertEqual(result["status"], "INVALIDATED")
        self.assertEqual(result["filled_amount_usd"], 200)

    def test_future_boundary_and_ohlc_are_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "after the decision"):
            simulate_execution_plan(PLAN, [bar(1)], decision_as_of="2026-01-01T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            simulate_execution_plan(
                PLAN,
                [bar(2, open=100, high=99, low=90, close=95)],
                decision_as_of="2026-01-01T00:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
