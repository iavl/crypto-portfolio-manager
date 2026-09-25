"""Score reachability math (Strategy V2 Phase 2)."""

import unittest

from crypto_portfolio.engine.scoring import score_factors, score_reachability


class ScoreReachabilityTests(unittest.TestCase):
    def test_full_coverage_reaches_the_whole_scale(self):
        result = score_reachability(1.0)
        self.assertAlmostEqual(result["reachable_min"], 0.0)
        self.assertAlmostEqual(result["reachable_max"], 100.0)

    def test_zero_coverage_collapses_to_neutral(self):
        result = score_reachability(0.0)
        self.assertAlmostEqual(result["reachable_min"], 50.0)
        self.assertAlmostEqual(result["reachable_max"], 50.0)

    def test_half_coverage_reaches_thirty_five_to_sixty_five(self):
        result = score_reachability(0.3)
        self.assertAlmostEqual(result["reachable_min"], 35.0)
        self.assertAlmostEqual(result["reachable_max"], 65.0)

    def test_invalid_coverage_is_rejected(self):
        for bad in (-0.1, 1.5, float("nan")):
            with self.assertRaises(ValueError):
                score_reachability(bad)

    def test_scored_result_carries_its_own_reachable_range(self):
        result = score_factors(
            {"trend": 80.0, "valuation": 70.0, "fundamentals": 60.0, "onchain": 50.0,
             "capital_flows": 40.0, "relative_strength_btc": 55.0},
            critical_data_complete=True,
        )
        self.assertAlmostEqual(result.reachable_min, 50.0 - 50.0 * result.coverage)
        self.assertAlmostEqual(result.reachable_max, 50.0 + 50.0 * result.coverage)
        self.assertLessEqual(result.score, result.reachable_max + 1e-9)
        self.assertGreaterEqual(result.score, result.reachable_min - 1e-9)

    def test_missing_factors_shrink_the_reachable_range_not_the_ceiling_logic(self):
        complete = score_factors(
            {"trend": 100.0, "valuation": 100.0, "fundamentals": 100.0, "onchain": 100.0,
             "capital_flows": 100.0, "relative_strength_btc": 100.0},
            critical_data_complete=True,
        )
        partial = score_factors({"trend": 100.0}, critical_data_complete=True)
        self.assertAlmostEqual(complete.reachable_max, 100.0)
        # Missing factors keep their weight and shrink toward neutral, so a
        # perfect observed trend at partial coverage cannot read as a perfect
        # asset — but its normalized score can (separate space, next file).
        self.assertLess(partial.reachable_max, complete.reachable_max)
        self.assertAlmostEqual(partial.score, partial.reachable_max)


if __name__ == "__main__":
    unittest.main()
