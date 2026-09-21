"""Trend-factor level calibration regression tests.

The trend factor is built as ``base_score + signed contributions``, so the
no-information reading must sit on its 50 base. These tests pin that level,
not just the differences and monotonicity the recalibration tests already
cover: a factor that is internally consistent but offset by +22 points
invisibly shifts every consumer's 0-100 threshold band.

Only ``tests/test_strategy_recalibration.py`` asserted trend behaviour before
this file, and it asserts differences (``ma50_up - ma50_down == 16``) and
monotonicity (``> 50``). Neither detects a uniform level shift.
"""

import unittest

from crypto_portfolio.engine.factors.trend import calculate_trend_factor
from crypto_portfolio.models.execution import PriceZone
from crypto_portfolio.models.market import TechnicalSnapshot


def snapshot(**values):
    defaults = {
        "symbol": "ETH",
        "as_of": "2026-09-20T00:00:00Z",
        "current_spot_price": 100,
        "last_completed_close": 100,
        "history_days": 365,
        "ma20": None,
        "ma50": None,
        "ma100": None,
        "ma200": None,
        "return_30d": None,
        "return_90d": None,
        "return_180d": None,
        "atr14": None,
        "atr_percent": None,
        "relative_volume": None,
        "support_zones": (),
        "volume_state": "UNKNOWN",
        "trend_state": "NEUTRAL",
        "volume_profile_hash": None,
        "market_data_fresh": True,
        "history_sufficient": True,
        "data_quality_flags": (),
        "data_confidence": "HIGH",
    }
    defaults.update(values)
    return TechnicalSnapshot(**defaults)


def flat_market(**values):
    """Price exactly on every moving average with zero momentum."""
    baseline = {
        "ma20": 100,
        "ma50": 100,
        "ma100": 100,
        "ma200": 100,
        "return_30d": 0,
        "return_90d": 0,
        "return_180d": 0,
    }
    baseline.update(values)
    return snapshot(**baseline)


SUPPORT_ZONE = (PriceZone(99, 101, kind="SUPPORT", strength=60.0),)


class TrendNeutralCalibrationTests(unittest.TestCase):
    def test_zero_information_reads_neutral_50(self):
        """Price on every MA, zero momentum, no structure, no volume edge."""
        for volume_state in ("NEUTRAL", "UNKNOWN"):
            with self.subTest(volume_state=volume_state):
                result = calculate_trend_factor(flat_market(volume_state=volume_state))
                self.assertAlmostEqual(result.score, 50.0, places=9)

    def test_exact_tie_applies_no_directional_authority(self):
        """A price exactly on a moving average carries no trend information.

        Only MA50 authority is varied, so the score delta is exactly the
        configured MA50 authority in each direction.
        """
        neutral = calculate_trend_factor(flat_market()).score
        below = calculate_trend_factor(flat_market(ma50=101)).score
        above = calculate_trend_factor(flat_market(ma50=99)).score
        self.assertAlmostEqual(neutral, 50.0, places=9)
        self.assertAlmostEqual(neutral - below, 8.0, places=9)
        self.assertAlmostEqual(above - neutral, 8.0, places=9)
        self.assertAlmostEqual(above - below, 16.0, places=9)

    def test_moving_average_authority_is_symmetric(self):
        """Above-all and below-all must be equidistant from the neutral base."""
        all_above = calculate_trend_factor(flat_market(ma20=99, ma50=99, ma100=99, ma200=99))
        all_below = calculate_trend_factor(flat_market(ma20=101, ma50=101, ma100=101, ma200=101))
        self.assertAlmostEqual(all_above.score - 50.0, 50.0 - all_below.score, places=9)
        self.assertAlmostEqual(all_above.score - all_below.score, 44.0, places=9)

    def test_reasons_state_the_tie_explicitly(self):
        result = calculate_trend_factor(flat_market())
        self.assertTrue(
            any("exactly at MA20" in reason for reason in result.reasons),
            result.reasons,
        )
        self.assertFalse(any("price is above" in reason for reason in result.reasons))

    def test_structural_bonuses_are_the_only_remaining_offset(self):
        """Confirmed support and supportive volume stay explicit, additive inputs.

        They are directional readings rather than neutral ones, so the
        no-information invariant above deliberately excludes them.
        """
        without = calculate_trend_factor(flat_market(volume_state="NEUTRAL")).score
        zone_only = calculate_trend_factor(
            flat_market(volume_state="NEUTRAL", support_zones=SUPPORT_ZONE)
        ).score
        zone_and_volume = calculate_trend_factor(
            flat_market(volume_state="SUPPORTIVE", support_zones=SUPPORT_ZONE)
        ).score
        self.assertAlmostEqual(without, 50.0, places=9)
        self.assertAlmostEqual(zone_only - without, 4.0, places=9)
        self.assertAlmostEqual(zone_and_volume - zone_only, 5.0, places=9)

    def test_neutral_reading_keeps_full_coverage(self):
        """The level fix must not be paid for with trend coverage."""
        result = calculate_trend_factor(flat_market(volume_state="NEUTRAL"))
        self.assertAlmostEqual(result.coverage, 1.0, places=9)


if __name__ == "__main__":
    unittest.main()
