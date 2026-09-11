"""Regression coverage: factor/evidence lineage must never cross-bind."""

import unittest

from crypto_portfolio.models.decision import Decision, validate_factor_evidence_binding
from crypto_portfolio.models.evidence import AssetAssessment, Evidence, FactorScore


def _evidence(evidence_id, factor, asset="ETH"):
    return Evidence(
        evidence_id,
        asset,
        factor,
        "fixture",
        "2026-09-11T00:00:00Z",
        "2026-09-11T00:00:00Z",
        "CURRENT",
        "HIGH",
        value=1.0,
    )


class FactorEvidenceBindingTests(unittest.TestCase):
    def test_fundamentals_and_onchain_evidence_stay_on_their_own_factor(self):
        fundamentals_evidence = _evidence("F1", "fundamentals")
        onchain_evidence = _evidence("O1", "onchain")
        factor_scores = {
            "fundamentals": FactorScore("fundamentals", 60.0, ("F1",)),
            "onchain": FactorScore("onchain", 55.0, ("O1",)),
        }
        by_id = {item.id: item for item in (fundamentals_evidence, onchain_evidence)}
        validate_factor_evidence_binding(factor_scores, by_id, symbol="ETH")

    def test_cross_factor_binding_is_rejected(self):
        by_id = {"O1": _evidence("O1", "onchain")}
        factor_scores = {
            "fundamentals": FactorScore("fundamentals", 60.0, ("O1",)),
        }
        with self.assertRaises(ValueError):
            validate_factor_evidence_binding(factor_scores, by_id, symbol="ETH")

    def test_cross_asset_binding_is_rejected(self):
        by_id = {"B1": _evidence("B1", "trend", asset="BTC")}
        with self.assertRaises(ValueError):
            validate_factor_evidence_binding(
                {"trend": FactorScore("trend", 50.0, ("B1",))}, by_id, symbol="ETH"
            )

    def test_missing_evidence_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_factor_evidence_binding(
                {"trend": FactorScore("trend", 50.0, ("MISSING",))}, {}, symbol="ETH"
            )

    def test_factor_key_mismatch_is_rejected(self):
        by_id = {"T1": _evidence("T1", "valuation")}
        with self.assertRaises(ValueError):
            validate_factor_evidence_binding(
                {"trend": FactorScore("valuation", 50.0, ("T1",))}, by_id, symbol="ETH"
            )


class DecisionPersistGateTests(unittest.TestCase):
    def _decision(self, factor_scores, evidence):
        return Decision(
            timestamp="2026-09-11T00:00:00Z",
            market_regime="NORMAL",
            current_weights={"BTC": 0.5, "USDT": 0.5},
            target_weights={"BTC": 0.5, "USDT": 0.5},
            evidence=evidence,
            factor_scores={"ETH": AssetAssessment("ETH", factor_scores)},
        )

    def test_valid_binding_survives_serialization_round_trip(self):
        evidence = (_evidence("F1", "fundamentals"), _evidence("O1", "onchain"))
        factor_scores = {
            "fundamentals": FactorScore("fundamentals", 60.0, ("F1",)),
            "onchain": FactorScore("onchain", 55.0, ("O1",)),
        }
        decision = self._decision(factor_scores, evidence)
        restored = Decision.from_mapping(decision.as_dict())
        self.assertEqual(
            restored.factor_scores["ETH"].factor_scores["fundamentals"].evidence_ids, ("F1",)
        )
        self.assertEqual(
            restored.factor_scores["ETH"].factor_scores["onchain"].evidence_ids, ("O1",)
        )

    def test_swapped_evidence_assignment_is_rejected_at_persist(self):
        evidence = (_evidence("F1", "fundamentals"), _evidence("O1", "onchain"))
        factor_scores = {
            "fundamentals": FactorScore("fundamentals", 60.0, ("O1",)),
            "onchain": FactorScore("onchain", 55.0, ("F1",)),
        }
        with self.assertRaises(ValueError):
            self._decision(factor_scores, evidence)


if __name__ == "__main__":
    unittest.main()
