"""Regression coverage: explicit cash-flow resolutions restore NAV finality."""

import unittest

from crypto_portfolio.engine.benchmark import benchmark_return_with_cash_flows
from crypto_portfolio.engine.cash_flow import (
    InternalReallocation,
    apply_cash_flow_resolutions,
    attribute_asset_changes,
    cash_flow_adjusted_performance,
    find_unresolved_cash_flow_snapshots,
)
from crypto_portfolio.models.cash_flow import CashFlowResolution
from crypto_portfolio.models.portfolio import snapshot_from_mapping


BASELINE = {
    "snapshot_id": "snap-2026-09-01",
    "timestamp": "2026-09-01T00:00:00Z",
    "total_value_usd": 10000.0,
    "external_cash_flow": None,
    "external_cash_flow_type": None,
    "cash_flow_resolution_status": "UNRESOLVED",
}


def _snapshots():
    return (
        dict(BASELINE),
        {
            "snapshot_id": "snap-2026-09-11",
            "timestamp": "2026-09-11T00:00:00Z",
            "total_value_usd": 11000.0,
            "external_cash_flow": 0.0,
            "external_cash_flow_type": "NONE",
            "cash_flow_resolution_status": "ASSUMED_NONE",
        },
    )


def _resolution(status="CONFIRMED_NONE", amount=None, flow_type=None):
    if status == "CONFIRMED_NONE":
        amount, flow_type = 0.0, "NONE"
    elif status == "BASELINE_RESET":
        amount, flow_type = 0.0, "NONE"
    return {
        "resolution_id": "cfr-test-1",
        "snapshot_id": "snap-2026-09-01",
        "timestamp": "2026-09-11T02:27:00Z",
        "cash_flow_resolution_status": status,
        "external_cash_flow": amount,
        "external_cash_flow_type": flow_type,
        "rationale": "User confirmed classification",
    }


class CashFlowResolutionOverlayTests(unittest.TestCase):
    def test_legacy_unresolved_without_resolution_keeps_nav_provisional(self):
        result = cash_flow_adjusted_performance(_snapshots())
        self.assertEqual(result["performance_finality"], "PROVISIONAL")
        self.assertIsNone(result["return"])
        self.assertEqual(
            result["unresolved_snapshots"],
            ({"snapshot_id": "snap-2026-09-01", "timestamp": BASELINE["timestamp"],
              "original_status": "UNRESOLVED", "has_resolution": False},),
        )

    def test_cash_flow_resolution_overlay_restores_nav(self):
        result = cash_flow_adjusted_performance(_snapshots(), resolutions=(_resolution(),))
        self.assertEqual(result["performance_finality"], "FINAL")
        self.assertAlmostEqual(result["return"], 0.1)
        self.assertEqual(result["unresolved_snapshots"], ())

    def test_overlay_does_not_mutate_snapshot_history(self):
        snapshots = _snapshots()
        original = dict(snapshots[0])
        apply_cash_flow_resolutions(snapshots, (_resolution(),))
        self.assertEqual(snapshots[0], original)
        self.assertEqual(snapshots[0]["cash_flow_resolution_status"], "UNRESOLVED")

    def test_confirmed_deposit_excludes_external_cash_flow_from_performance(self):
        # 10000 -> 11000 (1000 deposit) -> 11000 market: performance is 0, and
        # the raw 1000/10000 balance change never becomes return.
        snapshots = (
            *_snapshots(),
            {
                "snapshot_id": "snap-2026-09-12",
                "timestamp": "2026-09-12T00:00:00Z",
                "total_value_usd": 11000.0,
                "external_cash_flow": None,
                "external_cash_flow_type": None,
                "cash_flow_resolution_status": "UNRESOLVED",
            },
            {
                "snapshot_id": "snap-2026-09-13",
                "timestamp": "2026-09-13T00:00:00Z",
                "total_value_usd": 11000.0,
                "external_cash_flow": 0.0,
                "external_cash_flow_type": "NONE",
                "cash_flow_resolution_status": "ASSUMED_NONE",
            },
        )
        deposit = dict(
            _resolution("CONFIRMED_AMOUNT", 1000.0, "DEPOSIT"),
            snapshot_id="snap-2026-09-12",
            resolution_id="cfr-test-2",
        )
        result = cash_flow_adjusted_performance(snapshots, resolutions=(_resolution(), deposit))
        self.assertEqual(result["performance_finality"], "FINAL")
        self.assertAlmostEqual(result["return"], 0.0)

    def test_confirmed_withdrawal_adjusts_units(self):
        # 10000 -> 11000 market -> 11000 after a -1000 withdrawal: NAV units
        # shrink so the withdrawal itself is not return; final NAV 1.20.
        snapshots = (
            *_snapshots(),
            {
                "snapshot_id": "snap-2026-09-12",
                "timestamp": "2026-09-12T00:00:00Z",
                "total_value_usd": 11000.0,
                "external_cash_flow": None,
                "external_cash_flow_type": None,
                "cash_flow_resolution_status": "UNRESOLVED",
            },
            {
                "snapshot_id": "snap-2026-09-13",
                "timestamp": "2026-09-13T00:00:00Z",
                "total_value_usd": 11000.0,
                "external_cash_flow": 0.0,
                "external_cash_flow_type": "NONE",
                "cash_flow_resolution_status": "ASSUMED_NONE",
            },
        )
        withdrawal = dict(
            _resolution("CONFIRMED_AMOUNT", -1000.0, "WITHDRAWAL"),
            snapshot_id="snap-2026-09-12",
            resolution_id="cfr-test-2",
        )
        result = cash_flow_adjusted_performance(snapshots, resolutions=(_resolution(), withdrawal))
        self.assertEqual(result["performance_finality"], "FINAL")
        self.assertAlmostEqual(result["return"], 0.2)

    def test_confirmed_amount_on_initial_snapshot_is_rejected(self):
        with self.assertRaises(ValueError):
            cash_flow_adjusted_performance(
                _snapshots(), resolutions=(_resolution("CONFIRMED_AMOUNT", 1000.0, "DEPOSIT"),)
            )

    def test_baseline_reset_starts_new_verified_nav_window(self):
        result = cash_flow_adjusted_performance(
            _snapshots(), resolutions=(_resolution("BASELINE_RESET"),)
        )
        self.assertEqual(result["performance_finality"], "FINAL")
        self.assertAlmostEqual(result["return"], 0.1)
        states = result["states"]
        self.assertEqual(states[0]["cash_flow_resolution_status"], "BASELINE_RESET")
        self.assertAlmostEqual(states[0]["nav_per_unit"], 1.0)

    def test_duplicate_resolution_is_rejected(self):
        with self.assertRaises(ValueError):
            apply_cash_flow_resolutions(_snapshots(), (_resolution(), _resolution()))

    def test_resolution_referencing_unknown_snapshot_is_rejected(self):
        stray = dict(_resolution(), snapshot_id="snap-does-not-exist")
        with self.assertRaises(ValueError):
            apply_cash_flow_resolutions(_snapshots(), (stray,))
        with self.assertRaises(ValueError):
            find_unresolved_cash_flow_snapshots(_snapshots(), (stray,))

    def test_user_explicit_classification_cannot_be_reresolved(self):
        snapshots = (
            dict(
                BASELINE,
                cash_flow_resolution_status="CONFIRMED_NONE",
                cash_flow_classification_source="USER_EXPLICIT",
            ),
            _snapshots()[1],
        )
        with self.assertRaises(ValueError):
            apply_cash_flow_resolutions(snapshots, (_resolution(),))

    def test_derived_assumed_none_accepts_a_late_resolution(self):
        # A snapshot recorded before the classification contract (no status of
        # its own) derives to ASSUMED_NONE; the user may still resolve it later.
        snapshots = (
            dict(BASELINE, cash_flow_resolution_status="ASSUMED_NONE"),
            _snapshots()[1],
        )
        effective, lineage = apply_cash_flow_resolutions(
            snapshots,
            (_resolution("CONFIRMED_AMOUNT", 500.0, "DEPOSIT"),),
        )
        self.assertEqual(effective[0]["cash_flow_resolution_status"], "CONFIRMED_AMOUNT")
        self.assertEqual(lineage["lineage"][0]["original_status"], "ASSUMED_NONE")
        self.assertEqual(lineage["lineage"][0]["effective_status"], "CONFIRMED_AMOUNT")

    def test_raw_pre_contract_record_replays_without_full_model_parse(self):
        # Real 2026-09-09 runtime shape: no status key, the unresolved intent in
        # external_cash_flow_type, and a stale embedded policy blob that the
        # current policy model would reject. History replay must consume the
        # ledger fields only and still allow an explicit resolution.
        legacy = {
            "snapshot_id": "snap-legacy",
            "timestamp": "2026-09-09T12:52:27Z",
            "base_currency": "USD",
            "positions": [{"symbol": "BTC", "quantity": 0.1, "value_usd": 10000.0}],
            "external_cash_flow": 0.0,
            "external_cash_flow_type": "UNRESOLVED",
            "resolved_policy": {"positioning": {"deleveraging": {"enabled": True}}},
        }
        blocking = find_unresolved_cash_flow_snapshots((legacy,))
        self.assertEqual(
            blocking,
            ({"snapshot_id": "snap-legacy", "timestamp": "2026-09-09T12:52:27Z",
              "original_status": "UNRESOLVED", "has_resolution": False},),
        )
        resolution = dict(_resolution(), snapshot_id="snap-legacy")
        effective, lineage = apply_cash_flow_resolutions((legacy,), (resolution,))
        self.assertEqual(effective[0]["cash_flow_resolution_status"], "CONFIRMED_NONE")
        self.assertEqual(lineage["lineage"][0]["source"], "USER_EXPLICIT")
        result = cash_flow_adjusted_performance(
            (legacy,),
            resolutions=(resolution,),
        )
        self.assertEqual(result["performance_finality"], "FINAL")

    def test_find_unresolved_reports_each_blocking_snapshot(self):
        snapshots = (
            dict(BASELINE),
            dict(_snapshots()[1], cash_flow_resolution_status="UNRESOLVED",
                 external_cash_flow=None, external_cash_flow_type=None),
        )
        blocking = find_unresolved_cash_flow_snapshots(snapshots)
        self.assertEqual(len(blocking), 2)
        self.assertEqual({item["snapshot_id"] for item in blocking}, {"snap-2026-09-01", "snap-2026-09-11"})

    def test_ledger_result_lists_blocking_snapshots_and_lineage(self):
        effective, diagnostics = apply_cash_flow_resolutions(_snapshots(), (_resolution(),))
        self.assertEqual(diagnostics["lineage"][0]["effective_status"], "CONFIRMED_NONE")
        self.assertEqual(diagnostics["lineage"][0]["source"], "USER_EXPLICIT")
        self.assertEqual(diagnostics["lineage"][1]["source"], "PERSISTED")
        self.assertEqual(effective[0]["cash_flow_resolution_status"], "CONFIRMED_NONE")

    def test_benchmark_uses_same_effective_cash_flows(self):
        # Benchmark with the same confirmed deposit must reproduce the same
        # flow timing: deposit enters before the second period valuation.
        result = benchmark_return_with_cash_flows(
            ({"BTC": 0.0},),
            (1000.0,),
            weights={"BTC": 1.0},
            initial_value=10000.0,
            timestamps=("2026-09-01T00:00:00Z", "2026-09-11T00:00:00Z"),
        )
        self.assertAlmostEqual(result, 0.0)

    def test_persisted_unresolved_snapshot_records_round_trip(self):
        record = {
            "snapshot_id": "snap-2026-09-01",
            "timestamp": BASELINE["timestamp"],
            "base_currency": "USD",
            "positions": [{"symbol": "BTC", "quantity": 0.1, "value_usd": 10000.0}],
            "cash_flow_resolution_status": "UNRESOLVED",
        }
        snapshot, _, _ = snapshot_from_mapping(record)
        result = cash_flow_adjusted_performance(
            (snapshot, snapshot_from_mapping({
                "snapshot_id": "snap-2026-09-11",
                "timestamp": "2026-09-11T00:00:00Z",
                "base_currency": "USD",
                "positions": [{"symbol": "BTC", "quantity": 0.1, "value_usd": 11000.0}],
            })[0]),
            resolutions=(_resolution(),),
        )
        self.assertEqual(result["performance_finality"], "FINAL")


class CashFlowResolutionModelTests(unittest.TestCase):
    def test_resolution_model_round_trip(self):
        resolution = CashFlowResolution.from_mapping(_resolution())
        self.assertEqual(resolution.cash_flow_resolution_status, "CONFIRMED_NONE")
        restored = CashFlowResolution.from_mapping(resolution.as_dict())
        self.assertEqual(restored, resolution)

    def test_confirmed_amount_requires_nonzero_and_type(self):
        with self.assertRaises(ValueError):
            CashFlowResolution.from_mapping(_resolution("CONFIRMED_AMOUNT", 0.0, "DEPOSIT"))
        with self.assertRaises(ValueError):
            CashFlowResolution.from_mapping(_resolution("CONFIRMED_AMOUNT", 100.0, None))


class InternalReallocationTests(unittest.TestCase):
    """A stable-to-stable exchange is neither external flow nor market P&L."""

    @staticmethod
    def _snapshot(**positions: float) -> dict:
        return {
            "snapshot_id": "snap",
            "timestamp": "2026-09-15T01:05:00Z",
            "total_value_usd": sum(positions.values()),
            "positions": [
                {"symbol": symbol, "value_usd": value}
                for symbol, value in sorted(positions.items())
            ],
        }

    def test_matched_usdt_u_swap_is_internal_reallocation(self):
        # The 2026-09-15 review case: USDT -3000, U +2999.5, external flow 0.
        previous = self._snapshot(BTC=50000.0, USDT=20000.0, U=3000.0)
        current = self._snapshot(BTC=50000.0, USDT=17000.0, U=5999.5)
        attribution = attribute_asset_changes(previous, current)
        self.assertEqual(attribution["external_cash_flow"], 0.0)
        reallocation = attribution["internal_reallocation"]
        self.assertIsNotNone(reallocation)
        self.assertEqual(reallocation["attribution"], "INFERRED_INTERNAL_REALLOCATION")
        self.assertEqual(reallocation["from_symbol"], "USDT")
        self.assertEqual(reallocation["to_symbol"], "U")
        self.assertAlmostEqual(reallocation["amount_usd"], 2999.5)
        self.assertGreater(reallocation["confidence"], 0.99)
        # Market P&L does not receive the +/- ~3000 transfer principal.
        self.assertAlmostEqual(attribution["market_change_usd"]["USDT"], -0.5)
        self.assertAlmostEqual(attribution["market_change_usd"]["U"], 0.0)
        self.assertAlmostEqual(attribution["market_change_usd"]["BTC"], 0.0)
        # The magnitude mismatch surfaces as swap cost/slippage, not P&L.
        self.assertAlmostEqual(attribution["swap_cost_slippage_usd"], 0.5)

    def test_dollars_conserve_across_the_attribution(self):
        previous = self._snapshot(BTC=50000.0, USDT=20000.0, U=3000.0)
        current = self._snapshot(BTC=50800.0, USDT=17000.0, U=5999.5)
        attribution = attribute_asset_changes(previous, current)
        # With zero external flow, market attribution plus the matched
        # reallocation principal plus its visible residual reconciles to the
        # portfolio's total dollar change exactly.
        total_market = sum(attribution["market_change_usd"].values())
        self.assertAlmostEqual(
            total_market,
            current["total_value_usd"] - previous["total_value_usd"],
            places=6,
        )

    def test_no_match_returns_none_instead_of_guessing(self):
        # Unmatched magnitudes (3000 vs 500) must not be forced into a swap.
        previous = self._snapshot(USDT=20000.0, U=3000.0)
        current = self._snapshot(USDT=17000.0, U=3500.0)
        attribution = attribute_asset_changes(previous, current)
        self.assertIsNone(attribution["internal_reallocation"])
        self.assertAlmostEqual(attribution["market_change_usd"]["USDT"], -3000.0)
        self.assertAlmostEqual(attribution["market_change_usd"]["U"], 500.0)

    def test_explicit_reallocation_wins_over_inference(self):
        previous = self._snapshot(USDT=20000.0, U=3000.0)
        current = self._snapshot(USDT=17000.0, U=5999.5)
        attribution = attribute_asset_changes(
            previous,
            current,
            explicit_reallocation={
                "from_symbol": "USDT",
                "to_symbol": "U",
                "amount_usd": 3000.0,
                "attribution": "INTERNAL_REALLOCATION",
                "confidence": 1.0,
                "source": "USER_EXPLICIT",
                "rationale": "user confirmed USDT->U swap on the venue",
            },
        )
        reallocation = attribution["internal_reallocation"]
        self.assertEqual(reallocation["attribution"], "INTERNAL_REALLOCATION")
        self.assertEqual(reallocation["source"], "USER_EXPLICIT")
        self.assertAlmostEqual(attribution["market_change_usd"]["USDT"], 0.0)

    def test_inference_respects_stable_boundary(self):
        # BTC dropping while USDT rises is a market move, not a sleeve swap.
        previous = self._snapshot(BTC=50000.0, USDT=20000.0)
        current = self._snapshot(BTC=47000.0, USDT=23000.0)
        attribution = attribute_asset_changes(previous, current)
        self.assertIsNone(attribution["internal_reallocation"])
        self.assertAlmostEqual(attribution["market_change_usd"]["BTC"], -3000.0)

    def test_model_validates(self):
        with self.assertRaises(ValueError):
            InternalReallocation("USDT", "USDT", 100.0, "INTERNAL_REALLOCATION", 1.0, "USER_EXPLICIT", "x")
        with self.assertRaises(ValueError):
            InternalReallocation("USDT", "U", -1.0, "INTERNAL_REALLOCATION", 1.0, "USER_EXPLICIT", "x")
        with self.assertRaises(ValueError):
            InternalReallocation("USDT", "U", 100.0, "GUESS", 1.0, "USER_EXPLICIT", "x")


class ExchangeDerivedClassificationTests(unittest.TestCase):
    """EXCHANGE_DERIVED snapshots are exchange-confirmed at creation time."""

    def _mapping(self, **flow):
        base = {
            "timestamp": "2026-09-19T00:00:00Z",
            "positions": [{"symbol": "BTC", "value_usd": 1000.0}],
            "cash_flow_classification_source": "EXCHANGE_DERIVED",
        }
        base.update(flow)
        return base

    def test_exchange_derived_confirmed_amount_is_accepted(self):
        snapshot, _, _ = snapshot_from_mapping(
            self._mapping(
                external_cash_flow=250.0,
                external_cash_flow_type="DEPOSIT",
                cash_flow_resolution_status="CONFIRMED_AMOUNT",
            )
        )
        self.assertEqual(snapshot.cash_flow_classification_source, "EXCHANGE_DERIVED")
        self.assertEqual(snapshot.external_cash_flow, 250.0)

    def test_exchange_derived_confirmed_none_beats_the_default_assumption(self):
        snapshot, _, _ = snapshot_from_mapping(
            self._mapping(
                external_cash_flow=0.0,
                external_cash_flow_type="NONE",
                cash_flow_resolution_status="CONFIRMED_NONE",
            )
        )
        self.assertEqual(snapshot.cash_flow_resolution_status, "CONFIRMED_NONE")
        self.assertEqual(snapshot.cash_flow_classification_source, "EXCHANGE_DERIVED")

    def test_exchange_derived_cannot_back_non_confirmed_statuses(self):
        for status, flow in (
            ("ASSUMED_NONE", {}),
            ("UNRESOLVED", {}),
            ("BASELINE_RESET", {"snapshot_id": "snap-1"}),
        ):
            with self.assertRaises(ValueError, msg=status):
                snapshot_from_mapping(
                    self._mapping(
                        cash_flow_resolution_status=status,
                        external_cash_flow=0.0,
                        external_cash_flow_type="NONE",
                    )
                )

    def test_exchange_derived_confirmed_amount_still_requires_valid_amount(self):
        with self.assertRaises(ValueError):
            snapshot_from_mapping(
                self._mapping(
                    external_cash_flow=0.0,
                    external_cash_flow_type="NONE",
                    cash_flow_resolution_status="CONFIRMED_AMOUNT",
                )
            )


if __name__ == "__main__":
    unittest.main()
