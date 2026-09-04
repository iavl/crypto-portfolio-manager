from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from crypto_portfolio.acquisition import AcquisitionManager, FetchMode
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest
from crypto_portfolio.engine.metric_normalization import normalize_metric_result
from crypto_portfolio.models.events import EventScanResult
from crypto_portfolio.models.market import Candle, OHLCVSeries
from crypto_portfolio.providers.alternative_me import parse_fear_greed
from crypto_portfolio.providers.base import ProviderRequest, ProviderUnavailable
from crypto_portfolio.providers.binance import BinanceProvider
from crypto_portfolio.providers.cache import CacheExpired, ProviderCache, request_hash
from crypto_portfolio.providers.coinmetrics import CoinMetricsProvider, catalog_metrics, parse_timeseries
from crypto_portfolio.providers.coinglass import (
    API_KEY_HEADER,
    BASE_URL as COINGLASS_BASE_URL,
    CoinGlassProvider,
    parse_etf_flow_history,
    parse_liquidation_history,
)
from crypto_portfolio.providers.config import load_provider_config, provider_enabled, provider_runtime_status, provider_status
from crypto_portfolio.providers.defillama import identifier_for_asset
from crypto_portfolio.providers.http import HttpClient, redact_secrets
from crypto_portfolio.providers.router import ProviderRouter
from crypto_portfolio.providers.routes import provider_chain


def config_for(*providers):
    return {
        "version": 1,
        "providers": {name: {"enabled": True} for name in providers},
        "cache_ttl_seconds": {"default": 3600, "spot": 600},
        "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
        "fallback": {"allow_web": True},
    }


class Response:
    def __init__(self, status=200, body=b"{}", headers=None):
        self.status = status
        self.headers = headers or {}
        self.body = body

    def read(self, *_args):
        return self.body


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        return self.responses.pop(0)


class StructuredProvider:
    def __init__(self, value=100, source="test", error=None):
        self.calls = 0
        self.value = value
        self.source = source
        self.error = error

    def collect(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return [
            {
                "asset": request.asset,
                "metric_key": key,
                "value": self.value,
                "unit": "USD" if key == "fundamentals.tvl" else "fraction",
                "observed_at": "2026-09-04T00:00:00Z",
                "fetched_at": "2026-09-04T00:01:00Z",
                "source": self.source,
                "confidence": "HIGH",
            }
            for key in request.metric_keys
        ]


class DataAcquisitionTests(unittest.TestCase):
    def test_http_retry_size_and_redaction(self):
        sleeper = []
        client = Client([
            Response(429, headers={"Retry-After": "0"}),
            Response(200, b'{"ok": true}'),
        ])
        http = HttpClient(opener=client, sleeper=sleeper.append, max_attempts=3)
        self.assertEqual(http.get_json("https://example.test/data"), {"ok": True})
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(sleeper, [0.0])

        bad = Client([Response(400)])
        with self.assertRaises(Exception):
            HttpClient(opener=bad, max_attempts=3, sleeper=lambda _: None).get_json("https://example.test/data")
        self.assertEqual(len(bad.calls), 1)

        oversized = Client([Response(200, b"12345", {"Content-Length": "5"})])
        with self.assertRaises(Exception):
            HttpClient(opener=oversized, max_response_bytes=4).get_json("https://example.test/data")
        self.assertEqual(redact_secrets({"api_key": "secret"})["api_key"], "[REDACTED]")
        self.assertNotIn("secret", redact_secrets("api_key=secret Authorization: Bearer secret"))

    def test_modes_and_normalized_observation_reuse(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (MetricRequest("ETH", "fundamentals.tvl"),))
        fresh = normalize_metric_result({
            "asset": "ETH", "metric_key": "fundamentals.tvl", "value": 99, "unit": "USD",
            "observed_at": "2026-09-04T00:00:00Z", "fetched_at": "2026-09-04T00:01:00Z",
            "source": "local", "confidence": "HIGH",
        }).observation
        with TemporaryDirectory() as directory:
            cache = ProviderCache(Path(directory) / "provider-cache", market_data_directory=Path(directory) / "market-data")
            provider = StructuredProvider()
            router = ProviderRouter({"defillama": provider}, config=config_for("defillama"), cache=cache)
            manager = AcquisitionManager(router, observation_path=Path(directory) / "observations.jsonl", event_path=Path(directory) / "events.jsonl")
            reused = manager.run(plan, mode=FetchMode.AUTO, as_of="2026-09-04T00:02:00Z", now="2026-09-04T00:02:00Z", cached_observations=(fresh,))
            self.assertEqual(provider.calls, 0)
            self.assertEqual(reused.summary["fresh_observation_hits"], 1)

            fetched = manager.run(plan, mode=FetchMode.CACHE_ONLY, as_of="2026-09-04T00:02:00Z", now="2026-09-04T00:02:00Z", cached_observations=())
            self.assertEqual(provider.calls, 0)
            self.assertEqual(fetched.results[0].status, "FAILED")
            self.assertEqual(fetched.web_fallbacks, ())

            fetched = manager.run(plan, mode=FetchMode.AUTO, as_of="2026-09-04T00:02:00Z", now="2026-09-04T00:02:00Z", cached_observations=())
            self.assertEqual(provider.calls, 1)
            self.assertEqual(fetched.results[0].status, "SUCCESS")
            self.assertEqual(fetched.web_fallbacks, ())
            manager.run(plan, mode=FetchMode.REFRESH, as_of="2026-09-04T00:02:00Z", now="2026-09-04T00:02:00Z", cached_observations=(fresh,))
            self.assertEqual(provider.calls, 2)

    def test_relative_returns_are_python_derived_from_market_observations(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("BTC", "market.return_30d"),
            MetricRequest("ETH", "market.return_30d"),
            MetricRequest("ETH", "relative.return_vs_btc_30d"),
        ))
        provider = StructuredProvider(value=0.2, source="binance")
        with TemporaryDirectory() as directory:
            router = ProviderRouter({"binance": provider}, config=config_for("binance"), cache=ProviderCache(Path(directory) / "cache"))
            result = AcquisitionManager(router, observation_path=Path(directory) / "observations.jsonl", persist=False).run(plan, as_of="2026-09-04T00:02:00Z", now="2026-09-04T00:02:00Z")
            self.assertEqual(provider.calls, 2)
            relative = next(item for item in result.observations if item.metric_key == "relative.return_vs_btc_30d")
            self.assertEqual(relative.value, 0)
            self.assertEqual(relative.source, "python-derived")
            self.assertEqual(result.web_fallbacks, ())

    def test_cache_identity_expiry_and_secret_exclusion(self):
        request_a = ProviderRequest("test", "spot", "BTC", {"symbol": "BTCUSDT", "api_key": "one"}, ("market.spot_price",), True, 60)
        request_b = ProviderRequest("test", "spot", "BTC", {"symbol": "BTCUSDT", "api_key": "two"}, ("market.spot_price",), True, 60)
        self.assertEqual(request_hash(request_a), request_hash(request_b))
        self.assertNotIn("one", str(request_a.as_dict()))
        with TemporaryDirectory() as directory:
            cache = ProviderCache(Path(directory))
            path = cache.save_response(request_a, [{"asset": "BTC", "metric_key": "market.spot_price", "value": 1}], fetched_at="2026-09-04T00:00:00Z")
            self.assertNotIn("one", path.read_text())
            self.assertEqual(cache.load_response(request_b, now="2026-09-04T00:00:30Z")[0]["value"], 1)
            with self.assertRaises(CacheExpired):
                cache.load_response(request_b, now="2026-09-04T00:02:00Z")
            historical = ProviderRequest("test", "history", "BTC", {"start": "2026-09-01T00:00:00Z", "end": "2026-09-03T00:00:00Z"}, ("market.return_30d",), False)
            cache.save_response(historical, [{"asset": "BTC", "metric_key": "market.return_30d", "value": 0.1}], fetched_at="2026-09-04T00:00:00Z", observed_range={"start": "2026-09-02T00:00:00Z", "end": "2026-09-02T00:00:00Z"})
            self.assertEqual(cache.load_response(historical, as_of="2026-09-03T00:00:00Z")[0]["value"], 0.1)

    def test_bundle_fallback_and_cache_suppresses_web(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("BTC", "derivatives.funding_rate"),
            MetricRequest("BTC", "derivatives.funding_rate_24h_avg"),
            MetricRequest("BTC", "derivatives.funding_rate_7d_avg"),
        ))
        primary = StructuredProvider(value=0.001, source="binance", error=ProviderUnavailable("down"))
        secondary = StructuredProvider(value=0.002, source="bybit")
        with TemporaryDirectory() as directory:
            router = ProviderRouter(
                {"binance": primary, "bybit": secondary},
                config=config_for("binance", "bybit"),
                cache=ProviderCache(Path(directory) / "cache", market_data_directory=Path(directory) / "market-data"),
            )
            requests = router.build_requests(plan.requests, as_of="2026-09-04T00:00:00Z", now="2026-09-04T00:01:00Z")
            self.assertEqual(len(requests), 1)
            routed = router.collect(requests, as_of="2026-09-04T00:00:00Z", now="2026-09-04T00:01:00Z")
            self.assertEqual(len(routed.observations), 3)
            self.assertEqual(routed.provider_fallbacks, 1)
            self.assertTrue(all(item["source"] == "bybit" for item in routed.observations))

    def test_router_retains_exhausted_unresolved_details(self):
        request = ProviderRequest(
            "binance", "funding", "BTC", {}, ("derivatives.funding_rate",),
        )
        primary = StructuredProvider(error=ProviderUnavailable("provider unavailable"))
        with TemporaryDirectory() as directory:
            router = ProviderRouter(
                {"binance": primary},
                config=config_for("binance"),
                cache=ProviderCache(Path(directory) / "cache"),
            )
            result = router.collect((request,), as_of="2026-09-04T00:00:00Z", now="2026-09-04T00:00:00Z")
        self.assertEqual(result.unresolved, (("BTC", "derivatives.funding_rate"),))
        self.assertEqual(result.unresolved_details[0]["asset"], "BTC")
        self.assertEqual(result.unresolved_details[0]["metric_key"], "derivatives.funding_rate")
        self.assertEqual(result.unresolved_details[0]["providers_attempted"], ["binance", "bybit"])
        self.assertNotIn("provider unavailable", str(result.unresolved_details[0]))

    def test_router_unresolved_details_only_include_missing_bundle_identities(self):
        class PartialProvider(StructuredProvider):
            def collect(self, request):
                self.calls += 1
                return [
                    {
                        "asset": request.asset,
                        "metric_key": request.metric_keys[0],
                        "value": self.value,
                        "unit": "fraction",
                        "observed_at": "2026-09-04T00:00:00Z",
                        "fetched_at": "2026-09-04T00:00:00Z",
                        "source": self.source,
                        "confidence": "HIGH",
                    }
                ]

        request = ProviderRequest(
            "binance", "funding", "BTC", {},
            ("derivatives.funding_rate", "derivatives.funding_rate_24h_avg"),
        )
        with TemporaryDirectory() as directory:
            result = ProviderRouter(
                {"binance": PartialProvider(source="binance")},
                config=config_for("binance"),
                cache=ProviderCache(Path(directory) / "cache"),
            ).collect((request,), as_of="2026-09-04T00:00:00Z", now="2026-09-04T00:00:00Z")
        self.assertEqual(result.unresolved, (("BTC", "derivatives.funding_rate_24h_avg"),))
        self.assertEqual(len(result.unresolved_details), 1)

    def test_acquisition_uses_router_unresolved_reason_for_web_fallback(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("ETH", "fundamentals.tvl"),
        ))
        class FailingProvider(StructuredProvider):
            def collect(self, request):
                raise ProviderUnavailable("provider unavailable")

        with TemporaryDirectory() as directory:
            result = AcquisitionManager(
                ProviderRouter(
                    {"defillama": FailingProvider()},
                    config=config_for("defillama"),
                    cache=ProviderCache(Path(directory) / "cache"),
                ),
                persist=False,
            ).run(plan, as_of="2026-09-04T00:00:00Z", now="2026-09-04T00:00:00Z", cached_observations=())
        self.assertEqual(len(result.web_fallbacks), 1)
        self.assertIn("provider unavailable", result.web_fallbacks[0].reason)

    def test_coinglass_bundles_etf_flows_and_liquidations(self):
        flows = [
            {"timestamp": int((datetime(2026, 8, 6, tzinfo=timezone.utc) + timedelta(days=index)).timestamp() * 1000), "flow_usd": index * 10}
            for index in range(30)
        ]
        flow_values = parse_etf_flow_history(
            {"code": "0", "data": flows},
            ("flows.etf_net_1d", "flows.etf_net_7d", "flows.etf_net_30d"),
            fetched_at="2026-09-05T00:00:00Z", as_of="2026-09-04T00:00:00Z",
        )
        self.assertEqual(len(flow_values), 3)
        self.assertEqual(flow_values[0]["value"], 290)
        self.assertEqual(flow_values[1]["value"], sum(index * 10 for index in range(23, 30)))
        self.assertEqual(flow_values[2]["value"], sum(index * 10 for index in range(30)))

        liquidation_rows = [
            {
                "time": int((datetime(2026, 8, 29, tzinfo=timezone.utc) + timedelta(days=index)).timestamp() * 1000),
                "aggregated_long_liquidation_usd": index + 1,
                "aggregated_short_liquidation_usd": (index + 1) * 2,
            }
            for index in range(7)
        ]
        liquidation_values = parse_liquidation_history(
            {"code": 0, "data": liquidation_rows},
            ("derivatives.long_liquidations_24h_usd", "derivatives.short_liquidations_24h_usd", "derivatives.total_liquidations_24h_usd", "derivatives.long_liquidations_7d_usd"),
            asset="BTC", fetched_at="2026-09-05T00:00:00Z", as_of="2026-09-04T00:00:00Z",
        )
        values = {item["metric_key"]: item["value"] for item in liquidation_values}
        self.assertEqual(values["derivatives.long_liquidations_24h_usd"], 7)
        self.assertEqual(values["derivatives.short_liquidations_24h_usd"], 14)
        self.assertEqual(values["derivatives.total_liquidations_24h_usd"], 21)
        self.assertEqual(values["derivatives.long_liquidations_7d_usd"], 28)
        self.assertEqual(
            parse_liquidation_history(
                {"code": 0, "data": liquidation_rows[:2]},
                ("derivatives.long_liquidations_7d_usd",),
                asset="BTC", fetched_at="2026-09-05T00:00:00Z",
            ),
            (),
        )

    def test_coinglass_auth_header_and_runtime_registration(self):
        class FakeCoinGlassClient:
            def __init__(self):
                self.calls = []

            def get_json(self, url, *, params=None, headers=None):
                self.calls.append((url, params, headers))
                return {"code": "0", "data": [{
                    "timestamp": 1788476400000, "flow_usd": 10,
                }]}

        client = FakeCoinGlassClient()
        provider = CoinGlassProvider(client=client, api_key="fake-secret")
        values = provider.collect(ProviderRequest("coinglass", "etf", "MARKET", {}, ("flows.etf_net_1d",)))
        self.assertEqual(values[0]["value"], 10)
        self.assertEqual(client.calls[0][0], COINGLASS_BASE_URL + "/api/etf/bitcoin/flow-history")
        self.assertEqual(client.calls[0][2], {API_KEY_HEADER: "fake-secret"})
        self.assertEqual(provider.capabilities.requires_api_key, True)
        self.assertEqual(provider_chain("derivatives.total_liquidations_24h_usd"), ("coinglass",))

        config = config_for("coinglass")
        config["providers"]["coinglass"] = {"enabled": "AUTO", "api_key_env": "COINGLASS_API_KEY"}
        with patch.dict("os.environ", {"COINGLASS_API_KEY": "fake-secret"}, clear=False):
            router = ProviderRouter(config=config, http_client=object())
            self.assertIn("coinglass", router.providers)
        with patch.dict("os.environ", {}, clear=True):
            router = ProviderRouter(config=config, http_client=object())
            self.assertNotIn("coinglass", router.providers)

    def test_coinglass_secret_is_redacted_from_router_diagnostics(self):
        class LeakingClient:
            def get_json(self, url, *, params=None, headers=None):
                raise RuntimeError(f"request failed for {headers[API_KEY_HEADER]}")

        provider = CoinGlassProvider(client=LeakingClient(), api_key="fake-secret")
        request = ProviderRequest("coinglass", "etf", "MARKET", {}, ("flows.etf_net_1d",))
        config = config_for("coinglass")
        config["providers"]["coinglass"] = {"enabled": True, "api_key_env": "COINGLASS_API_KEY"}
        with patch.dict("os.environ", {"COINGLASS_API_KEY": "fake-secret"}, clear=False):
            with TemporaryDirectory() as directory:
                result = ProviderRouter(
                    {"coinglass": provider},
                    config=config,
                    cache=ProviderCache(Path(directory) / "cache"),
                ).collect((request,), now="2026-09-04T00:00:00Z")
        self.assertNotIn("fake-secret", str(result.as_dict()))

    def test_coinglass_plan_denial_is_a_safe_provider_fallback(self):
        class DeniedClient:
            def get_json(self, url, *, params=None, headers=None):
                return {"code": "1003", "msg": "upgrade plan required", "data": []}

        provider = CoinGlassProvider(client=DeniedClient(), api_key="fake-secret")
        request = ProviderRequest("coinglass", "etf", "MARKET", {}, ("flows.etf_net_1d",))
        config = config_for("coinglass")
        config["providers"]["coinglass"] = {"enabled": True, "api_key_env": "COINGLASS_API_KEY"}
        with patch.dict("os.environ", {"COINGLASS_API_KEY": "fake-secret"}, clear=False):
            with TemporaryDirectory() as directory:
                result = ProviderRouter(
                    {"coinglass": provider},
                    config=config,
                    cache=ProviderCache(Path(directory) / "cache"),
                ).collect((request,), now="2026-09-04T00:00:00Z")
        self.assertEqual(result.unresolved, (("MARKET", "flows.etf_net_1d"),))
        self.assertEqual(result.attempts[0].status, "UNSUPPORTED")
        self.assertNotIn("fake-secret", str(result.as_dict()))

    def test_incremental_series_requests_only_tail(self):
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        def series(days):
            return OHLCVSeries(
                "BTC", "1D",
                tuple(Candle((base + timedelta(days=index)).isoformat().replace("+00:00", "Z"), 100 + index, 101 + index, 99 + index, 100 + index, 1) for index in days),
                source="binance", fetched_at="2026-09-04T00:00:00Z", venue="BINANCE", market="spot", quote_currency="USDT",
            )
        class CandlesProvider:
            def __init__(self): self.ranges = []
            def candles(self, symbol, *, timeframe="1D", start=None, end=None):
                self.ranges.append((start, end))
                return series((4,))
            def observations_from_series(self, value, keys, *, as_of=None):
                return []
        with TemporaryDirectory() as directory:
            cache = ProviderCache(Path(directory) / "cache", market_data_directory=Path(directory) / "market-data")
            cache.store_series(series((1, 2, 3)), provider="binance", market="spot", quote_currency="USDT")
            provider = CandlesProvider()
            router = ProviderRouter({"binance": provider}, config=config_for("binance"), cache=cache)
            request = ProviderRequest("binance", "ohlcv", "BTC", {
                "timeframe": "1D", "start": "2026-09-02T00:00:00Z", "end": "2026-09-05T00:00:00Z",
            }, ("market.ma20",), False)
            router.collect((request,), as_of="2026-09-05T00:00:00Z", now="2026-09-05T00:00:00Z")
            self.assertEqual(provider.ranges, [("2026-09-05T00:00:00Z", "2026-09-05T00:00:00Z")])

    def test_public_provider_parsers_and_event_scan(self):
        fear = parse_fear_greed({"data": [
            {"value": "60", "timestamp": "1788476400", "value_classification": "Greed"},
            {"value": "50", "timestamp": "1788390000", "value_classification": "Neutral"},
        ]}, fetched_at="2026-09-04T00:01:00Z", as_of="2026-09-04T00:00:00Z")
        self.assertEqual(fear["value"], 60)
        self.assertEqual(identifier_for_asset("AAVE"), "aave")
        self.assertEqual(catalog_metrics({"metrics": [{"metric": "CapMVRVCur"}]}), frozenset({"capmvrvcur"}))
        parsed = parse_timeseries({"data": [{"time": "2026-09-03T00:00:00Z", "CapMVRVCur": "1.5"}]}, ("onchain.btc.mvrv",), fetched_at="2026-09-04T00:00:00Z")
        self.assertEqual(parsed[0]["value"], 1.5)
        scan = EventScanResult("ETH", "security", "2026-09-04T00:00:00Z", 7, ("status page",), ({"published_at": "2026-08-20T00:00:00Z"},), 1, "HIGH")
        result = normalize_metric_result(scan.to_observation("risk.security_event_status", fetched_at="2026-09-04T00:01:00Z"), as_of="2026-09-04T00:02:00Z")
        self.assertEqual(result.observation.freshness, "CURRENT")
        self.assertEqual(result.observation.value, "MATERIAL_EVENT_FOUND")

    def test_optional_credentials_and_catalog_filtering(self):
        config = load_provider_config()
        self.assertFalse(provider_enabled("coinmetrics_pro", config, {}))
        self.assertTrue(provider_enabled("coinmetrics_pro", config, {"COINMETRICS_API_KEY": "configured"}))

        class CoinMetricsClient:
            def __init__(self):
                self.calls = []

            def get_json(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if "catalog" in url:
                    return {"metrics": [{"metric": "CapMVRVCur"}]}
                return {"data": [{"time": "2026-09-03T00:00:00Z", "CapMVRVCur": "1.5"}]}

        client = CoinMetricsClient()
        provider = CoinMetricsProvider(client=client)
        values = provider.collect(ProviderRequest("coinmetrics_community", "onchain", "BTC", {}, ("onchain.btc.mvrv", "onchain.btc.sopr")))
        self.assertEqual([item["metric_key"] for item in values], ["onchain.btc.mvrv"])
        self.assertEqual(client.calls[1][1]["params"]["metrics"], "CapMVRVCur")

        with TemporaryDirectory() as directory:
            override = Path(directory) / "providers.json"
            override.write_text('{"providers":{"binance":{"enabled":false}}}', encoding="utf-8")
            with patch.dict("os.environ", {"CRYPTO_PORTFOLIO_PROVIDER_CONFIG": str(override)}, clear=False):
                self.assertFalse(load_provider_config()["providers"]["binance"]["enabled"])

    def test_provider_runtime_status_separates_config_adapter_and_credential(self):
        config = config_for("binance", "coinglass")
        config["providers"]["coinglass"] = {"enabled": "AUTO", "api_key_env": "COINGLASS_API_KEY"}
        adapters = {"binance": object(), "coinglass": object()}
        missing = provider_runtime_status(config, adapters=adapters, environ={})
        by_name = {item.provider: item for item in missing}
        self.assertTrue(by_name["binance"].runtime_ready)
        self.assertFalse(by_name["coinglass"].config_enabled)
        self.assertTrue(by_name["coinglass"].adapter_available)
        self.assertTrue(by_name["coinglass"].credential_required)
        self.assertFalse(by_name["coinglass"].runtime_ready)
        self.assertNotIn("COINGLASS", str(by_name["coinglass"]))

        ready = provider_runtime_status(config, adapters=adapters, environ={"COINGLASS_API_KEY": "secret"})
        self.assertTrue({item.provider: item for item in ready}["coinglass"].runtime_ready)
        status = provider_status(config, {"COINGLASS_API_KEY": "secret"}, adapters=adapters)
        row = {item["provider"]: item for item in status}["coinglass"]
        self.assertTrue(row["enabled"])
        self.assertTrue(row["config_enabled"])
        self.assertTrue(row["runtime_ready"])
        self.assertNotIn("secret", str(status))

        unavailable = provider_runtime_status(config, adapters={"binance": object()}, environ={"COINGLASS_API_KEY": "secret"})
        row = {item.provider: item for item in unavailable}["coinglass"]
        self.assertTrue(row.config_enabled)
        self.assertFalse(row.adapter_available)
        self.assertFalse(row.runtime_ready)

    def test_binance_spot_and_klines_use_public_endpoints(self):
        class FakeClient:
            def __init__(self): self.urls = []
            def get_json(self, url, *, params=None, headers=None):
                self.urls.append((url, params))
                if url.endswith("/ticker/price"):
                    return {"price": "100", "time": 1788307200000}
                return [[1788220800000, "90", "101", "89", "100", "10", 1788307199999]]
        client = FakeClient()
        provider = BinanceProvider(client=client, clock=lambda: datetime(2026, 9, 2, tzinfo=timezone.utc))
        self.assertEqual(provider.spot_price("BTC").price, 100)
        self.assertTrue(provider.candles("BTC").candles[0].completed)
        self.assertEqual([url.split("//", 1)[1].split("/", 1)[0] for url, _ in client.urls], ["api.binance.com", "api.binance.com"])


if __name__ == "__main__":
    unittest.main()
