import math
import unittest

from crypto_portfolio.models.decision import Decision
from crypto_portfolio.models.evidence import AssetAssessment, Evidence, FactorScore
from crypto_portfolio.models.portfolio import Position, PortfolioSnapshot


class DomainModelTests(unittest.TestCase):
    def test_portfolio_position_normalizes_symbol(self):
        position = Position(" btc ", value_usd=100, quantity=2)
        self.assertEqual(position.symbol, "BTC")
        self.assertEqual(position.quantity, 2)

    def test_invalid_financial_numbers_fail(self):
        for value in (float("nan"), float("inf"), -1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    Position("BTC", value_usd=value)
        with self.assertRaises(ValueError):
            Position("BTC", value_usd=1, quantity=-1)
        with self.assertRaises(ValueError):
            Position("BTC", value_usd=1, cost_basis_usd=float("inf"))

    def test_duplicate_positions_fail(self):
        with self.assertRaises(ValueError):
            PortfolioSnapshot(
                "2026-09-01T00:00:00Z",
                positions=(Position("BTC", value_usd=1), Position(" btc ", value_usd=1)),
            )

    def test_evidence_and_factor_score_are_traceable(self):
        evidence = Evidence(
            id="btc-flow-1",
            asset="btc",
            factor="capital_flows",
            source="example",
            observed_at="2026-09-01T00:00:00Z",
            fetched_at="2026-09-01T01:00:00Z",
            freshness="current",
            confidence="high",
            value=123,
        )
        score = FactorScore("capital_flows", 72, (evidence.id,))
        assessment = AssetAssessment(
            "BTC", {"capital_flows": score}, weighted_score=72, confidence="HIGH", asset_type="core"
        )
        self.assertEqual(assessment.as_dict()["factor_scores"]["capital_flows"]["evidence_ids"], ["btc-flow-1"])
        self.assertEqual(evidence.as_dict()["asset"], "BTC")

    def test_thesis_broken_is_typed_and_serialized(self):
        assessment = AssetAssessment("SOL", {}, thesis_broken=True)
        self.assertTrue(assessment.thesis_broken)
        self.assertTrue(assessment.as_dict()["thesis_broken"])

    def test_decision_rejects_invalid_weights_and_preserves_evidence(self):
        evidence = Evidence(
            "e-1", "BTC", "trend", "example", "2026-09-01", "2026-09-01", "CURRENT", "HIGH"
        )
        decision = Decision(
            "2026-09-01T00:00:00Z",
            "NORMAL",
            1,
            {"BTC": 1.0},
            {"BTC": 0.9, "USDC": 0.1},
            evidence=(evidence,),
        )
        result = decision.as_dict()
        self.assertEqual(result["evidence_ids"], ["e-1"])
        self.assertEqual(result["policy_version"], 1)
        with self.assertRaises(ValueError):
            Decision("2026-09-01", "NORMAL", 1, {"BTC": math.nan}, {"BTC": 1.0})

    def test_factor_score_evidence_references_are_integral(self):
        evidence = Evidence(
            "e-1", "BTC", "trend", "example", "2026-09-01", "2026-09-01", "CURRENT", "HIGH"
        )
        with self.assertRaisesRegex(ValueError, "missing evidence"):
            Decision(
                "2026-09-01",
                "NORMAL",
                1,
                {"BTC": 1.0},
                {"BTC": 1.0},
                evidence=(evidence,),
                factor_scores={
                    "BTC": AssetAssessment(
                        "BTC", {"trend": FactorScore("trend", 80, ("missing",))}
                    )
                },
            )
        with self.assertRaisesRegex(ValueError, "wrong asset"):
            Decision(
                "2026-09-01",
                "NORMAL",
                1,
                {"BTC": 1.0},
                {"BTC": 1.0},
                evidence=(Evidence("e-2", "ETH", "trend", "example", "2026-09-01", "2026-09-01", "CURRENT", "HIGH"),),
                factor_scores={
                    "BTC": AssetAssessment(
                        "BTC", {"trend": FactorScore("trend", 80, ("e-2",))}
                    )
                },
            )


if __name__ == "__main__":
    unittest.main()
