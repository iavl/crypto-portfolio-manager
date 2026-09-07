import math
import unittest

from crypto_portfolio.engine.metrics import (
    annualized_volatility,
    benchmark_70_30,
    current_drawdown,
    max_drawdown,
    moving_average,
    simple_return,
)


class MetricsTests(unittest.TestCase):
    def test_simple_return(self):
        self.assertAlmostEqual(simple_return(100, 120), 0.2)

    def test_drawdowns(self):
        values = [100, 120, 90, 110]
        self.assertAlmostEqual(max_drawdown(values), -0.25)
        self.assertAlmostEqual(current_drawdown(values), 110 / 120 - 1)

    def test_moving_average(self):
        self.assertAlmostEqual(moving_average([1, 2, 3, 4], 3), 3.0)

    def test_volatility_non_negative(self):
        vol = annualized_volatility([100, 101, 99, 102, 103])
        self.assertTrue(math.isfinite(vol))
        self.assertGreaterEqual(vol, 0)

    def test_benchmark(self):
        self.assertAlmostEqual(benchmark_70_30(0.1, 0.2), 0.13)


if __name__ == "__main__":
    unittest.main()
