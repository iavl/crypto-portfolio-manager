import math
import unittest
from datetime import date, timedelta

from crypto_portfolio.engine.technical import (
    average_true_range,
    build_structural_zones,
    build_technical_snapshot,
    completed_candles,
    detect_swings,
    history_position,
    lookback_return,
    realized_volatility,
    relative_volume,
    true_ranges,
    volume_moving_average,
)
from crypto_portfolio.models.market import Candle, OHLCVSeries


def make_series(count=365, *, last_volume=100, source="synthetic"):
    start = date(2025, 1, 1)
    candles = []
    for index in range(count):
        close = 100 + index * 0.5
        if index == 330:
            close -= 20
        candles.append(
            Candle(
                (start + timedelta(days=index)).isoformat(),
                close - 0.5,
                close + 2,
                close - 2,
                close,
                last_volume if index == count - 1 else 100,
            )
        )
    return OHLCVSeries("ETH", "1D", tuple(candles), source=source)


class MarketModelTests(unittest.TestCase):
    def test_candle_validation_and_normalization(self):
        candle = Candle("2026-01-01T08:00:00+08:00", 100, 110, 90, 105, 20)
        self.assertEqual(candle.timestamp, "2026-01-01T00:00:00Z")
        for kwargs in (
            {"high": 99},
            {"low": 106},
            {"open": 0},
            {"volume": -1},
            {"close": math.nan},
            {"close": math.inf},
        ):
            with self.subTest(kwargs=kwargs):
                values = {"open": 100, "high": 110, "low": 90, "close": 105, "volume": 20}
                values.update(kwargs)
                with self.assertRaises(ValueError):
                    Candle("2026-01-01", **values)
        with self.assertRaises(ValueError):
            Candle("2026-01-01T00:00:00", 100, 110, 90, 105, 20)

    def test_series_rejects_duplicate_and_unordered_timestamps(self):
        first = Candle("2026-01-01", 100, 110, 90, 105, 20)
        duplicate = Candle("2026-01-01T00:00:00Z", 100, 110, 90, 105, 20)
        earlier = Candle("2025-12-31", 100, 110, 90, 105, 20)
        with self.assertRaises(ValueError):
            OHLCVSeries("ETH", "1D", (first, duplicate))
        with self.assertRaises(ValueError):
            OHLCVSeries("ETH", "1D", (first, earlier))

    def test_completed_candle_filter_excludes_current_and_explicit_incomplete(self):
        candles = tuple(
            Candle(
                f"2026-01-0{index}",
                100,
                110,
                90,
                105,
                20,
                completed=index != 3,
            )
            for index in range(1, 4)
        )
        series = OHLCVSeries("ETH", "1D", candles)
        self.assertEqual(len(series.completed_candles()), 2)
        self.assertEqual(
            len(completed_candles(series, as_of="2026-01-03T12:00:00Z")),
            2,
        )


class TechnicalMetricTests(unittest.TestCase):
    def test_hand_checkable_metrics(self):
        candles = (
            Candle("2026-01-01", 100, 110, 90, 100, 1),
            Candle("2026-01-02", 100, 120, 95, 115, 2),
            Candle("2026-01-03", 115, 130, 110, 125, 3),
        )
        self.assertEqual(true_ranges(candles), [20.0, 25.0, 20.0])
        self.assertAlmostEqual(average_true_range(candles, 2), 22.5)
        self.assertAlmostEqual(lookback_return([100, 110, 121], 2), 0.21)
        self.assertIsNone(lookback_return([100, 110], 2))
        high, distance, drawdown = history_position([100, 120, 110], 90)
        self.assertEqual(high, 120)
        self.assertAlmostEqual(distance, -0.25)
        self.assertAlmostEqual(drawdown, -0.25)
        self.assertAlmostEqual(volume_moving_average(list(range(1, 22)), 20), 10.5)
        self.assertAlmostEqual(relative_volume(list(range(1, 22)), 20), 2.0)
        self.assertIsNotNone(realized_volatility([100 + index for index in range(32)], 30))

    def test_ma_coverage_and_missing_volume(self):
        full = build_technical_snapshot(make_series(), 282)
        self.assertIsNotNone(full.ma20)
        self.assertIsNotNone(full.ma50)
        self.assertIsNotNone(full.ma100)
        self.assertIsNotNone(full.ma200)
        self.assertEqual(full.data_quality, "FULL")
        self.assertEqual(full.technical_confidence, "HIGH")
        missing_volume = build_technical_snapshot(make_series(last_volume=100), 282, volume_reliable=False)
        self.assertIsNone(missing_volume.relative_volume)
        self.assertEqual(missing_volume.volume_state, "UNKNOWN")
        self.assertEqual(missing_volume.technical_confidence, "MEDIUM")
        short = build_technical_snapshot(make_series(119), 160)
        self.assertIsNone(short.ma200)
        self.assertEqual(short.data_quality, "INSUFFICIENT_HISTORY")
        self.assertEqual(short.technical_confidence, "LOW")

    def test_swings_and_zones_are_confirmed_without_future_candles(self):
        series = make_series()
        cutoff = "2025-11-28T12:00:00Z"
        live = detect_swings(series.candles, swing_window=5, as_of=cutoff)
        truncated = tuple(candle for candle in series.candles if candle.timestamp < "2025-11-28T00:00:00Z")
        replay = detect_swings(truncated, swing_window=5)
        self.assertEqual(live, replay)
        live_snapshot = build_technical_snapshot(series, 240, as_of=cutoff)
        replay_snapshot = build_technical_snapshot(
            OHLCVSeries("ETH", "1D", truncated, source="synthetic"),
            240,
        )
        self.assertEqual(live_snapshot.ohlcv_hash, replay_snapshot.ohlcv_hash)
        self.assertEqual(live_snapshot.support_zones, replay_snapshot.support_zones)

    def test_obvious_swing_and_atr_confluence(self):
        start = date(2026, 1, 1)
        lows = [100, 99, 98, 110, 90, 110, 98, 99, 100]
        candles = tuple(
            Candle(
                (start + timedelta(days=index)).isoformat(),
                low + 2,
                low + 5,
                low,
                low + 3,
                100,
            )
            for index, low in enumerate(lows)
        )
        swings = detect_swings(candles, swing_window=2)
        self.assertTrue(any(point.kind == "LOW" and point.price == 90 for point in swings))
        narrow = build_structural_zones(
            120,
            10,
            moving_averages={"MA50": 100, "MA100": 104},
            swing_points=(),
            zone_half_width_atr=0.25,
            minimum_zone_separation_atr=0.75,
        )
        self.assertEqual(len(narrow), 1)
        self.assertEqual(set(narrow[0].sources), {"MA50", "MA100"})
        wide = build_structural_zones(
            120,
            20,
            moving_averages={"MA50": 100},
            swing_points=(),
            zone_half_width_atr=0.25,
            minimum_zone_separation_atr=0.75,
        )
        self.assertGreater(wide[0].high - wide[0].low, narrow[0].high - narrow[0].low)

    def test_stale_metadata_is_explicit(self):
        stale = build_technical_snapshot(
            OHLCVSeries("ETH", "1D", make_series().candles, source="synthetic", fetched_at="2025-01-01"),
            282,
        )
        self.assertEqual(stale.data_quality, "STALE")
        self.assertEqual(stale.technical_confidence, "LOW")


if __name__ == "__main__":
    unittest.main()
