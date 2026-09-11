"""Regression coverage: explicit cash-flow resolutions restore NAV finality."""

import unittest

from crypto_portfolio.engine.benchmark import benchmark_return_with_cash_flows
from crypto_portfolio.engine.cash_flow import (
    apply_cash_flow_resolutions,
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

    def test_resolution_cannot_override_an_already_explicit_status(self):
        snapshots = (
            dict(BASELINE, cash_flow_resolution_status="ASSUMED_NONE"),
            _snapshots()[1],
        )
        with self.assertRaises(ValueError):
            apply_cash_flow_resolutions(snapshots, (_resolution(),))

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


if __name__ == "__main__":
    unittest.main()
