import unittest

from crypto_portfolio.engine.cash_flow import cash_flow_adjusted_performance
from crypto_portfolio.engine.metric_plan import MetricRequest, build_metric_collection_plan
from crypto_portfolio.engine.positioning import build_positioning_facts
from crypto_portfolio.metrics_registry import METRIC_REGISTRY
from crypto_portfolio.models.portfolio import normalize_snapshot
from crypto_portfolio.providers.base import ProviderRequest
from crypto_portfolio.providers.defillama import DeFiLlamaProvider, parse_chain_fees
from crypto_portfolio.providers.router import ProviderRouter
from crypto_portfolio.providers.routes import provider_chain


REMOVED_METRICS = {
    "eth.monetary.burn_30d_eth",
    "eth.monetary.burn_365d_eth",
    "eth.monetary.burn_to_issuance_30d",
    "eth.monetary.burn_to_issuance_365d",
    "eth.monetary.cumulative_burn_eth",
    "derivatives.long_liquidations_24h_usd",
    "derivatives.short_liquidations_24h_usd",
    "derivatives.total_liquidations_24h_usd",
    "derivatives.long_liquidations_7d_usd",
    "derivatives.short_liquidations_7d_usd",
    "eth.staking.staking_apr_7d",
    "eth.staking.staking_apr_30d",
    "eth.staking.participation_rate",
    "eth.staking.deposit_queue_eth",
    "eth.staking.exit_queue_eth",
    "eth.staking.withdrawal_backlog_eth",
    "eth.structural.consensus_client_largest_share",
    "eth.structural.execution_client_largest_share",
    "eth.structural.staking_entity_largest_share",
    "eth.structural.liquid_staking_largest_share",
    "eth.structural.builder_largest_share",
    "eth.structural.finality_participation_rate",
    "fundamentals.active_users",
    "flows.exchange_netflow",
    "flows.eth_exchange_netflow_to_market_cap",
    "sentiment.social_mentions_24h",
    "sentiment.social_sentiment_percentile",
    "onchain.btc.lth_supply_pct",
    "onchain.btc.sth_realized_price",
    "onchain.btc.lth_realized_price",
    "onchain.btc.nupl",
    "btc_network.hashrate",
    "btc_network.difficulty",
}


class PlanCleanupTests(unittest.TestCase):
    def test_removed_metrics_are_not_current_requests_or_capabilities(self):
        self.assertTrue(REMOVED_METRICS.isdisjoint(METRIC_REGISTRY))
        for key in REMOVED_METRICS:
            with self.subTest(key=key), self.assertRaises(ValueError):
                MetricRequest("ETH", key)
        router = ProviderRouter()
        capabilities = {
            key
            for provider in router.providers.values()
            for key in getattr(provider, "capabilities").metric_keys
        }
        self.assertTrue(REMOVED_METRICS.isdisjoint(capabilities))

    def test_registered_metric_providers_have_retained_capabilities(self):
        router = ProviderRouter()
        for name, provider in router.providers.items():
            keys = set(provider.capabilities.metric_keys)
            if not keys:
                self.assertEqual(name, "ethereum_beacon")
            else:
                self.assertTrue(keys & set(METRIC_REGISTRY), name)

    def test_bnb_plan_uses_blockspace_not_transaction_count(self):
        keys = {item.metric_key for item in build_metric_collection_plan(["BNB"]).for_asset("BNB")}
        self.assertNotIn("onchain.transaction_count", keys)
        self.assertIn("onchain.blockspace_fees", keys)
        self.assertEqual(provider_chain("onchain.blockspace_fees", "ETH"), ("growthepie", "defillama", "coinmetrics_community"))
        self.assertEqual(provider_chain("onchain.blockspace_fees", "BNB"), ("defillama", "coinmetrics_community"))

    def test_defillama_chain_fees_are_completed_day_and_asset_scoped(self):
        payload = {"totalDataChart": [["2026-09-08T00:00:00Z", 10], ["2026-09-09T00:00:00Z", 20]]}
        value = parse_chain_fees(
            payload,
            asset="BNB",
            metric_key="onchain.blockspace_fees",
            fetched_at="2026-09-10T00:00:00Z",
            as_of="2026-09-10T12:00:00Z",
            endpoint="https://api.llama.fi/overview/fees/BSC",
        )
        self.assertEqual(value["value"], 20)
        self.assertEqual(value["metadata"]["chain_scope"], "BSC")
        provider = DeFiLlamaProvider(client=type("Client", (), {"get_json": lambda *_args, **_kwargs: payload})())
        response = provider.collect(ProviderRequest("defillama", "onchain", "ETH", {"as_of": "2026-09-10T12:00:00Z"}, ("onchain.blockspace_fees",)))
        self.assertEqual(response.observations[0]["metric_key"], "onchain.blockspace_fees")

    def test_omitted_cash_flow_is_final_market_performance(self):
        first = {"timestamp": "2026-09-01T00:00:00Z", "positions": [{"symbol": "BTC", "value_usd": 100}]}
        second = {"timestamp": "2026-09-02T00:00:00Z", "positions": [{"symbol": "BTC", "value_usd": 150}]}
        self.assertEqual(normalize_snapshot(first)["cash_flow_resolution_status"], "ASSUMED_NONE")
        result = cash_flow_adjusted_performance((first, second))
        self.assertEqual(result["performance_finality"], "FINAL")
        self.assertAlmostEqual(result["return"], 0.5)

    def test_positioning_without_liquidations_is_supported(self):
        facts = build_positioning_facts({
            "derivatives.funding_rate_7d_avg": 0.0,
            "derivatives.open_interest_change_7d": -0.1,
            "derivatives.long_short_account_ratio": 1.0,
        }, as_of="2026-09-10T00:00:00Z")
        self.assertNotIn("liquidation", str(facts.as_dict()).lower())


if __name__ == "__main__":
    unittest.main()
