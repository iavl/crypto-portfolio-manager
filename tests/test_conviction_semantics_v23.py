"""Strategy V2.3 Phase 0: FULL_CONVICTION requires a strong structural score.

Structural data availability is not structural bullishness. The preregistered
thresholds (both 67, reused from the satellite entry score without tuning)
make FULL_CONVICTION a genuinely dual-strong state; a strong market with a
weak/neutral/missing structural case stays TACTICAL_ONLY, and a weak market
is WATCH_ONLY regardless of structure.
"""

import unittest

from crypto_portfolio.engine.scoring_v3 import (
    asset_conviction_state,
    conviction_state,
)
from crypto_portfolio.models.policy import PolicyError, load_policy, policy_from_mapping


MARKET_STRONG = {"normalized_score": 80.0, "available": True}
MARKET_WEAK = {"normalized_score": 55.0, "available": True}
STRUCTURAL_STRONG = {"normalized_score": 72.0, "available": True}
STRUCTURAL_WEAK = {"normalized_score": 35.0, "available": True}
STRUCTURAL_MISSING = {"normalized_score": None, "available": False}


def _state(market, structural, hard_risk=False):
    return conviction_state(
        market=market, structural=structural,
        evidence_class="ACTIONABLE", hard_risk=hard_risk,
        market_entry_score=67, structural_conviction_score=67,
    )


class PureSemanticsTests(unittest.TestCase):
    def test_market_high_structural_high_is_full_conviction(self):
        self.assertEqual(_state(MARKET_STRONG, STRUCTURAL_STRONG), "FULL_CONVICTION")

    def test_market_high_structural_low_is_tactical_only(self):
        # The V2.2 bug this phase fixes: availability used to imply strength.
        self.assertEqual(_state(MARKET_STRONG, STRUCTURAL_WEAK), "TACTICAL_ONLY")

    def test_market_high_structural_missing_is_tactical_only(self):
        self.assertEqual(_state(MARKET_STRONG, STRUCTURAL_MISSING), "TACTICAL_ONLY")

    def test_structural_boundary_is_inclusive(self):
        boundary = {"normalized_score": 67.0, "available": True}
        self.assertEqual(_state(MARKET_STRONG, boundary), "FULL_CONVICTION")
        below = {"normalized_score": 66.999, "available": True}
        self.assertEqual(_state(MARKET_STRONG, below), "TACTICAL_ONLY")

    def test_market_boundary_is_inclusive(self):
        boundary = {"normalized_score": 67.0, "available": True}
        self.assertEqual(_state(boundary, STRUCTURAL_STRONG), "FULL_CONVICTION")

    def test_market_low_structural_high_is_watch_only(self):
        self.assertEqual(_state(MARKET_WEAK, STRUCTURAL_STRONG), "WATCH_ONLY")

    def test_hard_risk_is_hard_exit_regardless(self):
        self.assertEqual(
            _state(MARKET_STRONG, STRUCTURAL_STRONG, hard_risk=True), "HARD_EXIT",
        )


class AssetAttributionTests(unittest.TestCase):
    def _factors(self, structural_score):
        return {
            "trend": {"score": 85, "availability": "AVAILABLE", "reliability": 1.0},
            "relative_strength_btc": {"score": 80, "availability": "AVAILABLE", "reliability": 1.0},
            "capital_flows": {"score": 75, "availability": "AVAILABLE", "reliability": 1.0},
            "valuation": {"score": structural_score, "availability": "AVAILABLE", "reliability": 1.0},
            "fundamentals": {"score": structural_score, "availability": "AVAILABLE", "reliability": 1.0},
            "onchain": {"score": structural_score, "availability": "AVAILABLE", "reliability": 1.0},
        }

    def _assessment(self, structural_score):
        return {
            "factor_scores": self._factors(structural_score),
            "weighted_score": 60,
            "normalized_score": 60,
            "critical_data_complete": True,
            "score_coverage": 1.0,
            "relative_strength_vs_btc": "OUTPERFORM",
        }

    def test_weak_structural_score_blocks_full_conviction(self):
        policy = load_policy()
        block = asset_conviction_state(
            self._assessment(40), policy=policy, symbol="SOL",
        )
        self.assertEqual(block["conviction_state"], "TACTICAL_ONLY")
        self.assertFalse(block["structural_strong"])
        self.assertIsNotNone(block["normalized_structural_score"])

    def test_strong_structural_score_reaches_full_conviction(self):
        policy = load_policy()
        block = asset_conviction_state(
            self._assessment(75), policy=policy, symbol="SOL",
        )
        self.assertEqual(block["conviction_state"], "FULL_CONVICTION")
        self.assertTrue(block["structural_strong"])
        self.assertEqual(block["market_entry_score"], 67.0)
        self.assertEqual(block["structural_conviction_score"], 67.0)

    def test_thresholds_come_from_the_policy_block(self):
        policy = load_policy()
        raw = policy.as_dict()
        raw["scoring_v3"]["conviction"] = {
            "market_entry_threshold": 60,
            "structural_conviction_threshold": 70,
        }
        adapted = policy_from_mapping(raw)
        mid_structural = asset_conviction_state(
            self._assessment(65), policy=adapted, symbol="SOL",
        )
        self.assertEqual(mid_structural["conviction_state"], "TACTICAL_ONLY")
        strong_structural = asset_conviction_state(
            self._assessment(80), policy=adapted, symbol="SOL",
        )
        self.assertEqual(strong_structural["conviction_state"], "FULL_CONVICTION")


class PolicyContractTests(unittest.TestCase):
    def test_canonical_policy_defines_the_conviction_block(self):
        conviction = load_policy().scoring_v3["conviction"]
        self.assertEqual(conviction["market_entry_threshold"], 67.0)
        self.assertEqual(conviction["structural_conviction_threshold"], 67.0)

    def test_unknown_conviction_fields_are_rejected(self):
        raw = load_policy().as_dict()
        raw["scoring_v3"]["conviction"] = {"market_entry_threshold": 67}
        with self.assertRaises(PolicyError):
            policy_from_mapping(raw)

    def test_missing_conviction_block_is_rejected(self):
        raw = load_policy().as_dict()
        del raw["scoring_v3"]["conviction"]
        with self.assertRaises(PolicyError):
            policy_from_mapping(raw)


if __name__ == "__main__":
    unittest.main()
