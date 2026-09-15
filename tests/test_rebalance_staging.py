"""Strategic vs execution targets, staging, and relative deviation (phase 4/5).

Ordinary allocation corrections are staged toward the strategic target
(max_gap_close_fraction of the gap, capped by max_step_pp) instead of forcing
a healthy position to its long-run target in one review; hard exits bypass
staging.  Priority classification reads both absolute and relative deviation.
"""

import unittest

from crypto_portfolio.engine.rebalance import recommend_rebalance


def _action(result, symbol):
    return next(action for action in result.actions if action.symbol == symbol)


class StagedExecutionTests(unittest.TestCase):
    def test_ordinary_overweight_correction_is_staged(self):
        # AAVE-style: current 12.90%, strategic 12.5% would be HOLD; use a
        # wider gap so the staged step is visible: 18% -> 10% strategic is
        # an 8pp gap; staged step = min(8pp * 0.5, 4pp) = 4pp.
        result = recommend_rebalance(
            {"AAVE": 0.18, "USDT": 0.82},
            {"AAVE": 0.10, "USDT": 0.90},
            10000.0,
        )
        aave = _action(result, "AAVE")
        self.assertEqual(aave.action, "REDUCE")
        self.assertEqual(aave.action_reason, "ALLOCATION_OVERWEIGHT")
        self.assertTrue(aave.staging_applied)
        self.assertAlmostEqual(aave.strategic_target_weight, 0.10)
        self.assertAlmostEqual(aave.execution_target_weight, 0.14)
        self.assertAlmostEqual(aave.target_weight, 0.14)
        self.assertAlmostEqual(aave.amount_usd, 400.0)
        self.assertAlmostEqual(aave.remaining_gap_after_action, -0.04)

    def test_large_relative_gap_still_moves_at_most_max_step_pp(self):
        # 30pp gap: staged step = min(15pp, 4pp) = 4pp.
        result = recommend_rebalance(
            {"SOL": 0.30, "USDT": 0.70},
            {"SOL": 0.0, "USDT": 1.0},
            10000.0,
        )
        sol = _action(result, "SOL")
        # Strategic target 0 is the hard-exit score path: unstaged.
        self.assertEqual(sol.action, "EXIT")
        self.assertFalse(sol.staging_applied)
        self.assertEqual(sol.action_reason, "HARD_EXIT_SCORE")
        self.assertAlmostEqual(sol.amount_usd, 3000.0)

    def test_hard_exits_bypass_staging(self):
        # Thesis-broken exit executes fully in one review.
        result = recommend_rebalance(
            {"SOL": 0.20, "USDT": 0.80},
            {"SOL": 0.10, "USDT": 0.90},
            10000.0,
            thesis_broken=["SOL"],
        )
        sol = _action(result, "SOL")
        self.assertEqual(sol.action, "EXIT")
        self.assertEqual(sol.action_reason, "THESIS_BROKEN")
        self.assertFalse(sol.staging_applied)
        self.assertAlmostEqual(sol.amount_usd, 2000.0)

    def test_caller_hard_reason_bypasses_staging_and_is_validated(self):
        result = recommend_rebalance(
            {"SOL": 0.20, "USDT": 0.80},
            {"SOL": 0.05, "USDT": 0.95},
            10000.0,
            hard_action_reasons={"SOL": "EVENT_RISK"},
        )
        sol = _action(result, "SOL")
        self.assertEqual(sol.action, "REDUCE")
        self.assertEqual(sol.action_reason, "EVENT_RISK")
        self.assertFalse(sol.staging_applied)
        self.assertAlmostEqual(sol.amount_usd, 1500.0)
        # A hard reason cannot attach to an increase.
        with self.assertRaisesRegex(ValueError, "REDUCE/EXIT"):
            recommend_rebalance(
                {"SOL": 0.05, "USDT": 0.95},
                {"SOL": 0.20, "USDT": 0.80},
                10000.0,
                hard_action_reasons={"SOL": "EVENT_RISK"},
            )
        with self.assertRaisesRegex(ValueError, "hard_action_reasons"):
            recommend_rebalance(
                {"SOL": 0.20, "USDT": 0.80},
                {"SOL": 0.05, "USDT": 0.95},
                10000.0,
                hard_action_reasons={"SOL": "THESIS_BROKEN"},
            )

    def test_staged_reduction_moves_monotonically_and_parks_in_the_watch_band(self):
        # Same evidence re-reviewed: each executable review closes half the
        # remaining gap (bounded by 4pp), never overshoots the strategic
        # target, and once the remaining deviation is inside the watch band
        # the position parks there instead of trading to exactness.
        current = 0.20
        previous_gap = float("inf")
        parked = False
        for _ in range(8):
            result = recommend_rebalance(
                {"AAVE": current, "USDT": round(1.0 - current, 10)},
                {"AAVE": 0.05, "USDT": 0.95},
                10000.0,
            )
            aave = _action(result, "AAVE")
            if aave.action == "REDUCE":
                self.assertFalse(parked, "a parked position must not trade again")
                self.assertGreaterEqual(aave.execution_target_weight, 0.05 - 1e-9)
                self.assertLessEqual(abs(current - aave.execution_target_weight), 0.04 + 1e-9)
                self.assertLess(abs(aave.remaining_gap_after_action), previous_gap + 1e-12)
                previous_gap = abs(aave.remaining_gap_after_action)
            else:
                self.assertEqual(aave.action, "WAIT")
                parked = True
            current = aave.execution_target_weight
        self.assertTrue(parked)
        self.assertLess(current, 0.09)

    def test_regime_derisk_labels_defensive_reductions(self):
        result = recommend_rebalance(
            {"SOL": 0.20, "USDT": 0.80},
            {"SOL": 0.10, "USDT": 0.90},
            10000.0,
            regime="DEFENSIVE",
        )
        sol = _action(result, "SOL")
        self.assertEqual(sol.action_reason, "REGIME_DERISK")
        # Regime de-risking is an ordinary correction: still staged.
        self.assertTrue(sol.staging_applied)


class RelativeDeviationTests(unittest.TestCase):
    def test_btc_sized_absolute_deviation(self):
        # 50% target, 8.1pp absolute deviation, relative only 16%: HIGH via
        # the absolute band.
        result = recommend_rebalance(
            {"BTC": 0.419, "USDT": 0.581},
            {"BTC": 0.50, "USDT": 0.50},
            10000.0,
        )
        btc = _action(result, "BTC")
        self.assertEqual(btc.action, "INCREASE")
        self.assertEqual(btc.priority, "HIGH")

    def test_satellite_small_target_relative_deviation(self):
        # 5% target with 8.1pp deviation: absolute already HIGH; relative
        # 162% reinforces it.
        result = recommend_rebalance(
            {"SOL": 0.131, "USDT": 0.869},
            {"SOL": 0.05, "USDT": 0.95},
            10000.0,
        )
        sol = _action(result, "SOL")
        self.assertEqual(sol.action, "REDUCE")
        self.assertEqual(sol.priority, "HIGH")

    def test_dust_target_uses_the_relative_floor(self):
        # 0.1% target, current 0.7%: absolute deviation 0.6pp is below the
        # hold band, but relative deviation (floor 2%) is 30% < 50% watch:
        # HOLD.  A doubled position (1.5%) reaches 70% relative -> WATCH,
        # never HIGH from the tiny denominator alone.
        hold = recommend_rebalance(
            {"SOL": 0.007, "USDT": 0.993},
            {"SOL": 0.001, "USDT": 0.999},
            10000.0,
        )
        self.assertEqual(_action(hold, "SOL").action, "HOLD")
        watch = recommend_rebalance(
            {"SOL": 0.015, "USDT": 0.985},
            {"SOL": 0.001, "USDT": 0.999},
            10000.0,
        )
        self.assertEqual(_action(watch, "SOL").action, "WAIT")
        self.assertEqual(_action(watch, "SOL").priority, "WATCH")

    def test_zero_target_exit_reads_absolute_bands(self):
        # 3% held against a zero target: 3pp absolute sits in the old watch
        # band, but the relative deviation (150% of the 2% floor) makes it
        # a high-priority exit.
        result = recommend_rebalance(
            {"SOL": 0.03, "USDT": 0.97},
            {"SOL": 0.0, "USDT": 1.0},
            10000.0,
        )
        sol = _action(result, "SOL")
        self.assertEqual(sol.action, "EXIT")
        self.assertEqual(sol.priority, "HIGH")

    def test_hold_requires_both_absolute_and_relative_quiet(self):
        # 1.5pp absolute is below hold, but relative is 60% of a 2.5%
        # target: not HOLD, WATCH instead.
        result = recommend_rebalance(
            {"SOL": 0.04, "USDT": 0.96},
            {"SOL": 0.025, "USDT": 0.975},
            10000.0,
        )
        sol = _action(result, "SOL")
        self.assertEqual(sol.action, "WAIT")


if __name__ == "__main__":
    unittest.main()


class PacketExplainabilityTests(unittest.TestCase):
    """Decision summaries expose strategic/execution/deviation fields."""

    def _packet(self):
        from crypto_portfolio.engine.decision_packet import build_decision_review_packet
        from crypto_portfolio.engine.rebalance import recommend_rebalance

        current = {"AAVE": 0.18, "BTC": 0.55, "USDT": 0.27}
        target = {"AAVE": 0.10, "BTC": 0.55, "USDT": 0.35}
        result = recommend_rebalance(current, target, 10000.0)
        return build_decision_review_packet(
            review_type="SNAPSHOT_REVIEW",
            market_regime="NORMAL",
            current_weights=current,
            target_weights=target,
            assessments={"AAVE": {"weighted_score": 74, "confidence": "HIGH"}},
            actions=[action.as_dict() for action in result.actions],
        )

    def test_asset_summary_carries_strategic_execution_and_deviation_fields(self):
        packet = self._packet()
        aave = next(item for item in packet.assets if item.symbol == "AAVE")
        self.assertAlmostEqual(aave.strategic_target_weight, 0.10)
        self.assertAlmostEqual(aave.execution_target_weight, 0.14)
        self.assertEqual(aave.action_reason, "ALLOCATION_OVERWEIGHT")
        self.assertTrue(aave.staging_applied)
        self.assertAlmostEqual(aave.deviation_pp, 8.0)
        self.assertAlmostEqual(aave.relative_deviation, 0.8)
        dumped = aave.as_dict()
        for key in ("strategic_target_weight", "execution_target_weight", "action_reason",
                    "staging_applied", "deviation_pp", "relative_deviation"):
            self.assertIn(key, dumped)

    def test_allocation_reports_the_stable_target_triple(self):
        from crypto_portfolio.engine.allocation import build_target_allocation

        # BTC capped at 50%, ETH water-fills to its 34% sleeve cap, and the
        # leftover 1pp is constraint residual cash, not strategic stable.
        result = build_target_allocation(
            assessments={
                "BTC": {"weighted_score": 65.52, "confidence": "HIGH"},
                "ETH": {"weighted_score": 83.78, "confidence": "HIGH", "relative_strength_vs_btc": 70},
            },
            current_weights={"BTC": 0.40, "ETH": 0.30, "USDT": 0.30},
        )
        self.assertAlmostEqual(result.strategic_stable_target, 0.15)
        self.assertAlmostEqual(result.constraint_residual_cash, 0.01, places=6)
        self.assertAlmostEqual(result.effective_stable_target, 0.16, places=6)
        stable = result.as_dict()["stable_targets"]
        self.assertEqual(
            sorted(stable),
            ["constraint_residual_cash", "effective_stable_target", "strategic_stable_target"],
        )
