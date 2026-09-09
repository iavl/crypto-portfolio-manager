import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from crypto_portfolio.acquisition import AcquisitionManager
from crypto_portfolio.data_collection import build_failed_data_fetches
from crypto_portfolio.engine.derived_metrics import derive_active_effective_stake_change, derive_cumulative_burn_delta
from crypto_portfolio.engine.metric_plan import MetricRequest
from crypto_portfolio.metric_availability import fallback_mode, metric_availability
from crypto_portfolio.metric_history_requirements import history_requirement
from crypto_portfolio.models.metrics_history import CollectionEvent
from crypto_portfolio.providers.base import ProviderRequest, ProviderResponse
from crypto_portfolio.providers.coinmetrics import CoinMetricsProvider
from crypto_portfolio.providers.routes import build_provider_requests, provider_chain
from crypto_portfolio.providers.etherscan import parse_ethsupply2
from crypto_portfolio.providers.ultrasound_money import parse_burn_rates


NOW = "2026-09-09T00:00:00Z"


class _CoinMetricsClient:
    def get_json(self, url, **_kwargs):
        if "catalog" in url:
            return {"data": [
                {"metric": "SplyCur", "frequencies": [{"frequency": "1d", "assets": ["eth"]}]},
                {"metric": "IssTotNtv", "frequencies": [{"frequency": "1d", "assets": ["eth"]}]},
            ]}
        base = datetime(2026, 6, 1, tzinfo=timezone.utc)
        return {"data": [
            {
                "time": (base + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
                "SplyCur": 100 + index,
                "IssTotNtv": 1,
            }
            for index in range(101)
        ]}


class PlanRepairTests(unittest.TestCase):
    def test_history_requirements_are_explicit(self):
        self.assertEqual(history_requirement("market.return_30d").as_dict(), {
            "mode": "BOUNDED", "days": 45, "tolerance_days": 7,
        })
        self.assertEqual(history_requirement("market.return_90d").days, 105)
        self.assertEqual(history_requirement("market.return_180d").days, 195)
        self.assertEqual(history_requirement("eth.monetary.issuance_365d_eth").days, 380)
        self.assertEqual(history_requirement("btc_valuation.mvrv_zscore").mode, "FULL_AVAILABLE")

    def test_execution_and_metric_history_cohorts_are_separate(self):
        requests = (
            MetricRequest("ETH", "market.return_180d"),
            MetricRequest("ETH", "eth.monetary.issuance_365d_eth"),
        )
        built = build_provider_requests(requests, as_of=NOW, now=NOW)
        by_dataset = {request.dataset: request for request in built}
        self.assertEqual((datetime.fromisoformat(by_dataset["ohlcv"].parameters["end"].replace("Z", "+00:00")) - datetime.fromisoformat(by_dataset["ohlcv"].parameters["start"].replace("Z", "+00:00"))).days, 240)
        self.assertEqual((datetime.fromisoformat(by_dataset["ethereum_protocol"].parameters["end"].replace("Z", "+00:00")) - datetime.fromisoformat(by_dataset["ethereum_protocol"].parameters["start"].replace("Z", "+00:00"))).days, 380)
        self.assertNotEqual(by_dataset["ohlcv"].history_cohort, by_dataset["ethereum_protocol"].history_cohort)
        full = build_provider_requests((MetricRequest("BTC", "btc_valuation.mvrv_zscore"),), as_of=NOW, now=NOW)[0]
        self.assertEqual(full.parameters["history_mode"], "FULL_AVAILABLE")
        self.assertNotIn("start", full.parameters)

    def test_coinmetrics_partial_success_keeps_siblings(self):
        response = CoinMetricsProvider(client=_CoinMetricsClient()).collect(ProviderRequest(
            "coinmetrics_community",
            "ethereum_protocol",
            "ETH",
            {"start": "2026-06-01T00:00:00Z", "end": NOW, "as_of": NOW},
            ("eth.monetary.current_supply_eth", "eth.monetary.issuance_30d_eth", "eth.monetary.issuance_365d_eth"),
        ))
        self.assertIsInstance(response, ProviderResponse)
        self.assertEqual(
            {item["metric_key"] for item in response.observations},
            {"eth.monetary.current_supply_eth", "eth.monetary.issuance_30d_eth"},
        )
        self.assertEqual(response.diagnostics["eth.monetary.issuance_365d_eth"]["error_code"], "PROVIDER_INSUFFICIENT_HISTORY")

    def test_full_history_provider_payload_is_cached_for_replay(self):
        class Client:
            def __init__(self):
                self.calls = 0

            def get_json(self, url, **_kwargs):
                self.calls += 1
                if "catalog" in url:
                    return {"data": [
                        {"metric": "CapMrktCurUSD", "frequencies": [{"frequency": "1d", "assets": ["btc"]}]},
                        {"metric": "CapRealUSD", "frequencies": [{"frequency": "1d", "assets": ["btc"]}]},
                    ]}
                start = _kwargs.get("params", {}).get("start_time")
                days = (4, 5, 6, 7, 8, 9) if start else (1, 2, 3)
                return {"data": [
                    {"time": f"2026-09-0{index}T00:00:00Z", "CapMrktCurUSD": 100 + index, "CapRealUSD": 50 + index}
                    for index in days
                ]}

        client = Client()
        with TemporaryDirectory() as directory:
            from crypto_portfolio.providers.cache import ProviderCache
            from crypto_portfolio.providers.router import ProviderRouter
            router = ProviderRouter(
                {"coinmetrics_community": CoinMetricsProvider(client=client)},
                config={
                    "providers": {"coinmetrics_community": {"enabled": True}},
                    "cache_ttl_seconds": {"default": 3600},
                    "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
                    "fallback": {"allow_web": False},
                },
                cache=ProviderCache(Path(directory) / "cache"),
            )
            request = build_provider_requests((MetricRequest("BTC", "btc_valuation.mvrv_zscore"),), as_of=NOW, now=NOW)[0]
            first = router.collect((request,), mode="REFRESH", as_of=NOW, now=NOW)
            second = router.collect((request,), mode="REFRESH", as_of=NOW, now=NOW)
        self.assertEqual(len(first.observations), 1)
        self.assertEqual(client.calls, 3)
        self.assertEqual(second.provider_cache_hits, 0)

    def test_cached_direct_mvrv_z_is_replayed_without_forcing_derivation(self):
        provider = CoinMetricsProvider()
        request = build_provider_requests(
            (MetricRequest("BTC", "btc_valuation.mvrv_zscore"),),
            as_of=NOW,
            now=NOW,
        )[0]
        response = provider.parse_cached_payload(
            request,
            {"data": [{"time": NOW, "CapMVRVZ": 4.0}]},
            fetched_at=NOW,
        )
        self.assertEqual(response.observations[0]["value"], 4.0)
        self.assertEqual(response.observations[0]["metadata"]["source_mode"], "DIRECT")

    def test_bnb_routes_to_catalog_aware_coinmetrics(self):
        self.assertEqual(provider_chain("onchain.active_addresses", "BNB"), ("coinmetrics_community", "coinmetrics_pro"))
        self.assertEqual(provider_chain("fundamentals.active_users", "BNB"), ())
        self.assertEqual(metric_availability("BNB", "fundamentals.active_users").requirement, "OPTIONAL")
        self.assertEqual(metric_availability("BNB", "onchain.transfer_volume").requirement, "OPTIONAL")
        self.assertEqual(metric_availability("BNB", "onchain.blockspace_fees").reason_code, "OPTIONAL_PROVIDER_UNSUPPORTED")

    def test_numeric_fallback_is_structured_only(self):
        for key in ("onchain.active_addresses", "eth.monetary.issuance_365d_eth", "eth.l2.tvs_usd"):
            self.assertEqual(fallback_mode(key), "STRUCTURED_ONLY")
        self.assertEqual(fallback_mode("risk.security_event_status"), "WEB_ALLOWED")
        self.assertEqual(fallback_mode("eth.structural.builder_largest_share"), "WEB_ALLOWED")
        self.assertEqual(MetricRequest("BTC", "risk.security_event_status").as_dict()["fallback_mode"], "WEB_ALLOWED")

    def test_cumulative_burn_requires_same_source_and_aligned_history(self):
        current = {"value": 120, "observed_at": "2026-09-01T00:00:00Z", "source": "etherscan", "metadata": {"methodology": "counter"}}
        prior = {"value": 100, "observed_at": "2026-08-01T00:00:00Z", "source": "etherscan", "metadata": {"methodology": "counter"}}
        result = derive_cumulative_burn_delta("ETH", current, prior, days=30, fetched_at="2026-09-01T01:00:00Z")
        self.assertEqual(result["value"], 20)
        self.assertIsNone(derive_cumulative_burn_delta(
            "ETH", current, {**prior, "source": "other"}, days=30, fetched_at="2026-09-01T01:00:00Z",
        ))

    def test_active_effective_stake_history_is_date_aligned(self):
        current = {"value": 32, "observed_at": "2026-09-09T00:00:00Z", "source": "fixture", "metadata": {"methodology": "effective_balance"}}
        prior = {"value": 30, "observed_at": "2026-08-10T00:00:00Z", "source": "fixture", "metadata": {"methodology": "effective_balance"}}
        result = derive_active_effective_stake_change("ETH", current, prior, days=30, fetched_at=NOW)
        self.assertEqual(result["value"], 2)
        self.assertIsNone(derive_active_effective_stake_change("ETH", current, prior, days=90, fetched_at=NOW))

    def test_public_eth_burn_and_supply_parsers_keep_units_and_fields(self):
        burn = parse_burn_rates({
            "d30": {"rate": {"eth_per_minute": 2}, "timestamp": NOW, "block_number": 10},
        }, ("eth.monetary.burn_30d_eth",), fetched_at=NOW)[0]
        self.assertEqual(burn["value"], 2 * 60 * 24 * 30)
        supply = parse_ethsupply2({
            "status": "1", "message": "OK",
            "result": {"EthSupply": str(10**18), "BurntFees": str(2 * 10**18)},
        }, ("eth.monetary.current_supply_eth", "eth.monetary.cumulative_burn_eth"), fetched_at=NOW)
        self.assertEqual([item["value"] for item in supply], [1.0, 2.0])

    def test_etherscan_errors_redact_the_api_key(self):
        from crypto_portfolio.providers.etherscan import EtherscanProvider

        class Client:
            def get_json(self, *_args, **kwargs):
                raise RuntimeError(f"failed {kwargs['params']['apikey']}")

        with self.assertRaises(Exception) as raised:
            EtherscanProvider(client=Client(), api_key="fake-key").collect(ProviderRequest(
                "etherscan", "etherscan", "ETH", {}, ("eth.monetary.current_supply_eth",),
            ))
        self.assertNotIn("fake-key", str(raised.exception))

    def test_l2beat_openapi_is_probeable_before_credentials(self):
        from unittest.mock import patch
        from crypto_portfolio.providers.l2beat import L2BeatProvider, BASE_URL, OPENAPI_PATH
        from crypto_portfolio.providers.probe import probe_provider
        from crypto_portfolio.providers.router import ProviderRouter

        class Client:
            def get_json(self, url, **_kwargs):
                self.url = url
                return {
                    "openapi": "3.1.0",
                    "servers": [{"url": BASE_URL}],
                    "components": {"securitySchemes": {"apiKeyAuth": {"in": "query", "name": "apiKey", "type": "apiKey"}}},
                    "security": [{"apiKeyAuth": []}],
                    "paths": {
                        "/v1/projects": {"get": {"parameters": []}},
                        "/v1/tvs": {"get": {"parameters": [{"in": "query", "name": "range", "schema": {"enum": ["7d", "30d", "90d", "180d", "1y", "max"]}}]}},
                        "/v1/activity": {"get": {"parameters": [{"in": "query", "name": "range", "schema": {"enum": ["30d", "90d", "180d", "1y", "max"]}}]}},
                    },
                }

        client = Client()
        config = {
            "providers": {"l2beat": {"enabled": "AUTO", "api_key_env": "L2BEAT_API_KEY"}},
            "cache_ttl_seconds": {"default": 3600},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": False},
        }
        with patch.dict("os.environ", {}, clear=True):
            rows = probe_provider(ProviderRouter({"l2beat": L2BeatProvider(client=client)}, config=config), "l2beat")
        self.assertEqual(rows[0]["auth_scheme"], "apiKey query parameter")
        self.assertEqual(rows[0]["error_code"], "CREDENTIAL_MISSING")
        self.assertEqual(client.url, BASE_URL + OPENAPI_PATH)

    def test_optional_failures_are_not_final_required_failures(self):
        event = CollectionEvent(
            "optional", NOW, "BTC", "btc_valuation.mvrv_zscore", "FAILED", reason="PROVIDER_UNSUPPORTED",
        )
        result = AcquisitionManager(persist=False)._failure(
            MetricRequest("BTC", "btc_valuation.mvrv_zscore"), NOW, event.reason,
        )
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(json.dumps(result.event.as_dict(), sort_keys=True).count("btc_valuation"), 1)

        optional = AcquisitionManager._failure(
            MetricRequest("ETH", "eth.monetary.issuance_365d_eth"), NOW, "PROVIDER_INSUFFICIENT_HISTORY",
        )
        self.assertEqual(build_failed_data_fetches({
            "plan": {"review_type": "SNAPSHOT_REVIEW"},
            "results": [optional.as_dict()],
            "attempts": [],
            "event_scans": [],
            "web_fallbacks": [],
            "pending_event_scans": [],
        }), ())


if __name__ == "__main__":
    unittest.main()
