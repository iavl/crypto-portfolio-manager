import json
import unittest
from dataclasses import replace
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
from crypto_portfolio.providers.rated import RatedProvider, parse_daily_rewards
from crypto_portfolio.providers.http import HttpClient
from crypto_portfolio.providers.routes import build_provider_requests, provider_chain
from crypto_portfolio.providers.cache import ProviderCache
from crypto_portfolio.providers.router import ProviderRouter
from crypto_portfolio.engine.derived_metrics import derive_metric_observations
from crypto_portfolio.engine.metric_plan import MetricRequest
from crypto_portfolio.metrics_registry import metric_definition
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
            "rated", "staking", "ETH", {"as_of": "2026-09-10T00:00:00Z"}, ("eth.staking.active_effective_stake_eth",),
        ))
        diagnostic = result.diagnostics["eth.staking.active_effective_stake_eth"]
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

    def test_rated_uses_effective_balance(self):
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
            ("eth.staking.active_effective_stake_eth",),
            fetched_at="2026-09-02T00:00:00Z",
            as_of="2026-09-02T00:00:00Z",
        )
        by_key = {item["metric_key"]: item for item in values}
        self.assertEqual(by_key["eth.staking.active_effective_stake_eth"]["value"], 62)
        self.assertEqual(set(by_key), {"eth.staking.active_effective_stake_eth"})

    def test_rated_provider_batches_active_stake(self):
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
                raise AssertionError(f"unexpected URL: {url}")

        client = Client()
        provider = RatedProvider(client=client, api_key="fake-key")
        result = provider.collect(ProviderRequest(
            "rated", "ethereum_staking", "ETH", {"as_of": "2026-09-02T00:00:00Z"},
            ("eth.staking.active_effective_stake_eth",),
        ))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual({item["metric_key"] for item in result.observations}, {"eth.staking.active_effective_stake_eth"})
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


_STABLE_START = datetime(2025, 8, 1, tzinfo=timezone.utc)
_STABLE_DAYS = 420
_STABLE_KEYS = (
    "flows.bnb_stablecoin_supply_change_7d",
    "flows.bnb_stablecoin_supply_change_30d",
    "flows.bnb_stablecoin_supply_change_90d",
)
_STABLE_ENDPOINT = "https://stablecoins.llama.fi/stablecoincharts/BSC"


def _stablecoin_payload(
    *,
    days: int = _STABLE_DAYS,
    base: float = 5_000_000_000.0,
    growth: float = 0.0,
    price_drift: float = 0.0,
    drop_day: int | None = None,
) -> list[dict]:
    """One BSC stablecoin history; ``totalCirculatingUSD`` tracks a price move."""
    rows = []
    supply = base
    price = 1.0
    for offset in range(days):
        supply *= 1.0 + growth
        price *= 1.0 + price_drift
        if drop_day is not None and offset == drop_day:
            continue
        rows.append({
            "date": int((_STABLE_START + timedelta(days=offset)).timestamp()),
            "totalCirculating": {"peggedUSD": supply},
            "totalCirculatingUSD": {"peggedUSD": supply * price},
        })
    return rows


def _stablecoin_as_of(days: int = _STABLE_DAYS) -> str:
    return (_STABLE_START + timedelta(days=days)).isoformat().replace("+00:00", "Z")


def _stablecoin_observations(observations) -> tuple[MetricObservation, ...]:
    return tuple(
        MetricObservation(
            stable_observation_id("BNB", item["metric_key"], item["observed_at"], item["source"], item["value"], item["period"]),
            "BNB", item["metric_key"], "capital_flows", item["value"], item["unit"], item["period"],
            item["observed_at"], item["fetched_at"], item["source"], "CURRENT", item["confidence"],
            metadata=item["metadata"],
        )
        for item in observations
    )


def _parse_supply(payload, *, keys=_STABLE_KEYS, as_of: str | None = None):
    from crypto_portfolio.providers.defillama import parse_stablecoin_supply_changes

    stamp = as_of or _stablecoin_as_of()
    return parse_stablecoin_supply_changes(
        payload,
        asset="BNB",
        metric_keys=keys,
        fetched_at=stamp,
        as_of=stamp,
        endpoint=_STABLE_ENDPOINT,
    )


class BnbStablecoinSupplyFlowTests(unittest.TestCase):
    """BNB capital flows from the free BSC USD-pegged stablecoin supply series."""

    def _parse(self, payload=None, *, keys=_STABLE_KEYS, days=_STABLE_DAYS, **kwargs):
        return _parse_supply(
            _stablecoin_payload(days=days, **kwargs) if payload is None else payload,
            keys=keys,
            as_of=_stablecoin_as_of(days),
        )

    def test_parse_emits_supply_changes_with_percentile_metadata(self):
        observations = self._parse(growth=0.002)
        self.assertEqual(len(observations), 3)
        by_key = {item["metric_key"]: item for item in observations}
        for horizon, window in (("7d", 7), ("30d", 30), ("90d", 90)):
            item = by_key[f"flows.bnb_stablecoin_supply_change_{horizon}"]
            metadata = item["metadata"]
            self.assertAlmostEqual(item["value"], 1.002 ** window - 1, places=6)
            self.assertEqual(metadata["methodology"], "pegged_usd_supply_change_abs_change_percentile")
            self.assertEqual(metadata["source_field"], "totalCirculating.peggedUSD")
            self.assertEqual(metadata["source_dataset"], "stablecoincharts")
            self.assertEqual(metadata["window_days"], window)
            self.assertEqual(metadata["sample_window_days"], 365)
            self.assertEqual(metadata["min_samples_required"], 180)
            self.assertEqual(metadata["calibration_state"], "CALIBRATED")
            self.assertGreaterEqual(metadata["sample_count"], 180)
            self.assertGreaterEqual(metadata["abs_change_percentile"], 0.0)
            self.assertLessEqual(metadata["abs_change_percentile"], 1.0)
            self.assertEqual(len(metadata["source_series_hash"]), 64)
            self.assertEqual(metadata["signal_interpretation"], "usd_pegged_supply_expansion_proxy")
            anchor = datetime.fromisoformat(metadata["anchor_date"].replace("Z", "+00:00"))
            base_day = datetime.fromisoformat(metadata["base_date"].replace("Z", "+00:00"))
            self.assertEqual((anchor - base_day).days, window)
            self.assertEqual(item["asset"], "BNB")
            self.assertEqual(item["confidence"], "MEDIUM")

    def test_a_price_move_alone_never_enters_the_supply_change(self):
        flat = self._parse(growth=0.0, price_drift=0.0)
        priced = self._parse(growth=0.0, price_drift=0.05)
        self.assertEqual(
            [item["value"] for item in flat],
            [item["value"] for item in priced],
        )
        # A fixed nominal supply stays exactly flat even when the price-adjusted
        # USD series inflates by 5% per day.
        for item in priced:
            self.assertEqual(item["value"], 0.0)

    def test_duplicate_malformed_future_and_zero_base_rows_fail(self):
        payload = _stablecoin_payload()
        conflicting = payload + [dict(payload[-1], totalCirculating={"peggedUSD": 1e12})]
        with self.assertRaises(ProviderDataError):
            self._parse(conflicting)
        future = payload + [{
            "date": int((_STABLE_START + timedelta(days=_STABLE_DAYS + 5)).timestamp()),
            "totalCirculating": {"peggedUSD": 9e9},
        }]
        with self.assertRaises(ProviderDataError):
            self._parse(future)
        with self.assertRaises(ProviderResponseError):
            self._parse({"not": "a list"})
        zeroed = _stablecoin_payload()
        zeroed[0] = dict(zeroed[0], totalCirculating={"peggedUSD": 0})
        # Removing the exact 30-day endpoint is a hard failure too.
        with self.assertRaises(ProviderInsufficientHistory):
            self._parse(_stablecoin_payload(drop_day=_STABLE_DAYS - 1 - 30))

    def test_insufficient_history_stays_uncalibrated_and_a_stale_anchor_fails(self):
        from crypto_portfolio.engine.factors.flows import calculate_flow_factor

        # 120 days of history cannot supply 180 same-horizon samples, so every
        # horizon is uncallibrated rather than scored.
        short = self._parse(days=120, growth=0.001)
        self.assertTrue(all(item["metadata"]["calibration_state"] == "UNCALIBRATED" for item in short))
        result = calculate_flow_factor(_stablecoin_observations(short), symbol="BNB")
        self.assertIsNone(result.score)
        self.assertEqual(result.state, "UNKNOWN")

        # A history whose latest completed day is more than three days old is
        # stale and fails closed.
        from crypto_portfolio.providers.defillama import parse_stablecoin_supply_changes

        with self.assertRaises(ProviderInsufficientHistory):
            parse_stablecoin_supply_changes(
                _stablecoin_payload(),
                asset="BNB",
                metric_keys=_STABLE_KEYS,
                fetched_at="2026-10-20T12:00:00Z",
                as_of="2026-10-20T12:00:00Z",
                endpoint=_STABLE_ENDPOINT,
            )

    def test_flow_factor_scores_every_horizon_and_keeps_normalized_flow_empty(self):
        from crypto_portfolio.engine.factors.flows import (
            METHOD_SUPPLY_CHANGE_PERCENTILE,
            calculate_flow_factor,
        )

        observations = _stablecoin_observations(self._parse(growth=0.002))
        result = calculate_flow_factor(observations, symbol="BNB")
        self.assertEqual(result.method, METHOD_SUPPLY_CHANGE_PERCENTILE)
        self.assertIsNone(result.normalized_flow)
        self.assertEqual(result.coverage, 1.0)
        self.assertEqual(result.effective_weight, 1.0)
        self.assertEqual(set(result.horizons), {"7d", "30d", "90d"})
        self.assertEqual([result.horizons[key]["weight"] for key in ("7d", "30d", "90d")], [0.2, 0.4, 0.4])
        expected = sum(
            result.horizons[key]["raw_score"] * result.horizons[key]["weight"]
            for key in result.horizons
        )
        self.assertAlmostEqual(result.score, expected, places=9)
        self.assertAlmostEqual(
            sum(result.horizons[key]["contribution"] for key in result.horizons),
            result.score,
            places=9,
        )
        # Every horizon is rising, so every raw score is above neutral.
        for row in result.horizons.values():
            self.assertGreater(row["raw_score"], 50.0)
        self.assertEqual(result.state, "POSITIVE")
        # A MEDIUM-confidence free source is never reported as HIGH quality.
        self.assertEqual(result.confidence, "MEDIUM")
        self.assertEqual(result.source_confidence, "MEDIUM")

    def test_zero_and_negative_changes_never_exceed_neutral(self):
        from crypto_portfolio.engine.factors.flows import calculate_flow_factor

        flat = calculate_flow_factor(_stablecoin_observations(self._parse(growth=0.0)), symbol="BNB")
        for row in flat.horizons.values():
            self.assertEqual(row["raw_score"], 50.0)
        self.assertEqual(flat.score, 50.0)
        self.assertEqual(flat.state, "NEUTRAL")

        shrinking = calculate_flow_factor(
            _stablecoin_observations(self._parse(growth=-0.001)), symbol="BNB"
        )
        for row in shrinking.horizons.values():
            self.assertLessEqual(row["raw_score"], 50.0)
        self.assertLess(shrinking.score, 50.0)
        self.assertEqual(shrinking.state, "NEGATIVE")

    def test_bigger_positive_change_never_lowers_the_score(self):
        from crypto_portfolio.engine.factors.flows import calculate_flow_factor

        scores = [
            calculate_flow_factor(_stablecoin_observations(self._parse(growth=rate)), symbol="BNB").score
            for rate in (0.0005, 0.001, 0.002, 0.004)
        ]
        self.assertEqual(scores, sorted(scores))

    def test_missing_horizon_reduces_completeness_without_double_penalty(self):
        from crypto_portfolio.engine.factors.flows import calculate_flow_factor
        from crypto_portfolio.engine.scoring import score_factors

        partial = calculate_flow_factor(
            _stablecoin_observations(
                self._parse(keys=("flows.bnb_stablecoin_supply_change_7d",), growth=0.002)
            ),
            symbol="BNB",
        )
        self.assertAlmostEqual(partial.coverage, 0.2)
        self.assertEqual(partial.effective_weight, 0.2)
        self.assertLess(partial.score, 100.0)
        scored = score_factors({"capital_flows": partial}, {"capital_flows": 1.0})
        # completeness 0.2 * freshness 1.0 * source quality 0.75
        self.assertAlmostEqual(scored.factor_reliability["capital_flows"], 0.15)

    def test_uncalibrated_history_stays_unavailable(self):
        from crypto_portfolio.engine.factors.flows import calculate_flow_factor

        # Fewer than 180 usable same-horizon samples leaves the horizon
        # uncalibrated rather than awarding it a full score.
        observations = self._parse(days=200, growth=0.002)
        uncalibrated = [
            item for item in observations if item["metadata"]["calibration_state"] == "UNCALIBRATED"
        ]
        self.assertEqual(
            {item["metric_key"] for item in uncalibrated},
            {"flows.bnb_stablecoin_supply_change_30d", "flows.bnb_stablecoin_supply_change_90d"},
        )
        result = calculate_flow_factor(_stablecoin_observations(observations), symbol="BNB")
        for key in ("30d", "90d"):
            row = result.horizons[key]
            self.assertEqual(row["calibration_state"], "UNCALIBRATED")
            self.assertIsNone(row["raw_score"])
        self.assertAlmostEqual(result.coverage, 0.2)
        self.assertIn("uncalibrated", " ".join(result.reasons))

    def test_no_usable_horizon_is_missing_not_a_full_score(self):
        from crypto_portfolio.engine.factors.flows import calculate_flow_factor
        from crypto_portfolio.engine.scoring import score_factors

        observations = _stablecoin_observations(self._parse())
        stripped = tuple(
            replace(item, metadata={**item.metadata, "abs_change_percentile": None, "calibration_state": "UNCALIBRATED", "calibration_reason": "no history"})
            for item in observations
        )
        result = calculate_flow_factor(stripped, symbol="BNB")
        self.assertIsNone(result.score)
        self.assertEqual(result.state, "UNKNOWN")
        self.assertEqual(result.coverage, 0.0)
        scored = score_factors({"capital_flows": result}, {"capital_flows": 1.0})
        self.assertEqual(scored.factor_availability["capital_flows"], "MISSING")

    def test_registry_and_routes_expose_only_the_new_bnb_metrics(self):
        from crypto_portfolio.providers.routes import dataset_for_metric
        from crypto_portfolio.metrics_registry import METRIC_REGISTRY

        for key in _STABLE_KEYS:
            self.assertEqual(provider_chain(key, "BNB"), ("defillama",))
            self.assertEqual(provider_chain(key, "ETH"), ())
            self.assertEqual(dataset_for_metric(key), "stablecoin")
        self.assertNotIn("flows.bnb_chain_tvl_change_1d", METRIC_REGISTRY)
        self.assertNotIn("flows.bnb_chain_tvl_change_7d", METRIC_REGISTRY)
        self.assertNotIn("flows.bnb_chain_tvl_change_30d", METRIC_REGISTRY)
        # BNB no longer consumes the "fees divided by revenue" pseudo-multiple,
        # and no other asset does either: it has no price denominator and it
        # re-scored the same series `fundamentals` already owns.
        self.assertNotIn("valuation.fee_revenue_multiple", METRIC_REGISTRY)
        self.assertFalse(METRIC_REGISTRY["fundamentals.fees_30d"].applies_to("BNB"))
        self.assertFalse(METRIC_REGISTRY["fundamentals.revenue_30d"].applies_to("BNB"))

    def test_provider_collect_uses_one_stablecoin_request_for_all_horizons(self):
        class Client:
            def __init__(self, payload):
                self._payload = payload
                self.calls = 0

            def get_json(self, url, *, params=None, headers=None, max_response_bytes=None):
                self.calls += 1
                assert url.endswith("/stablecoincharts/BSC"), url
                return self._payload

        from crypto_portfolio.providers.defillama import DeFiLlamaProvider

        client = Client(_stablecoin_payload(growth=0.001))
        provider = DeFiLlamaProvider(client=client)
        response = provider.collect(ProviderRequest(
            provider="defillama", dataset="stablecoin", asset="BNB",
            parameters={"as_of": _stablecoin_as_of()}, metric_keys=_STABLE_KEYS,
        ))
        self.assertEqual(client.calls, 1)
        self.assertEqual({item["metric_key"] for item in response.observations}, set(_STABLE_KEYS))


class BnbNetworkFeesTests(unittest.TestCase):
    """BNB network gas fees from the free dailyFees series, not /overview/fees."""

    def _payload(self, *, days=200, value=1_000_000.0, growth=0.0, drop_day=None):
        rows = []
        for offset in range(days):
            if drop_day is not None and offset == drop_day:
                continue
            rows.append([
                (_STABLE_START + timedelta(days=offset)).isoformat().replace("+00:00", "Z"),
                value * (1.0 + growth) ** offset,
            ])
        return {"totalDataChart": rows}

    def _parse(self, payload=None, *, keys=("onchain.blockspace_fees",) + (
        "onchain.bnb_network_fees_30d_usd",
        "onchain.bnb_network_fees_90d_usd",
        "onchain.bnb_network_fees_30d_change",
        "onchain.bnb_network_fees_90d_change",
    ), days=200, **kwargs):
        from crypto_portfolio.providers.defillama import parse_chain_daily_fees

        return parse_chain_daily_fees(
            self._payload(days=days, **kwargs) if payload is None else payload,
            asset="BNB",
            metric_keys=keys,
            fetched_at=_stablecoin_as_of(days),
            as_of=_stablecoin_as_of(days),
            endpoint="https://api.llama.fi/summary/fees/bsc",
        )

    def test_daily_fees_contract_is_attached_to_every_observation(self):
        observations, diagnostics = self._parse()
        self.assertEqual(diagnostics, {})
        by_key = {item["metric_key"]: item for item in observations}
        blockspace = by_key["onchain.blockspace_fees"]
        self.assertEqual(blockspace["value"], 1_000_000.0)
        self.assertEqual(blockspace["metadata"]["source_dataset"], "summary/fees")
        self.assertEqual(blockspace["metadata"]["fees_data_type"], "dailyFees")
        self.assertEqual(blockspace["metadata"]["methodology"], "daily_fees_latest_complete_utc_day")
        self.assertTrue(blockspace["metadata"]["usd_denominated"])
        for key, item in by_key.items():
            if key == "onchain.blockspace_fees":
                continue
            self.assertEqual(item["metadata"]["methodology"], "daily_fees_window_over_complete_utc_days")
            self.assertEqual(item["metadata"]["source_dataset"], "summary/fees")
            self.assertEqual(item["metadata"]["fees_data_type"], "dailyFees")
        self.assertEqual(by_key["onchain.bnb_network_fees_90d_usd"]["value"], 90_000_000.0)
        self.assertEqual(by_key["onchain.bnb_network_fees_30d_usd"]["value"], 30_000_000.0)

    def test_window_change_and_missing_day_behaviour(self):
        observations, diagnostics = self._parse(growth=0.01)
        by_key = {item["metric_key"]: item for item in observations}
        change = by_key["onchain.bnb_network_fees_30d_change"]
        self.assertGreater(change["value"], 0.0)
        self.assertEqual(change["metadata"]["window_days"], 30)
        self.assertEqual(change["metadata"]["complete_utc_days"], 30)

        partial, diagnostics = self._parse(drop_day=200 - 1 - 10)
        self.assertEqual({item["metric_key"] for item in partial}, {"onchain.blockspace_fees"})
        self.assertIn("onchain.bnb_network_fees_30d_usd", diagnostics)
        self.assertIn("onchain.bnb_network_fees_90d_usd", diagnostics)

    def test_legacy_aggregate_cache_is_rejected_for_bnb(self):
        from crypto_portfolio.providers.defillama import DeFiLlamaProvider

        provider = DeFiLlamaProvider()
        request = ProviderRequest(
            "defillama", "onchain", "BNB", {}, ("onchain.blockspace_fees",),
        )
        stale = [{
            "metric_key": "onchain.blockspace_fees",
            "metadata": {
                "source_dataset": "overview/fees",
                "methodology": "totalDataChart_latest_completed_utc_day",
            },
        }]
        self.assertFalse(provider.validate_cached_observations(request, stale))
        current = [{
            "metric_key": "onchain.blockspace_fees",
            "metadata": {
                "source_dataset": "summary/fees",
                "fees_data_type": "dailyFees",
                "methodology": "daily_fees_latest_complete_utc_day",
            },
        }]
        self.assertTrue(provider.validate_cached_observations(request, current))
        other_asset = ProviderRequest("defillama", "onchain", "ETH", {}, ("onchain.blockspace_fees",))
        self.assertTrue(provider.validate_cached_observations(other_asset, stale))

    def test_bnb_network_fees_route_to_defillama_only(self):
        from crypto_portfolio.providers.routes import dataset_for_metric

        for key in (
            "onchain.bnb_network_fees_30d_usd",
            "onchain.bnb_network_fees_90d_usd",
            "onchain.bnb_network_fees_30d_change",
            "onchain.bnb_network_fees_90d_change",
        ):
            self.assertEqual(provider_chain(key, "BNB"), ("defillama",))
            self.assertEqual(provider_chain(key, "ETH"), ())
            self.assertEqual(dataset_for_metric(key), "onchain")
        derived = "valuation.bnb_market_cap_to_annualized_network_fees_90d"
        self.assertEqual(provider_chain(derived, "BNB"), ())
        self.assertEqual(dataset_for_metric(derived), "derived")


class BnbValuationScaleTests(unittest.TestCase):
    """Market cap over annualized 90-day network fees — a scale, not a P/E."""

    def _observation(self, metric_key, value, unit, observed_at="2026-09-19T00:00:00Z", asset="BNB"):
        return MetricObservation(
            stable_observation_id(asset, metric_key, observed_at, "fixture", value),
            asset, metric_key, metric_definition(metric_key).factor, value, unit, "current",
            observed_at, "2026-09-20T00:00:00Z", "fixture", "CURRENT", "HIGH",
        )

    def test_annualization_is_90_day_based(self):
        from crypto_portfolio.engine.derived_metrics import (
            calculate_bnb_market_cap_to_annualized_network_fees,
        )

        value = calculate_bnb_market_cap_to_annualized_network_fees(90_000_000_000.0, 46_140_000.0)
        self.assertAlmostEqual(value, 90_000_000_000.0 / (46_140_000.0 * 365 / 90), places=9)
        for bad_cap, bad_fees in ((0.0, 46_140_000.0), (-1.0, 46_140_000.0), (9e10, 0.0), (9e10, -1.0)):
            with self.assertRaises(ValueError):
                calculate_bnb_market_cap_to_annualized_network_fees(bad_cap, bad_fees)

    def test_derivation_needs_aligned_same_asset_inputs(self):
        from crypto_portfolio.engine.derived_metrics import derive_metric_observations

        request = (MetricRequest("BNB", "valuation.bnb_market_cap_to_annualized_network_fees_90d"),)
        cap = self._observation("valuation.market_cap", 90_000_000_000.0, "USD")
        fees = self._observation("onchain.bnb_network_fees_90d_usd", 46_140_000.0, "USD")
        values, unresolved = derive_metric_observations(
            request,
            {("BNB", "valuation.market_cap"): cap, ("BNB", "onchain.bnb_network_fees_90d_usd"): fees},
            {},
            fetched_at="2026-09-20T00:00:00Z",
            as_of="2026-09-20T00:00:00Z",
        )
        key = ("BNB", "valuation.bnb_market_cap_to_annualized_network_fees_90d")
        self.assertEqual(unresolved, {})
        derived = values[key]
        self.assertEqual(derived["unit"], "ratio")
        self.assertEqual(derived["period"], "90d")
        self.assertEqual(derived["source"], "python-derived")
        self.assertAlmostEqual(
            derived["value"], 90_000_000_000.0 / (46_140_000.0 * 365 / 90), places=9
        )

        # A market cap nine days away from the fee anchor cannot describe the
        # same period.
        stale_cap = self._observation("valuation.market_cap", 90_000_000_000.0, "USD", "2026-09-10T00:00:00Z")
        values, unresolved = derive_metric_observations(
            request,
            {("BNB", "valuation.market_cap"): stale_cap, ("BNB", "onchain.bnb_network_fees_90d_usd"): fees},
            {},
            fetched_at="2026-09-20T00:00:00Z",
            as_of="2026-09-20T00:00:00Z",
        )
        self.assertEqual(values, {})
        self.assertIn(key, unresolved)

        # Zero fees never produce an infinite scale.
        zero_fees = self._observation("onchain.bnb_network_fees_90d_usd", 0.0, "USD")
        values, unresolved = derive_metric_observations(
            request,
            {("BNB", "valuation.market_cap"): cap, ("BNB", "onchain.bnb_network_fees_90d_usd"): zero_fees},
            {},
            fetched_at="2026-09-20T00:00:00Z",
            as_of="2026-09-20T00:00:00Z",
        )
        self.assertEqual(values, {})
        self.assertIn(key, unresolved)

    def test_derivation_is_bnb_only(self):
        from crypto_portfolio.engine.derived_metrics import (
            derive_bnb_market_cap_to_annualized_network_fees,
        )

        self.assertIsNone(
            derive_bnb_market_cap_to_annualized_network_fees(
                "ETH",
                self._observation("valuation.market_cap", 9e10, "USD", asset="ETH"),
                self._observation("onchain.bnb_network_fees_90d_usd", 4.6e7, "USD", asset="ETH"),
                fetched_at="2026-09-20T00:00:00Z",
                as_of="2026-09-20T00:00:00Z",
            )
        )


class BnbSupplyProxyContractTests(unittest.TestCase):
    """The persisted supply-proxy inputs are validated, not re-derived."""

    def _metadata(self, *, horizon_days=7, percentile=0.4, state="CALIBRATED", sample_count=200):
        anchor = _STABLE_START + timedelta(days=_STABLE_DAYS - 1)
        return {
            "source_dataset": "stablecoincharts",
            "source_url": _STABLE_ENDPOINT,
            "source_field": "totalCirculating.peggedUSD",
            "methodology": "pegged_usd_supply_change_abs_change_percentile",
            "chain_scope": "BSC",
            "signal_interpretation": "usd_pegged_supply_expansion_proxy",
            "window_days": horizon_days,
            "sample_window_days": 365,
            "min_samples_required": 180,
            "supply_change_ratio": 0.01,
            "abs_change_percentile": percentile,
            "sample_count": sample_count,
            "calibration_state": state,
            "anchor_date": anchor.isoformat().replace("+00:00", "Z"),
            "base_date": (anchor - timedelta(days=horizon_days)).isoformat().replace("+00:00", "Z"),
            "anchor_supply_usd": 1.0e10,
            "base_supply_usd": 9.9e9,
            "source_series_hash": "b" * 64,
            "source_confidence": "MEDIUM",
            **({"calibration_reason": "no history"} if state == "UNCALIBRATED" else {}),
        }

    def test_valid_metadata_passes(self):
        from crypto_portfolio.metrics_registry import validate_metric_observation_metadata

        validate_metric_observation_metadata(
            "flows.bnb_stablecoin_supply_change_7d", "BNB", 0.01, self._metadata()
        )

    def test_tampered_metadata_is_rejected(self):
        from crypto_portfolio.metrics_registry import validate_metric_observation_metadata

        key = "flows.bnb_stablecoin_supply_change_7d"
        cases = {
            "percentile above one": {"abs_change_percentile": 1.4},
            "percentile missing while calibrated": {"abs_change_percentile": None},
            "wrong methodology": {"methodology": "latest_totalCirculatingUSD.peggedUSD"},
            "wrong window": {"window_days": 30},
            "too few samples while calibrated": {"sample_count": 179},
            "base day not exactly one horizon earlier": {"base_date": "2026-09-10T00:00:00Z"},
            "anchor not a UTC day boundary": {"anchor_date": "2026-09-19T05:00:00Z"},
            "bad series hash": {"source_series_hash": "not-a-digest"},
            "uncalibrated with a percentile": {"calibration_state": "UNCALIBRATED", "abs_change_percentile": 0.5},
            "uncalibrated without a reason": {"calibration_state": "UNCALIBRATED", "abs_change_percentile": None},
        }
        for name, patch in cases.items():
            with self.subTest(case=name):
                metadata = {**self._metadata(), **patch}
                with self.assertRaises(ValueError):
                    validate_metric_observation_metadata(key, "BNB", 0.01, metadata)

    def test_metadata_value_must_match_the_observation(self):
        from crypto_portfolio.metrics_registry import validate_metric_observation_metadata

        with self.assertRaises(ValueError):
            validate_metric_observation_metadata(
                "flows.bnb_stablecoin_supply_change_7d", "BNB", 0.5, self._metadata()
            )
        with self.assertRaises(ValueError):
            validate_metric_observation_metadata(
                "flows.bnb_stablecoin_supply_change_7d", "ETH", 0.01, self._metadata()
            )

    def test_the_percentile_method_cannot_publish_a_normalized_flow(self):
        from crypto_portfolio.engine.factors.flows import (
            METHOD_SUPPLY_CHANGE_PERCENTILE,
            FlowFactorResult,
        )
        from crypto_portfolio.facts.models import FlowFacts

        facts = FlowFacts(symbol="BNB", current={}, previous={}, changes={}, trends={}, coverage=1.0, freshness="CURRENT")
        with self.assertRaises(ValueError):
            FlowFactorResult(
                score=60.0, state="POSITIVE", facts=facts, confidence="MEDIUM", coverage=1.0,
                normalized_flow=0.4, method=METHOD_SUPPLY_CHANGE_PERCENTILE,
            )
        with self.assertRaises(ValueError):
            FlowFactorResult(
                score=60.0, state="POSITIVE", facts=facts, confidence="MEDIUM", coverage=1.0,
                method=METHOD_SUPPLY_CHANGE_PERCENTILE, horizons={"365d": {}},
            )

    def test_receipts_catch_a_tampered_score_and_a_mutated_input(self):
        from copy import deepcopy

        from crypto_portfolio.engine.calculation_evidence import (
            flow_calculation_evidence,
            validate_flow_calculation,
        )
        from crypto_portfolio.engine.factors.flows import calculate_flow_factor
        from crypto_portfolio.models.policy import resolve_policy

        observations = _stablecoin_observations(
            _parse_supply(_stablecoin_payload(growth=0.002), keys=_STABLE_KEYS)
        )
        value = {"asset": "BNB", "observations": [item.as_dict() for item in observations]}
        as_of = _stablecoin_as_of()
        policy = resolve_policy()
        result = calculate_flow_factor(tuple(observations), symbol="BNB")
        receipt = flow_calculation_evidence(value, symbol="BNB", as_of=as_of, policy=policy)

        class _Factor:
            evidence_ids = (receipt.id,)

        good = _Factor()
        good.score = result.score
        validated = validate_flow_calculation(
            good, {receipt.id: receipt}, symbol="BNB", as_of=as_of, policy=policy
        )
        self.assertEqual(validated["method"], "supply_change_percentile")
        self.assertIsNone(validated["normalized_flow"])
        self.assertEqual(set(validated["horizons"]), {"7d", "30d", "90d"})

        tampered = _Factor()
        tampered.score = result.score + 5.0
        with self.assertRaisesRegex(ValueError, "CALCULATION_SCORE_MISMATCH"):
            validate_flow_calculation(
                tampered, {receipt.id: receipt}, symbol="BNB", as_of=as_of, policy=policy
            )

        # Editing a rank inside the stored input changes the hash and is
        # rejected before it can be rescored.
        mutated_value = deepcopy(value)
        mutated_value["observations"][0]["metadata"]["abs_change_percentile"] = 0.99
        from dataclasses import replace as dataclass_replace

        mutated = dataclass_replace(receipt, value=mutated_value)
        with self.assertRaisesRegex(ValueError, "CALCULATION_HASH_MISMATCH"):
            validate_flow_calculation(
                good, {mutated.id: mutated}, symbol="BNB", as_of=as_of, policy=policy
            )


if __name__ == "__main__":
    unittest.main()
