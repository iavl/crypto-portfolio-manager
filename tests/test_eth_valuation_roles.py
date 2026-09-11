"""Regression coverage: ETH realized-price redundancy must not hurt scoring.

MVRV is the primary ETH valuation scoring signal; realized-price context and
the price/realized-price ratio must never re-enter scoring coverage.
"""

import unittest

from crypto_portfolio.acquisition import AcquisitionManager
from crypto_portfolio.engine.confidence import calculate_data_confidence
from crypto_portfolio.engine.metric_plan import (
    MetricCollectionPlan,
    MetricRequest,
    build_metric_collection_plan,
)
from crypto_portfolio.engine.metric_normalization import normalize_metric_result
from crypto_portfolio.metric_availability import (
    contributes_to_scoring_coverage,
    evaluate_factor_sufficiency,
    metric_availability,
)
from crypto_portfolio.metrics_registry import metric_definition
from crypto_portfolio.models.metrics_history import CollectionEvent
from crypto_portfolio.models.policy import load_policy
from crypto_portfolio.providers.cache import ProviderCache
from crypto_portfolio.providers.router import ProviderRouter

from pathlib import Path
from tempfile import TemporaryDirectory


AS_OF = "2026-09-11T02:27:00Z"


def _event(metric, status, asset="ETH"):
    return CollectionEvent(
        f"event-{asset}-{metric}", AS_OF, asset, metric, status,
        source="test", reason=None if status == "SUCCESS" else "provider unavailable",
        observed_at=AS_OF if status == "SUCCESS" else None,
        fetched_at=AS_OF if status == "SUCCESS" else None,
    )


def _observation(asset, metric, value, unit):
    return normalize_metric_result({
        "asset": asset,
        "metric_key": metric,
        "value": value,
        "unit": unit,
        "observed_at": AS_OF,
        "fetched_at": AS_OF,
        "source": "fixture",
        "confidence": "HIGH",
    }).observation


def _config():
    return {
        "providers": {},
        "cache_ttl_seconds": {"default": 3600, "spot": 600},
        "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
        "fallback": {"allow_web": False},
    }


class EthValuationRoleTests(unittest.TestCase):
    def test_eth_mvrv_is_the_primary_scoring_signal(self):
        definition = metric_definition("eth_valuation.mvrv")
        self.assertEqual(definition.decision_role, "SCORING_FACTOR")
        self.assertEqual(metric_availability("ETH", "eth_valuation.mvrv").requirement, "PRIMARY")

    def test_eth_realized_price_is_context_only(self):
        for key in ("eth_valuation.realized_price", "eth_valuation.realized_cap_usd"):
            with self.subTest(metric=key):
                definition = metric_definition(key)
                self.assertEqual(definition.direction, "CONTEXTUAL")
                self.assertEqual(definition.decision_role, "EXECUTION_CONTEXT")
                self.assertEqual(definition.context_group, "eth_valuation")
                self.assertEqual(metric_availability("ETH", key).requirement, "OPTIONAL")
                self.assertFalse(contributes_to_scoring_coverage("ETH", key))

    def test_eth_price_to_realized_price_keeps_registry_key_but_is_not_scored(self):
        definition = metric_definition("eth_valuation.price_to_realized_price")
        self.assertEqual(definition.decision_role, "EXECUTION_CONTEXT")
        self.assertFalse(contributes_to_scoring_coverage("ETH", "eth_valuation.price_to_realized_price"))

    def test_eth_price_to_realized_price_not_in_default_plan(self):
        plan = build_metric_collection_plan(["BTC", "ETH", "AAVE"])
        requested = {
            (request.asset, request.metric_key)
            for request in plan.requests
        }
        self.assertNotIn(("ETH", "eth_valuation.price_to_realized_price"), requested)
        self.assertIn(("ETH", "eth_valuation.mvrv"), requested)
        self.assertIn(("ETH", "eth_valuation.realized_price"), requested)

    def test_btc_valuation_roles_are_unchanged(self):
        self.assertEqual(metric_definition("btc_valuation.realized_price").decision_role, "SCORING_FACTOR")
        self.assertEqual(metric_definition("btc_valuation.price_to_realized_price").decision_role, "SCORING_FACTOR")
        self.assertEqual(metric_definition("btc_valuation.realized_cap_usd").decision_role, "EXECUTION_CONTEXT")
        self.assertTrue(contributes_to_scoring_coverage("BTC", "btc_valuation.realized_price"))
        self.assertTrue(contributes_to_scoring_coverage("BTC", "btc_valuation.price_to_realized_price"))

    def test_eth_derived_dependency_is_not_part_of_the_default_graph_entry(self):
        # The default ETH plan never requests the redundant ratio, so its
        # DERIVED_INPUT_UNAVAILABLE tail can no longer occur; the explicit
        # derivation helper remains available for direct requests.
        plan = build_metric_collection_plan(["ETH"])
        self.assertNotIn(
            ("ETH", "eth_valuation.price_to_realized_price"),
            {(request.asset, request.metric_key) for request in plan.requests},
        )


class EthValuationSufficiencyTests(unittest.TestCase):
    def test_eth_mvrv_alone_satisfies_valuation(self):
        events = (
            _event("eth_valuation.mvrv", "SUCCESS"),
            _event("eth_valuation.realized_price", "FAILED"),
            _event("eth_valuation.realized_cap_usd", "FAILED"),
        )
        result = evaluate_factor_sufficiency("valuation", events, policy=load_policy(), asset="ETH")
        self.assertEqual(result.status, "SUFFICIENT")
        self.assertEqual(result.primary_available, 1)
        self.assertEqual(result.missing, ())

    def test_eth_realized_price_failure_is_not_a_required_scoring_failure(self):
        events = (
            _event("eth_valuation.mvrv", "SUCCESS"),
            _event("eth_valuation.realized_price", "FAILED"),
        )
        result = evaluate_factor_sufficiency("valuation", events, policy=load_policy(), asset="ETH")
        self.assertNotIn("eth_valuation.realized_price", result.missing)
        self.assertEqual(result.status, "SUFFICIENT")

    def test_optional_eth_realized_price_failure_does_not_lower_scoring_coverage(self):
        failing = calculate_data_confidence(
            (
                {"asset": "ETH", "metric_key": "eth_valuation.mvrv", "value": 1.1, "observed_at": AS_OF, "source": "a", "source_group": "a"},
            ),
            metric_weights={"eth_valuation.mvrv": 1.0},
            as_of=AS_OF,
            policy=load_policy(),
        )
        with_context = calculate_data_confidence(
            (
                {"asset": "ETH", "metric_key": "eth_valuation.mvrv", "value": 1.1, "observed_at": AS_OF, "source": "a", "source_group": "a"},
                {"asset": "ETH", "metric_key": "eth_valuation.realized_price", "value": 2400.0, "observed_at": AS_OF, "source": "a", "source_group": "a"},
            ),
            metric_weights={"eth_valuation.mvrv": 1.0, "eth_valuation.realized_price": 0.0},
            as_of=AS_OF,
            policy=load_policy(),
        )
        self.assertAlmostEqual(
            failing.dimensions["coverage"].score,
            with_context.dimensions["coverage"].score,
        )


class EthValuationAcquisitionTests(unittest.TestCase):
    def test_mvrv_success_with_realized_price_unavailable_keeps_valuation_available(self):
        class FixtureProvider:
            def __init__(self, values):
                self.values = values

            def collect(self, request):
                return [dict(item) for item in self.values.get(request.dataset, ())]

        providers = {
            "coinmetrics_community": FixtureProvider({
                "ethereum_valuation": (
                    {
                        "asset": "ETH",
                        "metric_key": "eth_valuation.mvrv",
                        "value": 1.1,
                        "unit": "ratio",
                        "observed_at": AS_OF,
                        "fetched_at": AS_OF,
                        "source": "coinmetrics_community",
                        "confidence": "HIGH",
                    },
                ),
            }),
        }
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("ETH", "eth_valuation.mvrv"),
            MetricRequest("ETH", "eth_valuation.realized_price"),
            MetricRequest("ETH", "eth_valuation.realized_cap_usd"),
        ))
        with TemporaryDirectory() as directory:
            result = AcquisitionManager(
                ProviderRouter(providers, config=_config(), cache=ProviderCache(Path(directory) / "cache")),
                persist=False,
            ).run(plan, mode="AUTO", cached_observations=(), as_of=AS_OF, now=AS_OF)
        statuses = {item.event.metric_key: item.status for item in result.results}
        self.assertEqual(statuses["eth_valuation.mvrv"], "SUCCESS")
        self.assertEqual(statuses["eth_valuation.realized_price"], "SKIPPED")
        self.assertEqual(statuses["eth_valuation.realized_cap_usd"], "SKIPPED")
        summary = result.summary
        self.assertNotIn(
            "eth_valuation.mvrv",
            {item.get("metric_key") for item in summary.get("required_scoring_failures", ())},
        )
        optional_keys = {
            item.get("metric_key")
            for item in summary.get("optional_data_unavailable", summary.get("optional_data", ()))
        }
        self.assertIn("eth_valuation.realized_price", optional_keys)


if __name__ == "__main__":
    unittest.main()
