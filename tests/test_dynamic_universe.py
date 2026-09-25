"""Dynamic-universe membership in replay and validation (Strategy V2 Phase 6.3)."""

import unittest
from datetime import date, timedelta

from crypto_portfolio.models.market import Candle, OHLCVSeries
from crypto_portfolio.models.policy import load_policy, policy_from_mapping
from crypto_portfolio.research.historical_builder import build_historical_reviews


def _series(symbol, count, start=date(2023, 1, 1), price=100.0, volume=5_000.0):
    candles = []
    for index in range(count):
        close = price + index * 0.1
        candles.append(Candle(
            (start + timedelta(days=index)).isoformat() + "T00:00:00Z",
            close - 0.05, close + 0.1, close - 0.1, close, volume,
        ))
    return OHLCVSeries(symbol, "1D", tuple(candles), "synthetic", "2026-01-01T00:00:00Z")


def _reviews(series_by_symbol, policy, **kwargs):
    symbols = list(series_by_symbol)
    return build_historical_reviews(
        daily_by_symbol=series_by_symbol,
        execution_by_symbol=series_by_symbol,
        execution_timeframe="1D",
        symbols=symbols,
        initial_weights={"USD": 1.0},
        initial_value=100_000.0,
        start_at=kwargs.pop("start_at"),
        end_at=kwargs.pop("end_at"),
        policy=policy,
    )


def _policy(**universe):
    import json
    data = json.loads(json.dumps(load_policy().as_dict()))
    data["dynamic_universe"].update(universe)
    return policy_from_mapping(data)


class DynamicUniverseMembershipTests(unittest.TestCase):
    def test_policy_declares_membership_rules(self):
        config = load_policy().dynamic_universe
        self.assertTrue(config["enabled"])
        self.assertEqual(config["minimum_history_days"], 365)
        self.assertEqual(config["minimum_median_volume_usd"], 1_000_000.0)

    def test_short_history_asset_is_not_a_member_early_on(self):
        # SOL has 400 days of history ending 2024-02-04; a window starting
        # 2024-01-01 gives it only ~366 trailing days... use a tighter
        # configured threshold so the boundary arithmetic is unambiguous.
        policy = _policy(minimum_history_days=200, minimum_median_volume_usd=0)
        series = {
            "BTC": _series("BTC", 420),
            "SOL": _series("SOL", 400),
        }
        # The first boundary at/after start uses data before it: SOL has
        # 2023-01-01..2023-12-31 minus warmup... with 400 candles starting
        # 2023-01-01 and a window starting 2023-05-01, SOL has ~120 trailing
        # days: below the 200-day rule, so it is not a member yet.
        reviews = _reviews(
            series, policy,
            start_at="2023-05-01T00:00:00Z", end_at="2023-05-03T00:00:00Z",
        )
        first = reviews[0]
        self.assertIn("BTC", first.assessments)
        self.assertNotIn("SOL", first.assessments)
        # Prices and returns keep flowing for the non-member.
        self.assertIn("SOL", first.current_prices)
        self.assertIn("SOL", first.next_returns)

    def test_membership_opens_once_history_qualifies(self):
        policy = _policy(minimum_history_days=200, minimum_median_volume_usd=0)
        series = {
            "BTC": _series("BTC", 420),
            "SOL": _series("SOL", 400),
        }
        # By 2023-08-10 SOL has 222 trailing days: a member again.
        reviews = _reviews(
            series, policy,
            start_at="2023-08-10T00:00:00Z", end_at="2023-08-12T00:00:00Z",
        )
        self.assertIn("SOL", reviews[0].assessments)

    def test_thin_liquidity_blocks_membership(self):
        policy = _policy(minimum_history_days=100, minimum_median_volume_usd=1_000_000.0)
        series = {
            "BTC": _series("BTC", 420, volume=50_000.0),
            "SOL": _series("SOL", 400, volume=100.0),  # ~$10k/day: thin
        }
        reviews = _reviews(
            series, policy,
            start_at="2023-08-01T00:00:00Z", end_at="2023-08-03T00:00:00Z",
        )
        self.assertNotIn("SOL", reviews[0].assessments)

    def test_disabled_membership_restores_full_participation(self):
        # Same data that the liquidity rule would exclude; with the gate
        # off, every listed symbol participates as before Phase 6.
        policy = _policy(enabled=False, minimum_history_days=365, minimum_median_volume_usd=10**9)
        series = {
            "BTC": _series("BTC", 420, volume=50_000.0),
            "SOL": _series("SOL", 400, volume=100.0),
        }
        reviews = _reviews(
            series, policy,
            start_at="2023-08-01T00:00:00Z", end_at="2023-08-03T00:00:00Z",
        )
        self.assertIn("SOL", reviews[0].assessments)


if __name__ == "__main__":
    unittest.main()
