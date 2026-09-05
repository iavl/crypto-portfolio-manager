from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import socket
import ssl
import unittest
from urllib.error import URLError
from unittest.mock import patch

from crypto_portfolio.acquisition import AcquisitionManager, FetchMode
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest
from crypto_portfolio.engine.metric_normalization import normalize_metric_result
from crypto_portfolio.models.events import EventScanResult
from crypto_portfolio.models.market import Candle, OHLCVSeries
from crypto_portfolio.providers.alternative_me import parse_fear_greed
from crypto_portfolio.providers.base import (
    ProviderAuthenticationError,
    ProviderDiagnostic,
    ProviderRequest,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderUnavailable,
)
from crypto_portfolio.providers.binance import BinanceProvider
from crypto_portfolio.providers.bybit import BybitProvider
from crypto_portfolio.providers.cache import CacheExpired, ProviderCache, request_hash
from crypto_portfolio.providers.coinmetrics import CoinMetricsProvider, catalog_metrics, parse_timeseries
from crypto_portfolio.providers.sosovalue import (
    API_KEY_HEADER as SOSOVALUE_API_KEY_HEADER,
    BASE_URL as SOSOVALUE_BASE_URL,
    ETF_SUMMARY_HISTORY_PATH,
    SoSoValueProvider,
    parse_etf_flow_history,
)
from crypto_portfolio.providers.config import load_provider_config, provider_enabled, provider_runtime_status, provider_status
from crypto_portfolio.providers.defillama import identifier_for_asset
from crypto_portfolio.providers.http import HttpClient, build_ssl_context, classify_transport_error, redact_secrets
from crypto_portfolio.providers.probe import probe_provider
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
                "unit": "USD" if key == "fundamentals.tvl" or key.startswith("flows.etf_") else "fraction",
                "observed_at": "2026-09-04T00:00:00Z",
                "fetched_at": "2026-09-04T00:01:00Z",
                "source": self.source,
                "confidence": "HIGH",
            }
            for key in request.metric_keys
        ]


class DataAcquisitionTests(unittest.TestCase):
    def test_probe_is_explicit_and_reports_network_state(self):
        class ProbeClient:
            def __init__(self, value):
                self.value = value
                self.calls = 0

            def get_json(self, url, *, params=None, headers=None):
                self.calls += 1
                return self.value

        client = ProbeClient({"symbol": "BTCUSDT", "price": "100"})
        provider = type("Provider", (), {"client": client})()
        router = ProviderRouter({"binance": provider}, config=config_for("binance"))
        self.assertEqual(router.provider_status()[0]["runtime_ready"], True)
        result = probe_provider(router, "binance")[0]
        self.assertEqual(result["config"], "READY")
        self.assertEqual(result["network"], "OK")
        self.assertEqual(result["auth"], "NOT_REQUIRED")
        self.assertEqual(client.calls, 1)

    def test_sosovalue_probe_reports_current_endpoint_without_secret(self):
        class ProbeClient:
            def get_json(self, url, *, params=None, headers=None):
                return {"code": 0, "message": "success", "data": [
                    {"date": "2026-09-04", "total_net_inflow": 12},
                ]}

        config = config_for("sosovalue")
        config["providers"]["sosovalue"] = {"enabled": "AUTO", "api_key_env": "SOSOVALUE_API_KEY"}
        provider = SoSoValueProvider(client=ProbeClient(), api_key="fake-secret")
        with patch.dict("os.environ", {"SOSOVALUE_API_KEY": "fake-secret"}, clear=True):
            router = ProviderRouter({"sosovalue": provider}, config=config)
            result = probe_provider(router, "sosovalue")[0]
        self.assertEqual(result["network"], "OK")
        self.assertEqual(result["method"], "GET")
        self.assertEqual(result["endpoint"], SOSOVALUE_BASE_URL + ETF_SUMMARY_HISTORY_PATH)
        self.assertEqual(result["history_rows"], 1)
        self.assertEqual(result["latest_source_date"], "2026-09-04")
        self.assertNotIn("fake-secret", str(result))

    def test_transport_errors_are_classified_and_tls_stays_verified(self):
        certificate_error = URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        self.assertEqual(classify_transport_error(certificate_error), "TLS_CERTIFICATE_VERIFY_FAILED")
        self.assertEqual(classify_transport_error(socket.gaierror("Name or service not known")), "DNS_RESOLUTION_FAILED")
        self.assertEqual(classify_transport_error(TimeoutError(), phase="read"), "READ_TIMEOUT")
        self.assertEqual(classify_transport_error(ConnectionResetError()), "CONNECTION_RESET")
        with self.assertRaises(ValueError):
            build_ssl_context(ssl._create_unverified_context())
        client = HttpClient(opener=Client([Response(429)]), max_attempts=1)
        with self.assertRaises(ProviderRateLimited) as raised:
            client.get_json("https://example.test/data?api_key=secret")
        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic.error_code, "HTTP_429")
        self.assertEqual(diagnostic.endpoint, "https://example.test/data?api_key=%5BREDACTED%5D")
        self.assertNotIn("secret", str(diagnostic.as_dict()))

        contexts = []

        def opener(request, timeout, *, context):
            contexts.append(context)
            return Response(200, b"{}")

        HttpClient(opener=opener).get_json("https://example.test/data")
        self.assertEqual(contexts[0].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(contexts[0].check_hostname)

    def test_stale_observation_retains_refresh_diagnostic(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (MetricRequest("AAVE", "fundamentals.fees_30d"),))

        class FailingProvider:
            def collect(self, request):
                raise ProviderUnavailable(
                    "refresh failed",
                    diagnostic=ProviderDiagnostic(
                        endpoint="https://api.llama.fi/summary/fees/aave",
                        error_code="TLS_CERTIFICATE_VERIFY_FAILED",
                        exception_class="SSLCertVerificationError",
                        detail="unable to get local issuer certificate",
                    ),
                )

        old = normalize_metric_result({
            "asset": "AAVE", "metric_key": "fundamentals.fees_30d", "value": 10, "unit": "USD",
            "observed_at": "2026-08-01T00:00:00Z", "fetched_at": "2026-08-01T00:00:00Z",
            "source": "old", "confidence": "HIGH",
        }).observation
        config = config_for("defillama")
        result = AcquisitionManager(
            ProviderRouter({"defillama": FailingProvider()}, config=config), persist=False,
        ).run(
            plan,
            as_of="2026-09-05T00:00:00Z",
            now="2026-09-05T00:00:00Z",
            cached_observations=(old,),
        )
        event = result.results[0].event
        self.assertEqual(result.results[0].status, "STALE")
        self.assertIn("last observation is stale", event.reason)
        self.assertIn("TLS_CERTIFICATE_VERIFY_FAILED", event.reason)
        self.assertEqual(event.refresh_error_code, "TLS_CERTIFICATE_VERIFY_FAILED")
        self.assertEqual(event.refresh_endpoint, "https://api.llama.fi/summary/fees/aave")
        self.assertEqual(event.last_observation_at, old.observed_at)

    def test_defillama_partial_bundle_keeps_fee_subrequest_failure(self):
        class PartialClient:
            def get_json(self, url, *, params=None, headers=None):
                if "/tvl/" in url:
                    return 123
                raise ProviderUnavailable(
                    "fees TLS failure",
                    diagnostic=ProviderDiagnostic(
                        endpoint="https://api.llama.fi/summary/fees/aave",
                        error_code="TLS_CERTIFICATE_VERIFY_FAILED",
                        detail="unable to get local issuer certificate",
                    ),
                )

        from crypto_portfolio.providers.defillama import DeFiLlamaProvider

        response = DeFiLlamaProvider(client=PartialClient()).collect(
            ProviderRequest(
                "defillama",
                "protocol",
                "AAVE",
                {"as_of": "2026-09-04T00:00:00Z"},
                ("fundamentals.tvl", "fundamentals.fees_30d"),
            )
        )
        self.assertEqual([item["metric_key"] for item in response.observations], ["fundamentals.tvl"])
        self.assertEqual(response.diagnostics["fundamentals.fees_30d"]["error_code"], "TLS_CERTIFICATE_VERIFY_FAILED")
        self.assertIn("summary/fees/aave", response.diagnostics["fundamentals.fees_30d"]["endpoint"])

    def test_defillama_aave_uses_lightweight_tvl_endpoint(self):
        class AaveClient:
            def __init__(self):
                self.urls = []

            def get_json(self, url, *, params=None, headers=None):
                self.urls.append(url)
                if url.endswith("/tvl/aave"):
                    return 123
                if url.endswith("/summary/fees/aave"):
                    return {"total30d": 10}
                raise AssertionError(f"unexpected endpoint: {url}")

        from crypto_portfolio.providers.defillama import DeFiLlamaProvider

        client = AaveClient()
        response = DeFiLlamaProvider(client=client).collect(
            ProviderRequest(
                "defillama",
                "protocol",
                "AAVE",
                {},
                ("fundamentals.tvl", "fundamentals.fees_30d"),
            )
        )
        self.assertEqual([item["metric_key"] for item in response.observations], ["fundamentals.tvl", "fundamentals.fees_30d"])
        self.assertNotIn("/protocol/aave", client.urls)

    def test_defillama_probe_uses_lightweight_tvl_endpoint(self):
        class TvlClient:
            def get_json(self, url, *, params=None, headers=None):
                self.url = url
                return 123

        from crypto_portfolio.providers.defillama import DeFiLlamaProvider

        client = TvlClient()
        router = ProviderRouter(
            {"defillama": DeFiLlamaProvider(client=client)},
            config=config_for("defillama"),
        )
        result = probe_provider(router, "defillama")[0]
        self.assertEqual(result["network"], "OK")
        self.assertEqual(result["schema"], "OK")
        self.assertEqual(client.url, "https://api.llama.fi/tvl/aave")

    def test_coinmetrics_catalog_omits_unsupported_assets_filter(self):
        class CatalogClient:
            def __init__(self):
                self.calls = []

            def get_json(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return {"data": [{"metric": "CapMVRVCur"}]}

        client = CatalogClient()
        CoinMetricsProvider(client=client).catalog()
        self.assertNotIn("assets", client.calls[0][1].get("params", {}))

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

    def test_http_json_post_reuses_transport_and_requires_explicit_idempotence(self):
        class RecordingClient:
            def __init__(self):
                self.responses = [Response(429, headers={"Retry-After": "0"}), Response(200, b'{"ok": "yes"}')]
                self.requests = []

            def __call__(self, request, timeout):
                self.requests.append((request, timeout))
                return self.responses.pop(0)

        opener = RecordingClient()
        sleeper = []
        http = HttpClient(opener=opener, sleeper=sleeper.append, max_attempts=3)
        self.assertEqual(
            http.post_json(
                "https://example.test/etf",
                json_body={"type": "us-btc-spot", "说明": "只读"},
                headers={"x-soso-api-key": "fake-secret"},
                idempotent=True,
            ),
            {"ok": "yes"},
        )
        request, timeout = opener.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.data, '{"type":"us-btc-spot","说明":"只读"}'.encode("utf-8"))
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertGreater(timeout, 0)
        self.assertEqual(http.request_count, 2)
        self.assertEqual(sleeper, [0.0])

        with self.assertRaises(ProviderRateLimited) as raised:
            HttpClient(opener=Client([Response(429)]), max_attempts=3).post_json(
                "https://example.test/etf",
                json_body={"type": "us-btc-spot"},
                headers={"x-soso-api-key": "fake-secret"},
            )
        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic.method, "POST")
        self.assertEqual(diagnostic.attempt, 1)
        self.assertNotIn("fake-secret", str(diagnostic.as_dict()))

        server = Client([Response(500), Response(200, b"{}")])
        self.assertEqual(
            HttpClient(opener=server, sleeper=lambda _: None).post_json(
                "https://example.test/etf", json_body={"type": "us-btc-spot"}, idempotent=True
            ),
            {},
        )
        self.assertEqual(len(server.calls), 2)

        with self.assertRaises(ProviderResponseError) as raised:
            HttpClient(opener=Client([Response(200, b"not-json")])).post_json(
                "https://example.test/etf", json_body={"type": "us-btc-spot"}
            )
        self.assertEqual(raised.exception.diagnostic.method, "POST")
        self.assertEqual(raised.exception.diagnostic.error_code, "INVALID_JSON")
        for status in (401, 403):
            with self.subTest(status=status), self.assertRaises(ProviderAuthenticationError):
                HttpClient(opener=Client([Response(status)])).post_json(
                    "https://example.test/etf", json_body={"type": "us-btc-spot"}
                )

    def test_sosovalue_parser_derives_calendar_windows_and_session_time(self):
        rows = [
            {
                "date": (datetime(2026, 8, 5) + timedelta(days=index)).strftime("%Y-%m-%d"),
                "total_net_inflow": index + 1,
            }
            for index in range(31)
        ]
        values = parse_etf_flow_history(
            {"code": 0, "message": "success", "data": rows},
            ("flows.etf_net_1d", "flows.etf_net_7d", "flows.etf_net_30d"),
            asset="BTC",
            fetched_at="2026-09-06T00:00:00Z",
            as_of="2026-09-06T00:00:00Z",
        )
        by_key = {item["metric_key"]: item for item in values}
        self.assertEqual(by_key["flows.etf_net_1d"]["value"], 31)
        self.assertEqual(by_key["flows.etf_net_7d"]["value"], sum(range(25, 32)))
        self.assertEqual(by_key["flows.etf_net_30d"]["value"], sum(range(2, 32)))
        self.assertEqual(by_key["flows.etf_net_1d"]["observed_at"], "2026-09-04T20:00:00Z")
        self.assertEqual(by_key["flows.etf_net_30d"]["metadata"]["source_end_date"], "2026-09-04")
        self.assertEqual(by_key["flows.etf_net_30d"]["metadata"]["aggregation"], "sum_total_net_inflow")

    def test_sosovalue_parser_handles_weekends_holidays_and_no_lookahead(self):
        weekend = parse_etf_flow_history(
            {"code": 0, "data": [
                {"date": "2026-09-04", "total_net_inflow": 4},
                {"date": "2026-09-03", "total_net_inflow": 3},
            ]},
            ("flows.etf_net_1d",),
            fetched_at="2026-09-06T00:00:00Z",
            as_of="2026-09-06T00:00:00Z",
        )
        self.assertEqual(weekend[0]["value"], 4)
        self.assertEqual(weekend[0]["metadata"]["source_end_date"], "2026-09-04")

        holiday = parse_etf_flow_history(
            {"code": 0, "data": [
                {"date": "2026-09-02", "total_net_inflow": 6},
                {"date": "2026-09-01", "total_net_inflow": 5},
            ]},
            ("flows.etf_net_1d",),
            fetched_at="2026-09-07T00:00:00Z",
            as_of="2026-09-07T00:00:00Z",
        )
        self.assertEqual(holiday[0]["value"], 6)
        self.assertEqual(holiday[0]["metadata"]["rows_used"], 1)

        rows = [
            {
                "date": (datetime(2026, 8, 5) + timedelta(days=index)).strftime("%Y-%m-%d"),
                "total_net_inflow": index + 1,
            }
            for index in range(32)
        ]
        cutoff = parse_etf_flow_history(
            {"code": 0, "data": rows},
            ("flows.etf_net_1d", "flows.etf_net_7d", "flows.etf_net_30d"),
            fetched_at="2026-09-06T00:00:00Z",
            as_of="2026-09-04T21:00:00Z",
        )
        self.assertEqual({item["metric_key"]: item["value"] for item in cutoff}, {
            "flows.etf_net_1d": 31,
            "flows.etf_net_7d": sum(range(25, 32)),
            "flows.etf_net_30d": sum(range(2, 32)),
        })

    def test_sosovalue_parser_strict_envelope_values_and_duplicates(self):
        valid = {"code": 0, "data": [{"date": "2026-09-04", "total_net_inflow": -5}]}
        self.assertEqual(parse_etf_flow_history(valid, ("flows.etf_net_1d",), fetched_at="2026-09-05T00:00:00Z")[0]["value"], -5)
        for payload in (
            {"data": []},
            {"code": 0},
            {"code": 1, "message": "bad request", "data": []},
            {"code": 0, "data": ["bad"]},
            {"code": 0, "data": [{"date": "2026-9-04", "total_net_inflow": 1}]},
            {"code": 0, "data": [{"date": "2026-09-04", "total_net_inflow": float("nan")}]},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(Exception):
                    parse_etf_flow_history(payload, ("flows.etf_net_1d",), fetched_at="2026-09-05T00:00:00Z")

        duplicate = parse_etf_flow_history(
            {"code": 0, "data": [
                {"date": "2026-09-04", "total_net_inflow": 1},
                {"date": "2026-09-04", "total_net_inflow": 1.0},
            ]},
            ("flows.etf_net_1d",),
            fetched_at="2026-09-05T00:00:00Z",
        )
        self.assertEqual(duplicate[0]["value"], 1)
        with self.assertRaises(Exception):
            parse_etf_flow_history(
                {"code": 0, "data": [
                    {"date": "2026-09-04", "total_net_inflow": 1},
                    {"date": "2026-09-04", "total_net_inflow": 2},
                ]},
                ("flows.etf_net_1d",),
                fetched_at="2026-09-05T00:00:00Z",
            )

    def test_sosovalue_provider_uses_documented_header_and_market_two_fetches(self):
        class FakeSoSoValueClient:
            def __init__(self):
                self.calls = []

            def get_json(self, url, *, params=None, headers=None):
                self.calls.append((url, params, headers))
                symbol = params["symbol"]
                return {"code": 0, "data": [{"date": "2026-09-04", "total_net_inflow": 10 if symbol == "BTC" else 3}]}

        client = FakeSoSoValueClient()
        provider = SoSoValueProvider(client=client, api_key="fake-secret")
        request = ProviderRequest(
            "sosovalue", "etf", "MARKET", {"as_of": "2026-09-06T00:00:00Z"}, ("flows.etf_net_1d",)
        )
        response = provider.collect(request)
        self.assertEqual(response.network_requests, 2)
        self.assertEqual(response.observations[0]["value"], 13)
        self.assertEqual(response.observations[0]["metadata"]["etf_scope"], "BTC+ETH")
        self.assertEqual(response.observations[0]["metadata"]["source_assets"], ["BTC", "ETH"])
        self.assertEqual([call[1]["symbol"] for call in client.calls], ["BTC", "ETH"])
        self.assertTrue(all(call[0] == SOSOVALUE_BASE_URL + ETF_SUMMARY_HISTORY_PATH for call in client.calls))
        self.assertTrue(all(call[2] == {SOSOVALUE_API_KEY_HEADER: "fake-secret"} for call in client.calls))
        self.assertEqual(provider.capabilities.metric_keys, (
            "flows.etf_net_1d", "flows.etf_net_7d", "flows.etf_net_30d",
        ))
        self.assertNotIn("liquidations", str(provider.capabilities.as_dict()).lower())

    def test_sosovalue_provider_redacts_key_from_provider_error(self):
        class LeakingClient:
            def get_json(self, url, *, params=None, headers=None):
                raise RuntimeError(f"request failed for {headers[SOSOVALUE_API_KEY_HEADER]}")

        with self.assertRaises(Exception) as raised:
            SoSoValueProvider(client=LeakingClient(), api_key="fake-secret").collect(
                ProviderRequest("sosovalue", "etf", "BTC", {}, ("flows.etf_net_1d",))
            )
        self.assertNotIn("fake-secret", str(raised.exception))

        class DiagnosticLeakingClient:
            def get_json(self, url, *, params=None, headers=None):
                raise ProviderUnavailable(
                    "request failed",
                    diagnostic=ProviderDiagnostic(
                        endpoint=url,
                        detail=f"header value {headers[SOSOVALUE_API_KEY_HEADER]}",
                    ),
                )

        with self.assertRaises(ProviderUnavailable) as raised:
            SoSoValueProvider(client=DiagnosticLeakingClient(), api_key="fake-secret").collect(
                ProviderRequest("sosovalue", "etf", "BTC", {}, ("flows.etf_net_1d",))
            )
        self.assertNotIn("fake-secret", str(raised.exception.diagnostic))

    def test_etf_routes_to_sosovalue_and_liquidations_have_no_structured_route(self):
        self.assertEqual(provider_chain("flows.etf_net_1d"), ("sosovalue",))
        self.assertEqual(provider_chain("flows.etf_net_7d"), ("sosovalue",))
        self.assertEqual(provider_chain("flows.etf_net_30d"), ("sosovalue",))
        self.assertEqual(provider_chain("derivatives.total_liquidations_24h_usd"), ())
        self.assertEqual(provider_chain("derivatives.long_liquidations_7d_usd"), ())
        self.assertEqual(load_provider_config()["providers"]["sosovalue"]["api_key_env"], "SOSOVALUE_API_KEY")
        self.assertNotIn("coinglass", load_provider_config()["providers"])

        provider = StructuredProvider(value=7, source="sosovalue")
        result = AcquisitionManager(
            ProviderRouter({"sosovalue": provider}, config=config_for("sosovalue")),
            persist=False,
        ).run(
            MetricCollectionPlan(
                "SNAPSHOT_REVIEW",
                (MetricRequest("BTC", "derivatives.total_liquidations_24h_usd"),),
            ),
            cached_observations=(),
            as_of="2026-09-05T00:00:00Z",
            now="2026-09-05T00:00:00Z",
        )
        self.assertEqual(provider.calls, 0)
        self.assertEqual(result.results[0].status, "FAILED")
        self.assertEqual(result.web_fallbacks[0].preferred_sources, ("official/current sources",))

    def test_sosovalue_registration_is_key_gated_and_ignores_old_key(self):
        config = config_for("sosovalue")
        config["providers"]["sosovalue"] = {"enabled": "AUTO", "api_key_env": "SOSOVALUE_API_KEY"}
        with patch.dict("os.environ", {"SOSOVALUE_API_KEY": "fake-secret", "COINGLASS_API_KEY": "old-secret"}, clear=False):
            router = ProviderRouter(config=config, http_client=object())
            self.assertIn("sosovalue", router.providers)
            self.assertNotIn("coinglass", router.providers)
        with patch.dict("os.environ", {}, clear=True):
            router = ProviderRouter(config=config, http_client=object())
            self.assertNotIn("sosovalue", router.providers)
            self.assertNotIn("coinglass", router.providers)

    def test_old_coinglass_etf_history_is_replayable_but_not_live_cache(self):
        old = normalize_metric_result({
            "asset": "BTC", "metric_key": "flows.etf_net_1d", "value": 10, "unit": "USD",
            "observed_at": "2026-09-04T20:00:00Z", "fetched_at": "2026-09-04T21:00:00Z",
            "source": "coinglass", "confidence": "HIGH",
        }).observation
        self.assertEqual(
            normalize_metric_result(old.as_dict()).observation.source,
            "coinglass",
        )

        provider = StructuredProvider(value=99, source="sosovalue")
        config = config_for("sosovalue")
        with TemporaryDirectory() as directory:
            result = AcquisitionManager(
                ProviderRouter(
                    {"sosovalue": provider},
                    config=config,
                    cache=ProviderCache(Path(directory) / "cache"),
                ),
                observation_path=Path(directory) / "observations.jsonl",
                persist=False,
            ).run(
                MetricCollectionPlan("SNAPSHOT_REVIEW", (MetricRequest("BTC", "flows.etf_net_1d"),)),
                mode=FetchMode.AUTO,
                as_of="2026-09-05T00:00:00Z",
                now="2026-09-05T00:00:00Z",
                cached_observations=(old,),
            )
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.observations[0].source, "sosovalue")
        self.assertEqual(result.observations[0].value, 99)
        self.assertEqual(result.web_fallbacks, ())

    def test_router_records_a_complete_provider_bundle_as_success(self):
        provider = StructuredProvider(value=7, source="sosovalue")
        with TemporaryDirectory() as directory:
            router = ProviderRouter(
                {"sosovalue": provider},
                config=config_for("sosovalue"),
                cache=ProviderCache(Path(directory) / "cache"),
            )
            result = AcquisitionManager(router, persist=False).run(
                MetricCollectionPlan(
                    "SNAPSHOT_REVIEW",
                    (MetricRequest("BTC", "flows.etf_net_1d"),),
                ),
                mode=FetchMode.REFRESH,
                as_of=None,
                now="2026-09-05T00:00:00Z",
                cached_observations=(),
            )
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.results[0].status, "SUCCESS")
        self.assertEqual(result.summary["counts"]["SUCCESS"], 1)
        self.assertEqual(result.attempts[0]["status"], "SUCCESS")

    def test_bybit_candles_accept_numeric_timestamp_strings(self):
        class BybitClient:
            def get_json(self, url, *, params=None, headers=None):
                return {"retCode": 0, "result": {"list": [[
                    "1767225600000", "100", "101", "99", "100.5", "10", "1005"
                ] ]}}

        provider = BybitProvider(
            client=BybitClient(),
            clock=lambda: datetime(2026, 1, 3, tzinfo=timezone.utc),
        )
        series = provider.candles("BTC", timeframe="1D")
        self.assertEqual(series.candles[0].timestamp, "2026-01-01T00:00:00Z")

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
        soso_a = ProviderRequest("sosovalue", "etf", "BTC", {"x-soso-api-key": "one"}, ("flows.etf_net_1d",), True, 60)
        soso_b = ProviderRequest("sosovalue", "etf", "BTC", {"x-soso-api-key": "two"}, ("flows.etf_net_1d",), True, 60)
        self.assertEqual(request_hash(soso_a), request_hash(soso_b))
        self.assertNotIn("one", str(soso_a.as_dict()))
        self.assertEqual(redact_secrets({"x-soso-api-key": "one"})["x-soso-api-key"], "[REDACTED]")
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
            override.write_text('{"providers":{"sosovalue":{"x-soso-api-key":"secret"}}}', encoding="utf-8")
            with patch.dict("os.environ", {"CRYPTO_PORTFOLIO_PROVIDER_CONFIG": str(override)}, clear=False):
                with self.assertRaises(ValueError):
                    load_provider_config()

    def test_provider_runtime_status_separates_config_adapter_and_credential(self):
        config = config_for("binance", "sosovalue")
        config["providers"]["sosovalue"] = {"enabled": "AUTO", "api_key_env": "SOSOVALUE_API_KEY"}
        adapters = {"binance": object(), "sosovalue": object()}
        missing = provider_runtime_status(config, adapters=adapters, environ={})
        by_name = {item.provider: item for item in missing}
        self.assertTrue(by_name["binance"].runtime_ready)
        self.assertFalse(by_name["sosovalue"].config_enabled)
        self.assertTrue(by_name["sosovalue"].adapter_available)
        self.assertTrue(by_name["sosovalue"].credential_required)
        self.assertFalse(by_name["sosovalue"].runtime_ready)

        ready = provider_runtime_status(config, adapters=adapters, environ={"SOSOVALUE_API_KEY": "secret"})
        self.assertTrue({item.provider: item for item in ready}["sosovalue"].runtime_ready)
        status = provider_status(config, {"SOSOVALUE_API_KEY": "secret"}, adapters=adapters)
        row = {item["provider"]: item for item in status}["sosovalue"]
        self.assertTrue(row["enabled"])
        self.assertTrue(row["config_enabled"])
        self.assertTrue(row["runtime_ready"])
        self.assertNotIn("secret", str(status))

        unavailable = provider_runtime_status(config, adapters={"binance": object()}, environ={"SOSOVALUE_API_KEY": "secret"})
        row = {item.provider: item for item in unavailable}["sosovalue"]
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

    def test_binance_supports_lunc_public_pair(self):
        class FakeClient:
            def get_json(self, url, *, params=None, headers=None):
                self.params = params
                return {"symbol": "LUNCUSDT", "price": "0.00005"}

        client = FakeClient()
        price = BinanceProvider(client=client).spot_price("LUNC")
        self.assertEqual(price.symbol, "LUNC")
        self.assertEqual(client.params["symbol"], "LUNCUSDT")


if __name__ == "__main__":
    unittest.main()
