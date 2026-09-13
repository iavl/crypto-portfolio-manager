"""Phase-7 offline evaluation harness validated on synthetic fixtures."""

import unittest
from datetime import date, timedelta

from crypto_portfolio.engine.evaluation import (
    EvaluationPeriod,
    buy_and_hold_path,
    sequential_splits,
    simulate_periods,
)


def _period(index: int, weights: dict, returns: dict) -> EvaluationPeriod:
    day = date(2026, 1, 1) + timedelta(days=7 * index)
    return EvaluationPeriod(day, weights, returns)


class SimulatePeriodsTests(unittest.TestCase):
    def test_costs_and_turnover_are_charged_once_per_period(self):
        periods = [
            _period(0, {"BTC": 0.5, "USDT": 0.5}, {"BTC": 0.10, "USDT": 0.0}),
            _period(1, {"BTC": 0.4, "ETH": 0.2, "USDT": 0.4}, {"BTC": -0.10, "ETH": 0.20, "USDT": 0.0}),
        ]
        free = simulate_periods(periods)
        costly = simulate_periods(periods, fee_bps=50, slippage_bps=25)
        # Period 0 deploys from cash, so its turnover is the full 1.0 and
        # costs apply to the initial buy-in as well.
        self.assertAlmostEqual(free["period_detail"][0]["net_return"], 0.05)
        self.assertAlmostEqual(free["period_detail"][0]["turnover"], 1.0)
        self.assertAlmostEqual(costly["period_detail"][0]["cost"], 0.0075)
        # Period 1 turnover: |0.4-0.5| + 0.2 + |0.4-0.5| = 0.4 -> cost 0.4*0.75%.
        self.assertAlmostEqual(free["period_detail"][1]["turnover"], 0.4)
        self.assertAlmostEqual(costly["period_detail"][1]["cost"], 0.4 * 0.0075)
        self.assertAlmostEqual(
            costly["total_return"], (1 + 0.05 - 0.0075) * (1 - 0.003) - 1, places=9
        )

    def test_drawdown_and_recovery_indices(self):
        periods = [
            _period(0, {"BTC": 1.0}, {"BTC": 0.10}),
            _period(1, {"BTC": 1.0}, {"BTC": -0.50}),
            _period(2, {"BTC": 1.0}, {"BTC": 0.10}),
            _period(3, {"BTC": 1.0}, {"BTC": 1.04}),
        ]
        result = simulate_periods(periods)
        # NAV: 1.10 -> 0.55 -> 0.605 -> 1.2252: trough at period 1, recovery
        # at period 3 when the pre-trough peak (1.10) is regained.
        self.assertAlmostEqual(result["max_drawdown"], 0.55 / 1.10 - 1.0)
        self.assertEqual(result["trough_period_index"], 2)
        self.assertEqual(result["recovery_period_index"], 4)

    def test_turnover_is_measured_against_drifted_holdings(self):
        # The review scenario: a 50/50 BTC/stable target held through a BTC
        # doubling. The drifted portfolio is ~67/33, so restoring the target
        # trades even though the target weights never changed.
        periods = [
            _period(0, {"BTC": 0.5, "USDT": 0.5}, {"BTC": 1.0, "USDT": 0.0}),
            _period(1, {"BTC": 0.5, "USDT": 0.5}, {"BTC": 0.0, "USDT": 0.0}),
        ]
        result = simulate_periods(periods, fee_bps=50)
        self.assertAlmostEqual(result["period_detail"][1]["turnover"], 1.0 / 3.0, places=9)
        self.assertAlmostEqual(result["period_detail"][1]["cost"], (1.0 / 3.0) * 0.005)

    def test_symbols_are_normalized_and_case_collisions_rejected(self):
        period = _period(0, {"BTC": 1.0}, {"btc": 0.20})
        self.assertAlmostEqual(simulate_periods([period])["total_return"], 0.20)
        with self.assertRaisesRegex(ValueError, "duplicate symbol"):
            _period(0, {"BTC": 0.5, "btc": 0.5}, {"BTC": 0.0})
        with self.assertRaisesRegex(ValueError, "missing realized return"):
            _period(0, {"BTC": 1.0}, {"ETH": 0.0})

    def test_zero_turnover_and_cash_weight_visibility(self):
        periods = [
            _period(0, {"BTC": 0.6, "USDT": 0.4}, {"BTC": 0.0, "USDT": 0.0}),
            _period(1, {"BTC": 0.6, "USDT": 0.4}, {"BTC": 0.02, "USDT": 0.0}),
        ]
        result = simulate_periods(periods)
        # Period 0 buys in from cash (turnover 1.0); period 1 holds.
        self.assertEqual(result["zero_turnover_periods"], 1)
        self.assertAlmostEqual(result["average_cash_weight"], 0.4)

    def test_missing_weighted_return_is_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "missing realized return"):
            _period(0, {"BTC": 1.0}, {"ETH": 0.0})
        with self.assertRaisesRegex(ValueError, "below -100%"):
            _period(0, {"BTC": 1.0}, {"BTC": -1.5})
        with self.assertRaisesRegex(ValueError, "must sum to 1"):
            _period(0, {"BTC": 0.9}, {"BTC": 0.0})


class BuyAndHoldBenchmarkTests(unittest.TestCase):
    def test_sleeves_compound_without_rebalancing(self):
        path = buy_and_hold_path(
            [{"BTC": 1.0, "ETH": 0.0}, {"BTC": 0.0, "ETH": 1.0}],
            weights={"BTC": 0.7, "ETH": 0.3},
        )
        # BTC sleeve: 0.7*2 = 1.4; ETH sleeve: 0.3*2 = 0.6 -> 2.0 total.
        self.assertAlmostEqual(path["final_nav"], 2.0)
        self.assertAlmostEqual(path["max_drawdown"], 0.0)

    def test_negative_benchmark_weights_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite fraction >= 0"):
            buy_and_hold_path([{"BTC": 0.0, "ETH": 0.0}], weights={"BTC": 2, "ETH": -1})

    def test_external_flows_allocate_at_the_configured_ratio(self):
        # A +0.5 flow at the start of period 2 is split 70/30, not by drifted
        # weights; the sleeves then compound on the flowed base.
        path = buy_and_hold_path(
            [{"BTC": 0.0, "ETH": 0.0}, {"BTC": 0.0, "ETH": 0.0}],
            weights={"BTC": 0.7, "ETH": 0.3},
            external_flows=[0.0, 0.5],
        )
        self.assertAlmostEqual(path["final_nav"], 1.5)
        with self.assertRaisesRegex(ValueError, "align with asset_returns"):
            buy_and_hold_path([{"BTC": 0.0}], weights={"BTC": 1.0}, external_flows=[0.0, 0.0])

    def test_missing_benchmark_return_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing return for BTC"):
            buy_and_hold_path([{"ETH": 0.0}], weights={"BTC": 0.7, "ETH": 0.3})


class SequentialSplitTests(unittest.TestCase):
    def test_blocks_are_contiguous_and_ordered(self):
        dates = [date(2026, 1, 1) + timedelta(days=7 * index) for index in range(10)]
        splits = sequential_splits(dates)
        self.assertEqual(
            [*splits["train"], *splits["validation"], *splits["holdout"]],
            sorted(dates),
        )
        self.assertLess(max(splits["train"]), min(splits["validation"]))
        self.assertLess(max(splits["validation"]), min(splits["holdout"]))
        self.assertEqual(len(splits["holdout"]), 2)

    def test_label_horizon_purges_boundary_samples(self):
        dates = [date(2026, 1, 1) + timedelta(days=30 * index) for index in range(12)]
        splits = sequential_splits(dates, train_fraction=0.5, validation_fraction=0.25, label_horizon_days=90)
        # No training or validation sample's 90-day forward window crosses
        # into the following block.
        self.assertLessEqual(max(splits["train"]) + timedelta(days=90), min(splits["validation"]))
        self.assertLessEqual(max(splits["validation"]) + timedelta(days=90), min(splits["holdout"]))
        self.assertLess(len(splits["train"]), 6)

    def test_no_lookahead_overlap_between_blocks(self):
        dates = [date(2026, 1, 1) + timedelta(days=30 * index) for index in range(12)]
        splits = sequential_splits(dates, train_fraction=0.5, validation_fraction=0.25)
        # A 90-day forward label starting on the last train date must not
        # reach into the holdout block.
        last_train_label_end = max(splits["train"]) + timedelta(days=90)
        self.assertLessEqual(last_train_label_end, max(splits["validation"]))


if __name__ == "__main__":
    unittest.main()
