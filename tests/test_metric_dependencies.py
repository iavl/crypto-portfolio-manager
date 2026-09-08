import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from crypto_portfolio.acquisition import AcquisitionManager, _expand_derived_dependencies
from crypto_portfolio.engine.metric_plan import (
    DERIVED_METRIC_DEPENDENCIES,
    RELATIVE_RETURN_DEPENDENCIES,
    MetricCollectionPlan,
    MetricRequest,
    build_metric_collection_plan,
)
from crypto_portfolio.engine.metric_normalization import normalize_metric_result
from crypto_portfolio.metrics_registry import metric_definition
from crypto_portfolio.models.portfolio import normalize_snapshot
from crypto_portfolio.providers.binance import BinanceProvider
from crypto_portfolio.providers.bybit import BybitProvider
from crypto_portfolio.providers.cache import ProviderCache
from crypto_portfolio.providers.router import ProviderRouter
from crypto_portfolio.providers.routes import build_provider_requests


NOW = "2026-09-06T00:00:00Z"


def _config():
    return {
        "providers": {"binance": {"enabled": True}},
        "cache_ttl_seconds": {"default": 3600, "spot": 600},
        "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
        "fallback": {"allow_web": False},
    }


class _MarketHistoryProvider:
    def __init__(self):
        self.calls = 0

    def collect(self, request):
        self.calls += 1
        rows = []
        for key in request.metric_keys:
            rows.append({
                "asset": request.asset,
                "metric_key": key,
                "value": 0.1,
                "unit": "fraction",
                "period": key.rsplit("_", 1)[-1],
                "observed_at": NOW,
                "fetched_at": NOW,
                "source": "binance",
                "confidence": "HIGH",
                "metadata": {
                    "venue": "BINANCE",
                    "market": "spot",
                    "quote_currency": "USDT",
                    "ohlcv_hash": "a" * 64,
                },
            })
        return rows


class _EmptyMarketHistoryProvider(_MarketHistoryProvider):
    def collect(self, _request):
        self.calls += 1
        return []


class MetricDependencyTests(unittest.TestCase):
    def test_every_declared_derived_edge_is_registered(self):
        scope_examples = {
            "valuation.fdv_market_cap_ratio": "AAVE",
            "derivatives.open_interest_to_market_cap": "BTC",
            "btc_valuation.price_to_realized_price": "BTC",
            "eth_valuation.price_to_realized_price": "ETH",
            "market.breadth_state": "MARKET",
        }
        for derived, dependencies in DERIVED_METRIC_DEPENDENCIES.items():
            with self.subTest(derived=derived):
                metric_definition(derived)
                for dependency in dependencies:
                    definition = metric_definition(dependency)
                    asset = scope_examples.get(derived, "ETH")
                    self.assertTrue(definition.applies_to(asset))

    def test_eth_realized_price_route_expands_and_derives_from_cached_inputs(self):
        plan = MetricCollectionPlan(
            "SNAPSHOT_REVIEW",
            (MetricRequest("ETH", "eth_valuation.price_to_realized_price"),),
        )
        expanded = _expand_derived_dependencies(plan)
        self.assertEqual(
            {(item.asset, item.metric_key) for item in expanded.requests},
            {
                ("ETH", "eth_valuation.price_to_realized_price"),
                ("ETH", "market.spot_price"),
                ("ETH", "eth_valuation.realized_price"),
            },
        )
        cached = tuple(normalize_metric_result({
            "asset": asset,
            "metric_key": metric,
            "value": value,
            "unit": unit,
            "observed_at": NOW,
            "fetched_at": NOW,
            "source": "fixture",
            "confidence": "HIGH",
        }).observation for asset, metric, value, unit in (
            ("ETH", "market.spot_price", 2000, "USD"),
            ("ETH", "eth_valuation.realized_price", 1500, "USD"),
        ))
        with TemporaryDirectory() as directory:
            result = AcquisitionManager(
                ProviderRouter({}, config=_config(), cache=ProviderCache(Path(directory) / "cache")),
                persist=False,
            ).run(plan, mode="AUTO", cached_observations=cached, as_of=NOW, now=NOW)
        self.assertEqual(result.results[0].status, "SUCCESS")
        self.assertAlmostEqual(result.observations[0].value, 2000 / 1500)
        self.assertEqual(result.observations[0].source, "python-derived")

    def test_relative_mapping_is_the_plan_source_of_truth(self):
        plan = build_metric_collection_plan(["ETH", "AAVE"])
        relative = {
            request.metric_key
            for request in plan.requests
            if request.metric_key.startswith("relative.return_vs_btc_")
        }
        self.assertEqual(relative, set(RELATIVE_RETURN_DEPENDENCIES))
        self.assertEqual(
            {metric: (dependency,) for metric, dependency in RELATIVE_RETURN_DEPENDENCIES.items()},
            {metric: DERIVED_METRIC_DEPENDENCIES[metric] for metric in RELATIVE_RETURN_DEPENDENCIES},
        )

    def test_excluded_watchlist_symbol_is_not_planned(self):
        plan = build_metric_collection_plan(["AAVE"], watchlist=["LUNC", "AAVE"])
        self.assertEqual(plan.excluded_assets, ("LUNC",))
        self.assertTrue(plan.for_asset("AAVE"))
        self.assertEqual(plan.for_asset("LUNC"), ())
        self.assertNotIn("LUNC", plan.discovery_required_assets)

    def test_realistic_portfolio_expands_only_active_relative_dependencies(self):
        portfolio = {
            "timestamp": NOW,
            "total_value": 1000,
            "positions": [
                {"symbol": symbol, "quantity": 1, "value_usd": 1}
                for symbol in ("BTC", "USDT", "ETH", "AAVE", "U", "USD1", "USDC", "LUNC")
            ],
        }
        normalized = normalize_snapshot(portfolio)
        positions = {item["symbol"]: item["asset_type"] for item in normalized["positions"]}
        self.assertEqual(positions["USDT"], "stablecoin")
        self.assertEqual(positions["U"], "stablecoin")
        self.assertEqual(positions["USD1"], "stablecoin")
        self.assertEqual(positions["USDC"], "stablecoin")

        plan = build_metric_collection_plan(portfolio)
        self.assertEqual(plan.excluded_assets, ("LUNC",))
        self.assertEqual(plan.discovery_required_assets, ())
        self.assertNotIn("LUNC", plan.assets)
        self.assertEqual(plan.for_asset("LUNC"), ())
        self.assertTrue(all(request.asset != "LUNC" for request in plan.requests))
        expanded = _expand_derived_dependencies(plan)
        identities = {(request.asset, request.metric_key) for request in expanded.requests}
        for asset in ("ETH", "AAVE"):
            for horizon in ("30d", "90d", "180d"):
                self.assertIn((asset, f"relative.return_vs_btc_{horizon}"), identities)
                self.assertIn((asset, f"market.return_{horizon}"), identities)
        self.assertIn(("BTC", "market.return_180d"), identities)
        self.assertNotIn("market.return_365d", {item.metric_key for item in expanded.requests})

        provider_plan = MetricCollectionPlan(
            expanded.review_type,
            tuple(
                request
                for request in expanded.requests
                if request.metric_key in {"market.return_30d", "market.return_90d", "market.return_180d"}
            ),
            assets=expanded.assets,
        )
        provider = _MarketHistoryProvider()
        with TemporaryDirectory() as directory:
            result = AcquisitionManager(
                ProviderRouter(
                    {"binance": provider},
                    config=_config(),
                    cache=ProviderCache(Path(directory) / "cache"),
                ),
                persist=False,
            ).run(provider_plan, mode="REFRESH", as_of=NOW, now=NOW, cached_observations=())
        self.assertGreater(provider.calls, 0)
        self.assertTrue(all(item.status == "SUCCESS" for item in result.results))

    def test_active_relative_dependency_reaches_provider_without_registry_error(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("BTC", "market.return_180d"),
            MetricRequest("ETH", "market.return_180d"),
            MetricRequest("ETH", "relative.return_vs_btc_180d"),
        ))
        provider = _MarketHistoryProvider()
        with TemporaryDirectory() as directory:
            result = AcquisitionManager(
                ProviderRouter(
                    {"binance": provider},
                    config=_config(),
                    cache=ProviderCache(Path(directory) / "cache"),
                ),
                persist=False,
            ).run(plan, mode="REFRESH", as_of=NOW, now=NOW, cached_observations=())
        self.assertGreater(provider.calls, 0)
        self.assertEqual(result.results[-1].status, "SUCCESS")
        self.assertEqual(result.observations[-1].metric_key, "relative.return_vs_btc_180d")
        self.assertEqual(result.observations[-1].value, 0.0)

    def test_active_provider_history_uses_recalibrated_window(self):
        requests = build_provider_requests(
            (MetricRequest("ETH", "market.return_180d"),),
            as_of=NOW,
            now=NOW,
        )
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.dataset, "ohlcv")
        start = datetime.fromisoformat(request.parameters["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(request.parameters["end"].replace("Z", "+00:00"))
        self.assertEqual((end - start).days, 240)

    def test_insufficient_active_history_is_structured_unavailability(self):
        provider = _EmptyMarketHistoryProvider()
        plan = MetricCollectionPlan(
            "SNAPSHOT_REVIEW",
            (MetricRequest("ETH", "market.return_180d"),),
        )
        with TemporaryDirectory() as directory:
            result = AcquisitionManager(
                ProviderRouter(
                    {"binance": provider},
                    config=_config(),
                    cache=ProviderCache(Path(directory) / "cache"),
                ),
                persist=False,
            ).run(plan, mode="REFRESH", as_of=NOW, now=NOW, cached_observations=())
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.results[0].status, "FAILED")
        self.assertNotIn("unknown metric key", result.results[0].event.reason.lower())

    def test_binance_and_bybit_advertise_only_active_return_metrics(self):
        for provider in (BinanceProvider(), BybitProvider()):
            self.assertTrue({"market.return_30d", "market.return_90d", "market.return_180d"}.issubset(provider.capabilities.metric_keys))
            self.assertNotIn("market.return_365d", provider.capabilities.metric_keys)

    def test_removed_365d_metric_is_not_a_current_contract(self):
        with self.assertRaises(ValueError):
            metric_definition("market.return_365d")
        with self.assertRaises(ValueError):
            metric_definition("relative.return_vs_btc_365d")


if __name__ == "__main__":
    unittest.main()
