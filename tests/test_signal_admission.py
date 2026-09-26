"""Unified admission rule across assets and windows (V2.3 Phase 5)."""

import unittest
from datetime import datetime, timedelta, timezone

from crypto_portfolio.engine.bnb_relative_alpha import (
    bnb_signal_observations,
    bnbbtc_ratio_series,
    evaluate_bnb_admission,
    evaluate_bnb_signals,
)
from crypto_portfolio.engine.eth_relative_alpha import (
    evaluate_signal_admission,
)
from crypto_portfolio.models.market import OHLCVSeries


def _series(symbol, closes, start=datetime(2022, 1, 1, tzinfo=timezone.utc)):
    candles = []
    for index, close in enumerate(closes):
        moment = start + timedelta(days=index)
        candles.append({
            "timestamp": moment.isoformat().replace("+00:00", "Z"),
            "open": close, "high": close, "low": close, "close": close,
            "volume": 10.0, "completed": True,
        })
    return OHLCVSeries.from_mapping({
        "symbol": symbol, "timeframe": "1D", "candles": candles,
        "source": "test", "fetched_at": "2024-01-01T00:00:00Z",
    })


def _moments(count, start, step_days=7):
    return [
        (start + timedelta(days=step_days * index)).isoformat().replace("+00:00", "Z")
        for index in range(count)
    ]


def _window(ic, blocks=40, buckets=(0.1, 0.2, 0.3)):
    return {"signals": {"rel_return_30d": {
        "90": {"spearman_ic": ic, "independent_blocks": blocks,
               "bucket_mean_forward_returns": list(buckets)},
    }}}


class UnifiedAdmissionTests(unittest.TestCase):
    def test_the_90d_horizon_is_the_decision_horizon(self):
        admission = evaluate_bnb_admission({"w1": _window(0.3), "w2": _window(0.2)})
        self.assertIn("90D IC", admission["admission_rule"])
        self.assertTrue(admission["signals"]["rel_return_30d"]["admitted"])
        self.assertTrue(admission["bnb_tilt_admitted"])

    def test_window_disagreement_blocks_admission(self):
        admission = evaluate_bnb_admission({"w1": _window(0.3), "w2": _window(-0.2)})
        self.assertFalse(admission["signals"]["rel_return_30d"]["admitted"])
        self.assertFalse(admission["bnb_tilt_admitted"])
        self.assertIn(
            "window 90D directions disagree or are flat",
            admission["signals"]["rel_return_30d"]["reasons"],
        )

    def test_inverted_terciles_block_admission(self):
        admission = evaluate_bnb_admission({
            "w1": _window(0.3, buckets=(0.3, 0.2, 0.1)),
            "w2": _window(0.3, buckets=(0.3, 0.2, 0.1)),
        })
        self.assertFalse(admission["signals"]["rel_return_30d"]["admitted"])

    def test_thin_samples_block_admission(self):
        admission = evaluate_bnb_admission({
            "w1": _window(0.3, blocks=4), "w2": _window(0.3, blocks=4),
        })
        self.assertFalse(admission["signals"]["rel_return_30d"]["admitted"])
        self.assertTrue(any("blocks" in reason for reason in
                            admission["signals"]["rel_return_30d"]["reasons"]))

    def test_eth_rule_is_the_same_unified_rule(self):
        admission = evaluate_signal_admission({"a": _window(0.25), "b": _window(0.15)})
        self.assertTrue(admission["signals"]["rel_return_30d"]["admitted"])
        self.assertIn("eth_tilt_admitted", admission)
        self.assertEqual(
            admission["admission_rule"],
            "both windows: positive 90D IC, same 90D direction, terciles not "
            "inverted, >=10 independent 90D blocks; 180D reported as confirmation",
        )

    def test_observations_feed_the_evaluation_end_to_end(self):
        bnb = [1.0 * (1.031 ** i) for i in range(400)]
        btc = [1.0 * (1.008 ** i) for i in range(400)]
        points = bnbbtc_ratio_series(_series("BNB", bnb), _series("BTC", btc))
        moments = _moments(50, datetime(2022, 3, 1, tzinfo=timezone.utc))
        rows = bnb_signal_observations(points, moments, horizons=(30, 90, 180))
        evaluation = evaluate_bnb_signals(rows, horizons=(30, 90, 180))
        self.assertEqual(evaluation["contract"], "STRICT_POINT_IN_TIME_INPUTS")
        self.assertGreater(
            evaluation["signals"]["rel_return_30d"]["30"]["samples"], 0,
        )


if __name__ == "__main__":
    unittest.main()
