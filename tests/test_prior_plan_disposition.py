"""Resting-order disposition for prior executable plans at publication time."""

import tempfile
import unittest
from types import SimpleNamespace

from crypto_portfolio.models.decision import Decision
from crypto_portfolio.state.review import finalize_review, prior_plan_disposition


def tranche(sequence, amount, low, high, reference, quantity):
    return {
        "sequence": sequence,
        "allocation_fraction": 0.5 if sequence == 1 else 0.5,
        "amount_usd": amount,
        "price_low": low,
        "price_high": high,
        "reference_price": reference,
        "estimated_quantity": quantity,
        "rationale": "synthetic",
        "structural_sources": ["SWING_LOW"],
        "zone_quality": 80.0,
    }


def prior_btc_plan(tranches=None):
    return {
        "action": "INCREASE",
        "planned_amount_usd": 3000.0,
        "tranches": tranches or [
            tranche(1, 2000.0, 83211.52, 85061.25, 84136.385, 0.023914551),
            tranche(2, 1000.0, 76775.41, 79985.81, 78380.607, 0.012759509),
        ],
    }


def prior_record(plan=None, status="PENDING"):
    return {
        "decision_id": "20260923T0113Z-snapshot-review",
        "timestamp": "2026-09-23T01:13:00Z",
        "status": status,
        "based_on_snapshot_id": "snap-a",
        "execution_plans": {"BTC": plan if plan is not None else prior_btc_plan()},
    }


def snapshot(snapshot_id, timestamp, quantity, flow=0.0):
    return {
        "snapshot_id": snapshot_id,
        "timestamp": timestamp,
        "external_cash_flow": flow,
        "positions": [{"symbol": "BTC", "quantity": quantity, "value_usd": quantity * 85000.0}],
    }


def current_decision(plans, based_on="snap-b"):
    return SimpleNamespace(
        timestamp="2026-09-23T21:51:00Z",
        execution_plans=plans,
        based_on_snapshot_id=based_on,
    )


SNAPSHOTS = [
    snapshot("snap-a", "2026-09-23T01:01:00Z", 0.42817962),
    snapshot("snap-b", "2026-09-23T21:43:00Z", 0.45183987),
]


class PriorPlanDispositionTests(unittest.TestCase):
    def test_partial_fill_with_slot_rotation_orders_cancel(self):
        status_events = [{
            "decision_id": "20260923T0113Z-snapshot-review",
            "timestamp": "2026-09-23T21:45:00Z",
            "status": "CONFIRMED",
            "reason": "partial T1 fill",
        }]
        result = prior_plan_disposition(
            current_decision({"AAVE": {"action": "INCREASE", "planned_amount_usd": 4000.0, "tranches": [tranche(1, 4000.0, 100.0, 110.0, 105.0, 38.0)]}}),
            [prior_record()],
            snapshots=SNAPSHOTS,
            status_events=status_events,
        )
        btc = result["BTC"]
        self.assertEqual(btc["effective_status"], "CONFIRMED")
        self.assertEqual(btc["attribution"], "EXCHANGE_QUANTITY_DELTA")
        self.assertAlmostEqual(btc["quantity_delta"], 0.02366025, places=9)
        self.assertEqual(btc["order_instruction"], "CANCEL_RESTING")
        self.assertEqual([t["fill_state"] for t in btc["tranches"]], ["PARTIAL", "UNFILLED"])
        self.assertGreater(btc["tranches"][0]["fill_fraction"], 0.98)
        self.assertAlmostEqual(
            btc["remaining_planned_usd"],
            (1 - btc["tranches"][0]["fill_fraction"]) * 2000.0 + 1000.0,
            places=4,
        )

    def test_full_fill_leaves_nothing_resting(self):
        filled = [
            snapshot("snap-a", "2026-09-23T01:01:00Z", 1.0),
            snapshot("snap-b", "2026-09-23T21:43:00Z", 1.0 + 0.023914551 + 0.012759509),
        ]
        result = prior_plan_disposition(current_decision({}), [prior_record()], snapshots=filled)
        self.assertEqual(result["BTC"]["order_instruction"], "NOTHING_RESTING")
        self.assertEqual([t["fill_state"] for t in result["BTC"]["tranches"]], ["FULL", "FULL"])
        self.assertEqual(result["BTC"]["remaining_planned_usd"], 0.0)

    def test_not_executed_orders_cancel(self):
        events = [{
            "decision_id": "20260923T0113Z-snapshot-review",
            "timestamp": "2026-09-23T21:45:00Z",
            "status": "NOT_EXECUTED",
            "reason": "zones never reachable",
        }]
        unchanged = [
            snapshot("snap-a", "2026-09-23T01:01:00Z", 0.42817962),
            snapshot("snap-b", "2026-09-23T21:43:00Z", 0.42817974),
        ]
        result = prior_plan_disposition(
            current_decision({}), [prior_record(status="PENDING")],
            snapshots=unchanged, status_events=events,
        )
        btc = result["BTC"]
        self.assertEqual(btc["effective_status"], "NOT_EXECUTED")
        self.assertEqual(btc["order_instruction"], "CANCEL_RESTING")
        self.assertIn("NOT_EXECUTED", btc["instruction_reason"])
        self.assertEqual([t["fill_state"] for t in btc["tranches"]], ["UNFILLED", "UNFILLED"])

    def test_delta_contradicting_terminal_status_is_a_conflict(self):
        events = [{
            "decision_id": "20260923T0113Z-snapshot-review",
            "timestamp": "2026-09-23T21:45:00Z",
            "status": "NOT_EXECUTED",
            "reason": "quantity unchanged",
        }]
        result = prior_plan_disposition(
            current_decision({}), [prior_record(status="PENDING")],
            snapshots=SNAPSHOTS, status_events=events,
        )
        btc = result["BTC"]
        self.assertEqual(btc["attribution"], "STATUS_EVENT_CONFLICT")
        self.assertEqual(btc["order_instruction"], "CANCEL_RESTING")
        self.assertIn("disagree", btc["instruction_reason"])

    def test_replan_identical_zones_keeps_equivalent_orders(self):
        result = prior_plan_disposition(
            current_decision({"BTC": prior_btc_plan()}), [prior_record()], snapshots=SNAPSHOTS,
        )
        btc = result["BTC"]
        self.assertEqual(btc["order_instruction"], "KEEP_EQUIVALENT_ORDERS")

    def test_replan_new_zones_replaces_orders(self):
        moved = prior_btc_plan([
            tranche(1, 2000.0, 82000.0, 84000.0, 83000.0, 0.024096386),
            tranche(2, 1000.0, 75000.0, 78000.0, 76500.0, 0.013071895),
        ])
        result = prior_plan_disposition(
            current_decision({"BTC": moved}), [prior_record()], snapshots=SNAPSHOTS,
        )
        self.assertEqual(result["BTC"]["order_instruction"], "REPLACE_WITH_NEW_PLAN")

    def test_replan_with_different_action_replaces_orders(self):
        sell = {
            "action": "REDUCE",
            "planned_amount_usd": 3000.0,
            "tranches": [
                tranche(1, 2000.0, 83211.52, 85061.25, 84136.385, 0.023914551),
                tranche(2, 1000.0, 76775.41, 79985.81, 78380.607, 0.012759509),
            ],
        }
        result = prior_plan_disposition(
            current_decision({"BTC": sell}), [prior_record()], snapshots=SNAPSHOTS,
        )
        self.assertEqual(result["BTC"]["order_instruction"], "REPLACE_WITH_NEW_PLAN")

    def test_sell_ladder_attributes_negative_delta(self):
        sell_prior = {
            "action": "REDUCE",
            "planned_amount_usd": 3000.0,
            "tranches": [
                tranche(1, 2000.0, 90000.0, 92000.0, 91000.0, 0.021978022),
                tranche(2, 1000.0, 95000.0, 97000.0, 96000.0, 0.010416667),
            ],
        }
        sold = [
            snapshot("snap-a", "2026-09-23T01:01:00Z", 1.0),
            snapshot("snap-b", "2026-09-23T21:43:00Z", 1.0 - 0.021978022),
        ]
        result = prior_plan_disposition(current_decision({}), [prior_record(plan=sell_prior)], snapshots=sold)
        states = [t["fill_state"] for t in result["BTC"]["tranches"]]
        self.assertEqual(states, ["FULL", "UNFILLED"])
        self.assertEqual(result["BTC"]["order_instruction"], "CANCEL_RESTING")

    def test_external_flow_between_snapshots_blocks_attribution(self):
        flowed = [
            snapshot("snap-a", "2026-09-23T01:01:00Z", 0.42817962),
            snapshot("snap-mid", "2026-09-23T10:00:00Z", 0.5, flow=6000.0),
            snapshot("snap-b", "2026-09-23T21:43:00Z", 0.45183987),
        ]
        events = [{
            "decision_id": "20260923T0113Z-snapshot-review",
            "timestamp": "2026-09-23T21:45:00Z",
            "status": "CONFIRMED",
            "reason": "partial",
        }]
        result = prior_plan_disposition(
            current_decision({}), [prior_record()], snapshots=flowed, status_events=events,
        )
        btc = result["BTC"]
        self.assertEqual(btc["attribution"], "UNRESOLVED_EXTERNAL_FLOW")
        self.assertEqual([t["fill_state"] for t in btc["tranches"]], ["UNKNOWN", "UNKNOWN"])
        self.assertEqual(btc["order_instruction"], "CANCEL_RESTING")
        self.assertIn("verify fills against exchange history", btc["instruction_reason"])

    def test_missing_snapshots_fall_back_to_status_event(self):
        events = [{
            "decision_id": "20260923T0113Z-snapshot-review",
            "timestamp": "2026-09-23T21:45:00Z",
            "status": "CONFIRMED",
            "reason": "partial",
        }]
        result = prior_plan_disposition(current_decision({}), [prior_record()], status_events=events)
        btc = result["BTC"]
        self.assertEqual(btc["attribution"], "STATUS_EVENT_ONLY")
        self.assertEqual([t["fill_state"] for t in btc["tranches"]], ["UNKNOWN", "UNKNOWN"])

    def test_newest_plan_per_symbol_wins_and_wait_plans_are_skipped(self):
        older = prior_record()
        older["decision_id"] = "older"
        older["timestamp"] = "2026-09-22T01:13:00Z"
        history = [older, prior_record()]
        result = prior_plan_disposition(current_decision({}), history, snapshots=SNAPSHOTS)
        self.assertEqual(result["BTC"]["decision_id"], "20260923T0113Z-snapshot-review")
        wait_prior = prior_record(plan={"action": "WAIT", "planned_amount_usd": 0.0, "tranches": ()})
        self.assertEqual(prior_plan_disposition(current_decision({}), [wait_prior]), {})

    def test_empty_history_and_future_history(self):
        self.assertEqual(prior_plan_disposition(current_decision({}), []), {})
        with self.assertRaisesRegex(ValueError, "precede"):
            prior_plan_disposition(
                current_decision({}), [{"timestamp": "2026-09-24T00:00:00Z", "execution_plans": {}}],
            )

    def test_finalize_review_output_carries_disposition(self):
        decision = Decision('2026-01-01T01:00:00Z', 'NORMAL', {'BTC': .6, 'USDT': .4}, {'BTC': .6, 'USDT': .4},
                            based_on_snapshot_id='synthetic-snapshot',
                            nav_performance={'status': 'AVAILABLE', 'current_drawdown': -.01})
        snap = {'snapshot_id': 'synthetic-snapshot', 'timestamp': '2026-01-01T00:00:00Z',
                'positions': [{'symbol': 'BTC', 'value_usd': 600}, {'symbol': 'USDT', 'value_usd': 400}]}
        prior = {
            'decision_id': 'prior', 'timestamp': '2025-12-31T00:00:00Z', 'status': 'PENDING',
            'based_on_snapshot_id': 'old-snapshot',
            'execution_plans': {'BTC': prior_btc_plan()},
        }
        with tempfile.TemporaryDirectory() as directory:
            result = finalize_review(decision, snap, acquisition={'finalized': True}, artifact_root=directory,
                                     history=[prior])
            self.assertEqual(result['prior_plan_disposition']['BTC']['order_instruction'], 'CANCEL_RESTING')


if __name__ == '__main__':
    unittest.main()
