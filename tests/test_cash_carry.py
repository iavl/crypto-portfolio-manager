"""Cash carry conventions and adjustment math (Strategy V2.3 Phase 4)."""

import unittest
from datetime import datetime, timedelta, timezone

from crypto_portfolio.engine.backtest import ValuationPoint
from crypto_portfolio.research.cash_carry import (
    CASH_CARRY_MODES,
    STABLE_HAIRCUT_COMPONENTS,
    CashCarryConvention,
    benchmark_uses_cash,
    carry_adjusted_valuations,
    carry_contribution,
    investable_stable_yield,
    period_carry_return,
    rate_points_from_percent_series,
)


def _point(index, value, weights, timestamp=None):
    moment = timestamp or (
        datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
    ).isoformat().replace("+00:00", "Z")
    return ValuationPoint(
        moment, value, value * weights.get("USD", 0.0),
        {symbol: value * weight for symbol, weight in weights.items() if symbol != "USD"},
        dict(weights), 0.0,
    )


RATE_POINTS = (
    ("2023-12-01T00:00:00Z", 0.05),
    ("2024-06-01T00:00:00Z", 0.04),
)


class ConventionTests(unittest.TestCase):
    def test_modes_are_preregistered(self):
        self.assertEqual(
            CASH_CARRY_MODES,
            ("ZERO", "RISK_FREE_PROXY", "INVESTABLE_STABLE_SENSITIVITY"),
        )

    def test_rate_lookup_is_point_in_time(self):
        convention = CashCarryConvention("RISK_FREE_PROXY", RATE_POINTS)
        self.assertAlmostEqual(convention.rate_at("2024-01-15T00:00:00Z"), 0.05)
        self.assertAlmostEqual(convention.rate_at("2024-07-01T00:00:00Z"), 0.04)
        self.assertIsNone(convention.rate_at("2023-11-01T00:00:00Z"))

    def test_zero_mode_always_earns_zero(self):
        convention = CashCarryConvention("ZERO")
        self.assertEqual(convention.rate_at("2024-01-01T00:00:00Z"), 0.0)

    def test_invalid_conventions_are_rejected(self):
        with self.assertRaises(ValueError):
            CashCarryConvention("MAGIC")
        with self.assertRaises(ValueError):
            CashCarryConvention("RISK_FREE_PROXY", (
                ("2024-01-01T00:00:00Z", 0.05), ("2024-01-01T00:00:00Z", 0.04),
            ))
        with self.assertRaises(ValueError):
            CashCarryConvention("RISK_FREE_PROXY", (("2024-01-01T00:00:00Z", float("nan")),))

    def test_period_carry_compounds_over_calendar_days(self):
        self.assertAlmostEqual(period_carry_return(0.05, 365.25 / 2), 1.05 ** 0.5 - 1)
        self.assertAlmostEqual(period_carry_return(0.05, 0.0), 0.0)

    def test_percent_series_becomes_fraction_points(self):
        class _Point:
            def __init__(self, observed_at, value):
                self.observed_at = observed_at
                self.value = value

        class _Series:
            points = (
                _Point("2024-01-01T00:00:00Z", 5.25),
                _Point("2024-02-01T00:00:00Z", 5.10),
            )

        points = rate_points_from_percent_series(_Series)
        self.assertEqual(points[0], ("2024-01-01T00:00:00Z", 0.0525))
        self.assertEqual(points[1], ("2024-02-01T00:00:00Z", 0.051))


class CarryAdjustmentTests(unittest.TestCase):
    def test_half_cash_book_earns_the_cash_share_of_carry(self):
        points = [
            _point(0, 100.0, {"BTC": 0.5, "USD": 0.5}),
            _point(30, 103.0, {"BTC": 0.5, "USD": 0.5}),  # +3% gross
        ]
        convention = CashCarryConvention("RISK_FREE_PROXY", RATE_POINTS)
        adjusted = carry_adjusted_valuations(
            points, cash_symbols=("USD", "USDT"), convention=convention,
        )
        carry = 0.5 * period_carry_return(0.05, 30)
        self.assertAlmostEqual(
            adjusted[-1].total_value_usd, 100.0 * (1.03 + carry), places=8,
        )

    def test_all_cash_book_earns_the_full_carry(self):
        points = [
            _point(0, 100.0, {"USD": 1.0}),
            _point(365.25, 100.0, {"USD": 1.0}),
        ]
        convention = CashCarryConvention("RISK_FREE_PROXY", RATE_POINTS)
        adjusted = carry_adjusted_valuations(
            points, cash_symbols=("USD",), convention=convention,
        )
        self.assertAlmostEqual(adjusted[-1].total_value_usd, 105.0, places=8)

    def test_zero_convention_changes_nothing(self):
        points = [
            _point(0, 100.0, {"BTC": 0.5, "USD": 0.5}),
            _point(30, 103.0, {"BTC": 0.5, "USD": 0.5}),
        ]
        adjusted = carry_adjusted_valuations(
            points, cash_symbols=("USD",), convention=CashCarryConvention("ZERO"),
        )
        self.assertAlmostEqual(adjusted[-1].total_value_usd, 103.0)

    def test_unknown_rate_credits_zero_carry_never_fabricates(self):
        points = [
            _point(0, 100.0, {"USD": 1.0}, timestamp="2023-01-01T00:00:00Z"),
            _point(0, 100.0, {"USD": 1.0}, timestamp="2023-02-01T00:00:00Z"),
        ]
        adjusted = carry_adjusted_valuations(
            points, cash_symbols=("USD",),
            convention=CashCarryConvention("RISK_FREE_PROXY", RATE_POINTS),
        )
        self.assertAlmostEqual(adjusted[-1].total_value_usd, 100.0)

    def test_drawdown_is_recomputed_on_the_adjusted_path(self):
        points = [
            _point(0, 100.0, {"USD": 1.0}),
            _point(90, 90.0, {"USD": 1.0}),
        ]
        adjusted = carry_adjusted_valuations(
            points, cash_symbols=("USD",), convention=CashCarryConvention("ZERO"),
        )
        self.assertAlmostEqual(adjusted[-1].drawdown, -0.10)

    def test_contribution_report_separates_zero_carry_and_carry(self):
        points = [
            _point(0, 100.0, {"USD": 1.0}),
            _point(365.25, 100.0, {"USD": 1.0}),
        ]
        report = carry_contribution(
            points, cash_symbols=("USD",),
            convention=CashCarryConvention("RISK_FREE_PROXY", RATE_POINTS),
        )
        self.assertEqual(report["mode"], "RISK_FREE_PROXY")
        self.assertAlmostEqual(report["total_return_zero_carry"], 0.0)
        self.assertAlmostEqual(report["total_return_with_carry"], 0.05, places=8)
        self.assertAlmostEqual(report["carry_contribution"], 0.05, places=8)


class StableSensitivityTests(unittest.TestCase):
    def test_stable_yield_is_proxy_minus_explicit_haircut(self):
        result = investable_stable_yield(0.05, 0.015)
        self.assertAlmostEqual(result["investable_stable_yield"], 0.035)
        self.assertEqual(
            set(result["haircut_components"]), set(STABLE_HAIRCUT_COMPONENTS),
        )

    def test_haircut_bounds_are_validated(self):
        with self.assertRaises(ValueError):
            investable_stable_yield(0.05, -0.01)
        with self.assertRaises(ValueError):
            investable_stable_yield(0.05, 1.5)


class BenchmarkCashTests(unittest.TestCase):
    def test_cash_legs_are_detected(self):
        self.assertTrue(benchmark_uses_cash({"BTC": 0.4, "USD": 0.6}, ("USDT",)))
        self.assertTrue(benchmark_uses_cash({"BTC": 0.4, "USDT": 0.6}, ("USDT",)))
        self.assertFalse(benchmark_uses_cash({"BTC": 1.0}, ("USDT",)))
        self.assertFalse(benchmark_uses_cash({"BTC": 0.7, "ETH": 0.3}, ("USDT",)))


if __name__ == "__main__":
    unittest.main()
