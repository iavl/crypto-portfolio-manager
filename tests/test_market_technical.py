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
from crypto_portfolio.models.market import Candle, OHLCVSeries, SpotPrice


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
    def test_spot_price_is_timestamped_and_strict(self):
        spot = SpotPrice(" eth ", 100, "2026-01-01T08:00:00+08:00", "exchange")
        self.assertEqual(spot.symbol, "ETH")
        self.assertEqual(spot.observed_at, "2026-01-01T00:00:00Z")
        for kwargs in (
            {"price": 0},
            {"price": math.nan},
            {"observed_at": "2026-01-01T00:00:00"},
            {"source": ""},
            {"fetched_at": "2026-01-01T00:00:00"},
        ):
            values = {
                "symbol": "ETH",
                "price": 100,
                "observed_at": "2026-01-01T00:00:00Z",
                "source": "exchange",
            }
            values.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SpotPrice(**values)

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
        with self.assertRaises(ValueError):
            OHLCVSeries(
                "ETH",
                "1D",
                (first, Candle("2026-01-01T12:00:00Z", 100, 110, 90, 105, 20)),
            )

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

    def test_historical_replay_requires_timestamped_spot(self):
        with self.assertRaises(ValueError):
            build_technical_snapshot(make_series(), 240, as_of="2025-11-28T12:00:00Z")

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

    def test_atr_requires_fourteen_fully_defined_true_ranges(self):
        candles = tuple(
            Candle(
                (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                100,
                105,
                95,
                100,
                1,
            )
            for index in range(15)
        )
        self.assertIsNone(average_true_range(candles[:14], 14))
        self.assertEqual(average_true_range(candles, 14), 10.0)

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
        spot = SpotPrice("ETH", 240, cutoff, "synthetic")
        live = detect_swings(series.candles, swing_window=5, as_of=cutoff)
        truncated = tuple(candle for candle in series.candles if candle.timestamp < "2025-11-28T00:00:00Z")
        replay = detect_swings(truncated, swing_window=5)
        self.assertEqual(live, replay)
        live_snapshot = build_technical_snapshot(series, spot, as_of=cutoff)
        replay_snapshot = build_technical_snapshot(
            OHLCVSeries("ETH", "1D", truncated, source="synthetic"),
            SpotPrice("ETH", 240, "2025-11-28T00:00:00Z", "synthetic"),
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
            OHLCVSeries("ETH", "1D", make_series().candles, source="synthetic", fetched_at="2026-09-02"),
            SpotPrice("ETH", 282, "2026-09-02T12:00:00Z", "synthetic", "2026-09-02T12:00:00Z"),
        )
        self.assertEqual(stale.data_quality, "STALE_MARKET_DATA")
        self.assertIn("STALE_MARKET_DATA", stale.data_quality_flags)
        self.assertEqual(stale.technical_confidence, "LOW")

    def test_fetched_after_historical_as_of_is_allowed(self):
        spot = SpotPrice("ETH", 240, "2025-11-28T12:00:00Z", "exchange", "2026-09-02T08:00:00Z")
        snapshot = build_technical_snapshot(
            OHLCVSeries(
                "ETH",
                "1D",
                make_series().candles,
                source="exchange",
                fetched_at="2026-09-02T08:00:00Z",
            ),
            spot,
            as_of="2025-11-28T12:00:00Z",
        )
        self.assertNotIn("DATA_CONFLICT", snapshot.data_quality_flags)

    def test_live_data_fetched_after_daily_cutoff_is_allowed(self):
        spot = SpotPrice("ETH", 282, "2026-01-01T08:00:00Z", "exchange", "2026-01-01T08:30:00Z")
        snapshot = build_technical_snapshot(
            OHLCVSeries("ETH", "1D", make_series().candles, source="exchange", fetched_at="2026-01-01T08:30:00Z"),
            spot,
            as_of="2026-01-01T08:00:00Z",
        )
        self.assertTrue(snapshot.market_data_fresh)

    def test_calendar_coverage_is_not_candle_count(self):
        candles = []
        start = date(2025, 1, 1)
        for index in range(365):
            day = start + timedelta(days=index * 2)
            candles.append(Candle(day.isoformat(), 100, 102, 98, 100, 100))
        snapshot = build_technical_snapshot(
            OHLCVSeries("ETH", "1D", tuple(candles), source="synthetic"),
            SpotPrice("ETH", 100, "2026-12-31T00:00:00Z", "synthetic"),
        )
        self.assertEqual(snapshot.candle_count, 365)
        self.assertGreater(snapshot.calendar_span_days, 700)
        self.assertLess(snapshot.coverage_ratio, 0.9)
        self.assertEqual(snapshot.data_quality, "INSUFFICIENT_COVERAGE")
        self.assertEqual(snapshot.data_confidence, "LOW")

    def test_small_gap_is_measured_without_being_treated_as_full(self):
        candles = []
        start = date(2025, 1, 1)
        for index in range(365):
            if index == 100:
                continue
            day = start + timedelta(days=index)
            candles.append(Candle(day.isoformat(), 100, 102, 98, 100, 100))
        snapshot = build_technical_snapshot(
            OHLCVSeries("ETH", "1D", tuple(candles), source="synthetic"),
            SpotPrice("ETH", 100, "2026-01-01T00:00:00Z", "synthetic"),
        )
        self.assertEqual(snapshot.missing_day_count, 1)
        self.assertEqual(snapshot.max_gap_days, 1)
        self.assertTrue(snapshot.cadence_valid)
        self.assertNotEqual(snapshot.data_quality, "FULL")
        self.assertEqual(snapshot.data_confidence, "MEDIUM")

    def test_calendar_lookbacks_do_not_use_arbitrary_candle_count(self):
        candles = []
        start = date(2025, 1, 1)
        for index in range(365):
            if index == 334:
                continue
            day = start + timedelta(days=index)
            close = 100 + index
            candles.append(Candle(day.isoformat(), close, close + 2, close - 2, close, 100))
        snapshot = build_technical_snapshot(
            OHLCVSeries("ETH", "1D", tuple(candles), source="synthetic"),
            SpotPrice("ETH", 464, "2026-01-01T00:00:00Z", "synthetic"),
        )
        self.assertTrue(snapshot.cadence_valid)
        self.assertIsNone(snapshot.return_30d)

    def test_large_gap_forces_wait_quality(self):
        candles = []
        start = date(2025, 1, 1)
        for index in range(365):
            if 100 <= index < 150:
                continue
            day = start + timedelta(days=index)
            candles.append(Candle(day.isoformat(), 100, 102, 98, 100, 100))
        snapshot = build_technical_snapshot(
            OHLCVSeries("ETH", "1D", tuple(candles), source="synthetic"),
            SpotPrice("ETH", 100, "2026-01-01T00:00:00Z", "synthetic"),
        )
        self.assertFalse(snapshot.cadence_valid)
        self.assertEqual(snapshot.data_confidence, "LOW")

    def test_provenance_and_spot_gap_are_explicit(self):
        unknown = build_technical_snapshot(
            make_series(source="unknown"),
            SpotPrice("ETH", 282, "2026-01-01T00:00:00Z", "unknown"),
        )
        self.assertNotEqual(unknown.data_confidence, "HIGH")
        mismatch = build_technical_snapshot(
            OHLCVSeries("ETH", "1D", make_series().candles, source="exchange", fetched_at="2026-01-01T00:00:00Z"),
            SpotPrice("ETH", 282, "2026-01-01T00:00:00Z", "other", "2026-01-01T00:00:00Z"),
        )
        self.assertNotEqual(mismatch.data_confidence, "HIGH")
        conflict = build_technical_snapshot(
            make_series(source="exchange"),
            SpotPrice("ETH", 500, "2026-01-01T00:00:00Z", "other", "2026-01-01T00:00:00Z"),
        )
        self.assertEqual(conflict.data_quality, "DATA_CONFLICT")
        self.assertEqual(conflict.data_confidence, "LOW")

    def test_cluster_span_and_positive_price_bounds(self):
        zones = build_structural_zones(
            130,
            10,
            moving_averages={"MA20": 100, "MA50": 106, "MA100": 112},
            maximum_zone_span_atr=1.0,
        )
        self.assertEqual(len(zones), 2)
        self.assertEqual(
            build_structural_zones(10, 10, moving_averages={"MA20": 1}),
            (),
        )


if __name__ == "__main__":
    unittest.main()
