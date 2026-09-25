"""Correlation and beta estimation (Strategy V2 Phase 1)."""

import random
import unittest

from crypto_portfolio.engine.portfolio_risk import (
    beta_to_btc,
    correlation_matrix,
    pearson_correlation,
)


def _series(count: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(0.0, 0.02) for _ in range(count)]


class PearsonCorrelationTests(unittest.TestCase):
    def test_identical_series_correlate_to_one(self):
        values = _series(120, 7)
        self.assertAlmostEqual(pearson_correlation(values, values), 1.0)

    def test_mirrored_series_correlate_to_minus_one(self):
        values = _series(120, 7)
        self.assertAlmostEqual(pearson_correlation(values, [-v for v in values]), -1.0)

    def test_constant_series_has_undefined_correlation(self):
        values = _series(120, 7)
        self.assertIsNone(pearson_correlation(values, [0.01] * len(values)))

    def test_length_mismatch_is_an_error(self):
        with self.assertRaises(ValueError):
            pearson_correlation([0.01, 0.02], [0.01])


class CorrelationMatrixTests(unittest.TestCase):
    def test_matrix_is_symmetric_with_unit_diagonal(self):
        series = {"BTC": _series(100, 1), "ETH": _series(100, 2), "SOL": _series(100, 3)}
        matrix = correlation_matrix(series, window=90)
        self.assertEqual(set(matrix), {"BTC", "ETH", "SOL"})
        for symbol, row in matrix.items():
            self.assertAlmostEqual(row[symbol], 1.0)
            for other, value in row.items():
                if other != symbol:
                    self.assertIsNotNone(value)
                    self.assertAlmostEqual(matrix[other][symbol], value)

    def test_window_trims_the_tail(self):
        long_series = {"A": _series(200, 11), "B": _series(200, 12)}
        direct = pearson_correlation(long_series["A"][-90:], long_series["B"][-90:])
        self.assertAlmostEqual(
            correlation_matrix(long_series, window=90)["A"]["B"], direct
        )

    def test_insufficient_window_history_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "insufficient history"):
            correlation_matrix({"A": _series(10, 1), "B": _series(10, 2)}, window=90)


class BetaToBtCTests(unittest.TestCase):
    def test_btc_beta_is_one_by_definition(self):
        series = {"BTC": _series(100, 5)}
        self.assertAlmostEqual(beta_to_btc(series, window=90)["BTC"], 1.0)

    def test_double_exposure_is_beta_two(self):
        btc = _series(100, 5)
        levered = [2.0 * value for value in btc]
        beta = beta_to_btc({"BTC": btc, "2X": levered}, window=90)
        self.assertAlmostEqual(beta["2X"], 2.0, places=6)

    def test_independent_series_has_beta_near_zero(self):
        beta = beta_to_btc(
            {"BTC": _series(500, 5), "IDLE": _series(500, 99)}, window=90
        )
        self.assertLess(abs(beta["IDLE"]), 0.5)

    def test_exact_beta_from_constructed_covariance(self):
        btc = _series(120, 5)
        constructed = [0.5 * value for value in btc]
        beta = beta_to_btc({"BTC": btc, "HALF": constructed}, window=90)
        self.assertAlmostEqual(beta["HALF"], 0.5, places=6)

    def test_missing_btc_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "BTC"):
            beta_to_btc({"ETH": _series(100, 1)}, window=90)

    def test_constant_btc_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "constant"):
            beta_to_btc(
                {"BTC": [0.001] * 100, "ETH": _series(100, 2)}, window=90
            )


if __name__ == "__main__":
    unittest.main()
