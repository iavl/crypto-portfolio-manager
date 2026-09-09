import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from crypto_portfolio.providers.base import (
    ProviderDataError,
    ProviderInsufficientHistory,
    ProviderRequest,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from crypto_portfolio.providers.config import load_provider_config, provider_enabled
from crypto_portfolio.providers.http import HttpClient
from crypto_portfolio.providers.lunarcrush import (
    BASE_URL,
    SUPPORTED_ASSETS,
    SUPPORTED_METRICS,
    LunarCrushProvider,
    parse_timeseries,
)
from crypto_portfolio.providers.probe import probe_provider
from crypto_portfolio.providers.router import ProviderRouter


ROOT = Path(__file__).resolve().parent
FIXTURE = json.loads((ROOT / "fixtures/providers/lunarcrush_coin_timeseries.json").read_text())


def _payload(days: int, *, start: datetime = datetime(2026, 6, 1, tzinfo=timezone.utc)) -> dict:
    return {
        "data": [
            {
                "time": int((start + timedelta(days=index)).timestamp()),
                "sentiment": 50 + index / 10,
                "posts_active": 100 + index,
            }
            for index in range(days)
        ]
    }


class Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.payload


class LunarCrushTests(unittest.TestCase):
    def test_bearer_header_and_no_query_key(self):
        client = Client(FIXTURE)
        provider = LunarCrushProvider(client=client, api_key="fake-secret")
        provider.collect(ProviderRequest(
            "lunarcrush", "sentiment", "BTC", {"as_of": "2026-09-10T00:00:00Z"},
            ("sentiment.social_mentions_24h",),
        ))
        url, kwargs = client.calls[0]
        self.assertEqual(url, f"{BASE_URL}/public/coins/btc/time-series/v2")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer fake-secret"})
        self.assertNotIn("fake-secret", str(kwargs["params"]))
        self.assertNotIn("fake-secret", url)

    def test_api_key_is_redacted_from_transport_diagnostics(self):
        def opener(request, **_kwargs):
            raise HTTPError(request.full_url, 403, "Bearer fake-secret", {}, None)

        provider = LunarCrushProvider(
            client=HttpClient(opener=opener, max_attempts=1),
            api_key="fake-secret",
        )
        with self.assertRaises(Exception) as raised:
            provider.collect(ProviderRequest(
                "lunarcrush", "sentiment", "BTC", {"as_of": "2026-09-10T00:00:00Z"},
                ("sentiment.social_mentions_24h",),
            ))
        self.assertNotIn("fake-secret", str(raised.exception))
        self.assertNotIn("fake-secret", str(getattr(raised.exception, "diagnostic", None)))

    def test_unsupported_asset_fails_cleanly(self):
        with self.assertRaises(ProviderUnsupportedMetric):
            parse_timeseries(FIXTURE, (SUPPORTED_METRICS[0],), asset="DOGE", fetched_at="2026-09-10T00:00:00Z")

    def test_partial_current_day_is_excluded(self):
        values = parse_timeseries(
            FIXTURE, ("sentiment.social_mentions_24h",),
            as_of="2026-09-09T12:00:00Z", fetched_at="2026-09-10T00:00:00Z",
        )
        self.assertEqual(values[0]["value"], 100)
        self.assertEqual(values[0]["observed_at"], "2026-09-08T00:00:00Z")

    def test_rows_are_sorted_by_timestamp(self):
        payload = {"data": list(reversed(FIXTURE["data"]))}
        values = parse_timeseries(
            payload, ("sentiment.social_mentions_24h",),
            as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z",
        )
        self.assertEqual(values[0]["value"], 140)

    def test_identical_duplicate_daily_rows_are_stable(self):
        payload = {"data": [*FIXTURE["data"], FIXTURE["data"][1]]}
        values = parse_timeseries(
            payload, ("sentiment.social_mentions_24h",),
            as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z",
        )
        self.assertEqual(values[0]["value"], 140)

    def test_conflicting_duplicate_daily_rows_fail(self):
        duplicate = {**FIXTURE["data"][1], "posts_active": 101}
        with self.assertRaises(ProviderDataError):
            parse_timeseries(
                {"data": [*FIXTURE["data"], duplicate]},
                ("sentiment.social_mentions_24h",),
                as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z",
            )

    def test_sentiment_normalization_and_posts_active_mapping(self):
        values = parse_timeseries(
            FIXTURE,
            ("sentiment.social_bullish_share", "sentiment.social_mentions_24h"),
            as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z",
        )
        by_key = {item["metric_key"]: item for item in values}
        self.assertEqual(by_key["sentiment.social_bullish_share"]["value"], 0.75)
        self.assertEqual(by_key["sentiment.social_bullish_share"]["metadata"]["raw_unit"], "percent")
        self.assertEqual(by_key["sentiment.social_mentions_24h"]["value"], 140)
        self.assertEqual(by_key["sentiment.social_mentions_24h"]["metadata"]["source_metric"], "posts_active")

    def test_change_uses_exactly_aligned_prior_day(self):
        values = parse_timeseries(
            _payload(8, start=datetime(2026, 9, 1, tzinfo=timezone.utc)),
            ("sentiment.social_mentions_change_7d",),
            as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z",
        )
        self.assertAlmostEqual(values[0]["value"], 7 / 100)
        self.assertEqual(values[0]["metadata"]["prior_observed_at"], "2026-09-01T00:00:00Z")

    def test_zero_change_denominator_fails(self):
        payload = _payload(8, start=datetime(2026, 9, 1, tzinfo=timezone.utc))
        payload["data"][0]["posts_active"] = 0
        with self.assertRaises(ProviderDataError):
            parse_timeseries(
                payload, ("sentiment.social_mentions_change_7d",),
                as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z",
            )

    def test_percentile_midrank_ties_are_deterministic(self):
        payload = _payload(90)
        payload["data"][-1]["sentiment"] = payload["data"][-2]["sentiment"]
        values = parse_timeseries(
            payload, ("sentiment.social_sentiment_percentile",),
            as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z",
        )
        self.assertAlmostEqual(values[0]["value"], 89 / 90)
        self.assertEqual(values[0]["metadata"]["rows_used"], 90)

    def test_percentiles_require_ninety_rows(self):
        with self.assertRaises(ProviderInsufficientHistory):
            parse_timeseries(
                _payload(89), ("sentiment.social_attention_percentile",),
                as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z",
            )

    def test_as_of_excludes_future_rows(self):
        payload = _payload(3, start=datetime(2026, 9, 8, tzinfo=timezone.utc))
        values = parse_timeseries(
            payload, ("sentiment.social_mentions_24h",),
            as_of="2026-09-10T12:00:00Z", fetched_at="2026-09-11T00:00:00Z",
        )
        self.assertEqual(values[0]["observed_at"], "2026-09-09T00:00:00Z")

    def test_missing_and_malformed_fields_fail_closed(self):
        with self.assertRaises(ProviderInsufficientHistory):
            parse_timeseries(
                {"data": [{"time": 1788825600}]},
                ("sentiment.social_mentions_24h",),
                as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z",
            )
        with self.assertRaises(ProviderDataError):
            parse_timeseries(
                {"data": [{"time": 1788825600, "posts_active": "bad"}]},
                ("sentiment.social_mentions_24h",),
                as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z",
            )
        with self.assertRaises(ProviderResponseError):
            parse_timeseries([], ("sentiment.social_mentions_24h",), fetched_at="2026-09-10T00:00:00Z")

    def test_capabilities_match_the_five_social_metrics(self):
        self.assertEqual(LunarCrushProvider(api_key="key").capabilities.metric_keys, SUPPORTED_METRICS)
        self.assertEqual(set(SUPPORTED_ASSETS), {"BTC", "ETH", "SOL", "BNB", "LINK", "AAVE"})

    def test_provider_config_is_key_gated(self):
        config = load_provider_config()
        self.assertFalse(provider_enabled("lunarcrush", config, {}))
        self.assertTrue(provider_enabled("lunarcrush", config, {"LUNARCRUSH_API_KEY": "key"}))

    def test_router_registers_lunarcrush_only_with_key(self):
        config = {
            "providers": {"lunarcrush": {"enabled": "AUTO", "api_key_env": "LUNARCRUSH_API_KEY"}},
            "cache_ttl_seconds": {"default": 3600},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": False},
        }
        with patch.dict("os.environ", {}, clear=True):
            self.assertNotIn("lunarcrush", ProviderRouter(config=config, http_client=object()).providers)
        with patch.dict("os.environ", {"LUNARCRUSH_API_KEY": "key"}, clear=True):
            router = ProviderRouter(config=config, http_client=object())
            self.assertIn("lunarcrush", router.providers)
            status = {item.provider: item for item in router.provider_runtime_status()}["lunarcrush"]
            self.assertTrue(status.runtime_ready)

    def test_probe_output_contains_no_secret(self):
        config = {
            "providers": {"lunarcrush": {"enabled": True, "api_key_env": "LUNARCRUSH_API_KEY"}},
            "cache_ttl_seconds": {"default": 3600},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": False},
        }
        provider = LunarCrushProvider(client=Client(FIXTURE), api_key="fake-secret")
        with patch.dict("os.environ", {"LUNARCRUSH_API_KEY": "fake-secret"}, clear=True):
            rows = probe_provider(ProviderRouter({"lunarcrush": provider}, config=config), "lunarcrush", asset="BTC")
        self.assertEqual(rows[0]["normalization"], "OK")
        self.assertNotIn("fake-secret", json.dumps(rows))


if __name__ == "__main__":
    unittest.main()
