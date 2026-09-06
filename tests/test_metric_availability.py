from datetime import datetime, timedelta, timezone
import unittest

from crypto_portfolio.acquisition import AcquisitionManager
from crypto_portfolio.data_collection import collection_summary
from crypto_portfolio.engine.derived_metrics import derive_open_interest_to_market_cap
from crypto_portfolio.engine.metric_normalization import normalize_metric_result
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest
from crypto_portfolio.metrics_registry import metric_definition
from crypto_portfolio.models.metrics_history import CollectionEvent
from crypto_portfolio.providers.base import ProviderRequest
from crypto_portfolio.providers.base import ProviderUnavailable
from crypto_portfolio.providers.coinmetrics import (
    CoinMetricsProvider,
    catalog_metrics_by_asset,
    parse_exchange_netflow,
    parse_tokenomics,
)
from crypto_portfolio.providers.github_activity import GitHubActivityProvider, count_commits
from crypto_portfolio.providers.router import ProviderRouter


NOW = "2026-09-06T00:00:00Z"


class MetricAvailabilityTests(unittest.TestCase):
    def test_skipped_is_event_only_and_excluded_from_coverage(self):
        skipped = normalize_metric_result({
            "asset": "ETH",
            "metric_key": "fundamentals.developer_activity",
            "status": "SKIPPED",
            "reason": "OPTIONAL_PROVIDER_UNAVAILABLE",
            "source": "python-availability",
            "timestamp": NOW,
        })
        self.assertIsNone(skipped.observation)
        success = CollectionEvent(
            "trend", NOW, "ETH", "market.return_30d", "SUCCESS",
            source="test", observed_at=NOW, fetched_at=NOW,
        )
        summary = collection_summary((success, skipped.event))
        self.assertEqual(summary["counts"]["SKIPPED"], 1)
        self.assertEqual(summary["counts"]["SKIPPED_OPTIONAL"], 1)
        self.assertEqual(summary["coverage"], 1.0)

    def test_required_metrics_cannot_be_skipped(self):
        with self.assertRaises(ValueError):
            CollectionEvent(
                "price", NOW, "BTC", "market.spot_price", "SKIPPED",
                reason="provider unavailable",
            )

    def test_semantic_applicability_is_fail_closed_before_routing(self):
        self.assertFalse(metric_definition("onchain.active_addresses").applies_to("AAVE"))
        self.assertFalse(metric_definition("fundamentals.active_users").applies_to("ETH"))
        self.assertFalse(metric_definition("tokenomics.next_unlock_pct").applies_to("BTC"))
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("AAVE", "onchain.active_addresses"),
            MetricRequest("ETH", "fundamentals.active_users"),
            MetricRequest("BTC", "tokenomics.next_unlock_pct"),
            MetricRequest("AAVE", "fundamentals.stablecoin_liquidity"),
        ))
        result = AcquisitionManager(persist=False).run(
            plan, mode="CACHE_ONLY", cached_observations=(), now=NOW,
        )
        statuses = {(item.event.asset, item.event.metric_key): item.status for item in result.results}
        self.assertEqual(statuses[("AAVE", "onchain.active_addresses")], "NOT_APPLICABLE")
        self.assertEqual(statuses[("ETH", "fundamentals.active_users")], "NOT_APPLICABLE")
        self.assertEqual(statuses[("BTC", "tokenomics.next_unlock_pct")], "NOT_APPLICABLE")
        self.assertEqual(statuses[("AAVE", "fundamentals.stablecoin_liquidity")], "SKIPPED")

    def test_oi_market_cap_is_python_derived(self):
        oi = {
            "asset": "BTC", "value": 10, "observation_id": "oi",
            "observed_at": "2026-09-05T00:00:00Z", "fetched_at": NOW,
            "source": "binance", "freshness": "CURRENT",
        }
        market_cap = {
            "asset": "BTC", "value": 100, "observation_id": "cap",
            "observed_at": "2026-09-04T00:00:00Z", "fetched_at": NOW,
            "source": "structured", "freshness": "CURRENT",
        }
        result = derive_open_interest_to_market_cap(
            "BTC", oi, market_cap, fetched_at=NOW, as_of=NOW,
        )
        self.assertEqual(result["value"], 0.1)
        self.assertEqual(result["source"], "python-derived")
        self.assertEqual(result["metadata"]["input_observation_ids"], ["oi", "cap"])

    def test_coinmetrics_catalog_is_asset_aware_and_tokenomics_is_gross(self):
        catalog = {
            "data": [
                {"metric": "AdrActCnt", "frequencies": [{"frequency": "1d", "assets": ["btc"]}]},
                {"metric": "TxCnt", "frequencies": [{"frequency": "1d", "assets": ["eth"]}]},
            ],
        }
        by_asset = catalog_metrics_by_asset(catalog)
        self.assertEqual(by_asset["btc"], {"adractcnt"})
        self.assertEqual(by_asset["eth"], {"txcnt"})

        base = datetime(2025, 9, 6, tzinfo=timezone.utc)
        rows = [{
            "time": (base + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
            "SplyCur": 100 + index,
            "IssTotNtv": 1,
        } for index in range(366)]
        values = parse_tokenomics(
            {"data": rows}, "ETH",
            ("tokenomics.supply_growth", "tokenomics.annualized_emissions"),
            fetched_at=NOW, as_of=NOW,
        )
        by_key = {item["metric_key"]: item for item in values}
        self.assertAlmostEqual(by_key["tokenomics.supply_growth"]["value"], 3.65)
        self.assertEqual(
            by_key["tokenomics.annualized_emissions"]["metadata"]["methodology"],
            "gross_issuance_trailing_365d / current_supply",
        )
        netflow = parse_exchange_netflow(
            {"data": [{"time": "2026-09-05T00:00:00Z", "FlowInExUSD": "12", "FlowOutExUSD": "5"}]},
            "BTC", fetched_at=NOW,
        )
        self.assertEqual(netflow["value"], 7)

    def test_coinmetrics_provider_requests_only_catalog_supported_asset_metrics(self):
        class Client:
            def __init__(self):
                self.calls = []

            def get_json(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if "catalog" in url:
                    return {"data": [
                        {"metric": "AdrActCnt", "frequencies": [{"frequency": "1d", "assets": ["btc", "eth"]}]},
                        {"metric": "TxCnt", "frequencies": [{"frequency": "1d", "assets": ["btc"]}]},
                    ]}
                return {"data": [{"time": "2026-09-05T00:00:00Z", "AdrActCnt": "3"}]}

        client = Client()
        values = CoinMetricsProvider(client=client).collect(ProviderRequest(
            "coinmetrics_community", "onchain", "ETH", {}, ("onchain.active_addresses", "onchain.transaction_count"),
        ))
        self.assertEqual([item["metric_key"] for item in values], ["onchain.active_addresses"])
        self.assertEqual(client.calls[1][1]["params"]["assets"], "eth")
        self.assertEqual(client.calls[1][1]["params"]["metrics"], "AdrActCnt")

    def test_configured_premium_provider_failure_remains_failed(self):
        class FailingProvider:
            def collect(self, _request):
                raise ProviderUnavailable("premium provider unavailable")

        config = {
            "version": 1,
            "providers": {
                "coinmetrics_community": {"enabled": True},
                "coinmetrics_pro": {"enabled": True},
            },
            "cache_ttl_seconds": {"default": 3600, "onchain": 86400},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": True},
        }
        result = AcquisitionManager(
            ProviderRouter(
                {"coinmetrics_community": FailingProvider(), "coinmetrics_pro": FailingProvider()},
                config=config,
            ),
            persist=False,
        ).run(
            MetricCollectionPlan("SNAPSHOT_REVIEW", (
                MetricRequest("BTC", "flows.exchange_netflow"),
            )),
            cached_observations=(), now=NOW,
        )
        self.assertEqual(result.results[0].status, "FAILED")

    def test_github_activity_is_bounded_and_allowlisted(self):
        page = [{
            "sha": "same-sha",
            "commit": {"author": {"date": "2026-09-05T00:00:00Z"}},
        }, {
            "sha": "old-sha",
            "commit": {"author": {"date": "2025-01-01T00:00:00Z"}},
        }]
        self.assertEqual(count_commits([page, page], start="2026-08-01T00:00:00Z", end=NOW), 1)

        class Client:
            def __init__(self):
                self.calls = []

            def get_json(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return page

        client = Client()
        provider = GitHubActivityProvider(
            client=client,
            clock=lambda: datetime(2026, 9, 6, tzinfo=timezone.utc),
        )
        response = provider.collect(ProviderRequest(
            "github", "github", "ETH",
            {"start": "2026-08-01T00:00:00Z", "end": NOW},
            ("fundamentals.developer_activity",),
        ))
        self.assertEqual(response.observations[0]["value"], 1)
        self.assertEqual(len(client.calls), 3)
        self.assertTrue(all("ethereum/" in call[0] for call in client.calls))
        self.assertNotIn("Authorization", response.observations[0])


if __name__ == "__main__":
    unittest.main()
