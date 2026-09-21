"""A semantic factor judgment must carry its declared evidence confidence.

Valuation, fundamentals, on-chain, BTC valuation, and macro/liquidity have no
deterministic scorer: their 0-100 value is a bounded semantic judgment.  That
judgment states a ``confidence`` because it is not backed by a provider, and
until this contract existed the declaration was silently ignored - a LOW
judgment scored exactly like a corroborated HIGH one, on factors that carry
40% (BTC), 45% (ETH), or 55% (AAVE) of the base score.

The judgment confidence is a source-quality statement, so it feeds the same
``reliability`` multiplier every other factor uses; it is not a second,
parallel confidence scale.
"""

import unittest

from crypto_portfolio.engine.scoring import score_factors
from crypto_portfolio.models.evidence import FactorScore
from crypto_portfolio.models.factor_packet import (
    JUDGMENT_SOURCE_QUALITY,
    FactorJudgment,
)

NEUTRAL = {
    "trend": 55.0,
    "valuation": 50.0,
    "onchain": 50.0,
    "capital_flows": 50.0,
    "relative_strength_btc": 50.0,
}


def judgment(confidence, score=85.0):
    return FactorJudgment(
        factor="fundamentals",
        score=score,
        confidence=confidence,
        trend="STABLE",
        summary="bounded judgment",
    )


def judgment_mapping(confidence, score=85.0):
    return {
        "factor": "fundamentals",
        "score": score,
        "confidence": confidence,
        "supporting_evidence_ids": ("EV-1",),
        "contrary_evidence_ids": (),
        "trend": "STABLE",
    }


class JudgmentConfidenceContractTests(unittest.TestCase):
    def test_mapping_matches_the_documented_source_quality_scale(self):
        self.assertEqual(JUDGMENT_SOURCE_QUALITY["HIGH"], 1.0)
        self.assertEqual(JUDGMENT_SOURCE_QUALITY["MEDIUM"], 0.75)
        self.assertEqual(JUDGMENT_SOURCE_QUALITY["LOW"], 0.5)
        for confidence, expected in JUDGMENT_SOURCE_QUALITY.items():
            with self.subTest(confidence=confidence):
                self.assertEqual(judgment(confidence).reliability, expected)

    def test_typed_judgment_confidence_reaches_the_score(self):
        results = {
            confidence: score_factors(
                {**NEUTRAL, "fundamentals": judgment(confidence)}, symbol="SOL"
            )
            for confidence in ("HIGH", "MEDIUM", "LOW")
        }
        self.assertAlmostEqual(results["HIGH"].score, 58.50, places=9)
        self.assertAlmostEqual(results["MEDIUM"].score, 56.75, places=9)
        self.assertAlmostEqual(results["LOW"].score, 55.00, places=9)
        self.assertAlmostEqual(results["HIGH"].factor_reliability["fundamentals"], 1.00)
        self.assertAlmostEqual(results["MEDIUM"].factor_reliability["fundamentals"], 0.75)
        self.assertAlmostEqual(results["LOW"].factor_reliability["fundamentals"], 0.50)

    def test_low_confidence_also_reduces_coverage(self):
        high = score_factors({**NEUTRAL, "fundamentals": judgment("HIGH")}, symbol="SOL")
        low = score_factors({**NEUTRAL, "fundamentals": judgment("LOW")}, symbol="SOL")
        self.assertAlmostEqual(high.coverage, 1.00, places=9)
        self.assertAlmostEqual(low.coverage, 0.90, places=9)
        self.assertLess(low.score, high.score)

    def test_mapping_judgment_is_read_the_same_way(self):
        for confidence in ("HIGH", "MEDIUM", "LOW"):
            with self.subTest(confidence=confidence):
                typed = score_factors({**NEUTRAL, "fundamentals": judgment(confidence)}, symbol="SOL")
                mapping = score_factors(
                    {**NEUTRAL, "fundamentals": judgment_mapping(confidence)}, symbol="SOL"
                )
                self.assertAlmostEqual(typed.score, mapping.score, places=9)
                self.assertAlmostEqual(typed.coverage, mapping.coverage, places=9)

    def test_identical_score_with_identical_confidence_is_reproducible(self):
        first = score_factors({**NEUTRAL, "fundamentals": judgment("MEDIUM")}, symbol="SOL")
        second = score_factors({**NEUTRAL, "fundamentals": judgment("MEDIUM")}, symbol="SOL")
        self.assertEqual(first.as_dict(), second.as_dict())

    def test_a_declared_confidence_cannot_be_raised_by_a_claim(self):
        """Explicit reliability may lower, never raise, the judged value."""
        raised = FactorScore("fundamentals", 85.0, reliability=1.0, source_quality=1.0)
        judged = judgment("LOW")
        self.assertLess(judged.reliability, raised.reliability)

    def test_result_payload_confidence_is_not_treated_as_a_judgment(self):
        """Factor *result* payloads carry a data-confidence band, not a judgment.

        Their own ``confidence`` field must keep unit reliability: it
        describes the data behind a calculation, and re-reading it here would
        shrink corroborated deterministic evidence by accident.
        """
        for confidence in ("HIGH", "MEDIUM", "LOW"):
            payload = {
                "score": 85.0,
                "confidence": confidence,
                "coverage": 1.0,
                "facts": {"coverage": 1.0, "freshness": "CURRENT", "source_ids": ()},
            }
            with self.subTest(confidence=confidence):
                result = score_factors(
                    {**NEUTRAL, "capital_flows": payload}, symbol="SOL"
                )
                self.assertAlmostEqual(result.factor_reliability["capital_flows"], 1.0, places=9)

    def test_inputs_without_a_judgment_contract_keep_unit_reliability(self):
        for label, value in (("bare number", 85.0), ("score mapping", {"score": 85.0})):
            with self.subTest(label=label):
                result = score_factors(
                    {**NEUTRAL, "fundamentals": value}, symbol="SOL"
                )
                self.assertAlmostEqual(result.factor_reliability["fundamentals"], 1.0, places=9)
                self.assertAlmostEqual(result.score, 58.50, places=9)

    def test_invalid_judgment_confidence_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            FactorJudgment(
                factor="fundamentals", score=85.0, confidence="UNKNOWN",
                trend="STABLE", summary="x",
            )


if __name__ == "__main__":
    unittest.main()
