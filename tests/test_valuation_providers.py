from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from crypto_portfolio.acquisition import AcquisitionManager
from crypto_portfolio.engine.derived_metrics import derive_fdv_market_cap_ratio
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest
from crypto_portfolio.providers.base import ProviderCapabilities, ProviderRequest
from crypto_portfolio.providers.cache import ProviderCache, request_hash
from crypto_portfolio.providers.coinmetrics import COINMETRICS_ASSETS, CoinMetricsProvider
from crypto_portfolio.providers.coingecko import (
    BASE_URL,
    COINGECKO_API_KEY_HEADER,
    CoinGeckoProvider,
    parse_breadth_payload,
)
from crypto_portfolio.providers.config import load_provider_config
from crypto_portfolio.providers.defillama import DeFiLlamaProvider, parse_protocol_payload, parse_stablecoin_chart
from crypto_portfolio.providers.http import redact_secrets
from crypto_portfolio.providers.router import ProviderRouter
from crypto_portfolio.providers.routes import (
    build_provider_requests,
    cache_ttl_seconds,
    dataset_for_metric,
    provider_chain,
)


NOW = "2026-09-06T00:00:00Z"


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, *, params=None, headers=None):
        self.calls.append((url, params, headers))
        return self.payload


class ValuationProviderTests(unittest.TestCase):
    def test_bnb_coingecko_market_cap_and_fdv_use_binancecoin(self):
        client = FakeClient([{
            "id": "binancecoin",
            "market_cap": 100,
            "fully_diluted_valuation": 110,
            "last_updated": "2026-09-05T23:59:00Z",
        }])
        response = CoinGeckoProvider(client=client, api_key="fake-key").collect(ProviderRequest(
            "coingecko", "valuation", "BNB", {"as_of": None},
            ("valuation.market_cap", "valuation.fdv"),
        ))
        self.assertEqual(client.calls[0][1]["ids"], "binancecoin")
        self.assertEqual({item["metric_key"] for item in response.observations}, {
            "valuation.market_cap", "valuation.fdv",
        })

    def test_current_coingecko_bundle_and_source_timestamp(self):
        client = FakeClient([{
            "id": "bitcoin",
            "market_cap": 100,
            "fully_diluted_valuation": 150,
            "last_updated": "2026-09-05T23:59:00Z",
        }])
        response = CoinGeckoProvider(client=client, api_key="fake-key").collect(ProviderRequest(
            "coingecko", "valuation", "BTC", {"as_of": None},
            ("valuation.market_cap", "valuation.fdv"),
        ))
        self.assertEqual(len(client.calls), 1)
        url, params, headers = client.calls[0]
        self.assertEqual(url, BASE_URL + "/coins/markets")
        self.assertEqual(params["ids"], "bitcoin")
        self.assertEqual(headers, {COINGECKO_API_KEY_HEADER: "fake-key"})
        self.assertEqual({item["metric_key"] for item in response.observations}, {
            "valuation.market_cap", "valuation.fdv",
        })
        self.assertTrue(all(item["observed_at"] == "2026-09-05T23:59:00Z" for item in response.observations))
        self.assertEqual(response.observations[0]["metadata"]["provider_asset_id"], "bitcoin")

    def test_null_fdv_keeps_market_cap_success(self):
        response = CoinGeckoProvider(
            client=FakeClient([{
                "id": "aave",
                "market_cap": 120,
                "fully_diluted_valuation": None,
                "last_updated": "2026-09-05T23:59:00Z",
            }]),
            api_key="fake-key",
        ).collect(ProviderRequest(
            "coingecko", "valuation", "AAVE", {"as_of": None},
            ("valuation.market_cap", "valuation.fdv"),
        ))
        self.assertEqual([item["metric_key"] for item in response.observations], ["valuation.market_cap"])
        self.assertEqual(response.diagnostics["valuation.fdv"]["error_code"], "COINGECKO_NO_FDV")

    def test_historical_request_uses_history_only_and_never_current_value(self):
        client = FakeClient({"market_data": {"market_cap": {"usd": 90}}})
        response = CoinGeckoProvider(client=client, api_key="fake-key").collect(ProviderRequest(
            "coingecko", "valuation", "BTC", {"as_of": "2026-09-05T12:00:00Z"},
            ("valuation.market_cap", "valuation.fdv"),
        ))
        self.assertEqual(client.calls[0][0], BASE_URL + "/coins/bitcoin/history")
        self.assertEqual(client.calls[0][1]["date"], "05-09-2026")
        self.assertEqual(response.observations[0]["value"], 90)
        self.assertEqual(response.observations[0]["observed_at"], "2026-09-05T00:00:00Z")
        self.assertEqual(response.diagnostics["valuation.fdv"]["error_code"], "COINGECKO_NO_FDV")

    def test_invalid_market_rows_do_not_become_observations(self):
        response = CoinGeckoProvider(client=FakeClient([
            {"id": "bitcoin", "market_cap": float("nan"), "fully_diluted_valuation": 2,
             "last_updated": "2026-09-05T23:59:00Z"},
        ]), api_key="fake-key").collect(ProviderRequest(
            "coingecko", "valuation", "BTC", {"as_of": None},
            ("valuation.market_cap", "valuation.fdv"),
        ))
        self.assertEqual([item["metric_key"] for item in response.observations], ["valuation.fdv"])
        self.assertEqual(response.diagnostics["valuation.market_cap"]["error_code"], "COINGECKO_SCHEMA")

    def test_routes_dataset_ttl_and_config(self):
        self.assertEqual(provider_chain("market.btc_dominance", "MARKET"), ("coingecko",))
        self.assertEqual(provider_chain("market.total_crypto_market_cap", "MARKET"), ("coingecko",))
        self.assertEqual(provider_chain("market.stablecoin_supply", "MARKET"), ("defillama",))
        self.assertEqual(provider_chain("market.breadth_state", "MARKET"), ())
        self.assertEqual(provider_chain("valuation.market_cap", "BTC"), (
            "coingecko", "coinmetrics_community", "coinmetrics_pro",
        ))
        self.assertEqual(provider_chain("valuation.fdv", "AAVE"), ("coingecko",))
        self.assertEqual(provider_chain("valuation.fdv_market_cap_ratio", "AAVE"), ())
        self.assertEqual(provider_chain("valuation.fee_revenue_multiple", "AAVE"), ("defillama",))
        self.assertEqual(dataset_for_metric("valuation.market_cap"), "valuation")
        self.assertEqual(dataset_for_metric("valuation.fdv_market_cap_ratio"), "derived")
        self.assertEqual(cache_ttl_seconds("valuation", load_provider_config()["cache_ttl_seconds"]), 3600)
        request = MetricRequest("BTC", "valuation.market_cap")
        current = build_provider_requests((request,), now=NOW, ttl_seconds={"valuation": 3600})[0]
        historical = build_provider_requests(
            (request,), as_of="2026-09-05T00:00:00Z", now=NOW, ttl_seconds={"valuation": 3600},
        )[0]
        self.assertTrue(current.mutable)
        self.assertFalse(historical.mutable)

    def test_coingecko_global_contract_batches_dominance_and_market_cap(self):
        client = FakeClient({
            "data": {
                "updated_at": 1788779806,
                "market_cap_percentage": {"btc": 59.1},
                "total_market_cap": {"usd": 2_690_000_000_000},
            },
        })
        response = CoinGeckoProvider(client=client, api_key="fake-key").collect(ProviderRequest(
            "coingecko", "market_global", "MARKET", {},
            ("market.btc_dominance", "market.total_crypto_market_cap"),
        ))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], BASE_URL + "/global")
        values = {item["metric_key"]: item["value"] for item in response.observations}
        self.assertAlmostEqual(values["market.btc_dominance"], 0.591)
        self.assertEqual(values["market.total_crypto_market_cap"], 2_690_000_000_000)

    def test_coingecko_breadth_uses_current_top20_return_contract(self):
        rows = [
            {
                "id": f"asset-{index}",
                "symbol": f"a{index}",
                "price_change_percentage_30d_in_currency": 1 if index < 12 else -1,
                "last_updated": "2026-09-07T00:00:00Z",
            }
            for index in range(20)
        ]
        value = parse_breadth_payload(rows, fetched_at=NOW)
        self.assertEqual(value["metric_key"], "market.breadth")
        self.assertEqual(value["value"], 0.6)
        self.assertEqual(value["metadata"]["universe_size"], 20)

    def test_defillama_stablecoin_chart_is_chain_supply_not_protocol_tvl(self):
        value = parse_stablecoin_chart(
            [
                {"date": "1788691200", "totalCirculatingUSD": {"peggedUSD": 100}},
                {"date": "1788777600", "totalCirculatingUSD": {"peggedUSD": 125}},
            ],
            asset="ETH",
            metric_key="fundamentals.stablecoin_liquidity",
            fetched_at=NOW,
            endpoint="https://stablecoins.llama.fi/stablecoincharts/Ethereum",
        )
        self.assertEqual(value["value"], 125)
        self.assertEqual(value["metadata"]["scope"], "Ethereum")

    def test_defillama_no_longer_owns_market_valuation(self):
        capabilities = DeFiLlamaProvider(client=object()).capabilities
        self.assertNotIn("valuation.market_cap", capabilities.metric_keys)
        self.assertNotIn("valuation.fdv", capabilities.metric_keys)
        self.assertNotIn("valuation.fdv_market_cap_ratio", capabilities.metric_keys)
        with self.assertRaises(Exception):
            parse_protocol_payload(
                {"mcap": 100, "fdv": 150}, "AAVE", ("valuation.market_cap",),
                fetched_at=NOW,
            )

    def test_coinmetrics_market_cap_is_catalog_and_timestamp_aware(self):
        class Client:
            def __init__(self):
                self.calls = []

            def get_json(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if "catalog" in url:
                    return {"data": [{
                        "metric": "CapMrktEstUSD",
                        "frequencies": [{"frequency": "1d", "assets": ["aave"]}],
                    }]}
                return {"data": [{"time": "2026-09-05T00:00:00Z", "CapMrktEstUSD": "88"}]}

        client = Client()
        values = CoinMetricsProvider(client=client).collect(ProviderRequest(
            "coinmetrics_community", "valuation", "AAVE", {"as_of": NOW},
            ("valuation.market_cap",),
        ))
        self.assertEqual(values[0]["source"], "coinmetrics_community")
        self.assertEqual(values[0]["value"], 88)
        self.assertEqual(values[0]["period"], "1d")
        self.assertEqual(values[0]["observed_at"], "2026-09-05T00:00:00Z")
        self.assertEqual(values[0]["metadata"]["coinmetrics_metric"], "CapMrktEstUSD")
        self.assertEqual(values[0]["metadata"]["methodology"], "coinmetrics_estimated_circulating_supply_market_cap")
        self.assertEqual(client.calls[1][1]["params"]["metrics"], "CapMrktEstUSD")

    def test_coinmetrics_bnb_market_cap_uses_verified_community_mapping(self):
        self.assertEqual(COINMETRICS_ASSETS["BNB"], "bnb")

        class Client:
            def __init__(self):
                self.calls = []

            def get_json(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if "catalog" in url:
                    return {"data": [{
                        "metric": "CapMrktEstUSD",
                        "frequencies": [{"frequency": "1d", "assets": ["bnb"]}],
                    }]}
                return {"data": [{"time": "2026-09-05T00:00:00Z", "CapMrktEstUSD": "88"}]}

        client = Client()
        values = CoinMetricsProvider(client=client).collect(ProviderRequest(
            "coinmetrics_community", "valuation", "BNB", {"as_of": NOW},
            ("valuation.market_cap",),
        ))
        self.assertEqual(values[0]["value"], 88)
        self.assertEqual(client.calls[1][1]["params"]["assets"], "bnb")

    def test_market_cap_falls_back_when_coingecko_is_disabled(self):
        class FallbackProvider:
            capabilities = ProviderCapabilities(
                "coinmetrics_community", ("valuation.market_cap",), ("valuation.market_cap",), True,
            )

            def collect(self, request):
                return [{
                    "asset": request.asset,
                    "metric_key": "valuation.market_cap",
                    "value": 88,
                    "unit": "USD",
                    "period": "1d",
                    "observed_at": "2026-09-05T00:00:00Z",
                    "fetched_at": NOW,
                    "source": "coinmetrics_community",
                    "confidence": "MEDIUM",
                    "metadata": {
                        "coinmetrics_metric": "CapMrktEstUSD",
                        "methodology": "coinmetrics_estimated_circulating_supply_market_cap",
                    },
                }]

        config = {
            "version": 1,
            "providers": {
                "coingecko": {"enabled": False},
                "coinmetrics_community": {"enabled": True},
            },
            "cache_ttl_seconds": {"default": 3600, "valuation": 3600},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": True},
        }
        with TemporaryDirectory() as directory:
            result = AcquisitionManager(
                ProviderRouter(
                    {"coingecko": CoinGeckoProvider(client=FakeClient([]), api_key="fake-key"),
                     "coinmetrics_community": FallbackProvider()},
                    config=config,
                    cache=ProviderCache(Path(directory) / "provider-cache"),
                ),
                persist=False,
            ).run(
                MetricCollectionPlan("SNAPSHOT_REVIEW", (MetricRequest("BTC", "valuation.market_cap"),)),
                cached_observations=(),
                now=NOW,
            )
        self.assertEqual(result.results[0].status, "SUCCESS")
        self.assertEqual(result.observations[0].source, "coinmetrics_community")
        self.assertEqual(result.observations[0].value, 88)

    def test_fdv_market_cap_ratio_is_python_derived_and_lookahead_safe(self):
        fdv = {
            "asset": "BTC", "value": 150, "observation_id": "fdv", "observed_at": "2026-09-05T00:00:00Z",
            "source": "coingecko", "freshness": "CURRENT",
        }
        cap = {
            "asset": "BTC", "value": 100, "observation_id": "cap", "observed_at": "2026-09-04T00:00:00Z",
            "source": "coinmetrics_community", "freshness": "CURRENT",
        }
        result = derive_fdv_market_cap_ratio("BTC", fdv, cap, fetched_at=NOW, as_of=NOW)
        self.assertEqual(result["value"], 1.5)
        self.assertEqual(result["source"], "python-derived")
        self.assertEqual(result["metadata"]["input_observation_ids"], ["fdv", "cap"])
        self.assertIsNone(derive_fdv_market_cap_ratio(
            "BTC", {**fdv, "observed_at": "2026-09-07T00:00:00Z"}, cap,
            fetched_at=NOW, as_of=NOW,
        ))

    def test_direct_ratio_request_fetches_dependencies_without_provider_route(self):
        client = FakeClient([{
            "id": "bitcoin",
            "market_cap": 100,
            "fully_diluted_valuation": 150,
            "last_updated": "2026-09-05T23:59:00Z",
        }])
        config = {
            "version": 1,
            "providers": {"coingecko": {"enabled": True}},
            "cache_ttl_seconds": {"default": 3600, "valuation": 3600},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": True},
        }
        with TemporaryDirectory() as directory:
            result = AcquisitionManager(
                ProviderRouter(
                    {"coingecko": CoinGeckoProvider(client=client, api_key="fake-key")},
                    config=config,
                    cache=ProviderCache(Path(directory) / "provider-cache"),
                ),
                persist=False,
            ).run(
                MetricCollectionPlan("SNAPSHOT_REVIEW", (
                    MetricRequest("BTC", "valuation.fdv_market_cap_ratio"),
                )),
                cached_observations=(),
                now=NOW,
            )
        self.assertEqual(result.results[0].status, "SUCCESS")
        self.assertEqual(result.observations[0].value, 1.5)
        self.assertEqual(len(client.calls), 1)

    def test_coin_gecko_secret_forms_are_redacted_and_not_cached(self):
        value = "COINGECKO_API_KEY=fake-key x-cg-demo-api-key: fake-key"
        self.assertNotIn("fake-key", redact_secrets(value))
        request = ProviderRequest(
            "coingecko", "valuation", "BTC",
            {"as_of": None, "x-cg-demo-api-key": "fake-key"},
            ("valuation.market_cap",),
        )
        self.assertNotIn("fake-key", str(request.parameters))
        self.assertEqual(request_hash(request), request_hash(ProviderRequest(
            "coingecko", "valuation", "BTC", {"as_of": None}, ("valuation.market_cap",),
        )))


if __name__ == "__main__":
    unittest.main()
