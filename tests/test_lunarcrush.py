import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from crypto_portfolio.providers.base import ProviderDataError, ProviderInsufficientHistory, ProviderRequest, ProviderResponseError, ProviderDiagnostic, ProviderRateLimited
from crypto_portfolio.providers.config import load_provider_config, provider_enabled
from crypto_portfolio.providers.http import HttpClient
from crypto_portfolio.providers.lunarcrush import BASE_URL, SUPPORTED_ASSETS, SUPPORTED_METRICS, LunarCrushProvider, parse_timeseries
from crypto_portfolio.providers.probe import probe_provider
from crypto_portfolio.providers.router import ProviderRouter


FIXTURE = json.loads((Path(__file__).parent / "fixtures/providers/lunarcrush_coin_timeseries.json").read_text())


def _payload(days: int, *, start: datetime = datetime(2026, 6, 1, tzinfo=timezone.utc)) -> dict:
    return {"data": [{"time": int((start + timedelta(days=index)).timestamp()), "sentiment": 50 + index / 10, "posts_active": 100 + index} for index in range(days)]}


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
        LunarCrushProvider(client=client, api_key="fake-secret").collect(ProviderRequest(
            "lunarcrush", "sentiment", "BTC", {"as_of": "2026-09-10T00:00:00Z"}, ("sentiment.social_bullish_share",),
        ))
        url, kwargs = client.calls[0]
        self.assertEqual(url, f"{BASE_URL}/public/coins/btc/time-series/v2")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer fake-secret"})
        self.assertNotIn("fake-secret", str(kwargs["params"]))

    def test_transport_diagnostics_redact_key(self):
        def opener(request, **_kwargs):
            raise HTTPError(request.full_url, 403, "Bearer fake-secret", {}, None)

        provider = LunarCrushProvider(client=HttpClient(opener=opener, max_attempts=1), api_key="fake-secret")
        with self.assertRaises(Exception) as raised:
            provider.collect(ProviderRequest("lunarcrush", "sentiment", "BTC", {"as_of": "2026-09-10T00:00:00Z"}, (SUPPORTED_METRICS[0],)))
        self.assertNotIn("fake-secret", str(raised.exception))

    def test_bullish_share_normalization_and_completed_day(self):
        values = parse_timeseries(FIXTURE, ("sentiment.social_bullish_share",), as_of="2026-09-09T12:00:00Z", fetched_at="2026-09-10T00:00:00Z")
        self.assertEqual(values[0]["value"], 0.6)
        self.assertEqual(values[0]["observed_at"], "2026-09-08T00:00:00Z")

    def test_change_uses_exactly_aligned_prior_day(self):
        values = parse_timeseries(_payload(8, start=datetime(2026, 9, 1, tzinfo=timezone.utc)), ("sentiment.social_mentions_change_7d",), as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z")
        self.assertAlmostEqual(values[0]["value"], 7 / 100)
        self.assertEqual(values[0]["metadata"]["prior_observed_at"], "2026-09-01T00:00:00Z")

    def test_attention_percentile_requires_ninety_rows(self):
        with self.assertRaises(ProviderInsufficientHistory):
            parse_timeseries(_payload(89), ("sentiment.social_attention_percentile",), as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z")

    def test_missing_and_malformed_fields_fail_closed(self):
        with self.assertRaises(ProviderInsufficientHistory):
            parse_timeseries({"data": [{"time": 1788825600}]}, ("sentiment.social_bullish_share",), as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z")
        with self.assertRaises(ProviderDataError):
            parse_timeseries({"data": [{"time": 1788825600, "sentiment": "bad"}]}, ("sentiment.social_bullish_share",), as_of="2026-09-10T00:00:00Z", fetched_at="2026-09-10T00:00:00Z")
        with self.assertRaises(ProviderResponseError):
            parse_timeseries([], ("sentiment.social_bullish_share",), fetched_at="2026-09-10T00:00:00Z")

    def test_capabilities_match_retained_social_metrics(self):
        self.assertEqual(LunarCrushProvider(api_key="key").capabilities.metric_keys, SUPPORTED_METRICS)
        self.assertEqual(set(SUPPORTED_METRICS), {"sentiment.social_bullish_share", "sentiment.social_mentions_change_7d", "sentiment.social_attention_percentile"})
        self.assertEqual(set(SUPPORTED_ASSETS), {"BTC", "ETH", "SOL", "BNB", "LINK", "AAVE"})

    def test_provider_config_and_probe(self):
        config = load_provider_config()
        self.assertFalse(provider_enabled("lunarcrush", config, {}))
        self.assertTrue(provider_enabled("lunarcrush", config, {"LUNARCRUSH_API_KEY": "key"}))
        provider = LunarCrushProvider(client=Client(FIXTURE), api_key="fake-secret")
        probe_config = {"providers": {"lunarcrush": {"enabled": True, "api_key_env": "LUNARCRUSH_API_KEY"}}, "cache_ttl_seconds": {"default": 3600}, "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30}, "fallback": {"allow_web": False}}
        with patch.dict("os.environ", {"LUNARCRUSH_API_KEY": "fake-secret"}, clear=True):
            rows = probe_provider(ProviderRouter({"lunarcrush": provider}, config=probe_config), "lunarcrush", asset="BTC")
        self.assertEqual(rows[0]["normalization"], "OK")
        self.assertNotIn("fake-secret", json.dumps(rows))

    def test_plan_and_rate_limit_diagnostics_are_distinct(self):
        class ErrorClient:
            def __init__(self, error): self.error = error
            def get_json(self, *_args, **_kwargs): raise self.error

        cases = (
            (ProviderResponseError("payment", diagnostic=ProviderDiagnostic(error_code="HTTP_402", status_code=402, detail="plan required")), "ENTITLEMENT_REQUIRED"),
            (ProviderRateLimited("slow", diagnostic=ProviderDiagnostic(error_code="HTTP_429", status_code=429, detail="retry later", retryable=True)), "RATE_LIMITED"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(Exception) as raised:
                    LunarCrushProvider(client=ErrorClient(error), api_key="key").collect(ProviderRequest("lunarcrush", "sentiment", "BTC", {"as_of": "2026-09-10T00:00:00Z"}, (SUPPORTED_METRICS[0],)))
                self.assertEqual(raised.exception.diagnostic["error_code"], expected)


if __name__ == "__main__":
    unittest.main()
