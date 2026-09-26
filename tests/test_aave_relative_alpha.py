"""AAVE/BTC relative-alpha contract (Strategy V2.3 Phase 1)."""

import unittest
from datetime import datetime, timedelta, timezone

from crypto_portfolio.engine.aave_relative_alpha import (
    AAVE_ALPHA_NEGATIVE,
    AAVE_ALPHA_NEUTRAL,
    AAVE_ALPHA_POSITIVE,
    GROWTH_WINDOWS,
    SIGNAL_NAMES,
    aave_alpha_state,
    aave_relative_signals,
    aave_signal_observations,
    aavebtc_ratio_series,
    evaluate_aave_admission,
    evaluate_aave_signals,
)
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
    def __init__(self, values):
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


class AaveRelativeAlphaTests(unittest.TestCase):
    def _ratio(self, aave_closes, btc_closes):
        return aavebtc_ratio_series(_series("AAVE", aave_closes), _series("BTC", btc_closes))

    def test_growth_windows_are_preregistered(self):
        self.assertEqual(GROWTH_WINDOWS, {
            "protocol_tvl_growth_90d": ("tvl", 90),
            "protocol_tvl_growth_180d": ("tvl", 180),
            "borrow_growth_90d": ("borrowed", 90),
            "fees_growth_30d": ("fees", 30),
        })
        self.assertEqual(len(SIGNAL_NAMES), 7 + 4)

    def test_growth_signals_read_protocol_series(self):
        points = self._ratio([1.0] * 200, [1.0] * 200)
        growth = {
            "tvl": _GrowthSeries([100.0 * (1.005 ** i) for i in range(200)]),
            "borrowed": _GrowthSeries([50.0 for _ in range(200)]),
            "fees": None,
        }
        signals = aave_relative_signals(
            points, datetime(2023, 4, 1, tzinfo=timezone.utc), growth,
        )
        self.assertGreater(signals["protocol_tvl_growth_90d"], 0.0)
        self.assertEqual(signals["borrow_growth_90d"], 0.0)
        self.assertIsNone(signals["fees_growth_30d"])

    def test_state_requires_admitted_signals(self):
        self.assertEqual(aave_alpha_state({"rel_return_30d": 0.5}, ()), AAVE_ALPHA_NEUTRAL)

    def test_state_majority_rules_both_directions(self):
        admitted = ("protocol_tvl_growth_90d", "borrow_growth_90d", "fees_growth_30d")
        bullish = {
            "protocol_tvl_growth_90d": 0.4,
            "borrow_growth_90d": 0.2,
            "fees_growth_30d": 0.1,
        }
        self.assertEqual(aave_alpha_state(bullish, admitted), AAVE_ALPHA_POSITIVE)
        bearish = {
            "protocol_tvl_growth_90d": -0.4,
            "borrow_growth_90d": -0.2,
            "fees_growth_30d": -0.1,
        }
        self.assertEqual(aave_alpha_state(bearish, admitted), AAVE_ALPHA_NEGATIVE)

    def test_unknown_admitted_signal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside the preregistered AAVE"):
            aave_alpha_state({}, ("chain_tvl_growth_90d",))

    def test_observations_and_admission_use_the_aave_label(self):
        aave = [1.0 * (1.02 ** i) for i in range(220)]
        btc = [1.0 * (1.01 ** i) for i in range(220)]
        points = self._ratio(aave, btc)
        moments = [
            (datetime(2023, 4, 1, tzinfo=timezone.utc) + timedelta(days=7 * i))
            for i in range(20)
        ]
        rows = aave_signal_observations(points, moments, horizons=(90,))
        available = [
            row for row in rows
            if row["labels"]["90"]["status"] == "AVAILABLE"
        ]
        self.assertGreater(len(available), 0)
        self.assertIn(
            "forward_aavebtc_return", available[0]["labels"]["90"],
        )
        evaluation = evaluate_aave_signals(rows, horizons=(90,))
        self.assertEqual(set(evaluation["signals"]), set(SIGNAL_NAMES))
        admission = evaluate_aave_admission({"w": evaluation, "x": evaluation})
        self.assertIn("aave_tilt_admitted", admission)


if __name__ == "__main__":
    unittest.main()
