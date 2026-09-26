"""Resting-order matching against execution-plan zones and its review wiring."""

import unittest
from types import SimpleNamespace

from crypto_portfolio.engine.resting_orders import match_plan_resting_orders
from crypto_portfolio.models.policy import resolve_policy
from crypto_portfolio.state.review import prior_plan_disposition, resting_order_coverage


MATCH = {"tolerance": 0.02, "full_fraction": 0.95, "partial_fraction": 0.05}


def order(order_id, price, quantity, *, side="BUY", order_type="LIMIT", executed=0.0):
    return {
        "order_id": order_id,
        "symbol": "AAVE",
        "side": side,
        "order_type": order_type,
        "price": price,
        "orig_quantity": quantity,
        "executed_quantity": executed,
        "status": "NEW",
        "time_in_force": "GTC",
        "created_at": "2026-09-25T02:15:12Z",
    }


def plan(tranches, action="INCREASE"):
    return {
        "action": action,
        "planned_amount_usd": sum(amount for _, amount, _, _ in tranches),
        "tranches": [
            {
                "sequence": sequence,
                "amount_usd": amount,
                "price_low": low,
                "price_high": high,
                "reference_price": (low + high) / 2,
                "estimated_quantity": amount / ((low + high) / 2),
                "allocation_fraction": 0.5,
                "rationale": "synthetic",
                "structural_sources": ["SWING_LOW"],
                "zone_quality": 80.0,
            }
            for sequence, amount, low, high in tranches
        ],
    }


AAVE_ZONES = [
    (1, 1240.0, 141.06, 152.05),
    (2, 1469.0, 99.80, 107.39),
    (3, 1480.0, 88.47, 101.24),
]


class MatcherTests(unittest.TestCase):
    def test_in_zone_order_covers_its_tranche(self):
        # 13.71 * 107.0 = $1466.97 of $1469 -> 99.86% -> full coverage.
        result = match_plan_resting_orders(plan(AAVE_ZONES), [order("A", 107.0, 13.71)], **MATCH)
        self.assertEqual(result["resting_state"], "RESTING_PARTIAL")
        self.assertEqual(result["tranches"][1]["resting_state"], "RESTING_FULL")
        self.assertEqual(result["tranches"][1]["matched_orders"][0]["match_kind"], "IN_ZONE")
        self.assertEqual(result["tranches"][0]["resting_state"], "NOT_RESTING")
        self.assertEqual(result["unmatched_orders"], [])

    def test_overlapping_zones_share_one_order_without_exclusivity(self):
        # $100 sits inside both T2 (99.80-107.39) and T3 (88.47-101.24); the
        # order legitimately serves both zones and neither may claim it alone.
        shared = order("S", 100.0, 14.67)
        result = match_plan_resting_orders(plan(AAVE_ZONES), [shared], **MATCH)
        self.assertEqual(result["tranches"][1]["resting_state"], "RESTING_FULL")
        self.assertEqual(result["tranches"][2]["resting_state"], "RESTING_FULL")
        self.assertEqual(result["tranches"][2]["matched_orders"][0]["match_kind"], "IN_ZONE")
        self.assertEqual(result["tranches"][0]["resting_state"], "NOT_RESTING")

    def test_near_zone_order_within_tolerance_counts_with_deviation_note(self):
        # 620 is above T3's 616.41 edge by 0.58% — inside the 2% tolerance;
        # 0.083 * 620 = $51.46 of $64.00 -> 80% -> partial coverage.
        bnb_zones = [(1, 124.0, 741.29, 763.78), (3, 64.0, 590.40, 616.41)]
        result = match_plan_resting_orders(plan(bnb_zones), [order("N", 620.0, 0.083)], **MATCH)
        matched = result["tranches"][1]["matched_orders"]
        self.assertEqual(matched[0]["match_kind"], "NEAR_ZONE_ABOVE")
        self.assertEqual(result["tranches"][1]["resting_state"], "RESTING_PARTIAL")

    def test_far_outside_order_is_unmatched(self):
        # 132 is 6.4% below T1's 141.06 — beyond every zone and tolerance.
        result = match_plan_resting_orders(plan(AAVE_ZONES), [order("F", 132.0, 9.5)], **MATCH)
        self.assertEqual(result["resting_state"], "NOT_RESTING")
        self.assertEqual(len(result["unmatched_orders"]), 1)
        self.assertIn("outside every planned zone", result["unmatched_orders"][0]["reason"])

    def test_wrong_side_non_limit_and_executed_orders_do_not_cover(self):
        orders = [
            order("SELL1", 150.0, 8.0, side="SELL"),
            order("MKT", 150.0, 8.0, order_type="MARKET"),
            order("DONE", 150.0, 8.0, executed=8.0),
        ]
        result = match_plan_resting_orders(plan(AAVE_ZONES), orders, **MATCH)
        self.assertEqual(result["resting_state"], "NOT_RESTING")
        reasons = {item["reason"] for item in result["unmatched_orders"]}
        self.assertIn("opposite side of the plan", reasons)
        self.assertIn("non-resting order type", reasons)
        self.assertIn("fully executed; nothing rests", reasons)

    def test_multiple_orders_stack_and_partial_fraction_is_reported(self):
        # Two half-sized orders at T1 stack to full coverage.
        each = 1240.0 / 2 / 146.0
        result = match_plan_resting_orders(plan(AAVE_ZONES), [order("P1", 146.0, each), order("P2", 147.0, each)], **MATCH)
        self.assertEqual(result["tranches"][0]["resting_state"], "RESTING_FULL")
        self.assertAlmostEqual(result["tranches"][0]["covered_fraction"], 1.0)

    def test_sell_side_matches_reduce_plans(self):
        sell_zones = [(1, 1000.0, 90.0, 100.0)]
        result = match_plan_resting_orders(plan(sell_zones, action="REDUCE"), [order("S", 95.0, 10.5, side="SELL")], **MATCH)
        self.assertEqual(result["tranches"][0]["matched_orders"][0]["order_id"], "S")
        self.assertEqual(result["tranches"][0]["resting_state"], "RESTING_FULL")

    def test_all_tranches_full_yields_plan_level_full(self):
        zones = [(1, 1000.0, 100.0, 110.0), (2, 1000.0, 90.0, 99.0)]
        result = match_plan_resting_orders(
            plan(zones), [order("A", 105.0, 1000.0 / 105.0), order("B", 95.0, 1000.0 / 95.0)], **MATCH)
        self.assertEqual(result["resting_state"], "RESTING_FULL")


def prior_aave_record(status="PENDING"):
    return {
        "decision_id": "20260925T1154Z-snapshot-review",
        "timestamp": "2026-09-25T11:54:00Z",
        "status": status,
        "based_on_snapshot_id": "snap-a",
        "execution_plans": {"AAVE": plan([(1, 1240.0, 139.23, 149.19), (2, 1469.0, 99.57, 107.21)])},
    }


def current_aave_decision(plans):
    return SimpleNamespace(
        timestamp="2026-09-26T02:11:00Z",
        execution_plans=plans,
        based_on_snapshot_id="snap-b",
    )


class RestingOrderCoverageTests(unittest.TestCase):
    def test_coverage_marks_current_plan_tranches(self):
        policy = resolve_policy()
        decision = current_aave_decision({"AAVE": plan(AAVE_ZONES)})
        coverage = resting_order_coverage(decision, {"AAVE": [order("A", 107.0, 13.71)]}, policy=policy)
        self.assertIn("AAVE", coverage)
        self.assertEqual(coverage["AAVE"]["tranches"][1]["resting_state"], "RESTING_FULL")
        self.assertEqual(coverage["AAVE"]["tranches"][0]["resting_state"], "NOT_RESTING")

    def test_coverage_skips_unfetched_symbols_and_wait_plans(self):
        policy = resolve_policy()
        decision = current_aave_decision({
            "AAVE": plan(AAVE_ZONES),
            "BTC": {"action": "WAIT", "planned_amount_usd": 0.0, "tranches": []},
        })
        coverage = resting_order_coverage(decision, {"BTC": []}, policy=policy)
        self.assertEqual(coverage, {})

    def test_missing_open_orders_input_yields_empty_coverage(self):
        decision = current_aave_decision({"AAVE": plan(AAVE_ZONES)})
        self.assertEqual(resting_order_coverage(decision, None), {})
        self.assertEqual(resting_order_coverage(decision, {}), {})


class DispositionWithOpenOrdersTests(unittest.TestCase):
    def test_not_executed_with_matching_resting_orders_keeps_them(self):
        events = [{
            "decision_id": "20260925T1154Z-snapshot-review",
            "timestamp": "2026-09-25T12:00:00Z",
            "status": "NOT_EXECUTED",
            "reason": "zones never reachable",
        }]
        result = prior_plan_disposition(
            current_aave_decision({"AAVE": plan(AAVE_ZONES)}),
            [prior_aave_record()],
            status_events=events,
            open_orders={"AAVE": [order("A", 107.0, 13.71), order("F", 132.0, 9.5)]},
            policy=resolve_policy(),
        )
        aave = result["AAVE"]
        self.assertEqual(aave["effective_status"], "NOT_EXECUTED")
        self.assertEqual(aave["order_instruction"], "KEEP_EQUIVALENT_ORDERS")
        self.assertIn("already rest inside the current plan's zones", aave["instruction_reason"])
        # The stale 132 order below every zone must surface for manual review.
        self.assertIn("outside every current plan zone", aave["instruction_reason"])
        self.assertEqual(len(aave["resting_order_coverage"]["unmatched_orders"]), 1)

    def test_not_executed_without_matching_orders_still_cancels(self):
        events = [{
            "decision_id": "20260925T1154Z-snapshot-review",
            "timestamp": "2026-09-25T12:00:00Z",
            "status": "NOT_EXECUTED",
            "reason": "zones never reachable",
        }]
        result = prior_plan_disposition(
            current_aave_decision({"AAVE": plan(AAVE_ZONES)}),
            [prior_aave_record()],
            status_events=events,
            open_orders={"AAVE": [order("F", 132.0, 9.5)]},
            policy=resolve_policy(),
        )
        self.assertEqual(result["AAVE"]["order_instruction"], "CANCEL_RESTING")

    def test_absent_symbol_key_behaves_like_not_fetched(self):
        result = prior_plan_disposition(
            current_aave_decision({"AAVE": plan(AAVE_ZONES)}),
            [prior_aave_record()],
            open_orders={"BTC": []},
            policy=resolve_policy(),
        )
        # No fetch for AAVE: fall back to the zone-equality heuristics
        # (different zones, PENDING prior) exactly as before the feature.
        self.assertEqual(result["AAVE"]["order_instruction"], "REPLACE_WITH_NEW_PLAN")
        self.assertNotIn("resting_order_coverage", result["AAVE"])

    def test_near_zone_deviation_is_noted_in_the_keep_reason(self):
        events = [{
            "decision_id": "20260925T1154Z-snapshot-review",
            "timestamp": "2026-09-25T12:00:00Z",
            "status": "NOT_EXECUTED",
            "reason": "zones never reachable",
        }]
        near_zones = [(1, 1469.0, 100.0, 107.39)]
        result = prior_plan_disposition(
            current_aave_decision({"AAVE": plan(near_zones)}),
            [prior_aave_record()],
            status_events=events,
            open_orders={"AAVE": [order("NEAR", 108.5, 13.71)]},  # 1.0% above 107.39
            policy=resolve_policy(),
        )
        self.assertEqual(result["AAVE"]["order_instruction"], "KEEP_EQUIVALENT_ORDERS")
        self.assertIn("within the configured tolerance", result["AAVE"]["instruction_reason"])


if __name__ == "__main__":
    unittest.main()
