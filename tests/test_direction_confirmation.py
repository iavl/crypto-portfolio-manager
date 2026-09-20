"""Regression tests for direction-flip confirmation and trend volume
confirmation.

These guards exist because a satellite direction reversed from REDUCE to
INCREASE within three days (2026-09-15..17): one weak daily volume reading
plus a marginal MA20 cross swung the trend factor 22 points for a single
review.  Confirmation by distinct daily closes removes single-review noise
from executable direction changes while hard risk paths stay immediate.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from crypto_portfolio.engine.factors.trend import calculate_trend_factor
from crypto_portfolio.engine.rebalance import (
    direction_history_from_decisions,
    recommend_rebalance,
    reconcile_trade_dollars,
)
from crypto_portfolio.models.policy import (
    PolicyError,
    policy_from_mapping,
    resolve_policy,
)


def _trend_snapshot(**overrides):
    base = dict(
        symbol="ETH", current_spot_price=100, ma20=100, ma50=100, ma100=100, ma200=100,
        return_30d=0, return_90d=0, return_180d=0, support_zones=(),
        volume_state="WEAK", atr14=None, atr_percent=None, realized_vol_30d=None,
        realized_vol_90d=None, relative_volume=0.5, trend_state="NEUTRAL",
        ohlcv_hash=None, volume_profile_hash=None, volume_profile_poc=None,
        volume_profile_val=None, volume_profile_vah=None, market_data_fresh=True,
        data_quality_flags=(), data_confidence="HIGH", current_drawdown=0.0,
        spot_source="binance", source="binance", spot_observed_at=None,
        spot_fetched_at=None, timeframe="1D", ohlcv_metadata={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TrendVolumeConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.policy = resolve_policy()

    def _score(self, previous=None, **overrides):
        snapshot = _trend_snapshot(**overrides)
        with patch("crypto_portfolio.engine.factors.trend._snapshot", return_value=snapshot):
            return calculate_trend_factor({}, policy=self.policy, previous_relative_volumes=previous)

    def test_single_weak_reading_applies_no_deduction(self):
        result = self._score(previous=None)
        self.assertEqual(result.contributions["volume"], 0.0)
        self.assertIn("volume weakness is unconfirmed (1 of 2", " ".join(result.reasons))

    def test_persistent_weak_closes_apply_the_deduction(self):
        result = self._score(previous=[0.5, 0.4])
        self.assertEqual(result.contributions["volume"], -5.0)
        single = self._score(previous=None)
        self.assertEqual(result.score + 5.0, single.score)

    def test_broken_weak_streak_applies_no_deduction(self):
        result = self._score(previous=[1.5, 0.5])
        self.assertEqual(result.contributions["volume"], 0.0)

    def test_supportive_volume_stays_immediate(self):
        result = self._score(volume_state="SUPPORTIVE", relative_volume=1.5, previous=None)
        self.assertEqual(result.contributions["volume"], 5.0)

    def test_policy_closes_of_one_restores_immediate_deduction(self):
        mapping = resolve_policy().as_dict()
        trend = mapping["factor_rules"]["trend"]
        trend["volume_weakness_confirmation_closes"] = 1
        policy = policy_from_mapping(mapping)
        snapshot = _trend_snapshot()
        with patch("crypto_portfolio.engine.factors.trend._snapshot", return_value=snapshot):
            result = calculate_trend_factor({}, policy=policy, previous_relative_volumes=None)
        self.assertEqual(result.contributions["volume"], -5.0)


class DirectionHistoryTests(unittest.TestCase):
    def test_history_is_most_recent_first_per_symbol(self):
        decisions = [
            {"timestamp": "2026-09-16T11:05:00Z", "actions": [{"symbol": "AAVE", "action": "WAIT"}]},
            {"timestamp": "2026-09-17T22:59:00Z", "actions": [{"symbol": "AAVE", "action": "INCREASE"}]},
        ]
        history = direction_history_from_decisions(decisions)
        self.assertEqual(
            history["AAVE"],
            [{"date": "2026-09-17", "action": "INCREASE"}, {"date": "2026-09-16", "action": "WAIT"}],
        )

    def test_same_day_duplicates_collapse_in_streak_counting(self):
        # Two decisions on one day cannot manufacture a second daily close:
        # under a 3-close requirement the repeated same-day signal still
        # shows only one confirmed close, so the flip waits.
        mapping = resolve_policy().as_dict()
        mapping["rebalance"]["direction_flip_confirmation"]["required_closes"] = 3
        policy = policy_from_mapping(mapping)
        result = recommend_rebalance(
            {"AAVE": 0.11, "USDT": 0.89},
            {"AAVE": 0.16, "USDT": 0.84},
            10000.0,
            policy=policy,
            direction_history={"AAVE": [
                {"date": "2026-09-18", "action": "REDUCE"},
                {"date": "2026-09-18", "action": "REDUCE"},
            ]},
        )
        aave = next(a for a in result.actions if a.symbol == "AAVE")
        self.assertEqual((aave.action, aave.action_reason), ("WAIT", "DIRECTION_CONFIRMATION"))
        self.assertIn("1 of 3 daily closes", aave.rationale)

    def test_invalid_entries_are_rejected(self):
        for bad in (
            [{"date": "2026-9-18", "action": "HOLD"}],
            [{"date": "2026-09-18", "action": "BUY"}],
            [{"date": "2026-09-18"}],
            [{"date": "2026-09-18", "action": "HOLD", "note": "x"}],
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    recommend_rebalance(
                        {"AAVE": 0.11, "USDT": 0.89},
                        {"AAVE": 0.16, "USDT": 0.84},
                        10000.0,
                        direction_history={"AAVE": bad},
                    )


class DirectionFlipConfirmationTests(unittest.TestCase):
    # AAVE current 11%, strategic 16%: deviation 5pp is 1pp beyond the 4pp
    # watch threshold, so the overshoot is marginal and confirmation applies.
    CURRENT = {"AAVE": 0.11, "USDT": 0.89}
    TARGET = {"AAVE": 0.16, "USDT": 0.84}

    def _run(self, history=None, **kwargs):
        return recommend_rebalance(
            self.CURRENT, self.TARGET, 10000.0,
            direction_history=history, **kwargs,
        )

    def test_marginal_flip_waits_for_confirmation(self):
        result = self._run({"AAVE": [{"date": "2026-09-17", "action": "REDUCE"}]})
        aave = next(a for a in result.actions if a.symbol == "AAVE")
        self.assertEqual((aave.action, aave.action_reason), ("WAIT", "DIRECTION_CONFIRMATION"))
        self.assertEqual(aave.amount_usd, 0.0)
        self.assertIn("direction flip from REDUCE to INCREASE", aave.rationale)
        self.assertIn("1 of 2 daily closes", aave.rationale)

    def test_wait_preserves_candidate_direction_for_next_daily_close(self):
        first = self._run({"AAVE": [{"date": "2026-09-16", "action": "REDUCE"}]})
        pending = next(a for a in first.actions if a.symbol == "AAVE")
        self.assertEqual(pending.action, "WAIT")
        self.assertEqual(pending.candidate_action, "INCREASE")
        history = direction_history_from_decisions([
            {"timestamp": "2026-09-17T22:00:00Z", "actions": [pending.as_dict()]},
            {"timestamp": "2026-09-16T22:00:00Z", "actions": [{"symbol": "AAVE", "action": "REDUCE"}]},
        ])
        second = self._run(history)
        aave = next(a for a in second.actions if a.symbol == "AAVE")
        self.assertEqual(aave.action, "INCREASE")
        self.assertGreater(aave.amount_usd, 0.0)

    def test_confirmed_flip_executes(self):
        result = self._run({"AAVE": [
            {"date": "2026-09-18", "action": "INCREASE"},
            {"date": "2026-09-17", "action": "REDUCE"},
        ]})
        aave = next(a for a in result.actions if a.symbol == "AAVE")
        self.assertEqual(aave.action, "INCREASE")
        self.assertGreater(aave.amount_usd, 0.0)

    def test_large_overshoot_executes_immediately(self):
        # 12pp deviation is 8pp beyond the threshold: immediate by magnitude.
        result = recommend_rebalance(
            {"AAVE": 0.04, "USDT": 0.96},
            {"AAVE": 0.16, "USDT": 0.84},
            10000.0,
            direction_history={"AAVE": [{"date": "2026-09-17", "action": "REDUCE"}]},
        )
        aave = next(a for a in result.actions if a.symbol == "AAVE")
        self.assertEqual(aave.action, "INCREASE")

    def test_hard_reasons_bypass_confirmation(self):
        # Overweight position with a standing INCREASE direction: EVENT_RISK
        # forces the risk reduction immediately instead of waiting for the
        # direction flip to confirm.
        result = recommend_rebalance(
            {"AAVE": 0.20, "USDT": 0.80},
            {"AAVE": 0.16, "USDT": 0.84},
            10000.0,
            hard_action_reasons={"AAVE": "EVENT_RISK"},
            direction_history={"AAVE": [{"date": "2026-09-17", "action": "INCREASE"}]},
        )
        aave = next(a for a in result.actions if a.symbol == "AAVE")
        self.assertEqual((aave.action, aave.action_reason), ("REDUCE", "EVENT_RISK"))
        self.assertGreater(aave.amount_usd, 0.0)

    def test_thesis_broken_exit_bypasses_confirmation(self):
        result = recommend_rebalance(
            {"AAVE": 0.11, "USDT": 0.89},
            {"AAVE": 0.16, "USDT": 0.84},
            10000.0,
            thesis_broken=["AAVE"],
            direction_history={"AAVE": [{"date": "2026-09-17", "action": "INCREASE"}]},
        )
        aave = next(a for a in result.actions if a.symbol == "AAVE")
        self.assertEqual((aave.action, aave.action_reason), ("EXIT", "THESIS_BROKEN"))

    def test_same_direction_continues_without_gating(self):
        result = self._run({"AAVE": [{"date": "2026-09-17", "action": "INCREASE"}]})
        aave = next(a for a in result.actions if a.symbol == "AAVE")
        self.assertEqual(aave.action, "INCREASE")

    def test_reduce_to_exit_stays_risk_reducing(self):
        # REDUCE -> EXIT does not reverse risk direction; no confirmation.
        result = recommend_rebalance(
            {"AAVE": 0.11, "USDT": 0.89},
            {"AAVE": 0.0, "USDT": 1.0},
            10000.0,
            direction_history={"AAVE": [{"date": "2026-09-17", "action": "REDUCE"}]},
        )
        aave = next(a for a in result.actions if a.symbol == "AAVE")
        self.assertEqual(aave.action, "EXIT")

    def test_stable_sleeve_is_never_direction_gated(self):
        result = self._run({"USDT": [{"date": "2026-09-17", "action": "INCREASE"}]})
        usdt = next(a for a in result.actions if a.symbol == "USDT")
        self.assertEqual(usdt.action, "REDUCE")
        self.assertNotEqual(usdt.action_reason, "DIRECTION_CONFIRMATION")

    def test_disabled_gate_preserves_legacy_behavior(self):
        mapping = resolve_policy().as_dict()
        mapping["rebalance"]["direction_flip_confirmation"]["enabled"] = False
        policy = policy_from_mapping(mapping)
        result = recommend_rebalance(
            self.CURRENT, self.TARGET, 10000.0, policy=policy,
            direction_history={"AAVE": [{"date": "2026-09-17", "action": "REDUCE"}]},
        )
        aave = next(a for a in result.actions if a.symbol == "AAVE")
        self.assertEqual(aave.action, "INCREASE")


class StableFundingCouplingTests(unittest.TestCase):
    def test_stable_sells_never_exceed_the_buys_they_fund(self):
        # The risk buy is flip-held; without the cap the staged sleeve step
        # would sell stables with no executable destination.
        result = recommend_rebalance(
            {"AAVE": 0.11, "USDT": 0.89},
            {"AAVE": 0.16, "USDT": 0.84},
            10000.0,
            direction_history={"AAVE": [{"date": "2026-09-17", "action": "REDUCE"}]},
        )
        reconciliation = reconcile_trade_dollars(result.actions, 0.0, ("USDT",))
        stable_sells = sum(
            a.amount_usd for a in result.actions
            if a.action == "REDUCE" and a.symbol == "USDT"
        )
        buys = sum(a.amount_usd for a in result.actions if a.action == "INCREASE")
        self.assertEqual(stable_sells, buys)
        self.assertEqual(reconciliation["planned_buys"], buys)

    def test_stable_leg_never_exceeds_its_own_gap(self):
        # Sleeve-proportional steps could previously exceed a small symbol's
        # own overweight and crash sizing attribution (>1 gap close).
        targets = {"BTC": 0.4388, "ETH": 0.1913, "AAVE": 0.2199,
                   "U": 0.041, "USD1": 0.0077, "USDC": 0.0015, "USDT": 0.0998}
        current = {"BTC": 0.4388, "ETH": 0.1913, "AAVE": 0.1845,
                   "U": 0.0917, "USD1": 0.0172, "USDC": 0.0034}
        current["USDT"] = round(1.0 - sum(current.values()), 6)
        result = recommend_rebalance(current, targets, 77851.95, new_cash_available=10000.0)
        for action in result.actions:
            if action.action == "REDUCE":
                self.assertLessEqual(action.amount_usd, action.current_weight * 77851.95 + 1e-6)
        buys = sum(a.amount_usd for a in result.actions if a.action == "INCREASE")
        stable_sells = sum(
            a.amount_usd for a in result.actions
            if a.action == "REDUCE" and a.symbol in {"U", "USD1", "USDC", "USDT"}
        )
        self.assertAlmostEqual(buys, stable_sells, places=2)


class PolicyValidationTests(unittest.TestCase):
    def test_defaults_parse_with_confirmation_fields(self):
        policy = resolve_policy()
        self.assertEqual(policy.factor_rules["trend"]["volume_weakness_confirmation_closes"], 2)
        flip = policy.rebalance["direction_flip_confirmation"]
        self.assertTrue(flip["enabled"])
        self.assertEqual(flip["required_closes"], 2)
        self.assertEqual(flip["immediate_overshoot_pp"], 2.0)
        self.assertIn("THESIS_BROKEN", flip["bypass_reasons"])

    def test_invalid_confirmation_values_are_rejected(self):
        base = resolve_policy().as_dict()
        cases = []
        trend_zero = json.loads(json.dumps(base))
        trend_zero["factor_rules"]["trend"]["volume_weakness_confirmation_closes"] = 0
        cases.append(trend_zero)
        trend_bool = json.loads(json.dumps(base))
        trend_bool["factor_rules"]["trend"]["volume_weakness_confirmation_closes"] = True
        cases.append(trend_bool)
        flip_zero = json.loads(json.dumps(base))
        flip_zero["rebalance"]["direction_flip_confirmation"]["required_closes"] = 0
        cases.append(flip_zero)
        flip_reason = json.loads(json.dumps(base))
        flip_reason["rebalance"]["direction_flip_confirmation"]["bypass_reasons"] = ["NOT_A_REASON"]
        cases.append(flip_reason)
        flip_missing = json.loads(json.dumps(base))
        del flip_missing["rebalance"]["direction_flip_confirmation"]["immediate_overshoot_pp"]
        cases.append(flip_missing)
        for mapping in cases:
            with self.subTest(mapping=mapping["rebalance"].get("direction_flip_confirmation")):
                with self.assertRaises(PolicyError):
                    policy_from_mapping(mapping)


if __name__ == "__main__":
    unittest.main()
