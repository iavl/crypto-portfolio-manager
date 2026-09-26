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

    def test_finalize_review_output_carries_resting_order_coverage(self):
        # Coverage computation itself is covered by the direct matcher and
        # disposition tests; here we pin the finalize boundary wiring: the
        # output exposes the key, supplied open orders flow through, and an
        # unfetched book yields an empty section without errors.
        decision = Decision('2026-01-01T01:00:00Z', 'NORMAL', {'BTC': .6, 'USDT': .4}, {'BTC': .6, 'USDT': .4},
                            based_on_snapshot_id='synthetic-snapshot',
                            nav_performance={'status': 'AVAILABLE', 'current_drawdown': -.01})
        snap = {'snapshot_id': 'synthetic-snapshot', 'timestamp': '2026-01-01T00:00:00Z',
                'positions': [{'symbol': 'BTC', 'value_usd': 600}, {'symbol': 'USDT', 'value_usd': 400}]}
        with tempfile.TemporaryDirectory() as directory:
            plain = finalize_review(decision, snap, acquisition={'finalized': True}, artifact_root=directory)
            self.assertEqual(plain['resting_order_coverage'], {})
            supplied = finalize_review(
                decision, snap, acquisition={'finalized': True}, artifact_root=directory,
                open_orders={'BTC': []},
            )
            self.assertEqual(supplied['resting_order_coverage'], {})


def fill(symbol="BTC", side="BUY", quantity=0.023914551, price=84400.0, fill_id="9001",
         executed_at="2026-09-23T15:00:00Z"):
    return {
        "fill_id": fill_id,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "notional": round(quantity * price, 8),
        "fee": 0.00002 * quantity,
        "fee_asset": symbol,
        "executed_at": executed_at,
        "source": "binance_api_account",
        "fetched_at": "2026-09-23T21:50:00Z",
    }


class FillsAttributionTests(unittest.TestCase):
    def test_records_match_zone_and_beat_snapshot_deltas(self):
        # A deposit sits between the snapshots (delta path would degrade), yet
        # trade records remain authoritative attribution evidence.
        flowed = list(SNAPSHOTS)
        flowed.insert(1, snapshot("snap-mid", "2026-09-23T10:00:00Z", 0.43, flow=6000.0))
        result = prior_plan_disposition(
            current_decision({}), [prior_record()],
            snapshots=flowed,
            fills={"BTC": [fill()]},
        )
        btc = result["BTC"]
        self.assertEqual(btc["attribution"], "EXCHANGE_TRADE_RECORDS")
        self.assertEqual([t["fill_state"] for t in btc["tranches"]], ["FULL", "UNFILLED"])
        self.assertAlmostEqual(btc["tranches"][0]["fill_quantity"], 0.023914551, places=9)
        self.assertAlmostEqual(btc["tranches"][0]["fill_notional_usd"], 0.023914551 * 84400.0, places=6)
        self.assertAlmostEqual(btc["remaining_planned_usd"], 1000.0, places=6)
        self.assertEqual(btc["order_instruction"], "CANCEL_RESTING")

    def test_fetched_zero_records_is_a_confident_unfilled(self):
        result = prior_plan_disposition(
            current_decision({}), [prior_record()], snapshots=SNAPSHOTS, fills={"BTC": []},
        )
        btc = result["BTC"]
        self.assertEqual(btc["attribution"], "EXCHANGE_TRADE_RECORDS")
        self.assertEqual([t["fill_state"] for t in btc["tranches"]], ["UNFILLED", "UNFILLED"])
        self.assertAlmostEqual(btc["remaining_planned_usd"], 3000.0, places=6)

    def test_symbol_without_records_falls_back_to_deltas(self):
        result = prior_plan_disposition(
            current_decision({}), [prior_record()], snapshots=SNAPSHOTS,
            fills={"ETH": []},
        )
        self.assertEqual(result["BTC"]["attribution"], "EXCHANGE_QUANTITY_DELTA")

    def test_out_of_zone_and_opposite_side_trades_surface_as_unmatched(self):
        records = [
            fill(fill_id="1", quantity=0.01, price=70000.0),            # below every zone
            fill(fill_id="2", side="SELL", quantity=0.005, price=84400.0),  # opposite side
            fill(fill_id="3", quantity=0.023914551, price=84400.0),     # matched T1
        ]
        result = prior_plan_disposition(
            current_decision({}), [prior_record()], snapshots=SNAPSHOTS, fills={"BTC": records},
        )
        btc = result["BTC"]
        unmatched = btc["unmatched_trades"]
        self.assertEqual(unmatched["out_of_zone"]["count"], 1)
        self.assertAlmostEqual(unmatched["out_of_zone"]["quantity"], 0.01, places=9)
        self.assertEqual(unmatched["opposite_side"]["count"], 1)
        self.assertIn("outside every planned zone", btc["instruction_reason"])
        self.assertIn("opposite side", btc["instruction_reason"])

    def test_overfill_caps_fraction_but_reports_actual_notional(self):
        result = prior_plan_disposition(
            current_decision({}), [prior_record()], snapshots=SNAPSHOTS,
            fills={"BTC": [fill(quantity=0.03, price=84400.0)]},
        )
        tranche = result["BTC"]["tranches"][0]
        self.assertEqual(tranche["fill_state"], "FULL")
        self.assertEqual(tranche["fill_fraction"], 1.0)
        self.assertAlmostEqual(tranche["fill_quantity"], 0.03, places=9)
        self.assertAlmostEqual(result["BTC"]["remaining_planned_usd"], 1000.0, places=6)

    def test_window_excludes_trades_outside_the_attribution_interval(self):
        result = prior_plan_disposition(
            current_decision({}), [prior_record()], snapshots=SNAPSHOTS,
            fills={"BTC": [fill(executed_at="2026-09-25T00:00:00Z"), fill(executed_at="2026-09-23T00:30:00Z")]},
        )
        # 09-23T00:30Z precedes the prior snapshot (01:01Z); 09-25 is after the
        # current snapshot (21:43Z) — neither is inside the window.
        self.assertEqual([t["fill_state"] for t in result["BTC"]["tranches"]], ["UNFILLED", "UNFILLED"])

    def test_not_executed_with_matched_records_is_a_conflict(self):
        events = [{
            "decision_id": "20260923T0113Z-snapshot-review",
            "timestamp": "2026-09-23T21:45:00Z",
            "status": "NOT_EXECUTED",
            "reason": "nothing filled",
        }]
        result = prior_plan_disposition(
            current_decision({}), [prior_record()], snapshots=SNAPSHOTS,
            fills={"BTC": [fill()]}, status_events=events,
        )
        self.assertEqual(result["BTC"]["attribution"], "STATUS_EVENT_CONFLICT")

    def test_confirmed_without_matching_records_keeps_a_coverage_caveat(self):
        events = [{
            "decision_id": "20260923T0113Z-snapshot-review",
            "timestamp": "2026-09-23T21:45:00Z",
            "status": "CONFIRMED",
            "reason": "partial",
        }]
        result = prior_plan_disposition(
            current_decision({}), [prior_record()], snapshots=SNAPSHOTS,
            fills={"BTC": []}, status_events=events,
        )
        btc = result["BTC"]
        self.assertEqual(btc["attribution"], "EXCHANGE_TRADE_RECORDS")
        self.assertIn("verify the trade-history fetch window", btc["instruction_reason"])

    def test_sell_plan_matches_sell_records(self):
        sell_prior = {
            "action": "REDUCE",
            "planned_amount_usd": 3000.0,
            "tranches": [
                tranche(1, 2000.0, 90000.0, 92000.0, 91000.0, 0.021978022),
                tranche(2, 1000.0, 95000.0, 97000.0, 96000.0, 0.010416667),
            ],
        }
        result = prior_plan_disposition(
            current_decision({}), [prior_record(plan=sell_prior)], snapshots=SNAPSHOTS,
            fills={"BTC": [fill(side="SELL", quantity=0.021978022, price=91000.0)]},
        )
        states = [t["fill_state"] for t in result["BTC"]["tranches"]]
        self.assertEqual(states, ["FULL", "UNFILLED"])


if __name__ == '__main__':
    unittest.main()
