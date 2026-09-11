"""Regression coverage for the 2026-09-11 missing-latest-daily-candle incident."""

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from crypto_portfolio.engine.technical import (
    completed_candles,
    daily_candle_freshness,
    expected_latest_completed_date,
)
from crypto_portfolio.models.market import Candle, OHLCVSeries
from crypto_portfolio.providers.binance import BinanceProvider
from crypto_portfolio.providers.cache import (
    ProviderCache,
    cached_series_is_complete_for_as_of,
    merge_ohlcv_series,
)
from crypto_portfolio.providers.base import ProviderRequest
from crypto_portfolio.providers.router import ProviderRouter


AS_OF = "2026-09-11T02:27:00Z"
EXPECTED_LATEST = "2026-09-10"


def _epoch(day: str) -> int:
    return int(datetime.fromisoformat(f"{day}T00:00:00+00:00").timestamp() * 1000)


def _kline_row(day: str) -> list:
    open_ms = _epoch(day)
    return [open_ms, "100", "102", "99", "101", "10", open_ms + 86399999]


def _candle(day: str, *, completed: bool = True) -> Candle:
    return Candle(
        f"{day}T00:00:00Z",
        100.0,
        102.0,
        99.0,
        101.0,
        10.0,
        completed=completed,
    )


def _series(*days: str) -> OHLCVSeries:
    return OHLCVSeries(
        "BTC",
        "1D",
        tuple(_candle(day) for day in days),
        source="binance",
        venue="BINANCE",
        market="spot",
        quote_currency="USDT",
    )


def _ohlcv_request() -> ProviderRequest:
    return ProviderRequest(
        provider="binance",
        dataset="ohlcv",
        asset="BTC",
        parameters={
            "symbol": "BTC",
            "market": "spot",
            "quote_currency": "USDT",
            "timeframe": "1D",
        },
        metric_keys=("market.return_30d",),
        mutable=False,
    )


def _config():
    return {
        "providers": {"binance": {"enabled": True}},
        "cache_ttl_seconds": {"default": 3600, "spot": 600},
        "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
        "fallback": {"allow_web": False},
    }


class _StubCandleProvider:
    """Provider whose candles() always returns a series through 2026-09-10."""

    def __init__(self):
        self.calls = 0

    def candles(self, symbol, *, timeframe="1D", start=None, end=None):
        self.calls += 1
        return _series("2026-09-08", "2026-09-09", "2026-09-10")

    def observations_from_ohlcv(self, series, metric_keys, *, as_of=None):
        from crypto_portfolio.providers.binance import observations_from_ohlcv

        return observations_from_ohlcv(series, metric_keys, as_of=as_of)


class ExpectedLatestCompletedDateTests(unittest.TestCase):
    def test_expected_latest_completed_utc_candle_is_previous_utc_day(self):
        self.assertEqual(
            expected_latest_completed_date("2026-09-11T02:27:00Z").isoformat(),
            EXPECTED_LATEST,
        )

    def test_expected_latest_completed_date_rolls_back_at_new_utc_day(self):
        self.assertEqual(
            expected_latest_completed_date("2026-09-11T00:00:00Z").isoformat(),
            "2026-09-10",
        )

    def test_case_a_incomplete_current_day_is_excluded_completed_09_10_is_kept(self):
        candles = (
            _candle("2026-09-09"),
            _candle("2026-09-10"),
            _candle("2026-09-11", completed=False),
        )
        series = OHLCVSeries(
            "BTC", "1D", candles,
            source="binance", venue="BINANCE", market="spot", quote_currency="USDT",
        )
        completed = completed_candles(series, as_of=AS_OF)
        self.assertEqual(len(completed), 2)
        self.assertEqual(completed[-1].timestamp, "2026-09-10T00:00:00Z")

    def test_case_b_provider_stops_at_09_09_reports_lag_one(self):
        report = daily_candle_freshness(_series("2026-09-08", "2026-09-09"), as_of=AS_OF, maximum_lag_days=1)
        self.assertEqual(report["expected_latest_completed_date"], EXPECTED_LATEST)
        self.assertEqual(report["actual_latest_completed_date"], "2026-09-09")
        self.assertEqual(report["lag_days"], 1)
        self.assertEqual(report["status"], "CURRENT")

    def test_case_b_stale_when_lag_exceeds_policy(self):
        report = daily_candle_freshness(_series("2026-09-08", "2026-09-09"), as_of=AS_OF, maximum_lag_days=0)
        self.assertEqual(report["lag_days"], 1)
        self.assertEqual(report["status"], "STALE")

    def test_case_c_just_past_utc_midnight_only_closed_candle_enters(self):
        series = _series("2026-09-09", "2026-09-10")
        completed = completed_candles(series, as_of="2026-09-11T00:00:01Z")
        self.assertEqual([candle.timestamp for candle in completed][-1], "2026-09-10T00:00:00Z")

    def test_case_d_historical_as_of_never_uses_future_candles(self):
        series = _series("2026-09-08", "2026-09-09", "2026-09-10")
        completed = completed_candles(series, as_of="2026-09-09T12:00:00Z")
        self.assertEqual([candle.timestamp for candle in completed][-1], "2026-09-08T00:00:00Z")

    def test_freshness_rejects_invalid_maximum_lag(self):
        with self.assertRaises(ValueError):
            daily_candle_freshness(_series("2026-09-10"), as_of=AS_OF, maximum_lag_days=-1)
        with self.assertRaises(ValueError):
            daily_candle_freshness(_series("2026-09-10"), as_of=AS_OF, maximum_lag_days=True)


class BinanceProviderCompletionTests(unittest.TestCase):
    def test_kline_completion_uses_close_timestamp_against_provider_clock(self):
        class FakeClient:
            def get_json(self, url, *, params=None, headers=None):
                # 09-09 closed, 09-10 closed, 09-11 still open at fetch time.
                return [
                    _kline_row("2026-09-09"),
                    _kline_row("2026-09-10"),
                    _kline_row("2026-09-11"),
                ]

        provider = BinanceProvider(
            client=FakeClient(),
            clock=lambda: datetime(2026, 9, 11, 2, 27, tzinfo=timezone.utc),
        )
        candles = provider.candles("BTC").candles
        self.assertEqual([candle.timestamp for candle in candles][-1], "2026-09-11T00:00:00Z")
        self.assertFalse(candles[-1].completed)
        self.assertTrue(candles[-2].completed)
        self.assertEqual(candles[-2].timestamp, "2026-09-10T00:00:00Z")


class CacheTailFreshnessTests(unittest.TestCase):
    def test_cached_series_missing_latest_completed_candle_is_incomplete(self):
        stale = _series("2026-09-08", "2026-09-09")
        self.assertFalse(
            cached_series_is_complete_for_as_of(stale, as_of=AS_OF, maximum_lag_days=0)
        )
        self.assertTrue(
            cached_series_is_complete_for_as_of(stale, as_of=AS_OF, maximum_lag_days=1)
        )

    def test_cached_series_with_latest_completed_candle_is_complete(self):
        self.assertTrue(
            cached_series_is_complete_for_as_of(
                _series("2026-09-08", "2026-09-09", "2026-09-10"), as_of=AS_OF, maximum_lag_days=0
            )
        )

    def test_cached_series_falls_back_to_fetched_at(self):
        stale = _series("2026-09-08", "2026-09-09")
        series = OHLCVSeries(
            "BTC", "1D", stale.candles,
            source="binance", fetched_at="2026-09-11T02:27:00Z",
            venue="BINANCE", market="spot", quote_currency="USDT",
        )
        self.assertFalse(cached_series_is_complete_for_as_of(series, as_of=None, maximum_lag_days=0))

    def test_router_refreshes_cache_whose_tail_misses_latest_completed_candle(self):
        provider = _StubCandleProvider()
        with TemporaryDirectory() as directory:
            router = ProviderRouter(
                {"binance": provider},
                config=_config(),
                cache=ProviderCache(Path(directory) / "cache"),
            )
            router.cache.store_series(
                _series("2026-09-08", "2026-09-09"),
                provider="binance", market="spot", quote_currency="USDT",
            )
            result = router.collect((_ohlcv_request(),), mode="AUTO", as_of=AS_OF, now=AS_OF)
            self.assertGreaterEqual(provider.calls, 1)
            self.assertEqual(result.provider_cache_hits, 0)
            merged = router.cache.load_series("binance", "BTC", "1D", market="spot", quote_currency="USDT")
            self.assertEqual(merged.candles[-1].timestamp, "2026-09-10T00:00:00Z")

    def test_router_serves_cache_when_tail_covers_expected_candle(self):
        provider = _StubCandleProvider()
        with TemporaryDirectory() as directory:
            router = ProviderRouter(
                {"binance": provider},
                config=_config(),
                cache=ProviderCache(Path(directory) / "cache"),
            )
            router.cache.store_series(
                _series("2026-09-08", "2026-09-09", "2026-09-10"),
                provider="binance", market="spot", quote_currency="USDT",
            )
            result = router.collect((_ohlcv_request(),), mode="AUTO", as_of=AS_OF, now=AS_OF)
            self.assertEqual(provider.calls, 0)
            self.assertEqual(result.provider_cache_hits, 1)

    def test_merge_fills_missing_tail_candle(self):
        merged = merge_ohlcv_series(_series("2026-09-09"), _series("2026-09-09", "2026-09-10"))
        self.assertEqual(len(merged.candles), 2)
        self.assertEqual(merged.candles[-1].timestamp, "2026-09-10T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
