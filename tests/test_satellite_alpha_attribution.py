"""Position-weighted satellite attribution (V2.2 Phase C, plans 6.4-6.6).

The portfolio-level contribution uses the exposure the book carried into
each period (quantities evolved only by the replay's own trades), so
partial exits shrink the attribution and multiple entries each count.
"""

import unittest
from datetime import timezone

from crypto_portfolio.research.opportunity_cost import satellite_alpha_attribution
from tests.test_opportunity_cost_attribution import (
    _PRICES,
    _rows_and_reviews,
)

UTC = timezone.utc


class SatelliteAlphaAttributionTests(unittest.TestCase):
    def test_position_weighted_contribution_follows_the_real_path(self):
        rows, reviews = _rows_and_reviews()
        report = satellite_alpha_attribution(
            rows, reviews, satellite_symbols=["SOL"], prices=_PRICES,
            horizons=(30,),
        )
        sol = report["assets"]["SOL"]
        # Quantities: 10 initial +5 buy = 15 into t1/t2; 15-2 sold = 13 into t3.
        # t0: the initial 10% sleeve earns SOL +10% vs BTC +2%.
        expected_t0 = 0.10 * (0.10 - 0.02)
        # t1 weight: 15*11/1020; t2 returns SOL +20% vs BTC +10%.
        w_t1 = 15 * 11.0 / 1020.0
        expected_t2 = w_t1 * (0.20 - 0.10)
        # t3 weight: 13*13.2/1116; SOL +5% vs BTC flat.
        w_t3 = 13 * 13.2 / 1116.0
        expected_t3 = w_t3 * (0.05 - 0.0)
        self.assertAlmostEqual(
            sol["position_weighted_contribution"], expected_t0 + expected_t2 + expected_t3,
            places=9,
        )
        self.assertAlmostEqual(
            report["total_satellite_alpha_contribution"],
            expected_t0 + expected_t2 + expected_t3, places=9,
        )
        # BTC foregone: the same weights earning the BTC return instead.
        self.assertAlmostEqual(
            report["btc_foregone_return"], 0.10 * 0.02 + w_t1 * 0.10 + w_t3 * 0.0,
            places=9,
        )

    def test_partial_exit_shrinks_the_attribution_base(self):
        rows, reviews = _rows_and_reviews()
        with_sale = satellite_alpha_attribution(
            rows, reviews, satellite_symbols=["SOL"], prices=_PRICES, horizons=(30,),
        )
        # Remove the partial exit from the record: t3's weight must grow.
        rows_no_exit = [dict(row) for row in rows]
        rows_no_exit[2] = {**rows_no_exit[2], "trades": []}
        without_sale = satellite_alpha_attribution(
            rows_no_exit, reviews, satellite_symbols=["SOL"], prices=_PRICES,
            horizons=(30,),
        )
        self.assertGreater(
            without_sale["assets"]["SOL"]["position_weighted_contribution"],
            with_sale["assets"]["SOL"]["position_weighted_contribution"],
        )

    def test_multiple_entries_aggregate_with_win_rate_and_conviction(self):
        rows, reviews = _rows_and_reviews()
        report = satellite_alpha_attribution(
            rows, reviews, satellite_symbols=["SOL"], prices=_PRICES,
            horizons=(30, 90, 180),
        )
        sol = report["assets"]["SOL"]
        self.assertEqual(sol["entries"], 2)
        self.assertAlmostEqual(sol["average_allocated_weight"], (0.05 + 13.2 / 1116.0) / 2)
        excess = (13.86 / 10.0 - 1.0) - (112.7556 / 100.0 - 1.0)
        for horizon in ("30", "90", "180"):
            stats = sol["horizons"][horizon]
            self.assertEqual(stats["samples"], 2)
            self.assertAlmostEqual(stats["mean_excess"], (excess + _second_excess()) / 2, places=9)
            self.assertEqual(stats["win_rate_vs_btc"], 1.0 if excess > 0 and _second_excess() > 0 else 0.5)
        # TACTICAL_ONLY and FULL_CONVICTION entries are counted separately.
        self.assertEqual(
            report["entries_by_conviction"]["TACTICAL_ONLY"]["entries"], 1,
        )
        self.assertEqual(
            report["entries_by_conviction"]["FULL_CONVICTION"]["entries"], 1,
        )

    def test_asset_without_entries_reports_zero_contribution(self):
        rows, reviews = _rows_and_reviews()
        report = satellite_alpha_attribution(
            rows, reviews, satellite_symbols=["SOL", "AAVE"], prices=_PRICES,
            horizons=(30,),
        )
        aave = report["assets"]["AAVE"]
        self.assertEqual(aave["entries"], 0)
        self.assertIsNone(aave["average_allocated_weight"])
        self.assertAlmostEqual(aave["position_weighted_contribution"], 0.0)

    def test_no_lookahead_weights_use_only_past_boundaries(self):
        rows, reviews = _rows_and_reviews()
        # Future labels (returns of later periods) must not change earlier
        # weights: zero out t3's return and the first three weights hold.
        modified = [
            _clone_review(reviews[0]),
            _clone_review(reviews[1]),
            _clone_review(reviews[2]),
            _clone_review(reviews[3], returns={"SOL": 0.0, "BTC": 0.0, "USD": 0.0}),
        ]
        base = satellite_alpha_attribution(
            rows, reviews, satellite_symbols=["SOL"], prices=_PRICES, horizons=(30,),
        )
        changed = satellite_alpha_attribution(
            rows, modified, satellite_symbols=["SOL"], prices=_PRICES, horizons=(30,),
        )
        # Only t3's contribution term changes; earlier terms are identical.
        self.assertAlmostEqual(
            base["assets"]["SOL"]["position_weighted_contribution"]
            - changed["assets"]["SOL"]["position_weighted_contribution"],
            (13 * 13.2 / 1116.0) * 0.05,
            places=9,
        )


def _second_excess():
    return (13.86 / 13.2 - 1.0) - (112.7556 / 112.2 - 1.0)


class _CloneReview:
    def __init__(self, review, returns=None):
        self.as_of = review.as_of
        self.current_weights = review.current_weights
        self.current_prices = review.current_prices
        self.next_returns = returns if returns is not None else review.next_returns
        self.portfolio_value = review.portfolio_value


def _clone_review(review, returns=None):
    return _CloneReview(review, returns=returns)


if __name__ == "__main__":
    unittest.main()
