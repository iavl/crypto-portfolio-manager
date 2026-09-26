"""Satellite entry extraction and forward opportunity cost (V2.2 Phase C).

Hand-computed scenario: entries are priced at the first completed daily
close at or after the fill, forward windows only see candles that printed,
and every label is a raw BTC-relative excess return.
"""

import unittest
from datetime import datetime, timedelta, timezone

from crypto_portfolio.research.opportunity_cost import (
    forward_opportunity_cost,
    satellite_entries,
)

UTC = timezone.utc
D0 = datetime(2025, 1, 1, tzinfo=UTC)


def _daily_closes(closes: dict[int, float]):
    """Daily closes on day offsets; tail repeats the last value to +200d."""
    rows = []
    last_day = max(closes)
    last_close = closes[last_day]
    for day in range(0, 201):
        close = closes.get(day, last_close)
        rows.append({
            "timestamp": (D0 + timedelta(days=day)).isoformat().replace("+00:00", "Z"),
            "close": close,
        })
    return rows


# SOL: 10 -> 11 (d1) -> 11 (d2) -> 13.2 (d3) -> 13.86 (d4), flat after.
_SOL = _daily_closes({0: 10.0, 1: 11.0, 2: 11.0, 3: 13.2, 4: 13.86})
# BTC: 100 -> 102 -> 102 -> 112.2 -> 112.7556, flat after.
_BTC = _daily_closes({0: 100.0, 1: 102.0, 2: 102.0, 3: 112.2, 4: 112.7556})

_PRICES = {"SOL": _SOL, "BTC": _BTC}


class _Review:
    def __init__(self, as_of, weights, prices, returns):
        self.as_of = as_of
        self.current_weights = weights
        self.current_prices = prices
        self.next_returns = returns
        self.portfolio_value = 1000.0


def _rows_and_reviews():
    reviews = [
        _Review(D0, {"USD": 0.9, "SOL": 0.10}, {"SOL": 10.0, "BTC": 100.0},
                {"SOL": 0.10, "BTC": 0.02, "USD": 0.0}),
        _Review(D0 + timedelta(days=1), {}, {"SOL": 11.0, "BTC": 102.0},
                {"SOL": 0.0, "BTC": 0.0, "USD": 0.0}),
        _Review(D0 + timedelta(days=2), {}, {"SOL": 11.0, "BTC": 102.0},
                {"SOL": 0.20, "BTC": 0.10, "USD": 0.0}),
        _Review(D0 + timedelta(days=3), {}, {"SOL": 13.2, "BTC": 112.2},
                {"SOL": 0.05, "BTC": 0.0, "USD": 0.0}),
    ]

    def _ts(moment):
        return moment.isoformat().replace("+00:00", "Z")

    rows = [
        {
            "as_of": _ts(reviews[0].as_of), "end_value_usd": 1020.0,
            "allocation": {"deployment_allowances": {
                "SOL": {"conviction_state": "TACTICAL_ONLY"},
            }},
            "trades": [{
                "timestamp": _ts(D0), "symbol": "SOL", "side": "BUY",
                "quantity": 5.0, "execution_price": 10.0,
                "gross_notional_usd": 50.0, "reason": "CONDITIONAL_ENTRY",
            }],
        },
        {"as_of": _ts(reviews[1].as_of), "end_value_usd": 1020.0, "trades": []},
        {
            "as_of": _ts(reviews[2].as_of), "end_value_usd": 1116.0,
            "trades": [{
                "timestamp": _ts(D0 + timedelta(days=2)), "symbol": "SOL",
                "side": "SELL", "quantity": 2.0, "execution_price": 11.0,
                "gross_notional_usd": 22.0, "reason": "REDUCE",
            }],
        },
        {
            "as_of": _ts(reviews[3].as_of), "end_value_usd": 1170.0,
            "allocation": {"deployment_allowances": {
                "SOL": {"conviction_state": "FULL_CONVICTION"},
            }},
            "trades": [{
                "timestamp": _ts(D0 + timedelta(days=3)), "symbol": "SOL",
                "side": "BUY", "quantity": 1.0, "execution_price": 13.2,
                "gross_notional_usd": 13.2, "reason": "CONDITIONAL_ENTRY",
            }],
        },
    ]
    return rows, reviews


class SatelliteEntriesTests(unittest.TestCase):
    def test_buys_only_with_entry_context(self):
        rows, reviews = _rows_and_reviews()
        entries = satellite_entries(
            rows, reviews, satellite_symbols=["SOL"], prices=_PRICES,
        )
        self.assertEqual(len(entries), 2)
        first, second = entries
        self.assertAlmostEqual(first.amount_usd, 50.0)
        # Weight is notional over the period-start portfolio value.
        self.assertAlmostEqual(first.allocated_weight, 0.05)
        self.assertAlmostEqual(first.entry_price_asset, 10.0)
        self.assertAlmostEqual(first.entry_price_btc, 100.0)
        self.assertEqual(first.conviction_state, "TACTICAL_ONLY")
        # Second entry prices against the d3 closes and d2's end value.
        self.assertAlmostEqual(second.amount_usd, 13.2)
        self.assertAlmostEqual(second.allocated_weight, 13.2 / 1116.0)
        self.assertAlmostEqual(second.entry_price_asset, 13.2)
        self.assertAlmostEqual(second.entry_price_btc, 112.2)
        self.assertEqual(second.conviction_state, "FULL_CONVICTION")

    def test_sells_and_other_symbols_are_not_entries(self):
        rows, reviews = _rows_and_reviews()
        entries = satellite_entries(
            rows, reviews, satellite_symbols=["SOL", "AAVE"], prices=_PRICES,
        )
        # The SELL and any non-satellite leg never create entries.
        self.assertEqual([entry.asset for entry in entries], ["SOL", "SOL"])

    def test_fill_without_a_priced_candle_is_rejected(self):
        rows, reviews = _rows_and_reviews()
        # BTC history starts after every fill in the record.
        prices = {"SOL": _SOL, "BTC": _BTC[10:]}
        with self.assertRaisesRegex(ValueError, "priced daily candle"):
            satellite_entries(rows, reviews, satellite_symbols=["SOL"], prices=prices)


class ForwardOpportunityCostTests(unittest.TestCase):
    def test_forward_excess_uses_completed_closes_from_the_fill(self):
        rows, reviews = _rows_and_reviews()
        entries = satellite_entries(
            rows, reviews, satellite_symbols=["SOL"], prices=_PRICES,
        )
        labeled = forward_opportunity_cost(entries, _PRICES, horizons=(30, 90, 180))
        first, second = labeled
        # First entry: SOL 10 -> 13.86, BTC 100 -> 112.7556 (flat tails make
        # every horizon agree).
        for horizon in ("30", "90", "180"):
            label = first["labels"][horizon]
            self.assertEqual(label["status"], "AVAILABLE")
            self.assertAlmostEqual(
                label["relative_alpha_vs_btc"],
                (13.86 / 10.0 - 1.0) - (112.7556 / 100.0 - 1.0),
                places=9,
            )
        # Second entry bases on the d3 closes.
        label = second["labels"]["90"]
        self.assertAlmostEqual(
            label["relative_alpha_vs_btc"],
            (13.86 / 13.2 - 1.0) - (112.7556 / 112.2 - 1.0),
            places=9,
        )

    def test_horizon_without_printed_candles_is_pending(self):
        rows, reviews = _rows_and_reviews()
        entries = satellite_entries(
            rows, reviews, satellite_symbols=["SOL"], prices=_PRICES,
        )
        labeled = forward_opportunity_cost(entries, _PRICES, horizons=(365,))
        self.assertEqual(labeled[0]["labels"]["365"]["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
