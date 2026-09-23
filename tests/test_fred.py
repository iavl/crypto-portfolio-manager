import unittest

from crypto_portfolio.engine.metric_normalization import normalize_metric_result
from crypto_portfolio.providers.base import ProviderRequest
from crypto_portfolio.providers.fred import FREDProvider, FRED_SERIES, parse_fred_series


AS_OF = "2026-09-07T00:00:00Z"


class FREDFreshnessTests(unittest.TestCase):
    def _status(self, metric_key, observed_at):
        return normalize_metric_result(
            {
                "asset": "BTC",
                "metric_key": metric_key,
                "value": 100,
                "unit": "index" if metric_key == "macro.dtwexbgs" else "USD_billions" if metric_key == "macro.m2sl" else "percent",
                "observed_at": observed_at,
                "fetched_at": AS_OF,
                "source": "fred",
                "confidence": "HIGH",
            },
            as_of=AS_OF,
        ).observation.freshness

    def test_series_specs_are_publication_aware_and_bounded(self):
        self.assertEqual(FRED_SERIES["macro.dff"].max_expected_observation_age_days, 7)
        self.assertEqual(FRED_SERIES["macro.dfii10"].max_expected_observation_age_days, 7)
        self.assertEqual(FRED_SERIES["macro.dtwexbgs"].max_expected_observation_age_days, 14)
        self.assertEqual(FRED_SERIES["macro.m2sl"].max_expected_observation_age_days, 75)
        self.assertEqual(FRED_SERIES["macro.m2sl"].observation_frequency, "monthly")

    def test_realistic_dtwexbgs_latest_publication_is_current(self):
        self.assertEqual(self._status("macro.dtwexbgs", "2026-08-28T00:00:00Z"), "CURRENT")
        self.assertEqual(self._status("macro.dtwexbgs", "2026-07-24T00:00:00Z"), "STALE")

    def test_realistic_m2sl_latest_month_is_current(self):
        self.assertEqual(self._status("macro.m2sl", "2026-07-01T00:00:00Z"), "CURRENT")
        self.assertEqual(self._status("macro.m2sl", "2026-05-01T00:00:00Z"), "STALE")

    def test_daily_macro_freshness_remains_strict(self):
        for metric_key in ("macro.dff", "macro.dfii10"):
            with self.subTest(metric_key=metric_key):
                self.assertEqual(self._status(metric_key, "2026-08-31T00:00:00Z"), "CURRENT")
                self.assertEqual(self._status(metric_key, "2026-08-30T00:00:00Z"), "STALE")

    def test_as_of_cutoff_is_preserved(self):
        points = parse_fred_series(
            {
                "observations": [
                    {"date": "2026-09-08", "value": "101"},
                    {"date": "2026-07-01", "value": "100"},
                ]
            },
            "M2SL",
            as_of=AS_OF,
        )
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].observed_at, "2026-07-01T00:00:00Z")

    def test_provider_retains_series_freshness_metadata(self):
        class Client:
            def get_json(self, _url, *, params=None, headers=None):
                return {"observations": [{"date": "2026-08-28", "value": "100"}]}

        response = FREDProvider(
            client=Client(),
            api_key="fake-key",
            clock=lambda: "2026-09-07T00:00:00Z",
        ).collect(
            ProviderRequest(
                "fred",
                "macro",
                "BTC",
                {"as_of": AS_OF},
                ("macro.dtwexbgs",),
            )
        )
        metadata = response.observations[0]["metadata"]
        self.assertEqual(metadata["observation_frequency"], "daily")
        self.assertEqual(metadata["expected_publication_lag_days"], 3)
        self.assertEqual(metadata["max_expected_observation_age_days"], 14)

    def _captured_params(self, as_of, clock):
        captured = {}

        class Client:
            def get_json(self, _url, *, params=None, headers=None):
                captured.update(params)
                return {"observations": [{"date": "2026-08-28", "value": "100"}]}

        FREDProvider(client=Client(), api_key="fake-key", clock=clock).collect(
            ProviderRequest("fred", "macro", "BTC", {"as_of": as_of}, ("macro.dff",))
        )
        return captured

    def test_live_as_of_today_pins_no_realtime_vintage(self):
        # A live request runs as_of the current UTC instant; FRED's server
        # date lags UTC across the US-night window and realtime_start after
        # the server's today is a hard HTTP 400, so no vintage is pinned.
        params = self._captured_params("2026-09-23T01:00:00Z", lambda: "2026-09-23T01:00:00Z")
        self.assertNotIn("realtime_start", params)
        self.assertNotIn("realtime_end", params)
        self.assertEqual(params["observation_end"], "2026-09-23")

    def test_past_simulated_as_of_pins_realtime_vintage(self):
        params = self._captured_params("2026-09-05T00:00:00Z", lambda: "2026-09-07T00:00:00Z")
        self.assertEqual(params["realtime_start"], "2026-09-05")
        self.assertEqual(params["realtime_end"], "2026-09-05")
        self.assertEqual(params["observation_end"], "2026-09-05")


if __name__ == "__main__":
    unittest.main()
