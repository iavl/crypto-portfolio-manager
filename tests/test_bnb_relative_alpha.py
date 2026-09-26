"""BNB/BTC relative-alpha contract (Strategy V2.3 Phase 1)."""

import unittest
from datetime import datetime, timedelta, timezone

from crypto_portfolio.engine.bnb_relative_alpha import (
    BNB_ALPHA_NEGATIVE,
    BNB_ALPHA_NEUTRAL,
    BNB_ALPHA_POSITIVE,
    GROWTH_WINDOWS,
    SIGNAL_NAMES,
    bnb_alpha_state,
    bnb_relative_signals,
    bnb_signal_observations,
    bnbbtc_ratio_series,
    evaluate_bnb_admission,
    evaluate_bnb_signals,
)
from crypto_portfolio.engine.relative_alpha_core import ENSEMBLE_THRESHOLD
from crypto_portfolio.models.market import OHLCVSeries


def _series(symbol: str, closes, start=datetime(2023, 1, 1, tzinfo=timezone.utc)):
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


class _GrowthSeries:
    """Minimal dated-series double with change_over_days(as_of, days)."""

    def __init__(self, values):
        # values maps day-offset -> value; change over N days is computed
        # linearly from the nearest entries at/before the as-of day.
        self.values = values

    def change_over_days(self, as_of, days):
        moment = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        start = datetime(2023, 1, 1, tzinfo=timezone.utc)
        offset = (moment - start).days
        prior = offset - days
        if prior < 0 or offset >= len(self.values):
            return None
        base = self.values[prior]
        if base == 0:
            return None
        return self.values[offset] / base - 1.0


class BnbRelativeAlphaTests(unittest.TestCase):
    def _ratio(self, bnb_closes, btc_closes):
        return bnbbtc_ratio_series(_series("BNB", bnb_closes), _series("BTC", btc_closes))

    def test_ratio_series_joins_completed_candles_only(self):
        points = self._ratio([1.0, 2.0, 3.0], [2.0, 2.0, 2.0])
        self.assertEqual(len(points), 3)
        self.assertAlmostEqual(points[-1].ratio, 1.5)

    def test_price_signals_are_preregistered_and_missing_stays_missing(self):
        points = self._ratio(
            [1.0 + 0.01 * i for i in range(200)], [1.0 for _ in range(200)],
        )
        signals = bnb_relative_signals(points, datetime(2023, 7, 19, tzinfo=timezone.utc))
        for name in SIGNAL_NAMES:
            if name in GROWTH_WINDOWS:
                continue
            if name in ("rel_return_30d", "rel_return_90d", "rel_return_180d"):
                self.assertIsNotNone(signals[name], name)
        short = self._ratio([1.0, 1.1], [1.0, 1.0])
        early = bnb_relative_signals(short, datetime(2023, 1, 2, tzinfo=timezone.utc))
        self.assertIsNone(early["rel_return_90d"])

    def test_growth_signals_read_caller_series_point_in_time(self):
        points = self._ratio([1.0] * 200, [1.0] * 200)
        growth = {
            "chain_tvl": _GrowthSeries([100.0 + i for i in range(200)]),
            "stablecoins": _GrowthSeries([50.0 for _ in range(200)]),
            "fees": None,
        }
        signals = bnb_relative_signals(
            points, datetime(2023, 4, 1, tzinfo=timezone.utc), growth,
        )
        self.assertGreater(signals["chain_tvl_growth_90d"], 0.0)
        self.assertEqual(signals["stablecoin_supply_growth_90d"], 0.0)
        self.assertIsNone(signals["fees_growth_30d"])

    def test_ensemble_state_requires_admitted_signals(self):
        signals = {"rel_return_30d": 0.2, "chain_tvl_growth_90d": 0.3}
        self.assertEqual(bnb_alpha_state(signals, ()), BNB_ALPHA_NEUTRAL)

    def test_ensemble_state_majority_rules(self):
        signals = {
            "rel_return_30d": 0.2, "rel_return_90d": 0.1, "rel_return_180d": 0.05,
        }
        admitted = ("rel_return_30d", "rel_return_90d", "rel_return_180d")
        self.assertEqual(bnb_alpha_state(signals, admitted), BNB_ALPHA_POSITIVE)
        mixed = {"rel_return_30d": 0.2, "rel_return_90d": -0.1, "rel_return_180d": 0.05}
        self.assertEqual(bnb_alpha_state(mixed, admitted), BNB_ALPHA_NEUTRAL)
        bearish = {"rel_return_30d": -0.2, "rel_return_90d": -0.1, "rel_return_180d": -0.05}
        self.assertEqual(bnb_alpha_state(bearish, admitted), BNB_ALPHA_NEGATIVE)

    def test_missing_admitted_signal_votes_neutral(self):
        signals = {"rel_return_30d": 0.2, "rel_return_90d": 0.1}
        admitted = ("rel_return_30d", "rel_return_90d", "rel_return_180d")
        # mean = (1 + 1 + 0) / 3 = 0.667 >= 0.5 still positive; a stronger
        # missing share pulls back to neutral.
        self.assertEqual(bnb_alpha_state(signals, admitted), BNB_ALPHA_POSITIVE)
        weaker = {"rel_return_30d": 0.2}
        self.assertEqual(bnb_alpha_state(weaker, admitted), BNB_ALPHA_NEUTRAL)

    def test_unknown_admitted_signal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside the preregistered BNB"):
            bnb_alpha_state({}, ("not_a_bnb_signal",))

    def test_ensemble_threshold_is_preregistered_at_half_strength(self):
        self.assertEqual(ENSEMBLE_THRESHOLD, 0.5)


class BnbEvaluationTests(unittest.TestCase):
    def test_observations_and_evaluation_cover_every_signal(self):
        bnb = [1.0 * (1.03 ** i) for i in range(220)]
        btc = [1.0 * (1.01 ** i) for i in range(220)]
        points = bnbbtc_ratio_series(
            _series("BNB", bnb), _series("BTC", btc),
        )
        moments = [
            (datetime(2023, 4, 1, tzinfo=timezone.utc) + timedelta(days=7 * i))
            for i in range(20)
        ]
        rows = bnb_signal_observations(points, moments, horizons=(30,))
        self.assertEqual(len(rows), 20)
        evaluation = evaluate_bnb_signals(rows, horizons=(30,))
        self.assertEqual(evaluation["contract"], "STRICT_POINT_IN_TIME_INPUTS")
        self.assertEqual(set(evaluation["signals"]), set(SIGNAL_NAMES))
        self.assertGreater(
            evaluation["signals"]["rel_return_30d"]["30"]["samples"], 0,
        )

    def test_admission_flag_key_is_asset_specific(self):
        bnb = [1.0 * (1.03 ** i) for i in range(220)]
        btc = [1.0 * (1.01 ** i) for i in range(220)]
        points = bnbbtc_ratio_series(_series("BNB", bnb), _series("BTC", btc))
        moments = [
            (datetime(2023, 4, 1, tzinfo=timezone.utc) + timedelta(days=7 * i))
            for i in range(20)
        ]
        evaluation = evaluate_bnb_signals(
            bnb_signal_observations(points, moments, horizons=(30, 90)), horizons=(30, 90),
        )
        admission = evaluate_bnb_admission({"w1": evaluation, "w2": evaluation})
        self.assertIn("bnb_tilt_admitted", admission)
        self.assertIn("signals", admission)


if __name__ == "__main__":
    unittest.main()
