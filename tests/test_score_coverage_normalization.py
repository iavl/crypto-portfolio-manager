"""Coverage-normalized score semantics (Strategy V2 Phase 2)."""

import unittest

from crypto_portfolio.engine.scoring import (
    coverage_normalized_score,
    score_assessment,
    score_factors,
)
from crypto_portfolio.models.evidence import AssetAssessment


class CoverageNormalizedScoreTests(unittest.TestCase):
    def test_full_coverage_normalized_equals_effective(self):
        result = coverage_normalized_score(64.0, 1.0, minimum_coverage=0.6)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["normalized"], 64.0)

    def test_plan_example_partial_coverage_amplifies_to_the_same_reading(self):
        # Plan 2.1 example: raw 64 at coverage 0.4 normalizes to 85.
        result = coverage_normalized_score(64.0, 0.4, minimum_coverage=0.4)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["normalized"], 85.0)

    def test_maximum_reachable_effective_score_normalizes_to_one_hundred(self):
        from crypto_portfolio.engine.scoring import score_reachability

        coverage = 0.4
        maximum = score_reachability(coverage)["reachable_max"]
        result = coverage_normalized_score(maximum, coverage, minimum_coverage=0.4)
        self.assertAlmostEqual(result["normalized"], 100.0, places=6)

    def test_minimum_reachable_effective_score_normalizes_to_zero(self):
        from crypto_portfolio.engine.scoring import score_reachability

        coverage = 0.4
        minimum = score_reachability(coverage)["reachable_min"]
        result = coverage_normalized_score(minimum, coverage, minimum_coverage=0.4)
        self.assertAlmostEqual(result["normalized"], 0.0, places=6)

    def test_below_minimum_coverage_is_unavailable_not_amplified(self):
        result = coverage_normalized_score(90.0, 0.2, minimum_coverage=0.6)
        self.assertIsNone(result["normalized"])
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "BELOW_MINIMUM_NORMALIZATION_COVERAGE")

    def test_clipping_never_leaves_the_zero_to_hundred_scale(self):
        result = coverage_normalized_score(100.0, 0.6, minimum_coverage=0.6)
        self.assertAlmostEqual(result["normalized"], 100.0)
        weak = coverage_normalized_score(0.0, 0.6, minimum_coverage=0.6)
        self.assertAlmostEqual(weak["normalized"], 0.0)

    def test_neutral_effective_score_stays_neutral_at_any_coverage(self):
        for coverage in (0.4, 0.7, 1.0):
            result = coverage_normalized_score(50.0, coverage, minimum_coverage=0.4)
            self.assertAlmostEqual(result["normalized"], 50.0)


class ScoredAssessmentCarriesAllSpacesTests(unittest.TestCase):
    def _assessment(self, factors):
        return score_assessment(AssetAssessment(symbol="SOL", factor_scores=factors))[0]

    def test_scored_assessment_exposes_normalized_and_effective(self):
        assessment, result = score_assessment(AssetAssessment(
            symbol="SOL",
            factor_scores={"trend": 90.0, "valuation": 80.0, "fundamentals": 70.0},
        ))
        self.assertIsNotNone(assessment.weighted_score)
        self.assertIsNotNone(assessment.normalized_score)
        expected = 50.0 + (result.score - 50.0) / result.coverage
        self.assertAlmostEqual(assessment.normalized_score, min(100.0, expected))
        # The persisted mapping round-trips both spaces.
        restored = AssetAssessment.from_mapping("SOL", assessment.as_dict())
        self.assertAlmostEqual(restored.normalized_score, assessment.normalized_score)

    def test_raw_factor_scores_are_preserved_for_diagnostics(self):
        result = score_factors(
            {"trend": 82.0, "valuation": 61.0, "fundamentals": None},
            critical_data_complete=True,
        )
        raw = result.raw_factor_scores
        self.assertAlmostEqual(raw["trend"], 82.0)
        self.assertAlmostEqual(raw["valuation"], 61.0)
        self.assertIsNone(raw["fundamentals"])

    def test_low_coverage_assessment_has_no_normalized_score(self):
        assessment = self._assessment({"trend": 95.0})
        # Coverage is the single observed factor's weight; below the minimum
        # normalization floor the field must be absent, not fabricated.
        self.assertLess(assessment.score_coverage, 0.6)
        self.assertIsNone(assessment.normalized_score)

    def test_invalid_normalized_score_is_rejected(self):
        with self.assertRaises(ValueError):
            AssetAssessment(symbol="SOL", factor_scores={}, normalized_score=150.0)


if __name__ == "__main__":
    unittest.main()
