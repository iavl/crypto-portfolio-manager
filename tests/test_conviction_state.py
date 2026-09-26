"""Conviction states (Strategy V2.1 Phase C).

FULL_CONVICTION / TACTICAL_ONLY / WATCH_ONLY / NO_NEW_RISK / HARD_EXIT combine
the market and structural families with the evidence-permission contract and
the hard risk gates.
"""

import unittest

from crypto_portfolio.engine.scoring_v3 import (
    asset_conviction_state,
    conviction_state,
)
from crypto_portfolio.models.policy import load_policy


def _factors(**overrides):
    base = {
        "trend": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
        "relative_strength_btc": {"score": 80, "availability": "AVAILABLE", "reliability": 1.0},
        "capital_flows": {"score": 75, "availability": "AVAILABLE", "reliability": 1.0},
        "valuation": {"score": 70, "availability": "AVAILABLE", "reliability": 1.0},
        "fundamentals": {"score": 72, "availability": "AVAILABLE", "reliability": 1.0},
        "onchain": {"score": 68, "availability": "AVAILABLE", "reliability": 1.0},
    }
    for key, value in overrides.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def _assessment(factors, **fields):
    structural_present = {"valuation", "fundamentals", "onchain"} & set(factors)
    return {
        "factor_scores": factors,
        "weighted_score": 60,
        "normalized_score": 60 if structural_present else None,
        "critical_data_complete": fields.pop("critical_data_complete", True),
        "score_coverage": fields.pop("score_coverage", 1.0),
        "relative_strength_vs_btc": fields.pop("relative_strength_vs_btc", "OUTPERFORM"),
        **fields,
    }


class PureConvictionStateTests(unittest.TestCase):
    MARKET = {"normalized_score": 80.0, "available": True}
    NO_MARKET = {"normalized_score": None, "available": False}
    STRUCTURAL = {"normalized_score": 70.0, "available": True}
    NO_STRUCTURAL = {"normalized_score": None, "available": False}

    def test_case_a_market_strong_structural_missing_is_tactical_only(self):
        self.assertEqual(
            conviction_state(
                market=self.MARKET, structural=self.NO_STRUCTURAL,
                evidence_class="ACTIONABLE", hard_risk=False,
                market_entry_score=67, structural_conviction_score=67,
            ),
            "TACTICAL_ONLY",
        )

    def test_case_b_market_and_structural_strong_is_full_conviction(self):
        self.assertEqual(
            conviction_state(
                market=self.MARKET, structural=self.STRUCTURAL,
                evidence_class="ACTIONABLE", hard_risk=False,
                market_entry_score=67, structural_conviction_score=67,
            ),
            "FULL_CONVICTION",
        )

    def test_weak_market_is_watch_only_even_with_structure(self):
        self.assertEqual(
            conviction_state(
                market=self.NO_MARKET, structural=self.STRUCTURAL,
                evidence_class="ACTIONABLE", hard_risk=False,
                market_entry_score=67, structural_conviction_score=67,
            ),
            "WATCH_ONLY",
        )

    def test_not_actionable_evidence_is_no_new_risk(self):
        self.assertEqual(
            conviction_state(
                market=self.MARKET, structural=self.STRUCTURAL,
                evidence_class="NOT_ACTIONABLE", hard_risk=False,
                market_entry_score=67, structural_conviction_score=67,
            ),
            "NO_NEW_RISK",
        )

    def test_hard_exit_overrides_everything(self):
        self.assertEqual(
            conviction_state(
                market=self.MARKET, structural=self.STRUCTURAL,
                evidence_class="ACTIONABLE", hard_risk=True,
                market_entry_score=67, structural_conviction_score=67,
            ),
            "HARD_EXIT",
        )

    def test_limited_evidence_still_permits_conviction(self):
        # LIMITED is a permission level, never a conviction killer.
        self.assertEqual(
            conviction_state(
                market=self.MARKET, structural=self.STRUCTURAL,
                evidence_class="LIMITED", hard_risk=False,
                market_entry_score=67, structural_conviction_score=67,
            ),
            "FULL_CONVICTION",
        )

    def test_entry_boundary_is_inclusive(self):
        boundary = {"normalized_score": 67.0, "available": True}
        self.assertEqual(
            conviction_state(
                market=boundary, structural=self.STRUCTURAL,
                evidence_class="ACTIONABLE", hard_risk=False,
                market_entry_score=67, structural_conviction_score=67,
            ),
            "FULL_CONVICTION",
        )

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "evidence_class"):
            conviction_state(
                market=self.MARKET, structural=self.STRUCTURAL,
                evidence_class="LOW", hard_risk=False,
                market_entry_score=67, structural_conviction_score=67,
            )


class AssetConvictionTests(unittest.TestCase):
    def test_full_conviction_from_complete_factors(self):
        block = asset_conviction_state(
            _assessment(_factors()), policy=load_policy(), symbol="SOL"
        )
        self.assertEqual(block["conviction_state"], "FULL_CONVICTION")
        self.assertAlmostEqual(block["normalized_market_score"], 81.0)
        self.assertTrue(block["normalized_structural_score"] is not None)
        self.assertEqual(block["comparison_score_space"], "NORMALIZED")

    def test_case_a_structural_missing_downgrades_to_tactical(self):
        factors = _factors(valuation=None, fundamentals=None, onchain=None)
        block = asset_conviction_state(
            _assessment(factors), policy=load_policy(), symbol="SOL"
        )
        self.assertEqual(block["conviction_state"], "TACTICAL_ONLY")
        self.assertIsNone(block["normalized_structural_score"])
        # The composite stays a diagnostic: 55% coverage is below the 60%
        # normalization gate, so no normalized composite score exists.
        self.assertEqual(block["comparison_score_space"], "EFFECTIVE_DIAGNOSTIC_ONLY")

    def test_thesis_broken_is_hard_exit(self):
        block = asset_conviction_state(
            _assessment(_factors(), thesis_broken=True), policy=load_policy(), symbol="SOL"
        )
        self.assertEqual(block["conviction_state"], "HARD_EXIT")

    def test_severe_event_is_hard_exit(self):
        block = asset_conviction_state(
            _assessment(_factors(), event_risk={"state": "SEVERE"}),
            policy=load_policy(), symbol="SOL",
        )
        self.assertEqual(block["conviction_state"], "HARD_EXIT")

    def test_coverage_below_investable_is_no_new_risk(self):
        block = asset_conviction_state(
            _assessment(_factors(), score_coverage=0.5), policy=load_policy(), symbol="SOL"
        )
        self.assertEqual(block["conviction_state"], "NO_NEW_RISK")
        self.assertEqual(block["evidence_class"], "NOT_ACTIONABLE")

    def test_weak_market_is_watch_only(self):
        factors = _factors(
            trend={"score": 40, "availability": "AVAILABLE", "reliability": 1.0},
            relative_strength_btc={"score": 45, "availability": "AVAILABLE", "reliability": 1.0},
        )
        block = asset_conviction_state(
            _assessment(factors), policy=load_policy(), symbol="SOL"
        )
        self.assertEqual(block["conviction_state"], "WATCH_ONLY")


if __name__ == "__main__":
    unittest.main()
