"""Regression coverage: premium/optional skips stay separated from failures."""

import unittest

from crypto_portfolio.data_collection import collection_summary
from crypto_portfolio.metric_availability import (
    contributes_to_scoring_coverage,
    evaluate_factor_sufficiency,
    metric_availability,
    skip_reason,
)
from crypto_portfolio.models.metrics_history import CollectionEvent
from crypto_portfolio.models.policy import load_policy
from crypto_portfolio.providers.config import load_provider_config
from crypto_portfolio.providers.router import ProviderRouter


NOW = "2026-09-11T02:27:00Z"


def _event(metric, status, asset="ETH"):
    return CollectionEvent(
        f"event-{asset}-{metric}", NOW, asset, metric, status,
        source="test", reason=None if status == "SUCCESS" else "provider unavailable",
        observed_at=NOW if status == "SUCCESS" else None,
        fetched_at=NOW if status == "SUCCESS" else None,
    )


class RatedStakingPremiumClassificationTests(unittest.TestCase):
    def test_rated_staking_is_premium_only(self):
        for metric in (
            "eth.staking.active_effective_stake_eth",
            "eth.staking.active_effective_stake_change_30d",
            "flows.eth_active_stake_change_to_supply_30d",
        ):
            with self.subTest(metric=metric):
                policy = metric_availability("ETH", metric)
                self.assertEqual(policy.requirement, "PREMIUM_ONLY")
                self.assertTrue(policy.is_skippable)
                self.assertFalse(contributes_to_scoring_coverage("ETH", metric))
                self.assertEqual(
                    skip_reason(policy),
                    "PREMIUM_PROVIDER_NOT_CONFIGURED",
                )

    def test_premium_skip_is_counted_separately_from_optional(self):
        events = (
            _event("eth.staking.active_effective_stake_eth", "SKIPPED"),
            _event("fundamentals.developer_activity", "SKIPPED"),
            _event("market.return_30d", "SUCCESS"),
        )
        summary = collection_summary(events)
        self.assertEqual(summary["skipped_premium"], 1)
        self.assertEqual(summary["skipped_optional"], 1)
        self.assertEqual(summary["counts"]["SKIPPED_PREMIUM"], 1)
        self.assertEqual(summary["counts"]["SKIPPED_OPTIONAL"], 1)

    def test_premium_skip_does_not_break_factor_sufficiency(self):
        events = (
            _event("eth.staking.active_effective_stake_eth", "SKIPPED"),
            _event("eth.staking.active_effective_stake_change_30d", "SKIPPED"),
            _event("flows.eth_active_stake_change_to_supply_30d", "SKIPPED"),
            _event("fundamentals.tvl", "SUCCESS"),
            _event("fundamentals.fees_30d", "SUCCESS"),
            _event("eth.monetary.current_supply_eth", "SUCCESS"),
        )
        result = evaluate_factor_sufficiency("fundamentals", events, policy=load_policy(), asset="ETH")
        self.assertEqual(result.status, "SUFFICIENT")
        self.assertNotIn("eth.staking.active_effective_stake_eth", result.missing)


class RatedSubscriptionConfigTests(unittest.TestCase):
    def _config(self, **rated_settings):
        return {
            "providers": {"rated": {"enabled": True, **rated_settings}},
            "cache_ttl_seconds": {"default": 3600},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": False},
        }

    def test_subscription_active_defaults_to_true(self):
        from crypto_portfolio.providers.config import provider_settings

        config = self._config()
        self.assertNotIn("subscription_active", provider_settings("rated", config))

    def test_invalid_subscription_active_flag_is_rejected(self):
        import tempfile
        import json as _json
        from pathlib import Path as _Path


        raw = {
            "providers": {"rated": {"enabled": True, "subscription_active": "no"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = _Path(directory) / "providers.json"
            path.write_text(_json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_provider_config(path)

    def test_inactive_subscription_removes_rated_from_active_providers(self):
        config = self._config(subscription_active=False)
        router = ProviderRouter(config=config)
        self.assertNotIn("rated", router.providers)

    def test_btc_cycle_context_metrics_stay_optional(self):
        for metric in ("onchain.btc.sopr", "onchain.btc.lth_net_position_change"):
            with self.subTest(metric=metric):
                policy = metric_availability("BTC", metric)
                self.assertEqual(policy.requirement, "OPTIONAL")
                self.assertTrue(policy.is_skippable)
                self.assertFalse(contributes_to_scoring_coverage("BTC", metric))


if __name__ == "__main__":
    unittest.main()
