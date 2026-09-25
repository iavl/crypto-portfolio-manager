"""Tests for the Tier-2 deterministic diagnostics (read-only checks, no thresholds)."""

import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from crypto_portfolio.engine.benchmark import exposure_timing_contribution
from crypto_portfolio.research.orchestrator import _cash_yield_sensitivity
from crypto_portfolio.research.sensitivity import drawdown_budget_sensitivity
from crypto_portfolio.research.validity_gate import (
    REGIME_PINNED_BY_OWN_DRAWDOWN,
    _regime_floor_findings,
    build_validity_report,
)


def _prices(closes):
    return [(f"2026-01-{index + 1:02d}T00:00:00Z", {"BTC": close}) for index, close in enumerate(closes)]


class ExposureTimingContributionTests(unittest.TestCase):
    def test_hand_math_correlated_exposure_is_positive(self):
        # Weights [1, 0] with BTC [+10%, -10%]: the path dodged the down leg.
        result = exposure_timing_contribution([1.0, 0.0], _prices([100.0, 110.0, 99.0]))
        self.assertAlmostEqual(result["realized_average_risky_weight"], 0.5)
        self.assertAlmostEqual(result["path_total_return"], 0.10, places=12)
        self.assertAlmostEqual(result["constant_total_return"], 0.9975 - 1.0, places=12)
        self.assertAlmostEqual(result["timing_contribution"], 0.1025, places=12)
        self.assertEqual(result["periods"], 2)

    def test_anticorrelated_exposure_is_negative(self):
        # Weights [0, 1] hold the down leg and skip the up leg.
        result = exposure_timing_contribution([0.0, 1.0], _prices([100.0, 110.0, 99.0]))
        self.assertAlmostEqual(result["path_total_return"], -0.10, places=12)
        self.assertLess(result["timing_contribution"], 0.0)

    def test_constant_path_has_zero_contribution(self):
        result = exposure_timing_contribution([0.3, 0.3, 0.3], _prices([100.0, 105.0, 98.0, 101.0]))
        self.assertAlmostEqual(result["timing_contribution"], 0.0, places=12)

    def test_validation(self):
        with self.assertRaises(ValueError):
            exposure_timing_contribution([], _prices([100.0, 110.0]))
        with self.assertRaises(ValueError):
            exposure_timing_contribution([0.5, 0.5], _prices([100.0, 110.0]))
        with self.assertRaises(ValueError):
            exposure_timing_contribution([1.5], _prices([100.0, 110.0]))
        with self.assertRaises(ValueError):
            exposure_timing_contribution([0.5], _prices([100.0, -110.0]))
        with self.assertRaises(ValueError):
            exposure_timing_contribution([0.5], _prices([100.0, 110.0]), leg_weights={"BTC": 0.6})
        with self.assertRaises(ValueError):
            exposure_timing_contribution([0.5], [("t0", {"ETH": 1.0}), ("t1", {"ETH": 1.1})])


@dataclass
class _Valuation:
    timestamp: str
    total_value_usd: float
    weights: dict = field(default_factory=dict)


class CashYieldSensitivityTests(unittest.TestCase):
    def test_all_cash_book_earns_the_yield(self):
        valuations = [
            _Valuation("2025-01-01T00:00:00Z", 100_000.0, {"USD": 1.0}),
            _Valuation("2026-01-01T00:00:00Z", 100_000.0, {"USD": 1.0}),
        ]
        result = _cash_yield_sensitivity(valuations, stable_symbols=("USD",))
        self.assertEqual(result["status"], "AVAILABLE")
        scenario = result["scenarios"]["yield_5.00%"]
        self.assertAlmostEqual(scenario["annual_yield"], 0.05)
        # One calendar year of 5% on a fully stable book; the engine's year is
        # 365.25 days, so the annualized figure carries that convention's dust.
        self.assertAlmostEqual(scenario["cagr"], 0.05, places=4)
        self.assertAlmostEqual(scenario["total_return"], 0.05, places=4)

    def test_zero_stable_share_is_unchanged(self):
        valuations = [
            _Valuation("2025-01-01T00:00:00Z", 100_000.0, {"BTC": 1.0}),
            _Valuation("2025-07-01T00:00:00Z", 120_000.0, {"BTC": 1.0}),
        ]
        result = _cash_yield_sensitivity(valuations, stable_symbols=("USD",))
        for scenario in result["scenarios"].values():
            self.assertAlmostEqual(scenario["total_return"], 0.20, places=9)

    def test_requires_a_span(self):
        result = _cash_yield_sensitivity(
            [_Valuation("2025-01-01T00:00:00Z", 1.0)], stable_symbols=("USD",),
        )
        self.assertEqual(result["status"], "UNAVAILABLE")


class RegimeFloorFindingsTests(unittest.TestCase):
    @staticmethod
    def _run(pinned, market_driven=0, label=0.844, floor=0.841):
        diagnostics = {
            "reviews": 995,
            "label_defensive_or_worse_share": label,
            "drawdown_at_or_below_defensive_floor_share": floor,
            "drawdown_at_or_below_cp_floor_share": 0.571,
            "market_driven_defensive_reviews": market_driven,
            "overlay_binding_share": 0.715,
        }
        return {
            "runs": {
                "core/core_existing/strict/main_cost": {
                    "status": "COMPLETED",
                    "result": {"regime_floor_diagnostics": diagnostics},
                },
            },
        }

    def test_pinned_run_is_flagged(self):
        findings = _regime_floor_findings(self._run(pinned=True))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, REGIME_PINNED_BY_OWN_DRAWDOWN)
        self.assertEqual(findings[0].severity, "WARNING")
        self.assertIn("own P&L", findings[0].message)

    def test_isolated_market_driven_reviews_do_not_mask_the_pin(self):
        findings = _regime_floor_findings(self._run(pinned=True, market_driven=3))
        self.assertEqual(len(findings), 1)

    def test_sustained_market_driven_defense_disqualifies_the_pin(self):
        findings = _regime_floor_findings(self._run(pinned=True, market_driven=62))
        self.assertEqual(findings, ())

    def test_divergent_shares_disqualify_the_pin(self):
        findings = _regime_floor_findings(self._run(pinned=True, label=0.60, floor=0.85))
        self.assertEqual(findings, ())

    def test_brief_floor_contact_is_not_a_pin(self):
        findings = _regime_floor_findings(self._run(pinned=True, label=0.30, floor=0.29))
        self.assertEqual(findings, ())

    def test_missing_diagnostics_are_skipped(self):
        self.assertEqual(_regime_floor_findings({"runs": {}}), ())
        self.assertEqual(_regime_floor_findings(None), ())

    def test_build_validity_report_reads_run_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "run.json").write_text(
                json.dumps(self._run(pinned=True)), encoding="utf-8",
            )
            report = build_validity_report(root)
            codes = [finding.code for finding in report.findings]
            self.assertIn(REGIME_PINNED_BY_OWN_DRAWDOWN, codes)


class BudgetSensitivityValidationTests(unittest.TestCase):
    def test_budgets_are_validated_before_any_io(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ((), (0.0,), (1.0,), (0.15, "x")):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        drawdown_budget_sensitivity(tmp, budgets=bad)


if __name__ == "__main__":
    unittest.main()
