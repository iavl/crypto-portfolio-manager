import json
import unittest
from io import BytesIO
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

from crypto_portfolio.providers.base import (
    ProviderDataError,
    ProviderInsufficientHistory,
    ProviderRequest,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from crypto_portfolio.providers.bgeometrics import BGeometricsProvider, parse_mvrv_zscore
from crypto_portfolio.providers.growthepie import (
    EXPORT_TVL_PATH,
    FUNDAMENTALS_PATH,
    GrowthepieProvider,
    LANDING_PAGE_PATH,
    MASTER_PATH,
    parse_landing_page_payload,
    parse_tvs_export_payload,
)
from crypto_portfolio.providers.ethereum_beacon import EthereumBeaconProvider
from crypto_portfolio.providers.rated import RatedProvider, parse_daily_rewards, parse_queues
from crypto_portfolio.providers.http import HttpClient
from crypto_portfolio.providers.routes import build_provider_requests, provider_chain
from crypto_portfolio.providers.cache import ProviderCache
from crypto_portfolio.providers.router import ProviderRouter
from crypto_portfolio.engine.derived_metrics import derive_metric_observations
from crypto_portfolio.engine.metric_plan import MetricRequest
from crypto_portfolio.models.metrics_history import MetricObservation, stable_observation_id


def _landing_payload(days: int = 32):
    start = date(2026, 8, 1)
    tx_rows = []
    fee_rows = []
    for index in range(days):
        stamp = datetime.combine(start + timedelta(days=index), datetime.min.time(), tzinfo=timezone.utc)
        unix_ms = int(stamp.timestamp() * 1000)
        tx_rows.append([unix_ms, index + 1])
        fee_rows.append([unix_ms, 100 + index, 1])
    return {
        "data": {
            "all_l2s": {"metrics": {"txcount": {"daily": {"types": ["unix", "value"], "data": tx_rows}}}},
            "ethereum": {"metrics": {"fees": {"daily": {"types": ["unix", "usd", "eth"], "data": fee_rows}}}},
        }
    }


_PROVIDER_FIXTURES = Path(__file__).parent / "fixtures/providers"


def _provider_fixture(name: str):
    return json.loads((_PROVIDER_FIXTURES / name).read_text())


class FreeProviderTests(unittest.TestCase):
    def test_rated_subscription_body_is_classified_without_raw_body(self):
        def opener(request, **_kwargs):
            raise HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                {},
                BytesIO(b'{"detail":"Subscription is not active."}'),
            )

        result = RatedProvider(
            client=HttpClient(opener=opener, max_attempts=1),
            api_key="secret",
        ).collect(ProviderRequest(
            "rated", "staking", "ETH", {"as_of": "2026-09-10T00:00:00Z"}, ("eth.staking.staking_apr_7d",),
        ))
        diagnostic = result.diagnostics["eth.staking.staking_apr_7d"]
        self.assertEqual(diagnostic["error_code"], "RATED_SUBSCRIPTION_INACTIVE")
        self.assertEqual(diagnostic["detail"], "Subscription is not active.")
        self.assertNotIn("secret", str(diagnostic))
    def test_bgeometrics_uses_source_date_and_rejects_stale_values(self):
        payload = {"d": "2026-09-08", "unixTs": 1788825600, "mvrvZscore": 0.8725}
        observation = parse_mvrv_zscore(payload, fetched_at="2026-09-09T00:00:00Z")
        self.assertEqual(observation["observed_at"], "2026-09-08T00:00:00Z")
        self.assertEqual(observation["metadata"]["source_url"], "https://bitcoin-data.com/v1/mvrv-zscore/last")
        with self.assertRaises(ProviderInsufficientHistory):
            parse_mvrv_zscore(payload, fetched_at="2026-09-20T00:00:00Z")

    def test_bgeometrics_provider_is_no_key_and_one_request(self):
        class Client:
            def __init__(self):
                self.calls = []

            def get_json(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return {"d": "2026-09-08", "mvrvZscore": 1.25}

        client = Client()
        provider = BGeometricsProvider(client=client, clock=lambda: datetime(2026, 9, 9, tzinfo=timezone.utc))
        result = provider.collect(ProviderRequest(
            "bgeometrics", "onchain", "BTC", {"as_of": "2026-09-09T00:00:00Z"},
            ("btc_valuation.mvrv_zscore",),
        ))
        self.assertFalse(provider.capabilities.requires_api_key)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result.observations[0]["value"], 1.25)

    def test_bgeometrics_latest_value_uses_ttl_cache_after_refresh(self):
        class Client:
            def __init__(self):
                self.calls = 0

            def get_json(self, *_args, **_kwargs):
                self.calls += 1
                return {"d": "2026-09-08", "mvrvZscore": 1.25}

        config = {
            "providers": {"bgeometrics": {"enabled": True}},
            "cache_ttl_seconds": {"onchain": 86400, "default": 3600},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": False},
        }
        client = Client()
        with TemporaryDirectory() as directory:
            router = ProviderRouter(
                {"bgeometrics": BGeometricsProvider(client=client)},
                config=config,
                cache=ProviderCache(Path(directory) / "cache"),
            )
            request = build_provider_requests(
                (MetricRequest("BTC", "btc_valuation.mvrv_zscore"),),
                as_of="2026-09-09T00:00:00Z",
                now="2026-09-09T00:00:00Z",
            )[0]
            first = router.collect((request,), mode="REFRESH", as_of="2026-09-09T00:00:00Z", now="2026-09-09T00:00:00Z")
            second = router.collect((request,), mode="AUTO", as_of="2026-09-09T00:00:00Z", now="2026-09-09T00:00:00Z")
        self.assertEqual(first.api_requests, 1)
        self.assertEqual(second.api_requests, 0)
        self.assertEqual(second.provider_cache_hits, 1)
        self.assertEqual(client.calls, 1)

    def test_growthepie_landing_page_sums_l2_activity_and_keeps_fees_daily(self):
        values = parse_landing_page_payload(
            _landing_payload(),
            ("eth.l2.activity_30d", "onchain.blockspace_fees"),
            fetched_at="2026-09-02T00:00:00Z",
            as_of="2026-09-02T00:00:00Z",
        )
        by_key = {item["metric_key"]: item for item in values}
        self.assertEqual(by_key["eth.l2.activity_30d"]["value"], sum(range(3, 33)))
        self.assertEqual(by_key["eth.l2.activity_30d"]["metadata"]["chain_scope"], "all_l2s")
        self.assertEqual(by_key["onchain.blockspace_fees"]["value"], 131)
        self.assertEqual(by_key["onchain.blockspace_fees"]["metadata"]["methodology"], "fees_paid_by_users")

    def test_growthepie_tvs_does_not_use_another_metric_as_a_substitute(self):
        with self.assertRaises(ProviderUnsupportedMetric):
            parse_landing_page_payload(
                _landing_payload(), ("eth.l2.tvs_usd",), fetched_at="2026-09-02T00:00:00Z",
            )

    def test_growthepie_tvs_aggregates_exact_usd_rows_on_common_completed_day(self):
        observation = parse_tvs_export_payload(
            _provider_fixture("growthepie_tvl_export.json"),
            _provider_fixture("growthepie_tvs_master.json"),
            fetched_at="2026-09-09T00:00:00Z",
            as_of="2026-09-09T12:00:00Z",
        )[0]
        self.assertEqual(observation["value"], 70)
        self.assertEqual(observation["observed_at"], "2026-09-05T00:00:00Z")
        self.assertEqual(observation["unit"], "USD")
        self.assertEqual(observation["source"], "growthepie")
        self.assertEqual(observation["metadata"]["chain_count"], 2)
        self.assertEqual(observation["metadata"]["chain_set"], ["arbitrum", "base"])
        self.assertEqual(observation["metadata"]["metric_key"], "tvl")

    def test_growthepie_tvs_excludes_l1_sidechain_and_aggregate_keys(self):
        observation = parse_tvs_export_payload(
            _provider_fixture("growthepie_tvl_export.json"),
            _provider_fixture("growthepie_tvs_master.json"),
            fetched_at="2026-09-09T00:00:00Z",
            as_of="2026-09-09T12:00:00Z",
        )[0]
        self.assertEqual(observation["value"], 70)
        self.assertNotIn("ethereum", observation["metadata"]["chain_set"])
        self.assertNotIn("polygon_pos", observation["metadata"]["chain_set"])
        self.assertNotIn("all_l2s", observation["metadata"]["chain_set"])
        self.assertNotIn("multiple", observation["metadata"]["chain_set"])

    def test_growthepie_tvs_obeys_as_of_and_never_uses_future_or_same_day_rows(self):
        export = _provider_fixture("growthepie_tvl_export.json")
        master = _provider_fixture("growthepie_tvs_master.json")
        current_day = parse_tvs_export_payload(
            export, master, fetched_at="2026-09-09T00:00:00Z", as_of="2026-09-06T12:00:00Z",
        )[0]
        earlier_day = parse_tvs_export_payload(
            export, master, fetched_at="2026-09-09T00:00:00Z", as_of="2026-09-05T12:00:00Z",
        )[0]
        self.assertEqual(current_day["value"], 70)
        self.assertEqual(current_day["observed_at"], "2026-09-05T00:00:00Z")
        self.assertEqual(earlier_day["value"], 30)
        self.assertEqual(earlier_day["observed_at"], "2026-09-04T00:00:00Z")

    def test_growthepie_tvs_does_not_zero_fill_missing_chain(self):
        export = [
            row for row in _provider_fixture("growthepie_tvl_export.json")
            if not (row["origin_key"] == "base" and row["metric_key"] == "tvl")
        ]
        with self.assertRaises(ProviderInsufficientHistory):
            parse_tvs_export_payload(
                export,
                _provider_fixture("growthepie_tvs_master.json"),
                fetched_at="2026-09-09T00:00:00Z",
                as_of="2026-09-09T12:00:00Z",
            )

    def test_growthepie_tvs_rejects_conflicting_duplicate_rows(self):
        export = _provider_fixture("growthepie_tvl_export.json")
        export.append({"metric_key": "tvl", "origin_key": "arbitrum", "date": "2026-09-05", "value": 31})
        with self.assertRaises(ProviderDataError):
            parse_tvs_export_payload(
                export,
                _provider_fixture("growthepie_tvs_master.json"),
                fetched_at="2026-09-09T00:00:00Z",
                as_of="2026-09-09T12:00:00Z",
            )

    def test_growthepie_tvs_requires_unambiguous_usd_metric_contract(self):
        master = _provider_fixture("growthepie_tvs_master.json")
        master["metrics"]["tvl"]["metric_keys"] = ["tvl", "tvl_eth", "usd_candidate"]
        with self.assertRaises(ProviderResponseError):
            parse_tvs_export_payload(
                _provider_fixture("growthepie_tvl_export.json"),
                master,
                fetched_at="2026-09-09T00:00:00Z",
                as_of="2026-09-09T12:00:00Z",
            )

    def test_growthepie_tvs_uses_bulk_export_and_caches_master(self):
        class Client:
            def __init__(self):
                self.urls = []

            def get_json(self, url, **_kwargs):
                self.urls.append(url)
                if url.endswith(MASTER_PATH):
                    return _provider_fixture("growthepie_tvs_master.json")
                if url.endswith(EXPORT_TVL_PATH):
                    return _provider_fixture("growthepie_tvl_export.json")
                raise AssertionError(f"unexpected URL: {url}")

        client = Client()
        provider = GrowthepieProvider(client=client, clock=lambda: datetime(2026, 9, 9, tzinfo=timezone.utc))
        request = ProviderRequest(
            "growthepie", "ethereum_l2", "ETH", {"as_of": "2026-09-09T12:00:00Z"}, ("eth.l2.tvs_usd",),
        )
        first = provider.collect(request)
        second = provider.collect(request)
        self.assertEqual(first.network_requests, 2)
        self.assertEqual(second.network_requests, 1)
        self.assertEqual(client.urls.count("https://api.growthepie.com/v1/master.json"), 1)
        self.assertEqual(client.urls.count("https://api.growthepie.com/v1/export/tvl.json"), 2)
        self.assertTrue(all(not url.endswith(LANDING_PAGE_PATH) for url in client.urls))
        self.assertEqual(first.observations[0]["source"], "growthepie")

    def test_growthepie_tvs_and_fundamentals_share_one_master_request(self):
        class Client:
            def __init__(self):
                self.urls = []

            def get_json(self, url, **_kwargs):
                self.urls.append(url)
                if url.endswith(MASTER_PATH):
                    return _provider_fixture("growthepie_tvs_master.json")
                if url.endswith(EXPORT_TVL_PATH):
                    return _provider_fixture("growthepie_tvl_export.json")
                if url.endswith(FUNDAMENTALS_PATH):
                    return []
                raise AssertionError(f"unexpected URL: {url}")

        client = Client()
        provider = GrowthepieProvider(client=client, clock=lambda: datetime(2026, 9, 9, tzinfo=timezone.utc))
        result = provider.collect(ProviderRequest(
            "growthepie", "ethereum_l2", "ETH", {"as_of": "2026-09-09T12:00:00Z"},
            ("eth.l2.tvs_usd", "eth.l2.rent_paid_30d_usd"),
        ))
        self.assertEqual(result.network_requests, 3)
        self.assertEqual(client.urls.count("https://api.growthepie.com/v1/master.json"), 1)
        self.assertEqual(client.urls.count("https://api.growthepie.com/v1/fundamentals.json"), 1)
        self.assertEqual([item["metric_key"] for item in result.observations], ["eth.l2.tvs_usd"])

    def test_free_routes_are_primary_for_confirmed_metrics(self):
        self.assertEqual(provider_chain("btc_valuation.mvrv_zscore", "BTC")[0], "bgeometrics")
        self.assertEqual(provider_chain("onchain.blockspace_fees", "ETH")[0], "growthepie")
        self.assertEqual(provider_chain("eth.l2.activity_30d", "ETH")[0], "growthepie")
        self.assertEqual(provider_chain("eth.l2.tvs_usd", "ETH"), ("growthepie",))
        self.assertEqual(provider_chain("onchain.transfer_volume", "ETH"), ("blockchair",))

    def test_rated_uses_effective_balance_and_declares_reward_components(self):
        rows = []
        for index in range(31):
            rows.append({
                "date": (date(2026, 8, 2) + timedelta(days=index)).isoformat(),
                "activeValidators": 1,
                "sumEffectiveBalance": str((32 + index) * 1_000_000_000),
                "sumConsensusRewards": 1_000_000_000,
                "sumExecutionRewards": 2_000_000_000,
                "sumPriorityFees": 0,
                "sumBaselineMev": 0,
            })
        values = parse_daily_rewards(
            {"data": rows},
            ("eth.staking.active_effective_stake_eth", "eth.staking.staking_apr_7d", "eth.staking.staking_apr_30d"),
            fetched_at="2026-09-02T00:00:00Z",
            as_of="2026-09-02T00:00:00Z",
        )
        by_key = {item["metric_key"]: item for item in values}
        self.assertEqual(by_key["eth.staking.active_effective_stake_eth"]["value"], 62)
        self.assertEqual(by_key["eth.staking.staking_apr_30d"]["metadata"]["reward_components"], ["consensus", "execution"])
        expected = (3 * 30) / (sum(range(33, 63)) / 30) * 365 / 30
        self.assertAlmostEqual(by_key["eth.staking.staking_apr_30d"]["value"], expected)

    def test_rated_queues_require_explicit_eth_balance_and_timestamp(self):
        payload = {"results": [{
            "date": "2026-09-01",
            "activatingStake": 40_000_000_000,
            "exitingStake": 5_000_000_000,
            "totalWithdrawingBalance": 7_000_000_000,
        }]}
        values = parse_queues(
            payload,
            ("eth.staking.deposit_queue_eth", "eth.staking.exit_queue_eth", "eth.staking.withdrawal_backlog_eth"),
            fetched_at="2026-09-02T00:00:00Z",
        )
        self.assertEqual({item["value"] for item in values}, {40.0, 5.0, 7.0})
        with self.assertRaises(ProviderResponseError):
            parse_queues(
                {"results": [{"activatingStake": 1, "exitingStake": 1, "totalWithdrawingBalance": 1}]},
                ("eth.staking.deposit_queue_eth",), fetched_at="2026-09-02T00:00:00Z",
            )

    def test_rated_provider_batches_daily_rewards_and_queues(self):
        class Client:
            def __init__(self):
                self.calls = []

            def get_json(self, url, *, params=None, headers=None):
                self.calls.append((url, params, headers))
                if url.endswith("dailyRewards"):
                    return {"data": [{
                        "date": "2026-09-01",
                        "sumEffectiveBalance": "40000000000",
                        "sumConsensusRewards": 1,
                        "sumExecutionRewards": 1,
                    }]}
                return {"results": [{
                    "date": "2026-09-01",
                    "activatingStake": 1,
                    "exitingStake": 2,
                    "totalWithdrawingBalance": 3,
                }]}

        client = Client()
        provider = RatedProvider(client=client, api_key="fake-key")
        result = provider.collect(ProviderRequest(
            "rated", "ethereum_staking", "ETH", {"as_of": "2026-09-02T00:00:00Z"},
            ("eth.staking.active_effective_stake_eth", "eth.staking.deposit_queue_eth"),
        ))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual({item["metric_key"] for item in result.observations}, {
            "eth.staking.active_effective_stake_eth", "eth.staking.deposit_queue_eth",
        })
        self.assertTrue(all(call[2]["Authorization"] == "Bearer fake-key" for call in client.calls))

    def test_active_stake_change_uses_cached_aligned_history(self):
        def observation(day, value):
            stamp = f"{day}T23:59:59Z"
            return MetricObservation(
                stable_observation_id("ETH", "eth.staking.active_effective_stake_eth", stamp, "rated", value, "1d"),
                "ETH", "eth.staking.active_effective_stake_eth", "fundamentals", value, "ETH", "1d",
                stamp, "2026-09-09T00:00:00Z", "rated", "CURRENT", "MEDIUM",
                metadata={"methodology": "rated_sum_effective_balance"},
            )

        current = observation("2026-09-08", 40)
        prior = observation("2026-08-09", 32)
        values, unresolved = derive_metric_observations(
            (MetricRequest("ETH", "eth.staking.active_effective_stake_change_30d"),),
            {("ETH", "eth.staking.active_effective_stake_eth"): current},
            {},
            fetched_at="2026-09-09T00:00:00Z",
            as_of="2026-09-09T00:00:00Z",
            historical_observations=(prior,),
        )
        self.assertEqual(values[("ETH", "eth.staking.active_effective_stake_change_30d")]["value"], 8)
        self.assertEqual(unresolved, {})

    def test_staking_raw_requests_share_one_budget_bundle(self):
        requests = build_provider_requests(tuple(
            MetricRequest("ETH", key) for key in (
                "eth.staking.active_effective_stake_eth",
                "eth.staking.staking_apr_7d",
                "eth.staking.staking_apr_30d",
                "eth.staking.deposit_queue_eth",
            )
        ), as_of="2026-09-09T00:00:00Z", now="2026-09-09T00:00:00Z")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].provider, "rated")

    def test_beacon_provider_is_bounded_and_does_not_scan_validators(self):
        class Client:
            def get_json(self, url, **kwargs):
                return {"data": {"version": "v", "finalized": "0x1"}}

        result = EthereumBeaconProvider(client=Client(), base_url="https://beacon.example").probe()
        self.assertTrue(result["bounded"])
        self.assertFalse(result["validator_registry_scan"])


if __name__ == "__main__":
    unittest.main()
