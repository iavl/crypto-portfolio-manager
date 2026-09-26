"""ETH/BTC relative-alpha signals and evaluation (Strategy V2.2 Phase B).

The signal layer is point-in-time by construction: everything a signal sees
at ``as_of`` is frozen completed-candle data, and the ranking-power
evaluation separates decision inputs from forward labels.
"""

import unittest
from datetime import datetime, timedelta, timezone

from crypto_portfolio.engine.eth_relative_alpha import (
    ETH_ALPHA_NEGATIVE,
    ETH_ALPHA_NEUTRAL,
    ETH_ALPHA_POSITIVE,
    eth_alpha_state,
    ethbtc_ratio_series,
    eth_tilt_fraction,
    evaluate_relative_signals,
    evaluate_signal_admission,
    etf_flow_differential,
    relative_signal_observations,
    relative_signals,
    spearman,
)
from crypto_portfolio.models.market import Candle, OHLCVSeries

UTC = timezone.utc


def _daily_series(symbol: str, closes: list[float], start: datetime) -> OHLCVSeries:
    candles = []
    for index, close in enumerate(closes):
        candles.append(Candle(
            timestamp=(start + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
            open=close, high=close, low=close, close=close, volume=1.0,
        ))
    return OHLCVSeries(symbol, "1D", tuple(candles), "fixture", start.isoformat())


class RatioSeriesTests(unittest.TestCase):
    def test_ratio_joins_only_shared_completed_dates(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        eth = _daily_series("ETH", [2.0, 4.0, 8.0], start)
        btc = _daily_series("BTC", [1.0, 2.0], start)
        points = ethbtc_ratio_series(eth, btc)
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[0].ratio, 2.0)
        self.assertAlmostEqual(points[1].ratio, 2.0)

    def test_ratio_requires_matching_timeframes(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        btc_hourly = OHLCVSeries(
            "BTC", "1H",
            (Candle(timestamp=start.isoformat().replace("+00:00", "Z"),
                    open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0),),
            "fixture", start.isoformat(),
        )
        with self.assertRaisesRegex(ValueError, "timeframe"):
            ethbtc_ratio_series(_daily_series("ETH", [2.0], start), btc_hourly)


class RelativeSignalsTests(unittest.TestCase):
    def _rising_ratio(self, days: int = 400):
        start = datetime(2022, 1, 1, tzinfo=UTC)
        closes = [0.010 * (1.005 ** index) for index in range(days)]
        return ethbtc_ratio_series(_daily_series("ETH", closes, start), _daily_series("BTC", [1.0] * days, start))

    def test_uptrend_signals_are_positive_and_complete(self):
        points = self._rising_ratio()
        as_of = points[-1].timestamp
        signals = relative_signals(points, as_of)
        self.assertGreater(signals["rel_return_30d"], 0.0)
        self.assertGreater(signals["rel_return_90d"], 0.0)
        self.assertGreater(signals["rel_return_180d"], 0.0)
        self.assertAlmostEqual(signals["ma_structure"], 1.0)
        self.assertGreater(signals["momentum_persistence_90d"], 0.5)
        self.assertGreater(signals["risk_adjusted_momentum_90d"], 0.0)
        self.assertAlmostEqual(signals["relative_drawdown_180d"], 0.0)

    def test_downtrend_signals_are_negative(self):
        start = datetime(2022, 1, 1, tzinfo=UTC)
        closes = [0.050 * (0.995 ** index) for index in range(400)]
        points = ethbtc_ratio_series(_daily_series("ETH", closes, start), _daily_series("BTC", [1.0] * 400, start))
        signals = relative_signals(points, points[-1].timestamp)
        self.assertLess(signals["rel_return_90d"], 0.0)
        self.assertAlmostEqual(signals["ma_structure"], -1.0)
        self.assertLess(signals["relative_drawdown_180d"], 0.0)

    def test_insufficient_lookback_leaves_signals_missing(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        points = ethbtc_ratio_series(_daily_series("ETH", [1.0] * 30, start), _daily_series("BTC", [1.0] * 30, start))
        signals = relative_signals(points, points[-1].timestamp)
        self.assertIsNone(signals["rel_return_90d"])
        self.assertIsNone(signals["ma_structure"])

    def test_signals_never_see_candles_after_as_of(self):
        points = self._rising_ratio()
        early = points[200].timestamp
        early_signals = relative_signals(points, early)
        # Truncating the tail of the series must not change the early read.
        truncated = relative_signals(points[:201], early)
        self.assertEqual(early_signals, truncated)

    def test_etf_flow_differential_combines_normalized_ratios(self):
        self.assertAlmostEqual(etf_flow_differential(0.02, 0.01), 0.01)
        self.assertIsNone(etf_flow_differential(None, 0.01))
        self.assertIsNone(etf_flow_differential(0.02, None))


class AlphaStateTests(unittest.TestCase):
    def test_agreeing_trend_and_structure_is_positive(self):
        state = eth_alpha_state({
            "rel_return_30d": 0.05, "rel_return_90d": 0.04, "rel_return_180d": 0.02,
            "ma_structure": 1.0,
        })
        self.assertEqual(state, ETH_ALPHA_POSITIVE)

    def test_structural_disagreement_stays_neutral(self):
        state = eth_alpha_state({
            "rel_return_30d": 0.05, "rel_return_90d": 0.04, "rel_return_180d": 0.02,
            "ma_structure": -1.0,
        })
        self.assertEqual(state, ETH_ALPHA_NEUTRAL)

    def test_below_threshold_stays_neutral(self):
        state = eth_alpha_state({
            "rel_return_30d": 0.01, "rel_return_90d": 0.01, "rel_return_180d": 0.01,
            "ma_structure": 1.0,
        })
        self.assertEqual(state, ETH_ALPHA_NEUTRAL)

    def test_mirror_rule_is_negative(self):
        state = eth_alpha_state({
            "rel_return_30d": -0.05, "rel_return_90d": -0.04, "rel_return_180d": -0.02,
            "ma_structure": -1.0,
        })
        self.assertEqual(state, ETH_ALPHA_NEGATIVE)

    def test_missing_inputs_fail_defensive_neutral(self):
        self.assertEqual(eth_alpha_state({}), ETH_ALPHA_NEUTRAL)
        self.assertEqual(
            eth_alpha_state({
                "rel_return_30d": 0.9, "rel_return_90d": 0.9, "rel_return_180d": 0.9,
                "ma_structure": None,
            }),
            ETH_ALPHA_NEUTRAL,
        )

    def test_tilt_fraction_is_preregistered(self):
        from crypto_portfolio.models.policy import load_policy
        policy = load_policy()
        self.assertAlmostEqual(eth_tilt_fraction(ETH_ALPHA_POSITIVE, policy), 0.2)
        self.assertEqual(eth_tilt_fraction(ETH_ALPHA_NEUTRAL, policy), 0.0)
        self.assertEqual(eth_tilt_fraction(ETH_ALPHA_NEGATIVE, policy), 0.0)
        with self.assertRaisesRegex(ValueError, "alpha_state"):
            eth_tilt_fraction("ETH_ALPHA_MASSIVE", policy)


class EvaluationTests(unittest.TestCase):
    def _observations(self, count: int = 40):
        start = datetime(2023, 1, 1, tzinfo=UTC)
        closes = [1.0]
        for index in range(count + 200):
            # Signal-friendly path: every 40 days the ratio steps up.
            step = 1.02 if (index // 40) % 2 == 0 else 0.99
            closes.append(closes[-1] * step)
        points = ethbtc_ratio_series(
            _daily_series("ETH", closes, start), _daily_series("BTC", [1.0] * len(closes), start),
        )
        moments = [points[100 + index * 3].timestamp for index in range(count)]
        return relative_signal_observations(points, moments, horizons=(30, 90))

    def test_perfectly_ranking_signal_has_positive_ic_and_monotone_buckets(self):
        rows = self._observations()
        # Replace one signal with the forward label itself: IC must be 1.0.
        for row in rows:
            label = row["labels"].get("90") or {}
            if label.get("status") == "AVAILABLE":
                row["signals"]["rel_return_90d"] = label["forward_ethbtc_return"]
        report = evaluate_relative_signals(rows, horizons=(30, 90))
        entry = report["signals"]["rel_return_90d"]["90"]
        self.assertAlmostEqual(entry["spearman_ic"], 1.0)
        self.assertTrue(entry["bucket_monotonic"])
        self.assertEqual(entry["independent_blocks"] > 0, True)

    def test_pending_labels_are_excluded(self):
        rows = self._observations(count=5)
        report = evaluate_relative_signals(rows, horizons=(180,))
        entry = report["signals"]["rel_return_30d"]["180"]
        self.assertEqual(entry["samples"], 0)
        self.assertIsNone(entry["spearman_ic"])

    def test_spearman_handles_ties_and_small_samples(self):
        self.assertIsNone(spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]))
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)

    def test_admission_requires_consistent_windows(self):
        def window(ic: float, blocks: int = 40):
            return {"signals": {"rel_return_90d": {
                "90": {"spearman_ic": ic, "independent_blocks": blocks,
                       "bucket_mean_forward_returns": [0.1, 0.2, 0.3]},
                "180": {"spearman_ic": ic / 2, "independent_blocks": blocks,
                        "bucket_mean_forward_returns": [0.1, 0.2, 0.3]},
            }}}
        admitted = evaluate_signal_admission({
            "bear": window(0.3), "recent": window(0.2),
        })
        self.assertTrue(admitted["signals"]["rel_return_90d"]["admitted"])
        self.assertTrue(admitted["eth_tilt_admitted"])
        rejected = evaluate_signal_admission({
            "bear": window(0.3), "recent": window(-0.2),
        })
        self.assertFalse(rejected["signals"]["rel_return_90d"]["admitted"])
        self.assertFalse(rejected["eth_tilt_admitted"])
        thin = evaluate_signal_admission({
            "bear": window(0.3, blocks=3), "recent": window(0.3, blocks=3),
        })
        self.assertFalse(thin["signals"]["rel_return_90d"]["admitted"])

    def test_admission_rejects_inverted_buckets(self):
        def window():
            return {"signals": {"rel_return_90d": {
                "90": {"spearman_ic": 0.3, "independent_blocks": 40,
                       "bucket_mean_forward_returns": [0.3, 0.2, 0.1]},
                "180": {"spearman_ic": 0.3, "independent_blocks": 40,
                        "bucket_mean_forward_returns": [0.3, 0.2, 0.1]},
            }}}
        report = evaluate_signal_admission({"bear": window(), "recent": window()})
        self.assertFalse(report["signals"]["rel_return_90d"]["admitted"])


if __name__ == "__main__":
    unittest.main()
