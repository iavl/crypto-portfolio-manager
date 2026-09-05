"""Delivery basis must retain contract identity and never fabricate missing data."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from crypto_portfolio.acquisition import AcquisitionManager
from crypto_portfolio.engine.metric_normalization import normalize_metric_result
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest
from crypto_portfolio.engine.metrics import annualized_futures_basis
from crypto_portfolio.providers.base import ProviderCapabilities, ProviderDataError, ProviderRequest, ProviderUnsupportedMetric
from crypto_portfolio.providers.binance import BinanceProvider
from crypto_portfolio.providers.cache import ProviderCache, request_hash
from crypto_portfolio.providers.router import ProviderRouter
from crypto_portfolio.providers.routes import BASIS_METHODOLOGY, build_provider_requests, provider_chain


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)
KEY = "derivatives.futures_basis_annualized"


def contract(days=20, **changes):
    expiry = NOW + timedelta(days=days)
    return {
        "symbol": "BTCUSDT_" + expiry.strftime("%y%m%d"), "pair": "BTCUSDT",
        "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING",
        "contractType": "CURRENT_QUARTER", "deliveryDate": int(expiry.timestamp() * 1000),
        "onboardDate": int((NOW - timedelta(days=90)).timestamp() * 1000), **changes,
    }


class BasisClient:
    def __init__(self):
        self.contracts = [contract(110, contractType="NEXT_QUARTER"), contract()]
        self.quote = {
            "symbol": contract()["symbol"], "markPrice": "102", "indexPrice": "100",
            "time": int((NOW - timedelta(minutes=1)).timestamp() * 1000),
        }
        self.calls = []

    def get_json(self, url, *, params=None, headers=None):
        self.calls.append((url, params))
        if url.endswith("/exchangeInfo"):
            return {"symbols": self.contracts}
        if url.endswith("/premiumIndex"):
            return self.quote
        raise AssertionError(f"unexpected endpoint: {url}")


class DeliveryBasisTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = ProviderCache(Path(self.directory.name) / "cache")
        self.client = BasisClient()
        self.provider = BinanceProvider(client=self.client, clock=lambda: NOW)
        self.plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (MetricRequest("BTC", KEY),))
        self.request = build_provider_requests(self.plan.requests, now=NOW)[0]
        self.config = {
            "version": 1, "providers": {"binance": {"enabled": True}, "bybit": {"enabled": True}},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": True},
        }
        self.router = ProviderRouter({"binance": self.provider}, config=self.config, cache=self.cache)
        self.manager = AcquisitionManager(self.router, persist=False)

    def test_act365_is_signed_fraction_and_validates_inputs(self):
        for price, expected in ((102, .2), (98, -.2), (100, 0)):
            self.assertAlmostEqual(annualized_futures_basis(price, 100, 36.5 * 86400), expected)
        for bad in (True, 0, -1, float("nan"), float("inf")):
            for index in range(3):
                values = [102, 100, 86400]
                values[index] = bad
                with self.subTest(values=values), self.assertRaises(ValueError):
                    annualized_futures_basis(*values)
        with self.assertRaises(ValueError):
            annualized_futures_basis(1e308, 1e-308, 1)

    def test_nearest_delivery_uses_exact_symbol_and_observed_remaining_time(self):
        for mark, expected_basis in (("102", .02), ("98", -.02), ("100", 0)):
            self.client.quote["markPrice"] = mark
            observation = self.provider.collect(self.request)[0]
            self.assertAlmostEqual(observation["value"], expected_basis * 365 * 86400 / (20 * 86400 + 60))
            self.assertEqual(observation["unit"], "fraction")
            self.assertEqual(observation["observed_at"], "2026-09-04T23:59:00Z")
            metadata = observation["metadata"]
            self.assertEqual(metadata["contract"], "BTCUSDT_260925")
            self.assertEqual(metadata["delivery_at"], "2026-09-25T00:00:00Z")
            self.assertEqual(metadata["methodology"], BASIS_METHODOLOGY)
            self.assertEqual(self.client.calls[-1][1], {"symbol": "BTCUSDT_260925"})

    def test_no_delivery_contract_is_unsupported_not_zero(self):
        for item in (
            contract(contractType="PERPETUAL"), contract(status="SETTLING"),
            contract(0), contract(-1), contract(quoteAsset="USDC"), contract(baseAsset="ETH"),
            contract(onboardDate=int((NOW + timedelta(days=1)).timestamp() * 1000)),
        ):
            with self.subTest(contract=item):
                self.client.contracts = [item]
                with self.assertRaises(ProviderUnsupportedMetric):
                    self.provider.collect(self.request)
        self.client.contracts = []
        with self.assertRaises(ProviderUnsupportedMetric):
            self.provider.collect(self.request)
        self.assertTrue(all(call[0].endswith("/exchangeInfo") for call in self.client.calls))

    def test_invalid_quote_fields_are_not_normalized(self):
        original = dict(self.client.quote)
        invalid = [
            (field, bad) for field in ("markPrice", "indexPrice")
            for bad in (None, "", "NaN", "Infinity", True, "0", "-1")
        ] + [("symbol", "BTCUSDT"), ("time", None), ("time", True)]
        for field, bad in invalid:
            with self.subTest(field=field, value=bad):
                self.client.quote = {**original, field: bad}
                with self.assertRaises(ProviderDataError):
                    self.provider.collect(self.request)

    def test_stale_future_and_pre_listing_quotes_are_rejected(self):
        for timestamp in (NOW - timedelta(days=2), NOW + timedelta(seconds=1), NOW - timedelta(days=91)):
            with self.subTest(timestamp=timestamp):
                self.client.quote["time"] = int(timestamp.timestamp() * 1000)
                with self.assertRaises(ProviderDataError):
                    self.provider.collect(self.request)

    def test_invalid_contract_metadata_is_rejected(self):
        for field, value in (("deliveryDate", None), ("deliveryDate", True), ("onboardDate", None), ("symbol", "BTCUSDT")):
            with self.subTest(field=field):
                self.client.contracts = [contract(**{field: value})]
                with self.assertRaises(ProviderDataError):
                    self.provider.collect(self.request)

    def test_historical_network_fetch_does_not_guess_old_contracts(self):
        request = build_provider_requests(self.plan.requests, as_of=NOW - timedelta(days=1), now=NOW)[0]
        with self.assertRaisesRegex(ProviderUnsupportedMetric, "historical delivery basis"):
            self.provider.collect(request)
        self.assertEqual(self.client.calls, [])

    def test_router_as_of_and_cached_payload_cannot_bypass_history_cutoff(self):
        past = (NOW - timedelta(days=1)).isoformat()
        result = self.router.collect((self.request,), as_of=past, now=NOW)
        self.assertEqual(result.observations, ())
        self.assertEqual(result.attempts[0].error_code, "PROVIDER_UNSUPPORTED")
        self.assertEqual(self.client.calls, [])
        raw = self.provider.collect(self.request)[0]
        historical_request = build_provider_requests(self.plan.requests, as_of=past, now=NOW)[0]
        self.cache.save_response(historical_request, [raw], fetched_at=NOW)
        self.client.calls.clear()
        result = self.router.collect((historical_request,), mode="CACHE_ONLY", now=NOW)
        self.assertEqual(result.observations, ())
        self.assertEqual(self.client.calls, [])

    def test_routing_and_request_identity_isolate_old_perpetual_basis(self):
        self.assertEqual(provider_chain(KEY), ("binance",))
        self.assertEqual(provider_chain("derivatives.funding_rate"), ("binance", "bybit"))
        old_parameters = {**self.request.parameters, "market": "perpetual"}
        old_parameters.pop("methodology")
        old = ProviderRequest("binance", "basis", "BTC", old_parameters, (KEY,))
        self.assertNotEqual(request_hash(old), request_hash(self.request))
        self.assertEqual(self.request.parameters["market"], "delivery")
        explicit = build_provider_requests(self.plan.requests, as_of=NOW, now=NOW)[0]
        self.assertEqual(explicit.parameters["as_of"], "2026-09-05T00:00:00Z")

    def test_acquisition_success_and_cache_only_reuse(self):
        first = self.manager.run(self.plan, now=NOW, cached_observations=())
        self.assertEqual(first.results[0].status, "SUCCESS")
        self.assertEqual(first.web_fallbacks, ())
        self.assertEqual(len(first.attempts), 1)
        second = self.manager.run(self.plan, mode="CACHE_ONLY", now=NOW, cached_observations=())
        self.assertEqual(second.results[0].status, "SUCCESS")
        self.assertEqual(second.summary["provider_cache_hits"], 1)
        self.assertEqual(len(self.client.calls), 2)
        historical = self.manager.run(self.plan, mode="CACHE_ONLY", now=NOW, as_of=NOW.isoformat(), cached_observations=first.observations)
        self.assertEqual(historical.summary["fresh_observation_hits"], 1)

    def test_legacy_normalized_basis_is_history_only_and_refreshes(self):
        raw = self.provider.collect(self.request)[0]
        raw["metadata"] = {"contract": "PERPETUAL"}
        legacy = normalize_metric_result(raw, now=NOW).observation
        before = legacy.as_dict()
        self.client.calls.clear()
        result = self.manager.run(self.plan, now=NOW, cached_observations=(legacy,))
        self.assertEqual(result.summary["fresh_observation_hits"], 0)
        self.assertEqual(result.results[0].status, "SUCCESS")
        self.assertEqual(result.observations[0].metadata["methodology"], BASIS_METHODOLOGY)
        self.assertEqual(legacy.as_dict(), before)
        self.assertEqual(len(self.client.calls), 2)

    def test_legacy_and_expired_response_caches_are_not_reused(self):
        raw = self.provider.collect(self.request)[0]
        for metadata in ({"contract": "PERPETUAL"}, {**raw["metadata"], "delivery_at": NOW.isoformat()}):
            with self.subTest(metadata=metadata):
                value = {**raw, "metadata": metadata}
                self.cache.save_response(self.request, [value], fetched_at=NOW)
                self.client.calls.clear()
                result = self.manager.run(self.plan, mode="CACHE_ONLY", now=NOW, cached_observations=())
                self.assertEqual(result.results[0].status, "FAILED")
                self.assertEqual(result.observations, ())
                self.assertEqual(self.client.calls, [])
                observation = normalize_metric_result(value, now=NOW).observation
                result = self.manager.run(self.plan, mode="CACHE_ONLY", now=NOW, cached_observations=(observation,))
                self.assertEqual(result.summary["fresh_observation_hits"], 0)
                self.assertEqual(result.observations, ())

    def test_liquidation_no_route_keeps_null_and_existing_web_policy(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (MetricRequest("BTC", "derivatives.total_liquidations_24h_usd"),))
        for mode, allow_web, expected_web in (("AUTO", True, 1), ("AUTO", False, 0), ("CACHE_ONLY", True, 0)):
            with self.subTest(mode=mode, allow_web=allow_web):
                self.router.config["fallback"]["allow_web"] = allow_web
                result = self.manager.run(plan, mode=mode, now=NOW, cached_observations=())
                self.assertEqual(result.results[0].status, "FAILED")
                self.assertEqual(result.events[0].refresh_error_code, "NO_PROVIDER_ROUTE")
                self.assertIn("no configured structured provider route", result.events[0].reason)
                self.assertEqual(result.observations, ())
                self.assertEqual(result.attempts, ())
                self.assertEqual(len(result.web_fallbacks), expected_web)
        self.assertEqual(self.client.calls, [])

    def test_unsupported_capability_and_bad_payload_have_distinct_diagnostics(self):
        self.provider.capabilities = ProviderCapabilities("binance")
        unsupported = self.manager.run(self.plan, now=NOW, cached_observations=())
        self.assertEqual(unsupported.events[0].refresh_error_code, "PROVIDER_UNSUPPORTED")
        self.assertEqual(len(unsupported.attempts), 1)
        self.provider.capabilities = BinanceProvider().capabilities
        self.client.quote["indexPrice"] = "NaN"
        failed = self.manager.run(self.plan, now=NOW, cached_observations=())
        self.assertEqual(failed.events[0].refresh_error_code, "PROVIDER_SCHEMA_ERROR")
        self.assertEqual(len(failed.attempts), 1)


if __name__ == "__main__":
    unittest.main()
